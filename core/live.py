#!/usr/bin/env python3

"""

live.py — 24/7 background live message interceptor and buffer updater.

"""

import os

import json

import logging

import asyncio

from datetime import datetime

from telethon import TelegramClient, events, types, functions

from telethon.tl.types import (

    User, DocumentAttributeFilename, DocumentAttributeAudio, DocumentAttributeVideo,

    DocumentAttributeAnimated, DocumentAttributeSticker,

    MessageActionChatMigrateTo, MessageActionChannelMigrateFrom

)

from . import db

from . import helpers

from .db import (

    get_db_conn, get_live_db_conn, init_live_db, load_all_users_from_db,

    get_chat_pipeline, get_chat_settings, get_global_settings, mark_live_buffer_dirty

)

from .helpers import (

    get_chat_id_variants, are_chat_ids_equal, sanitize_filename,

    fetch_and_save_user, dt_converter, get_media_target_dir, ensure_chat_media_subdirs

)

from .emoji import extract_custom_emoji_ids_from_message, async_fetch_and_save_custom_emojis

import core



_ARCHIVED_CHATS_CACHE = {}

_LIVE_USERS_CACHE = {}



def get_live_users_cache():

    global _LIVE_USERS_CACHE

    if not _LIVE_USERS_CACHE:

        try:

            m_conn = get_db_conn()

            m_cur = m_conn.cursor()

            _LIVE_USERS_CACHE = load_all_users_from_db(m_cur)

            m_conn.close()

        except Exception:

            _LIVE_USERS_CACHE = {}

    return _LIVE_USERS_CACHE



def is_chat_archived_in_main(chat_id):

    """Проверяет, скачан ли данный чат (или связанный через миграцию) в основную базу данных."""

    if not chat_id:

        return False

    import time

    now = time.time()

    if chat_id in _ARCHIVED_CHATS_CACHE:

        cached_res, exp = _ARCHIVED_CHATS_CACHE[chat_id]

        if now < exp:

            return cached_res



    try:

        conn = get_db_conn()

        cursor = conn.cursor()

        pipeline = get_chat_pipeline(cursor, chat_id)

        all_ids = set()

        for cid in pipeline:

            all_ids.update(get_chat_id_variants(cid))

        if not all_ids:

            all_ids.update(get_chat_id_variants(chat_id))

        variants = list(all_ids)

        placeholders = ','.join('?' for _ in variants)

        cursor.execute(f"""

            SELECT 1 FROM tbl_chats 

            WHERE chat_id IN ({placeholders})

              AND ((last_backup_offset_id IS NOT NULL AND last_backup_offset_id > 0)

                   OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = tbl_chats.chat_id))

        """, variants)

        row = cursor.fetchone()

        conn.close()

        res = row is not None

        _ARCHIVED_CHATS_CACHE[chat_id] = (res, now + 60.0)

        return res

    except Exception as e:

        logging.error('Exception caught in is_chat_archived_in_main', exc_info=True)

        return False



def is_message_already_logged(chat_id, message_id):

    """Проверяет, не сохранено ли уже это сообщение в основной или буферной БД."""

    if not chat_id or not message_id:

        return False

    try:

        main_conn = get_db_conn()

        main_cur = main_conn.cursor()

        pipeline = get_chat_pipeline(main_cur, chat_id)

        all_ids = set()

        for cid in pipeline:

            all_ids.update(get_chat_id_variants(cid))

        if not all_ids:

            all_ids.update(get_chat_id_variants(chat_id))

        variants = list(all_ids)

        placeholders = ','.join('?' for _ in variants)

        params = variants + [message_id]

        where_clause = f"chat_id IN ({placeholders}) AND message_id = ?"



        main_cur.execute(f"SELECT 1 FROM tbl_messages WHERE {where_clause}", params)

        in_main = main_cur.fetchone() is not None

        main_conn.close()

        if in_main:

            return True



        live_conn = get_live_db_conn()

        live_cur = live_conn.cursor()

        live_cur.execute(f"SELECT 1 FROM tbl_messages WHERE {where_clause}", params)

        in_live = live_cur.fetchone() is not None

        live_conn.close()

        return in_live

    except Exception as e:

        logging.error('Exception caught in is_message_already_logged', exc_info=True)

        return False



LIVE_CLIENT = None

LIVE_STARTING = False

LIVE_CALLBACK = None

LIVE_TASK = None

LIVE_REFRESH_TASK = None

LIVE_CATCHUP_TASK = None

ACTIVE_USER_STATUS = {}



async def _periodic_chatlist_refresh():

    """Периодически обновляет кэш списка диалогов Telegram в фоне."""

    await asyncio.sleep(45)

    while LIVE_CLIENT and LIVE_CLIENT.is_connected():

        try:

            if not getattr(core, 'IS_BULK_ARCHIVING', False) and os.path.exists(core.CACHE_FILE):

                from . import worker

                if worker.TELETHON_LOCK.acquire(blocking=False):

                    try:

                        dialogs = await core.async_quick_fetch_and_cache_dialogs(client=LIVE_CLIENT)

                        if dialogs is not None and LIVE_CALLBACK:

                            LIVE_CALLBACK('live_dialogs_refreshed', {'dialogs': dialogs})

                    except Exception as e_ref:

                        logging.warning(f"[LiveArchiver] Error during periodic dialog list refresh: {e_ref}")

                    finally:

                        worker.TELETHON_LOCK.release()

        except asyncio.CancelledError:

            break

        except Exception as e:

            logging.error("[LiveArchiver] Exception in _periodic_chatlist_refresh", exc_info=True)



        try:

            await asyncio.sleep(600)  # каждые 10 минут

        except asyncio.CancelledError:

            break




from .live_media import process_live_media



def _save_live_message_to_buffer(msg, chat_id, safe_name, m_res, sender_id_val=None, reactions_dict=None):

    """Saves a fully processed message into live_stream_backup.db buffer."""

    if sender_id_val is None:

        sender_id_val = getattr(msg, 'sender_id', None)

    msg_date_str = msg.date.isoformat() if getattr(msg, 'date', None) else datetime.utcnow().isoformat()

    reply_to_id_val = msg.reply_to_msg_id if getattr(msg, 'reply_to', None) else None

    entities_json = json.dumps([e.to_dict() for e in msg.entities], default=dt_converter) if getattr(msg, 'entities', None) else None

    

    fwd_data = None

    if getattr(msg, 'fwd_from', None):

        fwd = msg.fwd_from

        fwd_peer = getattr(getattr(msg, 'forward', None), 'chat', None) or getattr(getattr(msg, 'forward', None), 'sender', None)

        if not fwd_peer:

            fwd_peer = getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None) or getattr(fwd, 'saved_from_peer', None)



        from_id_val = None

        if hasattr(fwd_peer, 'id'): from_id_val = fwd_peer.id

        elif hasattr(fwd_peer, 'channel_id'): from_id_val = fwd_peer.channel_id

        elif hasattr(fwd_peer, 'user_id'): from_id_val = fwd_peer.user_id

        elif hasattr(fwd_peer, 'chat_id'): from_id_val = fwd_peer.chat_id

        elif isinstance(fwd_peer, int): from_id_val = fwd_peer

        elif getattr(fwd, 'saved_from_id', None): from_id_val = fwd.saved_from_id



        from_name_val = getattr(fwd_peer, 'title', None) or getattr(fwd_peer, 'first_name', None) or getattr(fwd, 'from_name', None) or getattr(fwd, 'saved_from_name', None)

        if hasattr(fwd_peer, 'last_name') and fwd_peer.last_name:

            from_name_val = f"{from_name_val} {fwd_peer.last_name}"



        fwd_data = {

            "from_id_raw": getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None),

            "from_id": from_id_val,

            "from_name": from_name_val,

            "story_id": getattr(fwd, 'story_id', None),

            "channel_post": getattr(fwd, 'channel_post', None),

            "post_author": getattr(fwd, 'post_author', None),

            "header_from_name": getattr(fwd, 'from_name', None),

            "saved_from_msg_id": getattr(fwd, 'saved_from_msg_id', None),

            "saved_from_id": getattr(fwd, 'saved_from_id', None),

            "saved_from_name": getattr(fwd, 'saved_from_name', None),

            "saved_date": dt_converter(getattr(fwd, 'saved_date', None)),

            "saved_from_peer": getattr(fwd, 'saved_from_peer', None),

            "imported": getattr(fwd, 'imported', None),

            "psa_type": getattr(fwd, 'psa_type', None),

        }

    fwd_json = json.dumps(fwd_data, default=dt_converter) if fwd_data else None



    action_json = None

    if getattr(msg, 'action', None):

        try:

            action_json = json.dumps(msg.action.to_dict(), default=dt_converter)

        except Exception:

            pass



    reactions_json = None

    if reactions_dict is not None:

        try:

            reactions_json = json.dumps(reactions_dict, default=dt_converter)

        except Exception:

            pass

    elif getattr(msg, 'reactions', None):

        try:

            reactions_json = json.dumps(msg.reactions.to_dict(), default=dt_converter)

        except Exception:

            pass



    poll_json = json.dumps(m_res['poll_data'], default=dt_converter) if m_res.get('poll_data') else None

    other_json = json.dumps(m_res['media_other_data'], default=dt_converter) if m_res.get('media_other_data') else None



    conn = get_live_db_conn()

    cursor = conn.cursor()

    init_live_db(cursor)

    try:

        cursor.execute(

            "INSERT OR IGNORE INTO tbl_chats (chat_id, chat_name, safe_folder_name) VALUES (?, ?, ?)",

            (chat_id, f"Chat_{chat_id}", safe_name)

        )

        cursor.execute("""

            INSERT OR REPLACE INTO tbl_messages (

                message_id, chat_id, sender_id, date, text, reply_to_msg_id,

                media_local_path, thumbnail_local_path, media_original_name,

                media_mime_type, media_skipped,

                group_id, forward_info_json, service_action_json, poll_data_json, entities_json,

                is_media_spoiler, reactions_json, media_other_json, views_count, via_bot_id,

                post_author_name, reply_markup_json, is_pinned, is_outgoing, is_mentioned,

                is_post, invert_media, is_legacy,

                is_voice, is_round_video, video_cover_path, media_type,

                video_timestamp, from_scheduled, forwards, is_deleted, file_size,

                duration, waveform_data

            ) VALUES (

                ?, ?, ?, ?, ?, ?,

                ?, ?, ?,

                ?, ?,

                ?, ?, ?, ?, ?,

                ?, ?, ?, ?, ?,

                ?, ?, ?, ?, ?,

                ?, ?, ?,

                ?, ?, ?, ?,

                ?, ?, ?, 0, ?,

                ?, ?

            )

        """, (

            msg.id, chat_id, sender_id_val, msg_date_str, msg.raw_text or "", reply_to_id_val,

            m_res.get('media_local_path'), m_res.get('thumbnail_local_path'), m_res.get('media_original_name'),

            m_res.get('media_mime_type'), m_res.get('media_skipped'),

            getattr(msg, 'grouped_id', None), fwd_json, action_json, poll_json, entities_json,

            1 if getattr(msg, 'spoiler', False) or (getattr(msg, 'media', None) and getattr(msg.media, 'spoiler', False)) else 0,

            reactions_json, other_json, getattr(msg, 'views', None), getattr(msg, 'via_bot_id', None),

            getattr(msg, 'post_author', None),

            json.dumps(msg.reply_markup.to_dict(), default=dt_converter) if getattr(msg, 'reply_markup', None) else None,

            1 if getattr(msg, 'pinned', False) else 0,

            1 if getattr(msg, 'out', False) else 0,

            1 if getattr(msg, 'mentioned', False) else 0,

            1 if getattr(msg, 'post', False) else 0,

            1 if getattr(msg, 'invert_media', False) else 0,

            1 if getattr(msg, 'legacy', False) else 0,

            m_res.get('is_voice', 0), m_res.get('is_round_video', 0), m_res.get('video_cover_path'), m_res.get('media_type'),

            m_res.get('video_timestamp'), 1 if getattr(msg, 'from_scheduled', False) else 0, getattr(msg, 'forwards', None),

            m_res.get('file_size', 0),

            m_res.get('duration', 0), m_res.get('waveform_data')

        ))

        conn.commit()

        mark_live_buffer_dirty()

    finally:

        conn.close()



async def start_live_archiver(callback=None):

    """Запускает фоновый перехват входящих/исходящих сообщений в реальном времени (без меток прочтения)."""

    global LIVE_CLIENT, LIVE_CALLBACK, LIVE_TASK, LIVE_REFRESH_TASK, LIVE_CATCHUP_TASK, LIVE_STARTING

    LIVE_CALLBACK = callback

    if LIVE_CLIENT and LIVE_CLIENT.is_connected():

        return True



    LIVE_STARTING = True

    if not core.API_ID or not core.API_HASH:
        logging.info("[LiveArchiver] api_id or api_hash not configured in config.ini. Live archiver idle.")
        LIVE_STARTING = False
        return False

    try:

        LIVE_CLIENT = None

        for attempt in range(6):

            try:

                client_instance = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)

                await client_instance.connect()

                LIVE_CLIENT = client_instance

                break

            except Exception as e_conn:

                try:

                    await client_instance.disconnect()

                except Exception:

                    pass

                if "locked" in str(e_conn).lower() and attempt < 5:

                    logging.warning(f"[LiveArchiver] Session database locked, retrying in 1.5s (attempt {attempt+1}/6)...")

                    await asyncio.sleep(1.5)

                    continue

                raise e_conn



        if not LIVE_CLIENT or not await LIVE_CLIENT.is_user_authorized():

            if LIVE_CLIENT:

                await LIVE_CLIENT.disconnect()

            LIVE_CLIENT = None

            LIVE_STARTING = False

            return False

        LIVE_STARTING = False



        @LIVE_CLIENT.on(events.NewMessage)

        async def _on_new_message(event):

            try:

                msg = event.message

                chat_id = event.chat_id

                if not chat_id: return



                # Check for chat migration events dynamically

                action = getattr(msg, 'action', None)

                if action:

                    if isinstance(action, types.MessageActionChatMigrateTo) or type(action).__name__ == 'MessageActionChatMigrateTo':

                        new_sg_id = int(f"-100{action.channel_id}")

                        try:

                            mig_conn = get_db_conn()

                            mig_cur = mig_conn.cursor()

                            mig_cur.execute("UPDATE tbl_chats SET migrated_to_chat_id = ? WHERE chat_id = ?", (new_sg_id, chat_id))

                            mig_cur.execute("""

                                INSERT OR IGNORE INTO tbl_chats (chat_id, chat_name, safe_folder_name, archive_settings_json, last_backup_offset_id)

                                SELECT ?, chat_name, safe_folder_name, archive_settings_json, 0 FROM tbl_chats WHERE chat_id = ?

                            """, (new_sg_id, chat_id))

                            mig_conn.commit()

                            mig_conn.close()

                        except Exception as e_mig:

                            logging.debug(f"[LiveArchiver] Error linking migrate_to: {e_mig}")

                    elif isinstance(action, types.MessageActionChannelMigrateFrom) or type(action).__name__ == 'MessageActionChannelMigrateFrom':

                        old_cid = -action.chat_id if action.chat_id > 0 else action.chat_id

                        try:

                            mig_conn = get_db_conn()

                            mig_cur = mig_conn.cursor()

                            mig_cur.execute("UPDATE tbl_chats SET migrated_to_chat_id = ? WHERE chat_id = ?", (chat_id, old_cid))

                            mig_cur.execute("""

                                INSERT OR IGNORE INTO tbl_chats (chat_id, chat_name, safe_folder_name, archive_settings_json, last_backup_offset_id)

                                SELECT ?, chat_name, safe_folder_name, archive_settings_json, 0 FROM tbl_chats WHERE chat_id = ?

                            """, (chat_id, old_cid))

                            mig_conn.commit()

                            mig_conn.close()

                        except Exception as e_mig:

                            logging.debug(f"[LiveArchiver] Error linking migrate_from: {e_mig}")



                # ФИЛЬТР 1: Проверяем, архивирован ли чат в БД

                is_archived = is_chat_archived_in_main(chat_id)

                msg_date_str = msg.date.isoformat() if getattr(msg, 'date', None) else datetime.utcnow().isoformat()



                # Auto-recovery: if chat was marked inaccessible, arriving event proves access is active

                try:

                    clr_conn = get_db_conn()

                    clr_cur = clr_conn.cursor()

                    clr_cur.execute("SELECT backup_status FROM tbl_chats WHERE chat_id = ?", (chat_id,))

                    b_st = clr_cur.fetchone()

                    if b_st and (b_st['backup_status'] == 'inaccessible' if hasattr(b_st, 'keys') else b_st[0] == 'inaccessible'):

                        clr_cur.execute("UPDATE tbl_chats SET backup_status = 'complete', last_halt_reason = NULL WHERE chat_id = ?", (chat_id,))

                        clr_conn.commit()

                    clr_conn.close()

                except Exception:

                    pass

                if not is_archived:

                    # Notify frontend sidebar for sorting unarchived chats without attempting message append

                    if LIVE_CALLBACK:

                        LIVE_CALLBACK('live_new_message', {

                            'message_id': msg.id, 'chat_id': chat_id, 'sender_id': getattr(msg, 'sender_id', 0),

                            'text': msg.raw_text or "", 'date': msg_date_str,

                            'timestamp': int(msg.date.timestamp()) if msg.date else 0,

                            'is_outgoing': 1 if getattr(msg, 'out', False) else 0,

                            'is_archived': False

                        })

                    return



                # ФИЛЬТР 1.5: Проверяем ограничения диапазона архивации (не логировать новое, если чат зафиксирован на старых сообщениях)

                try:

                    c_conn = get_db_conn()

                    c_cur = c_conn.cursor()

                    c_cur.execute("SELECT archive_range_mode, archive_range_end FROM tbl_chats WHERE chat_id = ?", (chat_id,))

                    c_row = c_cur.fetchone()

                    c_conn.close()

                    if c_row:

                        r_mode = c_row['archive_range_mode']

                        if r_mode == 'oldest_days':

                            return

                        elif r_mode == 'custom_range' and c_row['archive_range_end']:

                            if msg.date and msg.date.isoformat() > c_row['archive_range_end']:

                                return

                except Exception:

                    pass



                # ФИЛЬТР 2: Обрабатываем ТОЛЬКО новые сообщения, которых еще нет в БД

                if is_message_already_logged(chat_id, msg.id):

                    return



                while getattr(core, 'IS_BULK_ARCHIVING', False):

                    await asyncio.sleep(0.5)



                sender_id_val = msg.sender_id

                if not sender_id_val and getattr(msg, 'out', False):

                    try:

                        me = await LIVE_CLIENT.get_me()

                        sender_id_val = me.id

                    except Exception as e:

                        logging.error('Exception caught', exc_info=True)



                chat_name = f"Chat_{chat_id}"

                if event.chat:

                    chat_name = getattr(event.chat, 'title', getattr(event.chat, 'first_name', chat_name))

                safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '_')).rstrip()



                # Determine safe folder name and chat settings from DB / pipeline

                safe_folder = f"{safe_name}_{chat_id}"

                chat_settings = None

                try:

                    c_conn = get_db_conn()

                    c_cur = c_conn.cursor()

                    c_cur.execute("SELECT safe_folder_name FROM tbl_chats WHERE chat_id = ?", (chat_id,))

                    rf = c_cur.fetchone()

                    if rf and rf['safe_folder_name']:

                        safe_folder = rf['safe_folder_name']

                    else:

                        pipe = get_chat_pipeline(c_cur, chat_id)

                        for pid in pipe:

                            if pid != chat_id:

                                c_cur.execute("SELECT safe_folder_name FROM tbl_chats WHERE chat_id = ?", (pid,))

                                rfp = c_cur.fetchone()

                                if rfp and rfp['safe_folder_name']:

                                    safe_folder = rfp['safe_folder_name']

                                    break

                    chat_settings = get_chat_settings(c_cur, chat_id)

                    if not chat_settings:

                        chat_settings = get_global_settings(c_cur)

                    c_conn.close()

                except Exception as e_chk:

                    logging.debug(f"[LiveArchiver] Folder/settings query error: {e_chk}")



                # Immediate preliminary save to buffer FIRST so message is in DB before frontend receives notification

                try:

                    init_m_type = None

                    if getattr(msg, 'media', None):

                        if getattr(msg, 'photo', None): init_m_type = 'photo'

                        elif getattr(msg, 'video', None): init_m_type = 'video'

                        elif getattr(msg, 'voice', None): init_m_type = 'voice'

                        elif getattr(msg, 'document', None): init_m_type = 'document'

                        elif getattr(msg, 'poll', None): init_m_type = 'poll'

                        else: init_m_type = 'media'

                    init_m_res = {

                        'media_type': init_m_type,

                        'media_skipped': 'downloading' if getattr(msg, 'media', None) else None,

                        'file_size': 0

                    }

                    _save_live_message_to_buffer(msg, chat_id, safe_folder, init_m_res, sender_id_val=sender_id_val)

                except Exception as e_presave:

                    logging.debug(f"[LiveArchiver] Error in preliminary save: {e_presave}")



                # Notify frontend instantly - message is ALREADY in live buffer DB!

                if LIVE_CALLBACK:

                    LIVE_CALLBACK('live_new_message', {

                        'message_id': msg.id, 'chat_id': chat_id, 'sender_id': sender_id_val or 0,

                        'text': msg.raw_text or "", 'date': msg_date_str,

                        'timestamp': int(msg.date.timestamp()) if msg.date else 0,

                        'is_outgoing': 1 if getattr(msg, 'out', False) else 0,

                        'is_archived': True

                    })



                # Save user and forwarded peer metadata in background

                peers_to_save = []

                user_to_save = event.sender if event.sender else None

                if user_to_save:

                    peers_to_save.append((user_to_save, None))

                if getattr(msg, 'fwd_from', None):

                    fwd = msg.fwd_from

                    fwd_peer = getattr(getattr(msg, 'forward', None), 'chat', None) or getattr(getattr(msg, 'forward', None), 'sender', None)

                    if not fwd_peer:

                        fwd_peer = getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None) or getattr(fwd, 'saved_from_peer', None)

                    if fwd_peer:

                        peers_to_save.append((fwd_peer, getattr(fwd, 'from_name', None) or getattr(fwd, 'saved_from_name', None)))



                avatars_dir = os.path.join(core.MEDIA_BACKUP_DIR, "avatars_global")

                os.makedirs(avatars_dir, exist_ok=True)

                if peers_to_save:

                    u_conn = None

                    try:

                        u_conn = get_live_db_conn()

                        u_cur = u_conn.cursor()

                        init_live_db(u_cur)

                        u_cache = get_live_users_cache()

                        for p_obj, fb_name in peers_to_save:

                            try:

                                await fetch_and_save_user(p_obj, LIVE_CLIENT, u_cur, u_cache, avatars_dir, fallback_name=fb_name)

                            except Exception as pe:

                                logging.debug(f"[LiveArchiver] Error saving peer {p_obj}: {pe}")

                        u_conn.commit()

                    except Exception as e_u:

                        logging.debug(f"[LiveArchiver] Error saving live users: {e_u}")

                    finally:

                        if u_conn:

                            try: u_conn.close()

                            except Exception: pass



                # Process media according to filters and limits

                m_res = await process_live_media(msg, LIVE_CLIENT, chat_id, safe_folder, chat_settings)



                # Extract and enrich reactions if present

                rx_dict = None

                if getattr(msg, 'reactions', None):

                    try:

                        rx_dict = msg.reactions.to_dict()

                        if getattr(msg.reactions, 'can_see_list', False) and not getattr(msg.reactions, 'recent_reactions', None):

                            try:

                                peer_ent = event.chat if event.chat else msg.peer_id

                                rx_list = await LIVE_CLIENT(functions.messages.GetMessageReactionsListRequest(

                                    peer=peer_ent, id=msg.id, limit=20

                                ))

                                if rx_list and rx_list.reactions:

                                    rx_dict['recent_reactions'] = [r.to_dict() for r in rx_list.reactions]

                                    if rx_list.users:

                                        u_conn = get_live_db_conn()

                                        u_cur = u_conn.cursor()

                                        init_live_db(u_cur)

                                        u_cache = get_live_users_cache()

                                        for u in rx_list.users:

                                            await fetch_and_save_user(u, LIVE_CLIENT, u_cur, u_cache, avatars_dir)

                                        u_conn.commit()

                                        u_conn.close()

                            except Exception:

                                pass

                    except Exception:

                        pass



                # Save fully enriched message to live buffer DB

                _save_live_message_to_buffer(msg, chat_id, safe_folder, m_res, sender_id_val=sender_id_val, reactions_dict=rx_dict)



                # Check custom emojis

                try:

                    ce_ids = extract_custom_emoji_ids_from_message(msg)

                    if ce_ids:

                        ce_cfg = (chat_settings or {}).get('custom_emoji') or {'enabled': True}

                        if ce_cfg.get('enabled', True):

                            e_conn = get_live_db_conn()

                            await async_fetch_and_save_custom_emojis(LIVE_CLIENT, ce_ids, e_conn)

                            e_conn.close()

                except Exception as e_ce:

                    logging.debug(f"[LiveArchiver] Error saving live emoji: {e_ce}")



                # Notify frontend with updated rich details

                if LIVE_CALLBACK:

                    LIVE_CALLBACK('live_message_updated', {

                        'message_id': msg.id, 'chat_id': chat_id, 'sender_id': sender_id_val,

                        'text': msg.raw_text or "", 'date': msg_date_str,

                        'media_type': m_res.get('media_type'),

                        'media_skipped': m_res.get('media_skipped'),

                        'media_local_path': m_res.get('media_local_path'),

                        'thumbnail_local_path': m_res.get('thumbnail_local_path'),

                        'media_original_name': m_res.get('media_original_name'),

                        'media_mime_type': m_res.get('media_mime_type'),

                        'poll_data': m_res.get('poll_data'),

                        'reactions': rx_dict

                    })



            except Exception as e:

                print(f"[LiveArchiver] Error handling NewMessage: {e}")



        @LIVE_CLIENT.on(events.MessageEdited)

        async def _on_message_edited(event):

            try:

                msg = event.message

                if not msg: return

                chat_id = event.chat_id or getattr(msg, 'chat_id', None)

                if not chat_id and getattr(msg, 'peer_id', None):

                    try:

                        from telethon.utils import get_peer_id

                        chat_id = get_peer_id(msg.peer_id)

                    except Exception as e:

                        logging.error('Exception caught', exc_info=True)



                # Fallback: вытягиваем chat_id из БД, если он не указан в событии

                if not chat_id:

                    try:

                        main_conn = get_db_conn()

                        main_cur = main_conn.cursor()

                        main_cur.execute("SELECT chat_id FROM tbl_messages WHERE message_id = ?", (msg.id,))

                        res = main_cur.fetchone()

                        main_conn.close()

                        if res: chat_id = res['chat_id']

                    except Exception as e:

                        logging.error('Exception caught', exc_info=True)



                if chat_id and not is_chat_archived_in_main(chat_id): return



                # Update reactions if attached to this MessageEdited event

                if getattr(msg, 'reactions', None):

                    try:

                        rx_dict = msg.reactions.to_dict()

                        rx_json = json.dumps(rx_dict, default=dt_converter)

                        variants = list(get_chat_id_variants(chat_id)) if chat_id else []

                        for conn_fn in (get_db_conn, get_live_db_conn):

                            c = conn_fn()

                            cur = c.cursor()

                            if conn_fn == get_live_db_conn:

                                init_live_db(cur)

                            if variants:

                                placeholders = ','.join('?' for _ in variants)

                                cur.execute(f"UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ? AND chat_id IN ({placeholders})", [rx_json, msg.id] + variants)

                            else:

                                cur.execute("UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ?", (rx_json, msg.id))

                            c.commit()

                            c.close()



                        if LIVE_CALLBACK:

                            LIVE_CALLBACK('live_message_reactions', {

                                'chat_id': chat_id,

                                'message_id': msg.id,

                                'reactions': rx_dict

                            })

                    except Exception as e_rx:

                        logging.debug(f"[LiveArchiver] Error updating reactions from MessageEdited: {e_rx}")



                # If no real edit date or edit is hidden, it's NOT a content edit (e.g. reaction slapped or pin/view update)

                raw_edit_date = getattr(msg, 'edit_date', None)

                if not raw_edit_date or getattr(msg, 'edit_hide', False):

                    return



                entities_json = json.dumps([e.to_dict() for e in msg.entities], default=dt_converter) if getattr(msg, 'entities', None) else None

                edited_date = raw_edit_date.isoformat()

                variants = list(get_chat_id_variants(chat_id)) if chat_id else []



                updated_history = []

                # 1. Обновляем в основной базе (telegram_backup.db)

                try:

                    main_conn = get_db_conn()

                    main_cur = main_conn.cursor()

                    

                    # Читаем старый текст и историю перед обновлением

                    old_text = None

                    existing_history_json = None

                    

                    # 1. Проверяем буферную (живую) базу (т.к. новые сообщения еще не в основной)

                    try:

                        live_conn_check = get_live_db_conn()

                        live_cur_check = live_conn_check.cursor()

                        if variants:

                            placeholders = ','.join('?' for _ in variants)

                            live_cur_check.execute(f"SELECT text, edit_history_json FROM tbl_messages WHERE message_id = ? AND chat_id IN ({placeholders})", [msg.id] + variants)

                        else:

                            live_cur_check.execute("SELECT text, edit_history_json FROM tbl_messages WHERE message_id = ? AND chat_id > -1000000000000", (msg.id,))

                        row_live = live_cur_check.fetchone()

                        if row_live:

                            old_text = row_live['text']

                            existing_history_json = row_live['edit_history_json']

                        live_conn_check.close()

                    except Exception as e:

                        logging.error('Exception caught', exc_info=True)

                    

                    # 2. Если в живой нет, проверяем основную

                    if old_text is None:

                        if variants:

                            placeholders = ','.join('?' for _ in variants)

                            main_cur.execute(f"SELECT text, edit_history_json FROM tbl_messages WHERE message_id = ? AND chat_id IN ({placeholders})", [msg.id] + variants)

                        else:

                            main_cur.execute("SELECT text, edit_history_json FROM tbl_messages WHERE message_id = ? AND chat_id > -1000000000000", (msg.id,))

                        row = main_cur.fetchone()

                        if row:

                            old_text = row['text']

                            existing_history_json = row['edit_history_json']



                    if existing_history_json:

                        try: updated_history = json.loads(existing_history_json)

                        except Exception: updated_history = []

                    

                    if old_text and old_text != msg.raw_text:

                        # Записываем историю версии

                        updated_history.append({

                            "ts": edited_date,

                            "old": old_text

                        })



                    history_json_val = json.dumps(updated_history, ensure_ascii=False) if updated_history else None



                    if variants:

                        placeholders = ','.join('?' for _ in variants)

                        main_cur.execute(f"""

                            UPDATE tbl_messages SET text = ?, edited_date = ?, entities_json = ?, edit_history_json = ?

                            WHERE message_id = ? AND chat_id IN ({placeholders})

                        """, [msg.raw_text, edited_date, entities_json, history_json_val, msg.id] + variants)

                    else:

                        main_cur.execute("""

                            UPDATE tbl_messages SET text = ?, edited_date = ?, entities_json = ?, edit_history_json = ?

                            WHERE message_id = ? AND chat_id > -1000000000000

                        """, (msg.raw_text, edited_date, entities_json, history_json_val, msg.id))



                    if msg.raw_text and chat_id:

                        db.delete_fts_messages(main_cur, chat_id, [msg.id])

                        main_cur.execute("INSERT INTO tbl_messages_fts (message_id, chat_id, text) VALUES (?, ?, ?)",

                                         (msg.id, chat_id, msg.raw_text))

                    main_conn.commit()

                    main_conn.close()

                except Exception as e_main:

                    print(f"[LiveArchiver] Error updating main DB edit: {e_main}")



                # 2. Обновляем в буферной базе (live_stream_backup.db)

                try:

                    live_conn = get_live_db_conn()

                    live_cur = live_conn.cursor()

                    init_live_db(live_cur)

                    history_json_val = json.dumps(updated_history, ensure_ascii=False) if updated_history else None

                    if variants:

                        placeholders = ','.join('?' for _ in variants)

                        live_cur.execute(f"""

                            UPDATE tbl_messages SET text = ?, edited_date = ?, entities_json = ?, edit_history_json = ?

                            WHERE message_id = ? AND chat_id IN ({placeholders})

                        """, [msg.raw_text, edited_date, entities_json, history_json_val, msg.id] + variants)

                    else:

                        live_cur.execute("""

                            UPDATE tbl_messages SET text = ?, edited_date = ?, entities_json = ?, edit_history_json = ?

                            WHERE message_id = ? AND chat_id > -1000000000000

                        """, (msg.raw_text, edited_date, entities_json, history_json_val, msg.id))

                    live_conn.commit()

                    live_conn.close()

                    mark_live_buffer_dirty()

                except Exception as e_live:

                    print(f"[LiveArchiver] Error updating live DB edit: {e_live}")



                if LIVE_CALLBACK:

                    LIVE_CALLBACK('live_message_edited', {

                        'message_id': msg.id, 'chat_id': chat_id, 'text': msg.raw_text, 'edited_date': edited_date,

                        'edit_history': updated_history

                    })



            except Exception as e:

                print(f"[LiveArchiver] Error handling MessageEdited: {e}")



        @LIVE_CLIENT.on(events.MessageDeleted)

        async def _on_message_deleted(event):

            try:

                chat_id = event.chat_id

                if chat_id and not is_chat_archived_in_main(chat_id): return



                variants = list(get_chat_id_variants(chat_id)) if chat_id else []

                placeholders = ','.join('?' for _ in variants) if variants else ''



                # 1. Обновляем в основной базе (telegram_backup.db)

                try:

                    main_conn = get_db_conn()

                    main_cur = main_conn.cursor()

                    for msg_id in event.deleted_ids:
                        if variants:
                            main_cur.execute(f"UPDATE tbl_messages SET is_deleted = 1 WHERE message_id = ? AND chat_id IN ({placeholders})", [msg_id] + variants)
                        else:
                            main_cur.execute("SELECT DISTINCT chat_id FROM tbl_messages WHERE message_id = ? AND chat_id > -1000000000000", (msg_id,))
                            c_matches = [r[0] for r in main_cur.fetchall()]
                            if len(c_matches) == 1:
                                main_cur.execute("UPDATE tbl_messages SET is_deleted = 1 WHERE message_id = ? AND chat_id = ?", (msg_id, c_matches[0]))
                            else:
                                main_cur.execute("UPDATE tbl_messages SET is_deleted = 1 WHERE message_id = ? AND chat_id > -1000000000000", (msg_id,))

                    main_conn.commit()

                    main_conn.close()

                except Exception as e_main:

                    print(f"[LiveArchiver] Error updating main DB delete: {e_main}")



                # 2. Обновляем в буферной базе (live_stream_backup.db)

                try:

                    live_conn = get_live_db_conn()

                    live_cur = live_conn.cursor()

                    init_live_db(live_cur)

                    for msg_id in event.deleted_ids:

                        if variants:

                            live_cur.execute(f"UPDATE tbl_messages SET is_deleted = 1 WHERE message_id = ? AND chat_id IN ({placeholders})", [msg_id] + variants)

                        else:

                            live_cur.execute("UPDATE tbl_messages SET is_deleted = 1 WHERE message_id = ? AND chat_id > -1000000000000", (msg_id,))

                    live_conn.commit()

                    live_conn.close()

                    mark_live_buffer_dirty()

                except Exception as e_live:

                    print(f"[LiveArchiver] Error updating live DB delete: {e_live}")



                for msg_id in event.deleted_ids:

                    if LIVE_CALLBACK:

                        LIVE_CALLBACK('live_message_deleted', {'message_id': msg_id, 'chat_id': chat_id})

            except Exception as e:

                print(f"[LiveArchiver] Error handling MessageDeleted: {e}")



        @LIVE_CLIENT.on(events.UserUpdate)

        async def _on_user_update(event):

            try:

                user_id = getattr(event, 'user_id', None)

                if not user_id:

                    return



                # 1. Ephemeral online status (Zero DB writes)

                if getattr(event, 'status', None) is not None:

                    status_str = 'unknown'

                    until_str = None

                    was_online_str = None



                    if isinstance(event.status, types.UserStatusOnline):

                        status_str = 'online'

                        if getattr(event.status, 'expires', None):

                            until_str = event.status.expires.isoformat()

                    elif isinstance(event.status, types.UserStatusOffline):

                        status_str = 'offline'

                        if getattr(event.status, 'was_online', None):

                            was_online_str = event.status.was_online.isoformat()

                    elif isinstance(event.status, types.UserStatusRecently):

                        status_str = 'recently'

                    elif isinstance(event.status, types.UserStatusLastWeek):

                        status_str = 'last_week'

                    elif isinstance(event.status, types.UserStatusLastMonth):

                        status_str = 'last_month'



                    ACTIVE_USER_STATUS[user_id] = {

                        'status': status_str,

                        'until': until_str,

                        'was_online': was_online_str

                    }



                    if LIVE_CALLBACK:

                        LIVE_CALLBACK('user_status_update', {

                            'user_id': user_id,

                            'status': status_str,

                            'until': until_str,

                            'was_online': was_online_str

                        })



                # 2. Ephemeral typing / media action (Zero DB writes)

                if getattr(event, 'action', None) is not None or getattr(event, 'typing', False) or getattr(event, 'uploading', False):

                    action_type = 'typing' if getattr(event, 'typing', False) else (

                        'uploading' if getattr(event, 'uploading', False) else (

                            'recording' if getattr(event, 'recording', False) else (

                                'cancel' if getattr(event, 'cancel', False) else 'action'

                            )

                        )

                    )

                    media_type = 'photo' if getattr(event, 'photo', False) else (

                        'video' if getattr(event, 'video', False) else (

                            'audio' if getattr(event, 'audio', False) else (

                                'document' if getattr(event, 'document', False) else None

                            )

                        )

                    )

                    chat_id = getattr(event, 'chat_id', None)

                    if LIVE_CALLBACK:

                        LIVE_CALLBACK('chat_typing', {

                            'chat_id': chat_id,

                            'user_id': user_id,

                            'action': action_type,

                            'media': media_type

                        })

            except Exception as e:

                logging.debug(f"[LiveArchiver] Error in UserUpdate handler: {e}")



        @LIVE_CLIENT.on(events.ChatAction)

        async def _on_chat_action(event):

            try:

                chat_id = getattr(event, 'chat_id', None)

                if not chat_id:

                    return



                c_id = chat_id if chat_id < 0 else -1000000000000 - chat_id

                now_iso = datetime.now(timezone.utc).isoformat()

                user_id = getattr(event, 'user_id', None)



                if getattr(event, 'user_joined', False) or getattr(event, 'user_added', False):

                    event_type = 'join' if getattr(event, 'user_joined', False) else 'invite'

                    db.save_chat_participant(c_id, user_id, status='active', entered_at=now_iso)

                    db.save_participant_event(

                        chat_id=c_id, user_id=user_id, event_type=event_type,

                        date=now_iso, actor_user_id=getattr(event.action_message, 'sender_id', None) or user_id,

                        source='live_action'

                    )

                elif getattr(event, 'user_left', False):

                    db.save_chat_participant(c_id, user_id, status='left', left_at=now_iso, exit_reason='own_will')

                    db.save_participant_event(

                        chat_id=c_id, user_id=user_id, event_type='leave',

                        date=now_iso, actor_user_id=user_id, exit_reason='own_will',

                        source='live_action'

                    )

                elif getattr(event, 'user_kicked', False):

                    actor_id = getattr(event.action_message, 'sender_id', None)

                    db.save_chat_participant(c_id, user_id, status='kicked', left_at=now_iso, exit_reason='kicked', kicked_by_user_id=actor_id)

                    db.save_participant_event(

                        chat_id=c_id, user_id=user_id, event_type='kick',

                        date=now_iso, actor_user_id=actor_id, exit_reason='kicked',

                        source='live_action'

                    )



                # Broadcast live participant SSE event

                if 'emit_live_event' in globals():

                    await emit_live_event({

                        'type': 'participant_event',

                        'chat_id': c_id,

                        'user_id': user_id,

                        'action': 'join' if (getattr(event, 'user_joined', False) or getattr(event, 'user_added', False)) else ('left' if getattr(event, 'user_left', False) else 'kicked'),

                        'date': now_iso

                    })

            except Exception as e_ca:

                logging.debug(f"[LiveArchiver] ChatAction error: {e_ca}")



        @LIVE_CLIENT.on(events.Raw)

        async def _on_raw_event(event):

            try:

                # 1. UpdateMessageReactions: real-time reactions change on a message

                if isinstance(event, types.UpdateMessageReactions):

                    peer = getattr(event, 'peer', None)

                    if not peer: return

                    from telethon import utils

                    chat_id = utils.get_peer_id(peer)

                    msg_id = getattr(event, 'msg_id', None)

                    if not msg_id or not chat_id: return



                    if not is_chat_archived_in_main(chat_id): return



                    reactions_obj = getattr(event, 'reactions', None)

                    if not reactions_obj: return



                    reactions_dict = reactions_obj.to_dict()



                    # Pull recent reactions if can_see_list is True and recent_reactions is empty

                    if getattr(reactions_obj, 'can_see_list', False) and not getattr(reactions_obj, 'recent_reactions', None):

                        try:

                            rx_list = await LIVE_CLIENT(functions.messages.GetMessageReactionsListRequest(

                                peer=peer, id=msg_id, limit=20

                            ))

                            if rx_list and rx_list.reactions:

                                reactions_dict['recent_reactions'] = [r.to_dict() for r in rx_list.reactions]

                                if rx_list.users:

                                    u_conn = get_live_db_conn()

                                    u_cur = u_conn.cursor()

                                    init_live_db(u_cur)

                                    u_cache = get_live_users_cache()

                                    av_dir = os.path.join(core.MEDIA_BACKUP_DIR, "avatars_global")

                                    for u in rx_list.users:

                                        await fetch_and_save_user(u, LIVE_CLIENT, u_cur, u_cache, av_dir)

                                    u_conn.commit()

                                    u_conn.close()

                        except Exception as e_rx:

                            logging.debug(f"[LiveArchiver] Could not fetch recent reactions list: {e_rx}")



                    # Download custom emoji in reactions if any

                    try:

                        ce_ids = []

                        for res in (reactions_dict.get('results') or []):

                            rx = res.get('reaction') or {}

                            if rx.get('_') == 'ReactionCustomEmoji' and rx.get('document_id'):

                                ce_ids.append(rx['document_id'])

                        for rec in (reactions_dict.get('recent_reactions') or []):

                            rx = rec.get('reaction') or {}

                            if rx.get('_') == 'ReactionCustomEmoji' and rx.get('document_id'):

                                ce_ids.append(rx['document_id'])

                        if ce_ids:

                            e_conn = get_live_db_conn()

                            await async_fetch_and_save_custom_emojis(LIVE_CLIENT, ce_ids, e_conn)

                            e_conn.close()

                    except Exception:

                        pass



                    reactions_json_val = json.dumps(reactions_dict, default=dt_converter)



                    # Update in main DB (telegram_backup.db)

                    variants = list(get_chat_id_variants(chat_id)) if chat_id else []

                    try:

                        main_conn = get_db_conn()

                        main_cur = main_conn.cursor()

                        if variants:

                            placeholders = ','.join('?' for _ in variants)

                            main_cur.execute(f"UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ? AND chat_id IN ({placeholders})", [reactions_json_val, msg_id] + variants)

                        else:

                            main_cur.execute("UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ? AND chat_id = ?", (reactions_json_val, msg_id, chat_id))

                        main_conn.commit()

                        main_conn.close()

                    except Exception as e_m:

                        logging.debug(f"[LiveArchiver] Error updating main DB reactions: {e_m}")



                    # Update in live DB (live_stream_backup.db)

                    try:

                        live_conn = get_live_db_conn()

                        live_cur = live_conn.cursor()

                        init_live_db(live_cur)

                        if variants:

                            placeholders = ','.join('?' for _ in variants)

                            live_cur.execute(f"UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ? AND chat_id IN ({placeholders})", [reactions_json_val, msg_id] + variants)

                        else:

                            live_cur.execute("UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ? AND chat_id = ?", (reactions_json_val, msg_id, chat_id))

                        live_conn.commit()

                        live_conn.close()

                        mark_live_buffer_dirty()

                    except Exception as e_l:

                        logging.debug(f"[LiveArchiver] Error updating live DB reactions: {e_l}")



                    if LIVE_CALLBACK:

                        LIVE_CALLBACK('live_message_reactions', {

                            'chat_id': chat_id,

                            'message_id': msg_id,

                            'reactions': reactions_dict

                        })



                # 2. UpdateBotMessageReaction

                elif isinstance(event, (types.UpdateBotMessageReaction, types.UpdateBotMessageReactions)):

                    peer = getattr(event, 'peer', None)

                    if not peer: return

                    from telethon import utils

                    chat_id = utils.get_peer_id(peer)

                    msg_id = getattr(event, 'msg_id', None)

                    if not msg_id or not chat_id: return

                    if not is_chat_archived_in_main(chat_id): return

                    try:

                        chat_ent = await LIVE_CLIENT.get_entity(peer)

                        msgs = await LIVE_CLIENT.get_messages(chat_ent, ids=[msg_id])

                        if msgs and msgs[0] and getattr(msgs[0], 'reactions', None):

                            rx_dict = msgs[0].reactions.to_dict()

                            rx_json = json.dumps(rx_dict, default=dt_converter)

                            variants = list(get_chat_id_variants(chat_id))

                            placeholders = ','.join('?' for _ in variants)

                            main_conn = get_db_conn()

                            main_cur = main_conn.cursor()

                            main_cur.execute(f"UPDATE tbl_messages SET reactions_json = ? WHERE message_id = ? AND chat_id IN ({placeholders})", [rx_json, msg_id] + variants)

                            main_conn.commit()

                            main_conn.close()

                            if LIVE_CALLBACK:

                                LIVE_CALLBACK('live_message_reactions', {

                                    'chat_id': chat_id, 'message_id': msg_id, 'reactions': rx_dict

                                })

                    except Exception:

                        pass

            except Exception as e:

                logging.debug(f"[LiveArchiver] Error handling Raw event: {e}")



        LIVE_TASK = asyncio.create_task(LIVE_CLIENT.run_until_disconnected())

        LIVE_REFRESH_TASK = asyncio.create_task(_periodic_chatlist_refresh())

        LIVE_CATCHUP_TASK = asyncio.create_task(run_auto_catchup(LIVE_CLIENT, LIVE_CALLBACK))

        return True

    except Exception as e:

        print(f"[LiveArchiver] Error starting archiver: {e}")

        LIVE_CLIENT = None

        LIVE_STARTING = False

        return False



async def stop_live_archiver():

    """Останавливает фоновый перехват сообщений."""

    global LIVE_CLIENT, LIVE_TASK, LIVE_REFRESH_TASK, LIVE_CATCHUP_TASK

    if LIVE_REFRESH_TASK and not LIVE_REFRESH_TASK.done():

        LIVE_REFRESH_TASK.cancel()

        LIVE_REFRESH_TASK = None

    if LIVE_CATCHUP_TASK and not LIVE_CATCHUP_TASK.done():

        LIVE_CATCHUP_TASK.cancel()

        LIVE_CATCHUP_TASK = None

    if LIVE_CLIENT:

        try:

            await LIVE_CLIENT.disconnect()

        except Exception as e:

            logging.error('Exception caught', exc_info=True)

        LIVE_CLIENT = None

    if LIVE_TASK and not LIVE_TASK.done():

        LIVE_TASK.cancel()

        LIVE_TASK = None

    return True



from .live_catchup import _record_chat_offline_status, run_auto_catchup
