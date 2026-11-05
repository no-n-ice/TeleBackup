#!/usr/bin/env python3
"""
core/live_catchup.py — Startup offline status recorder and automatic gap catch-up engine.
"""
import os
import json
import logging
import sqlite3
import asyncio
from datetime import datetime
from telethon import types, functions
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
from .live_media import process_live_media
import core


def get_live_users_cache():
    from .live import get_live_users_cache as _gluc
    return _gluc()

def is_message_already_logged(chat_id, message_id):
    from .live import is_message_already_logged as _imal
    return _imal(chat_id, message_id)

def _save_live_message_to_buffer(msg, chat_id, safe_name, m_res, sender_id_val=None, reactions_dict=None):
    from .live import _save_live_message_to_buffer as _slmb
    return _slmb(msg, chat_id, safe_name, m_res, sender_id_val=sender_id_val, reactions_dict=reactions_dict)

def _record_chat_offline_status(target_id, chat_name, cat, reason, callback=None):

    """Updates tbl_chats status, logs action to console & logger, records service action into tbl_messages, and notifies SSE."""

    now_iso = datetime.now().isoformat()

    logging.warning(f"[LiveArchiver] [ACTION LOG] Chat {target_id} ('{chat_name}') status changed while offline: {cat} ({reason})")

    print(f"[LiveArchiver] [ACTION LOG] Chat {target_id} ('{chat_name}') was detected as {cat} while offline ({reason})")



    try:

        conn = get_db_conn()

        cur = conn.cursor()

        cur.execute("""

            UPDATE tbl_chats 

            SET backup_status = ?, last_halt_reason = ?, last_halt_date = ? 

            WHERE chat_id = ?

        """, (cat, reason, now_iso, target_id))



        cur.execute("""

            SELECT COUNT(*) FROM tbl_messages 

            WHERE chat_id = ? AND service_action_json LIKE '%MessageActionChatDelete%'

        """, (target_id,))

        if cur.fetchone()[0] == 0:

            cur.execute("SELECT COALESCE(MAX(message_id), 0) + 1 FROM tbl_messages WHERE chat_id = ?", (target_id,))

            next_mid = cur.fetchone()[0]

            action_payload = {

                "_": "MessageActionChatDelete",

                "title": f"Chat {cat} ({reason})",

                "reason": reason

            }

            cur.execute("""

                INSERT OR IGNORE INTO tbl_messages (

                    message_id, chat_id, date, text, service_action_json

                ) VALUES (?, ?, ?, ?, ?)

            """, (

                next_mid,

                target_id,

                now_iso,

                f"Chat became inaccessible: {reason}",

                json.dumps(action_payload)

            ))

        conn.commit()

        conn.close()

    except Exception as e_db:

        logging.error(f"[LiveArchiver] Error updating chat status in DB: {e_db}")



    if callback:

        try:

            callback('chat_status_updated', {

                'chat_id': target_id,

                'status': cat,

                'reason': reason,

                'chat_name': chat_name

            })

            callback('live_dialogs_refreshed', {})

        except Exception:

            pass



async def run_auto_catchup(client, callback=None):

    """

    Automatic catchup on boot: scans all archived chats for missed offline messages,

    fetches them incrementally, applies chat media settings, saves them to DB, and flushes to main.

    """

    await asyncio.sleep(4)

    while getattr(core, 'IS_BULK_ARCHIVING', False):

        await asyncio.sleep(1.0)



    try:

        conn = get_db_conn()

        cur = conn.cursor()

        try:
            cur.execute("""
                SELECT chat_id, chat_name, safe_folder_name, migrated_to_chat_id, archive_settings_json,
                       archive_range_mode, archive_range_end
                FROM tbl_chats
                WHERE (last_backup_offset_id IS NOT NULL AND last_backup_offset_id > 0)
                   OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = tbl_chats.chat_id)
            """)
        except sqlite3.OperationalError:
            try:
                cur.execute("ALTER TABLE tbl_chats ADD COLUMN archive_settings_json TEXT DEFAULT NULL")
                if hasattr(conn, 'commit'):
                    conn.commit()
            except Exception:
                pass
            cur.execute("""
                SELECT chat_id, chat_name, safe_folder_name, migrated_to_chat_id, archive_settings_json,
                       archive_range_mode, archive_range_end
                FROM tbl_chats
                WHERE (last_backup_offset_id IS NOT NULL AND last_backup_offset_id > 0)
                   OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = tbl_chats.chat_id)
            """)

        rows = cur.fetchall()



        # Group chats into target active entities (following migration chains)

        targets = {}

        for r in rows:

            cid = r['chat_id']

            pipe = get_chat_pipeline(cur, cid)

            settings = get_chat_settings(cur, cid)

            if not settings:

                settings = get_global_settings(cur)



            # The active chat is the terminal chat in the pipeline (supergroup or current)

            term_id = cid

            for pid in pipe:

                if pid < 0 and abs(pid) > abs(term_id):

                    term_id = pid



            if term_id not in targets:

                targets[term_id] = {

                    'chat_name': r['chat_name'],

                    'safe_folder_name': r['safe_folder_name'],

                    'settings': settings,

                    'pipeline': pipe,

                    'range_mode': r['archive_range_mode'],

                    'range_end': r['archive_range_end']

                }

        conn.close()



        u_cache = get_live_users_cache()



        for target_id, t_info in targets.items():

            if t_info.get('range_mode') == 'oldest_days':

                continue

            try:

                while getattr(core, 'IS_BULK_ARCHIVING', False):

                    await asyncio.sleep(1.0)



                cat = None

                reason = None

                chat_ent = None



                # 1. Resolve entity from Telegram

                try:

                    chat_ent = await client.get_entity(target_id)

                except Exception as e_ent:

                    cat, reason = helpers.classify_telegram_entity_error(e_ent)

                    if not cat:

                        cat, reason = 'inaccessible', f"error: {str(e_ent)[:60]}"



                # 2. Check entity status flags

                if not cat and chat_ent is not None:

                    if getattr(chat_ent, 'deleted', False):

                        cat, reason = 'deleted', 'permanent: entity marked deleted in Telegram'

                    elif getattr(chat_ent, 'deactivated', False):

                        cat, reason = 'deleted', 'permanent: chat deactivated'

                    elif getattr(chat_ent, 'left', False):

                        cat, reason = 'inaccessible', 'permanent: user left channel or group while offline'

                    elif getattr(chat_ent, 'kicked', False):

                        cat, reason = 'inaccessible', 'permanent: user was kicked/banned from channel or group while offline'



                # 3. Check message accessibility

                top_msgs = None

                if not cat and chat_ent is not None:

                    try:

                        top_msgs = await client.get_messages(chat_ent, limit=1)

                    except Exception as e_msgs:

                        cat, reason = helpers.classify_telegram_entity_error(e_msgs)

                        if not cat:

                            cat, reason = 'inaccessible', f"error: {str(e_msgs)[:60]}"



                if cat:

                    _record_chat_offline_status(target_id, t_info.get('chat_name'), cat, reason, callback)

                    continue



                # Auto-recovery: if chat was marked inaccessible/deleted but now resolved

                try:

                    conn_rec = get_db_conn()

                    cur_rec = conn_rec.cursor()

                    cur_rec.execute("SELECT backup_status FROM tbl_chats WHERE chat_id = ?", (target_id,))

                    st_row = cur_rec.fetchone()

                    if st_row and (st_row['backup_status'] in ('inaccessible', 'deleted') if hasattr(st_row, 'keys') else st_row[0] in ('inaccessible', 'deleted')):

                        cur_rec.execute("UPDATE tbl_chats SET backup_status = 'complete', last_halt_reason = NULL WHERE chat_id = ?", (target_id,))

                        conn_rec.commit()

                        logging.info(f"[LiveCatchup] [ACTION LOG] Chat {target_id} ('{t_info.get('chat_name')}') recovered access and is now active")

                        print(f"[LiveCatchup] [ACTION LOG] Chat {target_id} ('{t_info.get('chat_name')}') recovered access and is now active")

                        if callback:

                            callback('chat_status_updated', {'chat_id': target_id, 'status': 'complete', 'chat_name': t_info.get('chat_name')})

                    conn_rec.close()

                except Exception:

                    pass



                if not top_msgs:

                    continue

                top_id = top_msgs[0].id



                # Get latest message ID saved in our DB for this specific active target chat

                q_conn = get_db_conn()

                q_cur = q_conn.cursor()

                q_cur.execute("SELECT MAX(message_id) FROM tbl_messages WHERE chat_id = ?", (target_id,))

                row_max_sg = q_cur.fetchone()

                max_id_sg = row_max_sg[0] if row_max_sg and row_max_sg[0] else 0

                q_conn.close()



                if top_id <= max_id_sg:

                    continue



                missed_count = top_id - max_id_sg

                logging.info(f"[LiveCatchup] Catching up chat {target_id} ({t_info['chat_name']}): ~{missed_count} missed messages (from {max_id_sg} to {top_id})")



                curr_offset = max_id_sg

                safe_folder = t_info['safe_folder_name'] or f"Chat_{target_id}"

                c_settings = t_info['settings']



                while curr_offset < top_id:

                    if getattr(core, 'IS_BULK_ARCHIVING', False):

                        await asyncio.sleep(1.0)

                        continue



                    fetch_limit = min(100, max(1, top_id - curr_offset))

                    batch = await client.get_messages(chat_ent, limit=fetch_limit, min_id=curr_offset, reverse=True)

                    if not batch:

                        break



                    prev_offset = curr_offset

                    for msg in batch:

                        curr_offset = max(curr_offset, msg.id)

                        if is_message_already_logged(target_id, msg.id):

                            continue



                        # Save user if sender exists

                        if getattr(msg, 'sender', None):

                            try:

                                u_conn = get_live_db_conn()

                                u_cur = u_conn.cursor()

                                av_dir = os.path.join(core.MEDIA_BACKUP_DIR, "avatars_global")

                                await fetch_and_save_user(msg.sender, client, u_cur, u_cache, av_dir)

                                u_conn.commit()

                                u_conn.close()

                                mark_live_buffer_dirty()

                            except Exception:

                                pass



                        # Process media with settings

                        m_res = await process_live_media(msg, client, target_id, safe_folder, c_settings)



                        # Extract and enrich reactions if present

                        rx_dict = None

                        if getattr(msg, 'reactions', None):

                            try:

                                rx_dict = msg.reactions.to_dict()

                                if getattr(msg.reactions, 'can_see_list', False) and not getattr(msg.reactions, 'recent_reactions', None):

                                    try:

                                        rx_list = await client(functions.messages.GetMessageReactionsListRequest(

                                            peer=chat_ent, id=msg.id, limit=20

                                        ))

                                        if rx_list and rx_list.reactions:

                                            rx_dict['recent_reactions'] = [r.to_dict() for r in rx_list.reactions]

                                            if rx_list.users:

                                                u_conn = get_live_db_conn()

                                                u_cur = u_conn.cursor()

                                                av_dir = os.path.join(core.MEDIA_BACKUP_DIR, "avatars_global")

                                                for u in rx_list.users:

                                                    await fetch_and_save_user(u, client, u_cur, u_cache, av_dir)

                                                u_conn.commit()

                                                u_conn.close()

                                                mark_live_buffer_dirty()

                                    except Exception:

                                        pass

                            except Exception:

                                pass



                        # Save to live buffer

                        _save_live_message_to_buffer(msg, target_id, safe_folder, m_res, reactions_dict=rx_dict)



                    # Update offset in main DB

                    up_conn = get_db_conn()

                    up_cur = up_conn.cursor()

                    up_cur.execute("UPDATE tbl_chats SET last_backup_offset_id = ? WHERE chat_id = ?", (curr_offset, target_id))

                    up_conn.commit()

                    up_conn.close()



                    if curr_offset <= prev_offset or curr_offset >= top_id:

                        break



                # Flush caught up messages into main DB

                db.flush_live_db_to_main()

                if callback:

                    callback('catchup_completed', {'chat_id': target_id, 'new_offset': curr_offset})



            except Exception as e_chat:

                logging.error(f"[LiveCatchup] Error during catchup for chat {target_id}: {e_chat}", exc_info=True)



    except Exception as e:

        logging.error("[LiveCatchup] Error in run_auto_catchup", exc_info=True)



auto_catchup_archived_chats = run_auto_catchup

