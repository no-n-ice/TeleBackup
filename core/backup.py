#!/usr/bin/env python3
"""
backup.py — 3-Phase Telegram backup engine (History, Media, Custom Emoji) & Dialog Fetcher.
"""
import os
import re
import time
import json
import logging
import asyncio
from datetime import datetime
from telethon import TelegramClient, types, functions
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeCustomEmoji, User, PeerChannel, PeerChat
from .db import (
    get_db_conn, get_offset_id, flush_live_db_to_main, DB_FILE, CACHE_FILE,
    init_db, run_db_migration, load_all_users_from_db, link_migrated_chats, fetch_message_from_db
)
from . import db
from . import helpers
from .helpers import (
    dt_converter, sanitize_filename, get_entity_name, fetch_and_save_user,
    get_chat_id_variants, are_chat_ids_equal, get_media_target_dir, ensure_chat_media_subdirs,
    normalize_media_settings, get_message_forward_sender_id
)
from .emoji import extract_custom_emoji_ids_from_message, async_fetch_and_save_custom_emojis
import core


async def sync_pinned_messages(client, chat_entity, conn, cursor):
    """Syncs pinned message IDs list into tbl_chats.pinned_msg_ids_json and sets is_pinned=1 in tbl_messages."""
    from telethon.tl.types import InputMessagesFilterPinned
    try:
        pinned_ids = []
        async for pin_msg in client.iter_messages(chat_entity, filter=InputMessagesFilterPinned, limit=100):
            if pin_msg:
                pinned_ids.append(pin_msg.id)
        if pinned_ids:
            pinned_json = json.dumps(pinned_ids)
            chat_id = getattr(chat_entity, 'id', None)
            if chat_id:
                cursor.execute("UPDATE tbl_chats SET pinned_msg_ids_json = ? WHERE chat_id = ?", (pinned_json, chat_id))
                placeholders = ','.join('?' for _ in pinned_ids)
                cursor.execute(f"UPDATE tbl_messages SET is_pinned = 1 WHERE chat_id = ? AND message_id IN ({placeholders})", [chat_id] + pinned_ids)
                conn.commit()
    except Exception as e:
        logging.debug(f"[core] sync_pinned_messages error: {e}")


async def backup_channel_stats(client, chat_entity, conn, cursor):
    """Backs up official broadcast stats if admin rights are present."""
    from telethon.tl.functions.stats import GetBroadcastStatsRequest
    try:
        chat_id = getattr(chat_entity, 'id', None)
        if not chat_id:
            return
        res = await client(GetBroadcastStatsRequest(channel=chat_entity))
        if res:
            stats_dict = res.to_dict() if hasattr(res, 'to_dict') else res
            stats_json = json.dumps(stats_dict, default=dt_converter)
            now_iso = datetime.now().isoformat()
            cursor.execute("UPDATE tbl_chats SET stats_json = ?, stats_updated_at = ? WHERE chat_id = ?", (stats_json, now_iso, chat_id))
            conn.commit()
    except Exception as e:
        logging.debug(f"[core] backup_channel_stats skipped (not channel admin or <50 subs): {e}")


async def backup_admin_log(client, chat_entity, conn, cursor, limit=100):
    """Backs up admin event log for supergroups / channels."""
    from telethon.tl.functions.channels import GetAdminLogRequest
    from telethon.utils import get_peer_id
    try:
        if isinstance(chat_entity, (int, str)):
            chat_id = int(chat_entity)
            chat_entity = await client.get_entity(chat_id)
        else:
            chat_id = get_peer_id(chat_entity)

        log_res = await client(GetAdminLogRequest(channel=chat_entity, q='', limit=limit))
        events = []
        for ev in (getattr(log_res, 'events', None) or []):
            action = getattr(ev, 'action', None)
            action_dict = action.to_dict() if hasattr(action, 'to_dict') else {}
            events.append({
                'event_id': ev.id,
                'chat_id': chat_id,
                'date': dt_converter(ev.date),
                'user_id': ev.user_id,
                'action_type': type(action).__name__,
                'action_json': json.dumps(action_dict, default=dt_converter),
                'prev_value_json': json.dumps(getattr(action, 'prev_value', None), default=dt_converter) if hasattr(action, 'prev_value') else None,
                'new_value_json': json.dumps(getattr(action, 'new_value', None), default=dt_converter) if hasattr(action, 'new_value') else None
            })
        if events:
            db.save_admin_log_events(cursor, chat_id, events)
            conn.commit()
        return len(events)
    except Exception as e:
        logging.debug(f"[core] backup_admin_log skipped (non-admin or private chat): {e}")
        return 0


async def sync_chat_wallpaper(client, chat_entity, full_chat, conn, cursor):
    """Downloads chat wallpaper and records metadata in tbl_chats."""
    try:
        chat_id = getattr(chat_entity, 'id', None)
        if not chat_id or not full_chat:
            return
        wallpaper_obj = getattr(full_chat, 'wallpaper', None)
        if not wallpaper_obj:
            return
        wallpapers_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "wallpapers")
        os.makedirs(wallpapers_dir, exist_ok=True)
        
        wallpaper_path_rel = None
        wallpaper_document = getattr(wallpaper_obj, 'document', None)
        if wallpaper_document:
            wp_filename = f"chat_{chat_id}_wallpaper.jpg"
            wp_abs = os.path.join(wallpapers_dir, wp_filename)
            if not os.path.exists(wp_abs):
                try:
                    await client.download_media(wallpaper_document, file=wp_abs)
                except Exception as e_wp:
                    logging.debug(f"[core] Wallpaper download error: {e_wp}")
            if os.path.exists(wp_abs):
                wallpaper_path_rel = os.path.relpath(wp_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                
        wp_dict = wallpaper_obj.to_dict() if hasattr(wallpaper_obj, 'to_dict') else str(wallpaper_obj)
        wp_json = json.dumps(wp_dict, default=dt_converter)
        cursor.execute("UPDATE tbl_chats SET wallpaper_path = ?, wallpaper_json = ? WHERE chat_id = ?", (wallpaper_path_rel, wp_json, chat_id))
        conn.commit()
    except Exception as e:
        logging.debug(f"[core] sync_chat_wallpaper error: {e}")


async def async_run_backup(chat_id, settings, total_messages, progress_callback=None, repair=False, client=None, resume_stage=None, resume_boundary_id=None):
    core.GLOBAL_ABORT = False
    core.IS_BULK_ARCHIVING = True
    backup_exception = None

    # Flush accumulated live messages prior to bulk archiving
    flush_live_db_to_main()
    
    def notify(text, processed=None, total=None, status="running", last_item=None, phase=None):
        if progress_callback:
            progress_callback(chat_id, text, processed, total, status, last_item, phase)
    settings = normalize_media_settings(settings)
    is_text_only = not any(
        v.get('enabled', True) for k, v in settings.items()
        if isinstance(v, dict) and k in ('photos', 'videos', 'voice', 'video_notes', 'documents')
    )

    conn = get_db_conn()
    cursor = conn.cursor()
    is_delta = bool(settings and settings.get('is_delta'))

    client_was_created = False
    
    if client and client.is_connected():
        pass
    elif getattr(core, 'LIVE_CLIENT', None) and core.LIVE_CLIENT.is_connected():
        client = core.LIVE_CLIENT
    else:
        client = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
        client_was_created = True

    if client_was_created:
        await client.connect()
    
    if not await client.is_user_authorized():
        if client_was_created: await client.disconnect()
        conn.close()
        raise Exception("Client not authorized. Run cli.py to log in.")
    
    try:
        chat_entity = None
        try:
            chat_entity = await client.get_entity(chat_id)
        except Exception:
            pass

        if not chat_entity:
            try:
                logging.info(f"[async_run_backup] Entity {chat_id} not in Telethon cache, fetching dialogs to populate cache...")
                async for dialog in client.iter_dialogs(limit=None):
                    if dialog.entity:
                        if are_chat_ids_equal(getattr(dialog.entity, 'id', None), chat_id):
                            chat_entity = dialog.entity
                            break
                    if chat_entity:
                        break
            except Exception as ex:
                logging.debug(f"[async_run_backup] iter_dialogs fallback error: {ex}")

        if not chat_entity:
            from telethon.tl.types import PeerChannel, PeerChat
            for variant in get_chat_id_variants(chat_id):
                try:
                    short_id = abs(variant)
                    if short_id > 1000000000000:
                        short_id = short_id - 1000000000000
                    chat_entity = await client.get_entity(PeerChannel(short_id))
                    if chat_entity: break
                except Exception:
                    pass
                try:
                    chat_entity = await client.get_entity(PeerChat(short_id))
                    if chat_entity: break
                except Exception:
                    pass

        if not chat_entity:
            try:
                chat_entity = await client.get_entity(chat_id)
            except Exception as e:
                cat, reason = helpers.classify_telegram_entity_error(e)
                status_to_set = cat or 'inaccessible'
                cursor.execute("""
                    UPDATE tbl_chats 
                    SET backup_status = ?, last_halt_reason = ?, last_halt_date = ? 
                    WHERE chat_id = ?
                """, (status_to_set, reason, datetime.now().isoformat(), chat_id))
                conn.commit()
                icon = "🚫" if status_to_set == "deleted" else "🔒"
                notify(f"{icon} Chat {chat_id} is {status_to_set} ({reason[:40]})", 0, total_messages, status="error")
                return f"Chat {status_to_set} in Telegram: {reason}", None

        chat_name = getattr(chat_entity, 'title', getattr(chat_entity, 'first_name', f"Unknown_{chat_id}"))
        if not chat_name: chat_name = f"Deleted_or_Unknown_{chat_id}"

        init_db(cursor)
        run_db_migration(cursor)

        # Freeze folder name: check if chat already has an established safe_folder_name
        cursor.execute("SELECT safe_folder_name FROM tbl_chats WHERE chat_id = ?", (chat_id,))
        existing_chat_row = cursor.fetchone()
        existing_folder = (
            existing_chat_row['safe_folder_name']
            if (existing_chat_row and hasattr(existing_chat_row, 'keys') and existing_chat_row['safe_folder_name'])
            else (existing_chat_row[0] if (existing_chat_row and existing_chat_row[0]) else None)
        )

        safe_chat_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '_')).rstrip()
        final_folder_name = existing_folder if existing_folder else f"{safe_chat_name}_{chat_id}"

        chat_media_dir = os.path.join(core.MEDIA_BACKUP_DIR, final_folder_name)
        avatars_dir = os.path.join(chat_media_dir, "avatars")
        stickers_dir = os.path.join(chat_media_dir, "stickers")
        gifs_dir = os.path.join(chat_media_dir, "gifs")
        other_dir = os.path.join(chat_media_dir, "other")
        
        # Directory structure creation
        ensure_chat_media_subdirs(chat_media_dir)
        
        chat_avatar_path_rel = None
        chat_avatar_path_abs = os.path.join(chat_media_dir, "chat_photo.jpg")
        try:
            if getattr(chat_entity, 'photo', None):
                await asyncio.wait_for(client.download_profile_photo(chat_entity, file=chat_avatar_path_abs), timeout=8.0)
            if os.path.exists(chat_avatar_path_abs):
                 chat_avatar_path_rel = os.path.relpath(chat_avatar_path_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
        except Exception as e:
            logging.debug(f"[core] Chat avatar download skipped: {e}")
        
        cursor.execute(
            "INSERT OR IGNORE INTO tbl_chats (chat_id, chat_name, safe_folder_name) VALUES (?, ?, ?)",
            (chat_id, chat_name, final_folder_name)
        )
        cursor.execute(
            "UPDATE tbl_chats SET chat_name = ?, safe_folder_name = COALESCE(safe_folder_name, ?), chat_avatar_path = ? WHERE chat_id = ?",
            (chat_name, final_folder_name, chat_avatar_path_rel, chat_id)
        )
        conn.commit()

        users_data_cache = load_all_users_from_db(cursor)

        # --- Fetch full chat info (about, creation date, linked chat, admin list, wallpaper, emoji status) ---
        chat_about, chat_creation_date_val, linked_chat_id_val, admin_list_data = None, None, None, None
        full_result = None
        chat_emoji_status_doc_id = None
        chat_emoji_status_json_val = None

        try:
            from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantsRequest
            from telethon.tl.functions.messages import GetFullChatRequest
            from telethon.tl.types import ChannelParticipantsAdmins, Channel, Chat, User

            chat_creation_date_val = dt_converter(getattr(chat_entity, 'date', None))
            if isinstance(chat_entity, Channel):
                chat_type_val = 'channel' if getattr(chat_entity, 'broadcast', False) else 'group'
            elif isinstance(chat_entity, Chat):
                chat_type_val = 'group'
            elif isinstance(chat_entity, User):
                chat_type_val = 'bot' if getattr(chat_entity, 'bot', False) else 'user'
            else:
                chat_type_val = 'user' if chat_id > 0 else ('channel' if chat_id < -1000000000000 else 'group')

            if isinstance(chat_entity, Channel):
                try:
                    full_result = await client(GetFullChannelRequest(chat_entity))
                    chat_about = getattr(full_result.full_chat, 'about', None)
                    linked_chat_id_val = getattr(full_result.full_chat, 'linked_chat_id', None)
                    p_cnt = getattr(full_result.full_chat, 'participants_count', None)
                    if p_cnt is not None:
                        db.update_chat_participants_count(chat_id, p_cnt, cursor=cursor)
                except Exception as e_fc:
                    logging.debug(f"[core] GetFullChannelRequest error: {e_fc}")
                # Fetch admin list with ranks
                try:
                    admin_result = await client(GetParticipantsRequest(
                        channel=chat_entity, filter=ChannelParticipantsAdmins(), offset=0, limit=200, hash=0
                    ))
                    admin_list_data = []
                    for p in admin_result.participants:
                        admin_list_data.append({
                            "user_id": p.user_id,
                            "rank": getattr(p, 'rank', None),
                            "is_creator": 'Creator' in type(p).__name__
                        })
                except Exception as e:
                    logging.debug(f"[core] GetParticipantsRequest skipped (non-admin or restricted): {e}")
                if chat_type_val == 'group' and not is_delta:
                    try:
                        async for user_p in client.iter_participants(chat_entity, limit=200):
                            if user_p:
                                users_data_cache = await fetch_and_save_user(user_p, client, cursor, users_data_cache, avatars_dir)
                    except Exception as e:
                        logging.debug(f"[core] Fetching group participants error: {e}")
            elif isinstance(chat_entity, Chat):
                try:
                    full_result = await client(GetFullChatRequest(chat_id=getattr(chat_entity, 'id', chat_id)))
                    chat_about = getattr(full_result.full_chat, 'about', None)
                    p_cnt = getattr(full_result.full_chat, 'participants_count', None)
                    if p_cnt is not None:
                        db.update_chat_participants_count(chat_id, p_cnt, cursor=cursor)
                except Exception as e_gc:
                    logging.debug(f"[core] GetFullChatRequest error: {e_gc}")
            elif isinstance(chat_entity, User):
                chat_about = getattr(chat_entity, 'about', None)
        except Exception as e:
            logging.debug(f"[core] Metadata fetch error: {e}")

        # Extract chat emoji status if present
        c_emoji_status = getattr(chat_entity, 'emoji_status', None)
        if not c_emoji_status and full_result and getattr(full_result, 'full_chat', None):
            c_emoji_status = getattr(full_result.full_chat, 'emoji_status', None)
        if c_emoji_status:
            chat_emoji_status_doc_id = getattr(c_emoji_status, 'document_id', None)
            try:
                chat_emoji_status_json_val = json.dumps(dt_converter(c_emoji_status))
            except Exception:
                chat_emoji_status_json_val = json.dumps({"document_id": chat_emoji_status_doc_id})
            if chat_emoji_status_doc_id:
                try:
                    await async_fetch_and_save_custom_emojis(client, [chat_emoji_status_doc_id], conn)
                except Exception:
                    pass

        # Extract peer colors from chat entity (low priority)
        color_json_val = None
        profile_color_json_val = None
        try:
            color_obj = getattr(chat_entity, 'color', None)
            if color_obj is not None:
                color_json_val = json.dumps(color_obj.to_dict() if hasattr(color_obj, 'to_dict') else color_obj, default=dt_converter)
            profile_color_obj = getattr(chat_entity, 'profile_color', None)
            if profile_color_obj is not None:
                profile_color_json_val = json.dumps(profile_color_obj.to_dict() if hasattr(profile_color_obj, 'to_dict') else profile_color_obj, default=dt_converter)
        except Exception as e:
            logging.error('Exception caught', exc_info=True)

        cursor.execute(
            """UPDATE tbl_chats SET
                chat_about = ?, chat_creation_date = ?, linked_chat_id = ?,
                admin_list_json = ?, color_json = ?, profile_color_json = ?,
                chat_type = ?, emoji_status_document_id = ?, emoji_status_json = ?
            WHERE chat_id = ?""",
            (chat_about, chat_creation_date_val, linked_chat_id_val,
             json.dumps(admin_list_data, default=dt_converter) if admin_list_data else None,
             color_json_val, profile_color_json_val, chat_type_val,
             chat_emoji_status_doc_id, chat_emoji_status_json_val, chat_id)
        )
        conn.commit()

        # Sync wallpaper if present in full_chat
        if full_result and getattr(full_result, 'full_chat', None):
            await sync_chat_wallpaper(client, chat_entity, full_result.full_chat, conn, cursor)

        # Sync pinned messages list
        await sync_pinned_messages(client, chat_entity, conn, cursor)

        # Sync channel stats, admin log, and sponsored messages
        if chat_type_val == 'channel' and not is_delta:
            await backup_channel_stats(client, chat_entity, conn, cursor)
            try:
                from telethon.tl.functions.channels import GetSponsoredMessagesRequest
                sp_res = await client(GetSponsoredMessagesRequest(channel=chat_entity))
                if sp_res and hasattr(sp_res, 'messages') and sp_res.messages:
                    sp_list = []
                    for sm in sp_res.messages:
                        sp_list.append({
                            'message_id': getattr(sm, 'random_id', None) or getattr(sm, 'id', None),
                            'title': getattr(sm, 'title', None),
                            'text': getattr(sm, 'message', None),
                            'url': getattr(sm, 'url', None),
                            'button_text': getattr(sm, 'button_text', None),
                            'sponsor_info': getattr(sm, 'sponsor_info', None),
                            'additional_info': getattr(sm, 'additional_info', None),
                            'date': datetime.utcnow().isoformat()
                        })
                    if sp_list:
                        db.save_sponsored_messages(cursor, chat_id, sp_list)
                        conn.commit()
            except Exception as e_sp:
                logging.debug(f"[core] Sponsored messages skipped: {e_sp}")

        # Sync participants & subscriber churn if enabled
        try:
            from core import participants as part_module
            await part_module.sync_chat_participants(client, chat_id, db_conn=conn)
        except Exception as e_pt:
            logging.debug(f"[core] Participant tracker sync: {e_pt}")
        if chat_type_val in ('channel', 'group') and not is_delta:
            await backup_admin_log(client, chat_entity, conn, cursor)

        if linked_chat_id_val and not is_delta and not (settings and settings.get('skip_linked')):
            try:
                l_id = linked_chat_id_val if linked_chat_id_val < 0 else -1000000000000 - linked_chat_id_val
                target_settings = settings.get('linked_settings') if (settings and 'linked_settings' in settings) else settings
                from core import worker
                worker.auto_enqueue_chat(l_id, target_settings)
            except Exception as e:
                logging.debug(f"[core] Auto-enqueue linked chat error: {e}")
        
        # Time-period range settings extraction and persistence
        range_mode = settings.get('archive_range_mode')
        range_start = settings.get('archive_range_start')
        range_end = settings.get('archive_range_end')
        range_days = settings.get('archive_range_days')
        direction = settings.get('archive_direction') or 'backward'

        if not range_mode or range_mode == 'all':
            try:
                cursor.execute("SELECT archive_range_mode, archive_range_start, archive_range_end, archive_range_days, archive_direction FROM tbl_chats WHERE chat_id = ?", (chat_id,))
                rc_row = cursor.fetchone()
                if rc_row and rc_row['archive_range_mode'] and rc_row['archive_range_mode'] != 'all':
                    range_mode = rc_row['archive_range_mode']
                    range_start = range_start or rc_row['archive_range_start']
                    range_end = range_end or rc_row['archive_range_end']
                    range_days = range_days or rc_row['archive_range_days']
                    direction = rc_row['archive_direction'] or direction
            except Exception:
                pass
        range_mode = range_mode or 'all'

        try:
            cursor.execute("""
                UPDATE tbl_chats SET
                    archive_range_mode = ?, archive_range_start = ?,
                    archive_range_end = ?, archive_range_days = ?, archive_direction = ?
                WHERE chat_id = ?
            """, (range_mode, range_start, range_end, range_days, direction, chat_id))
            conn.commit()
        except Exception as e_cr:
            logging.debug(f"[core] Range settings update error: {e_cr}")

        from datetime import timedelta
        cutoff_date = None
        end_date_obj = None
        is_range_limited = range_mode in ('latest_days', 'custom_range', 'oldest_days')

        if range_mode == 'latest_days' and range_days:
            cutoff_date = datetime.utcnow() - timedelta(days=int(range_days))
        elif range_mode == 'oldest_days' and range_days:
            try:
                first_msgs = await client.get_messages(chat_entity, limit=1, reverse=True)
                if first_msgs and first_msgs[0].date:
                    f_dt = first_msgs[0].date.replace(tzinfo=None) if first_msgs[0].date.tzinfo else first_msgs[0].date
                    cutoff_date = f_dt + timedelta(days=int(range_days))
            except Exception as e_od:
                logging.debug(f"[core] oldest_days first message fetch error: {e_od}")
        elif range_mode == 'custom_range':
            if range_start:
                try:
                    cutoff_date = datetime.fromisoformat(range_start)
                except Exception:
                    pass
            if range_end:
                try:
                    end_date_obj = datetime.fromisoformat(range_end).replace(hour=23, minute=59, second=59, microsecond=999999)
                except Exception:
                    pass
            if cutoff_date and end_date_obj and cutoff_date > end_date_obj:
                cutoff_date, end_date_obj = end_date_obj.replace(hour=0, minute=0, second=0, microsecond=0), cutoff_date.replace(hour=23, minute=59, second=59, microsecond=999999)

        # If repair mode, ignore offset and start from beginning
        offset_id = 0 if repair else get_offset_id(cursor, chat_id)
        
        total_messages = 0
        top_id = 0
        try:
            # Fetch actual message count for accurate progress reporting
            msgs_total_obj = await client.get_messages(chat_entity, limit=0)
            total_messages = msgs_total_obj.total
            top_msg = await client.get_messages(chat_entity, limit=1)
            if top_msg:
                top_id = top_msg[0].id
        except Exception as e:
            logging.error('Exception caught', exc_info=True)

        if not repair and not resume_stage and range_mode == 'all' and top_id and offset_id and offset_id >= top_id:
            cursor.execute("SELECT has_older_gap FROM tbl_chats WHERE chat_id = ?", (chat_id,))
            gap_check = cursor.fetchone()
            if not gap_check or not gap_check['has_older_gap']:
                notify("Chat is already up to date.", 0, total_messages, status="done")
                cursor.execute("SELECT migrated_to_chat_id FROM tbl_chats WHERE chat_id = ?", (chat_id,))
                mig_result = cursor.fetchone()
                return f"Already up to date ({total_messages} messages)", mig_result[0] if mig_result else None

        # Cache existing message IDs for fast checking
        cursor.execute("SELECT message_id FROM tbl_messages WHERE chat_id = ?", (chat_id,))
        existing_ids = {row[0] for row in cursor.fetchall()}

        # =============================================
        # PHASE 1: SWEEP — Fast message traversal
        # =============================================
        stage_target = total_messages
        if resume_stage == 'latest':
            stage_target = max(0, (top_id or 0) - (resume_boundary_id or 0)) if top_id else max(0, total_messages - len(existing_ids))
        elif resume_stage == 'older':
            stage_target = min(max(0, (resume_boundary_id or 0) - 1), max(0, total_messages - len(existing_ids))) if resume_boundary_id else max(0, total_messages - len(existing_ids))
        elif not repair and range_mode == 'all' and direction != 'backward' and offset_id and offset_id > 0 and top_id:
            stage_target = max(0, top_id - offset_id)
        elif not repair and len(existing_ids) > 0 and not is_range_limited:
            stage_target = max(0, total_messages - len(existing_ids))
        elif is_range_limited:
            stage_target = 0

        if resume_stage:
            phase1_text = "Resuming: Downloading remaining messages..."
            notify(phase1_text, 0, stage_target, phase='resume')
        else:
            phase1_text = "Phase 1: Repairing archive..." if repair else "Phase 1: Downloading messages..."
            notify(phase1_text, 0, stage_target, phase=1)
        message_count = 0
        new_messages_list = []
        unflushed_messages = []
        recent_messages_map = {}
        download_queue = []  # (index_in_list, message_obj, file_path)
        custom_emoji_ids = set()

        reverse_fetch = True
        fetch_offset_date = None

        if resume_stage == 'latest':
            reverse_fetch = True
            current_offset_id = resume_boundary_id or 0
        elif resume_stage == 'older':
            reverse_fetch = False
            current_offset_id = resume_boundary_id or 0
        elif range_mode == 'latest_days':
            reverse_fetch = False
            current_offset_id = 0
        elif range_mode == 'custom_range':
            reverse_fetch = False
            current_offset_id = 0
            if end_date_obj and end_date_obj < datetime.utcnow():
                fetch_offset_date = end_date_obj
        elif range_mode == 'oldest_days':
            reverse_fetch = True
            current_offset_id = 0
        elif range_mode == 'all' and direction == 'backward':
            reverse_fetch = False
            current_offset_id = 0
        else:
            current_offset_id = offset_id

        cutoff_reached = False
        while True:
            try:
                try:
                    if reverse_fetch:
                        fetch_limit = 100
                        if top_id and top_id > current_offset_id:
                            fetch_limit = min(100, max(1, top_id - current_offset_id))
                        prev_offset_id = current_offset_id
                        messages_batch = await client.get_messages(chat_entity, limit=fetch_limit, min_id=current_offset_id, reverse=True)
                    else:
                        kwargs = {'limit': 100, 'reverse': False}
                        if current_offset_id and current_offset_id > 0:
                            kwargs['offset_id'] = current_offset_id
                        elif fetch_offset_date:
                            kwargs['offset_date'] = fetch_offset_date
                            fetch_offset_date = None
                        messages_batch = await client.get_messages(chat_entity, **kwargs)
                except Exception as e:
                    err_str = str(e).lower()
                    if any(term in err_str for term in ['private', 'banned', 'admin', 'invalid', 'channelprivateerror']):
                        notify("Message access closed by Telegram (removed). Preserved existing messages.", message_count, total_messages, status="done")
                        break
                    raise e

                if not messages_batch:
                    break

                # 1. Process metadata delivered by Telegram with messages
                if hasattr(messages_batch, 'chats') and messages_batch.chats:
                    for chat in messages_batch.chats:
                        users_data_cache = await fetch_and_save_user(chat, client, cursor, users_data_cache, avatars_dir)
                if hasattr(messages_batch, 'users') and messages_batch.users:
                    for user in messages_batch.users:
                        users_data_cache = await fetch_and_save_user(user, client, cursor, users_data_cache, avatars_dir)

                # 2. Collect all participant IDs from batch for mass resolution
                batch_peers_map = {} 
                peer_msg_map = {}
                def add_peer(p, msg_id=None):
                    if not p: return
                    pkey = None
                    if hasattr(p, 'channel_id'): pkey = f"c_{p.channel_id}"
                    elif hasattr(p, 'user_id'): pkey = f"u_{p.user_id}"
                    elif hasattr(p, 'chat_id'): pkey = f"g_{p.chat_id}"
                    elif hasattr(p, 'id'): pkey = f"e_{p.id}"
                    else: pkey = str(p)
                    batch_peers_map[pkey] = p
                    if msg_id and pkey not in peer_msg_map:
                        peer_msg_map[pkey] = msg_id

                for msg in messages_batch:
                    try:
                        m_id = getattr(msg, 'id', None)
                        if getattr(msg, 'sender_id', None): add_peer(msg.sender_id, m_id)
                        fwd = getattr(msg, 'fwd_from', None)
                        if fwd:
                            fwd_ent = getattr(getattr(msg, 'forward', None), 'chat', None) or getattr(getattr(msg, 'forward', None), 'sender', None)
                            if fwd_ent:
                                add_peer(fwd_ent, m_id)
                            else:
                                fwd_p = getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None)
                                if fwd_p: add_peer(fwd_p, m_id)
                                if getattr(fwd, 'saved_from_peer', None): add_peer(fwd.saved_from_peer, m_id)
                        if getattr(msg, 'via_bot_id', None): add_peer(msg.via_bot_id, m_id)
                        if getattr(msg, 'action', None):
                            act_users = getattr(msg.action, 'users', None)
                            if act_users:
                                for uid in act_users:
                                    if uid: add_peer(uid, m_id)
                            act_uid = getattr(msg.action, 'user_id', None)
                            if act_uid: add_peer(act_uid, m_id)
                        reply_to = getattr(msg, 'reply_to', None)
                        if reply_to:
                            r_peer = getattr(reply_to, 'reply_to_peer_id', None) or getattr(reply_to, 'peer', None)
                            if r_peer:
                                add_peer(r_peer, m_id)
                            r_from = getattr(reply_to, 'reply_from', None)
                            if r_from:
                                r_from_peer = getattr(r_from, 'from_id', None) or getattr(r_from, 'from_', None)
                                if r_from_peer:
                                    add_peer(r_from_peer, m_id)
                    except Exception as e_p:
                        logging.debug(f"[core] Peer extraction warning on msg #{getattr(msg, 'id', '?')}: {e_p}")
                
                if batch_peers_map:
                    try:
                        resolved_entities = await client.get_entity(list(batch_peers_map.values()))
                        if isinstance(resolved_entities, list):
                            for ent in resolved_entities:
                                users_data_cache = await fetch_and_save_user(ent, client, cursor, users_data_cache, avatars_dir)
                        else:
                            users_data_cache = await fetch_and_save_user(resolved_entities, client, cursor, users_data_cache, avatars_dir)
                    except Exception as e:
                        logging.debug(f"[core] Batch get_entity error ({e}), resolving peers individually...")
                        for pkey, pobj in batch_peers_map.items():
                            try:
                                users_data_cache = await fetch_and_save_user(pobj, client, cursor, users_data_cache, avatars_dir)
                            except Exception as pe:
                                orig_msg_id = peer_msg_map.get(pkey, 'unknown')
                                print(f"[Vector Trace] Message #{orig_msg_id} references inaccessible peer '{pkey}': {pe}")

                for message in messages_batch:
                    # Update offset for next iteration
                    current_offset_id = max(current_offset_id, message.id) if reverse_fetch else message.id
                    
                    # Cutoff check for time period archiving
                    if cutoff_date and getattr(message, 'date', None):
                        msg_dt = message.date.replace(tzinfo=None) if message.date.tzinfo else message.date
                        cut_dt = cutoff_date.replace(tzinfo=None) if cutoff_date.tzinfo else cutoff_date
                        if range_mode in ('latest_days', 'custom_range') and msg_dt < cut_dt:
                            cutoff_reached = True
                            break
                        elif range_mode == 'oldest_days' and msg_dt > cut_dt:
                            cutoff_reached = True
                            break

                    message_count += 1
                    custom_emoji_ids.update(extract_custom_emoji_ids_from_message(message))
                    is_new = message.id not in existing_ids
                    
                    if message_count % 5 == 0 or is_new:
                        txt_snippet = getattr(message, 'raw_text', '') or ''
                        prev_str = f"file - {message.id}" if getattr(message, 'media', None) else (f"msg - {txt_snippet[:35]}..." if txt_snippet else f"msg #{message.id}")
                        if resume_stage:
                            remaining_to_fetch = stage_target if stage_target > 0 else max(0, total_messages - len(existing_ids))
                            notify(f"Resuming: {len(new_messages_list)}/{remaining_to_fetch} messages...", len(new_messages_list), remaining_to_fetch, last_item=prev_str, phase='resume')
                        else:
                            p1_target = 0 if is_range_limited else (stage_target if stage_target > 0 else total_messages)
                            notify(f"{phase1_text} {message_count}...", message_count, p1_target, last_item=prev_str, phase=1)

                    # Skip if message exists in DB and not in repair mode
                    if not is_new and not repair:
                        continue

                    if core.GLOBAL_ABORT:
                        notify("Stopping upon request...", message_count, total_messages, status="error")
                        raise Exception("Archiving cancelled by user.")

                    local_media_path, thumbnail_local_path, media_id = None, None, None
                    media_mime_type, media_skipped, media_original_name = None, None, None
                    media_other_data, poll_data, file_path = None, None, None
                    file_size = 0
                    unique_filename = None
                    target_dir = chat_media_dir
                    action_data = message.action if message.action else None
                    is_sticker, is_gif = False, False
                    is_voice, is_round_video = 0, 0
                    video_cover_path = None
                    media_duration_val = 0
                    media_waveform_json = None
                    media_type_str = None
                    video_timestamp_val = None
                    from_scheduled_val = 1 if getattr(message, 'from_scheduled', False) else 0
                    forwards_val = getattr(message, 'forwards', None)

                    if message.poll:
                        poll = message.poll.poll
                        results = message.poll.results
                        poll_data = { "question": getattr(getattr(poll, 'question', None), 'text', 'Опрос'), "is_closed": getattr(poll, 'closed', False), "is_quiz": getattr(poll, 'quiz', False), "public_voters": getattr(poll, 'public_voters', False), "total_voters": getattr(results, 'total_voters', None), "options": [] }
                        results_map = {}
                        if results and getattr(results, 'results', None):
                            for res in results.results:
                                if res and getattr(res, 'option', None):
                                    try:
                                        opt_key = res.option.decode('utf-8') if isinstance(res.option, bytes) else str(res.option)
                                        results_map[opt_key] = getattr(res, 'voters', 0)
                                    except Exception as e:
                                        logging.debug(f"[core] Poll option decode error: {e}")
                        if poll and getattr(poll, 'answers', None):
                            for answer in poll.answers:
                                if answer:
                                    try:
                                        opt_key = answer.option.decode('utf-8') if isinstance(answer.option, bytes) else str(answer.option)
                                        ans_text = getattr(answer.text, 'text', str(answer.text)) if getattr(answer, 'text', None) else ''
                                        poll_data["options"].append({ "text": ans_text, "voters": results_map.get(opt_key, 0) })
                                    except Exception as e:
                                        logging.debug(f"[core] Poll answer decode error: {e}")
                    elif message.document:
                        media_id = message.document.id
                        media_mime_type = message.document.mime_type
                        file_size = message.document.size
                        attrs = message.document.attributes or []
                        
                        is_sticker = (media_mime_type == 'application/x-tgsticker')
                        is_gif = (media_mime_type == 'image/gif')
                        for attr in attrs:
                            attr_cls = getattr(attr, '__class__', type(attr)).__name__
                            if 'Sticker' in attr_cls:
                                is_sticker = True
                            elif 'Animated' in attr_cls:
                                is_gif = True
                            elif 'Audio' in attr_cls:
                                if getattr(attr, 'voice', False):
                                    is_voice = 1
                                    wf = getattr(attr, 'waveform', None)
                                    if wf:
                                        try:
                                            unpacked = helpers.unpack_telegram_waveform(wf)
                                            if unpacked:
                                                media_waveform_json = json.dumps(unpacked)
                                        except Exception:
                                            pass
                                if getattr(attr, 'duration', None):
                                    media_duration_val = int(attr.duration)
                            elif 'Video' in attr_cls and getattr(attr, 'round_message', False):
                                is_round_video = 1
                                if getattr(attr, 'duration', None):
                                    media_duration_val = int(attr.duration)
                        
                        original_filename = None
                        for attr in attrs:
                            if 'Audio' in getattr(attr, '__class__', type(attr)).__name__:
                                if getattr(attr, 'title', None): 
                                    original_filename = f"{attr.title}"
                                    if getattr(attr, 'performer', None): original_filename = f"{attr.performer} - {original_filename}"
                                    _, file_ext = os.path.splitext(getattr(attr, 'file_name', ''))
                                    if file_ext: original_filename += file_ext
                                    elif media_mime_type == 'audio/mpeg': original_filename += '.mp3'
                                    elif media_mime_type in ('audio/ogg', 'audio/x-m4a', 'audio/m4a'): original_filename += '.ogg' if 'ogg' in media_mime_type else '.m4a'
                                break
                        if not original_filename:
                            for attr in attrs:
                                if isinstance(attr, DocumentAttributeFilename): 
                                    original_filename = attr.file_name
                                    break
                                    
                        target_dir = get_media_target_dir(
                            chat_media_dir,
                            mime_type=media_mime_type,
                            attributes=attrs,
                            filename=original_filename,
                            is_voice=is_voice,
                            is_round_video=is_round_video
                        )
                                    
                        filename_ext = ".dat"
                        if is_sticker or is_gif:
                            if original_filename:
                                _, file_ext = os.path.splitext(original_filename)
                                if file_ext: filename_ext = file_ext
                            else:
                                ext_map = {'application/x-tgsticker': '.tgs', 'image/webp': '.webp', 'video/webm': '.webm', 'image/gif': '.gif', 'video/mp4': '.mp4'}
                                for key, ext in ext_map.items():
                                    if media_mime_type and key in media_mime_type: filename_ext = ext; break
                            unique_filename = f"{media_id}{filename_ext}"
                        else:
                            if original_filename:
                                unique_filename = f"{message.id} - {sanitize_filename(original_filename)}"
                            else:
                                ext_map = {'image/jpeg': '.jpg', 'image/png': '.png', 'video/mp4': '.mp4', 'audio/ogg': '.ogg', 'application/pdf': '.pdf', 'application/zip': '.zip'}
                                for key, ext in ext_map.items():
                                    if media_mime_type and key in media_mime_type: filename_ext = ext; break
                                unique_filename = f"{message.id} - {media_id}{filename_ext}"
                                
                        media_original_name = unique_filename
                        
                        # ФИКС: Убрано if media_mime_type:, теперь проверяем ВСЕ файлы
                        file_size_mb = (file_size / (1024 * 1024)) if file_size else 0
                        cfg, type_name = None, ""
                        
                        if is_sticker:
                            cfg, type_name = (settings.get('photos') or settings.get('photo', {})), "стикеров"
                        elif is_gif:
                            cfg, type_name = (settings.get('videos') or settings.get('video', {})), "GIF"
                        elif is_voice:
                            cfg, type_name = (settings.get('voice') or settings.get('voices', {})), "голосовых"
                        elif is_round_video:
                            cfg, type_name = (settings.get('video_notes') or settings.get('videos', {})), "видео-сообщений"
                        elif media_mime_type and media_mime_type.startswith('video/'):
                            cfg, type_name = (settings.get('videos') or settings.get('video', {})), "видео"
                        elif media_mime_type and media_mime_type.startswith('audio/'):
                            cfg, type_name = (settings.get('voice') or settings.get('voices') or settings.get('audio', {})), "аудио"
                        elif media_mime_type and media_mime_type.startswith('image/'):
                            cfg, type_name = (settings.get('photos') or settings.get('photo', {})), "фото"
                        else:
                            cfg, type_name = (settings.get('documents') or settings.get('files', {})), "файлов"
                            
                        # Единая проверка включения и размера
                        if cfg is not None:
                            if not cfg.get('enabled', True):
                                media_skipped = f"Загрузка {type_name} отключена"
                            elif cfg.get('max_mb', 0) > 0 and file_size_mb > cfg.get('max_mb'):
                                media_skipped = f"Больше {cfg.get('max_mb')} МБ"

                        # User & Bot Exclusion Rules for documents / media
                        if not media_skipped and settings.get('user_rules'):
                            u_rules = settings['user_rules']
                            s_id = getattr(message, 'sender_id', None)
                            v_id = getattr(message, 'via_bot_id', None)
                            f_id = get_message_forward_sender_id(message)
                            r_key = (
                                str(s_id) if s_id and str(s_id) in u_rules
                                else (str(v_id) if v_id and str(v_id) in u_rules
                                else (str(f_id) if f_id and str(f_id) in u_rules else None))
                            )
                            if r_key:
                                u_rule = u_rules[r_key]
                                r_name = u_rule.get('user_name') or u_rule.get('name') or f"User/Bot {r_key}"
                                r_mode = u_rule.get('mode', 'exclude_all')
                                if r_mode == 'exclude_all':
                                    media_skipped = f"Исключено правилом для {r_name} [user_rule_excluded:{r_key}]"
                                elif r_mode == 'custom':
                                    u_cat_key = 'documents'
                                    if is_sticker: u_cat_key = 'photos'
                                    elif is_gif: u_cat_key = 'videos'
                                    elif is_voice: u_cat_key = 'voice'
                                    elif is_round_video: u_cat_key = 'video_notes'
                                    elif media_mime_type and media_mime_type.startswith('video/'): u_cat_key = 'videos'
                                    elif media_mime_type and media_mime_type.startswith('audio/'): u_cat_key = 'voice'
                                    elif media_mime_type and media_mime_type.startswith('image/'): u_cat_key = 'photos'

                                    u_cfg = u_rule.get(u_cat_key) or {}
                                    if not u_cfg.get('enabled', True):
                                        media_skipped = f"Исключено ({u_cat_key}) для {r_name} [user_rule_excluded:{r_key}]"
                                    elif u_cfg.get('max_mb', 0) > 0 and file_size_mb > u_cfg.get('max_mb'):
                                        media_skipped = f"Больше {u_cfg.get('max_mb')} МБ ({r_name}) [user_rule_excluded:{r_key}]"

                        # Thumbnails (non-blocking best effort)
                        if not media_skipped and getattr(message.document, 'thumbs', None):
                            try:
                                thumb = message.document.thumbs[-1]
                                thumb_filename = f"{media_id}.thumb.jpg" if is_gif else f"{os.path.splitext(unique_filename)[0]}.thumb.jpg"
                                thumb_file_path = os.path.join(helpers.get_thumbs_target_dir(target_dir), thumb_filename)
                                if not os.path.exists(thumb_file_path):
                                    raw_thumb_path = await asyncio.wait_for(
                                        message.download_media(thumb=thumb, file=thumb_file_path),
                                        timeout=6.0
                                    )
                                    if raw_thumb_path and os.path.exists(raw_thumb_path):
                                        thumbnail_local_path = os.path.relpath(raw_thumb_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                                else: 
                                    thumbnail_local_path = os.path.relpath(thumb_file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            except Exception as e:
                                logging.debug(f"[core] Thumbnail download skipped for doc #{media_id}: {e}")
                        # Video cover (recent TL feature, non-blocking best effort)
                        if not media_skipped and getattr(message.media, 'video_cover', None):
                            try:
                                vc_filename = f"{message.id}_video_cover.jpg"
                                vc_path = os.path.join(helpers.get_thumbs_target_dir(target_dir), vc_filename)
                                if not os.path.exists(vc_path):
                                    await asyncio.wait_for(
                                        client.download_media(message.media.video_cover, file=vc_path),
                                        timeout=6.0
                                    )
                                if os.path.exists(vc_path):
                                    video_cover_path = os.path.relpath(vc_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            except Exception as e:
                                logging.debug(f"[core] Video cover download skipped for msg #{message.id}: {e}")
                        file_path = os.path.join(target_dir, unique_filename)
                        # Extract video_timestamp from media wrapper (recent TL feature)
                        if hasattr(message.media, 'video_timestamp'):
                            video_timestamp_val = getattr(message.media, 'video_timestamp', None)
                        
                    elif message.photo:
                        target_dir = get_media_target_dir(chat_media_dir, is_photo=True)
                        cfg = settings.get('photos', {})
                        # Проверяем новый ключ 'enabled' из JS, с фоллбэком на старый из cli.py
                        if not cfg.get('enabled', settings.get('download_photos', True)):
                            media_skipped = "Загрузка фото отключена"
                        else:
                            media_id = message.photo.id
                            unique_filename = f"{message.id} - {media_id}.jpg"
                            media_original_name = unique_filename
                            media_mime_type = 'image/jpeg'
                            last_size_obj = message.photo.sizes[-1] if getattr(message.photo, 'sizes', None) else None
                            file_size = getattr(last_size_obj, 'size', getattr(last_size_obj, 'file_size', 0)) if last_size_obj else 0
                            file_path = os.path.join(target_dir, unique_filename)
                            
                            # Проверка индивидуального лимита для фото
                            file_size_mb = (file_size / (1024 * 1024)) if file_size else 0
                            if cfg.get('max_mb', 0) > 0 and file_size_mb > cfg.get('max_mb'):
                                media_skipped = f"Больше {cfg.get('max_mb')} МБ"

                            # User & Bot Exclusion Rules for Photos
                            if not media_skipped and settings.get('user_rules'):
                                u_rules = settings['user_rules']
                                s_id = getattr(message, 'sender_id', None)
                                v_id = getattr(message, 'via_bot_id', None)
                                f_id = get_message_forward_sender_id(message)
                                r_key = (
                                    str(s_id) if s_id and str(s_id) in u_rules
                                    else (str(v_id) if v_id and str(v_id) in u_rules
                                    else (str(f_id) if f_id and str(f_id) in u_rules else None))
                                )
                                if r_key:
                                    u_rule = u_rules[r_key]
                                    r_name = u_rule.get('user_name') or u_rule.get('name') or f"User/Bot {r_key}"
                                    r_mode = u_rule.get('mode', 'exclude_all')
                                    if r_mode == 'exclude_all':
                                        media_skipped = f"Исключено правилом для {r_name} [user_rule_excluded:{r_key}]"
                                    elif r_mode == 'custom':
                                        u_cfg = u_rule.get('photos') or {}
                                        if not u_cfg.get('enabled', True):
                                            media_skipped = f"Исключено (фото) для {r_name} [user_rule_excluded:{r_key}]"
                                        elif u_cfg.get('max_mb', 0) > 0 and file_size_mb > u_cfg.get('max_mb'):
                                            media_skipped = f"Больше {u_cfg.get('max_mb')} МБ ({r_name}) [user_rule_excluded:{r_key}]"
                            
                    elif message.media:
                        media_cls = type(message.media).__name__
                        if media_cls == 'MessageMediaPaidMedia':
                            media_type_str = 'paid_media'
                            stars_amount = getattr(message.media, 'stars_amount', 0)
                            paid_items = []
                            for idx_em, em in enumerate(getattr(message.media, 'extended_media', None) or []):
                                em_type = type(em).__name__
                                if em_type == 'MessageExtendedMedia':
                                    sub_media = getattr(em, 'media', None)
                                    sub_path_rel = None
                                    if sub_media:
                                        paid_dir = os.path.join(chat_media_dir, "paid_media")
                                        os.makedirs(paid_dir, exist_ok=True)
                                        sub_fn = f"{message.id}_paid_{idx_em}.jpg"
                                        sub_abs = os.path.join(paid_dir, sub_fn)
                                        if not os.path.exists(sub_abs):
                                            try:
                                                await client.download_media(sub_media, file=sub_abs)
                                            except Exception as e_paid:
                                                logging.debug(f"[core] Paid media item download error: {e_paid}")
                                        if os.path.exists(sub_abs):
                                            sub_path_rel = os.path.relpath(sub_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                                    paid_items.append({"type": "media", "path": sub_path_rel})
                                elif em_type == 'MessageExtendedMediaPreview':
                                    paid_items.append({
                                        "type": "preview",
                                        "width": getattr(em, 'w', 0),
                                        "height": getattr(em, 'h', 0),
                                        "duration": getattr(em, 'video_duration', 0)
                                    })
                            media_other_data = {
                                "stars_amount": stars_amount,
                                "items": paid_items,
                                "raw": message.media.to_dict() if hasattr(message.media, 'to_dict') else str(message.media)
                            }
                        elif media_cls == 'MessageMediaInvoice':
                            media_type_str = 'invoice'
                            inv_photo = getattr(message.media, 'photo', None)
                            inv_photo_rel = None
                            if inv_photo:
                                inv_dir = os.path.join(chat_media_dir, "invoices")
                                os.makedirs(inv_dir, exist_ok=True)
                                inv_fn = f"{message.id}_invoice.jpg"
                                inv_abs = os.path.join(inv_dir, inv_fn)
                                if not os.path.exists(inv_abs):
                                    try:
                                        await client.download_media(inv_photo, file=inv_abs)
                                    except Exception as e_inv:
                                        logging.debug(f"[core] Invoice photo download error: {e_inv}")
                                if os.path.exists(inv_abs):
                                    inv_photo_rel = os.path.relpath(inv_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            media_other_data = {
                                "title": getattr(message.media, 'title', None),
                                "description": getattr(message.media, 'description', None),
                                "currency": getattr(message.media, 'currency', None),
                                "total_amount": getattr(message.media, 'total_amount', None),
                                "receipt_msg_id": getattr(message.media, 'receipt_msg_id', None),
                                "photo_path": inv_photo_rel,
                                "raw": message.media.to_dict() if hasattr(message.media, 'to_dict') else str(message.media)
                            }
                        elif media_cls in ('MessageMediaGiveaway', 'MessageMediaGiveawayResults'):
                            media_type_str = 'giveaway'
                            media_other_data = message.media.to_dict() if hasattr(message.media, 'to_dict') else str(message.media)
                        elif media_cls == 'MessageMediaStory':
                            media_type_str = 'story'
                            story_user_id = getattr(message.media, 'user_id', getattr(message.media, 'peer', None))
                            story_id_val = getattr(message.media, 'id', None)
                            story_story = getattr(message.media, 'story', None)
                            story_path_rel = None
                            is_expired = getattr(message.media, 'via_mention', False)
                            if story_story and getattr(story_story, 'media', None):
                                stories_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "stories")
                                os.makedirs(stories_dir, exist_ok=True)
                                story_fn = f"story_{story_user_id}_{story_id_val}.mp4"
                                story_abs = os.path.join(stories_dir, story_fn)
                                if not os.path.exists(story_abs):
                                    try:
                                        await client.download_media(story_story.media, file=story_abs)
                                    except Exception:
                                        story_fn = f"story_{story_user_id}_{story_id_val}.jpg"
                                        story_abs = os.path.join(stories_dir, story_fn)
                                        try:
                                            await client.download_media(story_story.media, file=story_abs)
                                        except Exception as e_st:
                                            logging.debug(f"[core] Story media download error: {e_st}")
                                if os.path.exists(story_abs):
                                    story_path_rel = os.path.relpath(story_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            media_other_data = {
                                "user_id": story_user_id,
                                "story_id": story_id_val,
                                "media_path": story_path_rel,
                                "is_expired": is_expired,
                                "raw": message.media.to_dict() if hasattr(message.media, 'to_dict') else str(message.media)
                            }
                        else:
                            media_other_data = message.media.to_dict() if hasattr(message.media, 'to_dict') else str(message.media)
                    if is_text_only and media_id:
                        media_skipped = "Режим «Только текст»"
                    
                    # --- ФАЗА 1: НЕ качаем медиа, только проверяем существование ---
                    if media_id and not media_skipped and unique_filename:
                        fwd_from = getattr(message, 'fwd_from', None)
                        if fwd_from and linked_chat_id_val:
                            chan_msg_id = getattr(fwd_from, 'saved_from_msg_id', None) or getattr(fwd_from, 'channel_post', None)
                            if chan_msg_id:
                                parent_chan_id = linked_chat_id_val if linked_chat_id_val < 0 else -1000000000000 - linked_chat_id_val
                                try:
                                    cursor.execute("SELECT media_local_path, thumbnail_local_path FROM tbl_messages WHERE chat_id = ? AND message_id = ? AND media_local_path IS NOT NULL", (parent_chan_id, chan_msg_id))
                                    p_row = cursor.fetchone()
                                    if p_row and p_row['media_local_path']:
                                        p_full = os.path.join(core.MEDIA_BACKUP_DIR, p_row['media_local_path'])
                                        if os.path.exists(p_full):
                                            local_media_path = p_row['media_local_path']
                                            if p_row['thumbnail_local_path']:
                                                thumbnail_local_path = p_row['thumbnail_local_path']
                                except Exception as e:
                                    logging.debug(f"[core] Reuse parent media check error: {e}")

                        if not local_media_path:
                            legacy_file = os.path.join(chat_media_dir, unique_filename)
                            if core.MAX_SIZE_BYTES > 0 and file_size > core.MAX_SIZE_BYTES:
                                media_skipped = f"Файл превышает лимит {core.MAX_FILE_SIZE_MB} МБ"
                            elif is_sticker:
                                shared_stk = os.path.join(helpers.get_shared_stickers_dir(), unique_filename)
                                if os.path.exists(shared_stk):
                                    local_media_path = os.path.relpath(shared_stk, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                                elif os.path.exists(file_path):
                                    helpers.try_create_hardlink(file_path, shared_stk)
                                    local_media_path = os.path.relpath(shared_stk, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                                else:
                                    local_media_path = None
                            elif os.path.exists(file_path):
                                # Файл уже скачан ранее
                                local_media_path = os.path.relpath(file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            elif os.path.exists(legacy_file):
                                # Файл уже скачан ранее в старой плоской структуре
                                local_media_path = os.path.relpath(legacy_file, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            else:
                                existing_src = helpers.find_existing_local_media(cursor, unique_filename, file_size)
                                if existing_src and helpers.try_create_hardlink(existing_src, file_path):
                                    local_media_path = os.path.relpath(file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                                else:
                                    # Помечаем для скачивания в Фазе 2
                                    local_media_path = None  # будет заполнен после скачивания

                    # Determine media type discriminator
                    if poll_data:
                        media_type_str = 'poll'
                    elif message.document:
                        if is_voice:
                            media_type_str = 'voice'
                        elif is_round_video:
                            media_type_str = 'video_note'
                        elif is_sticker:
                            media_type_str = 'sticker'
                        elif is_gif:
                            media_type_str = 'gif'
                        elif media_mime_type and media_mime_type.startswith('video/'):
                            media_type_str = 'video'
                        elif media_mime_type and media_mime_type.startswith('audio/'):
                            media_type_str = 'audio'
                        elif media_mime_type and media_mime_type.startswith('image/'):
                            media_type_str = 'photo'
                        else:
                            media_type_str = 'document'
                    elif message.photo:
                        media_type_str = 'photo'
                    elif media_other_data:
                        if not media_type_str:
                            media_cls = type(message.media).__name__
                            media_type_str = media_cls.replace('MessageMedia', '').lower() if media_cls.startswith('MessageMedia') else 'other'

                    if file_size > 0:
                        if media_other_data is None:
                            media_other_data = {'size': file_size}
                        elif isinstance(media_other_data, dict) and 'size' not in media_other_data:
                            media_other_data['size'] = file_size

                    fwd_data = None
                    if getattr(message, 'fwd_from', None):
                        try:
                            fwd = message.fwd_from
                            fwd_peer = getattr(getattr(message, 'forward', None), 'chat', None) or getattr(getattr(message, 'forward', None), 'sender', None)
                            if not fwd_peer:
                                fwd_peer = getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None) or getattr(fwd, 'saved_from_peer', None)
                            fwd_msg_id = getattr(fwd, 'channel_post', None) or getattr(fwd, 'saved_from_msg_id', None)
                            fwd_name, fwd_id = await get_entity_name(client, fwd_peer, msg_id=fwd_msg_id)
                            
                            # If get_entity_name failed or returned a placeholder, prioritize fwd.from_name
                            if getattr(fwd, 'from_name', None) and (not fwd_name or "ID:" in fwd_name):
                                fwd_name = getattr(fwd, 'from_name', None)
                            if getattr(fwd, 'saved_from_name', None) and (not fwd_name or "ID:" in fwd_name):
                                fwd_name = getattr(fwd, 'saved_from_name', None)
                                
                            if not fwd_id and fwd_peer:
                                fwd_id = getattr(fwd_peer, 'id', None) or getattr(fwd_peer, 'channel_id', None) or getattr(fwd_peer, 'user_id', None)

                            if fwd_id:
                                users_data_cache = await fetch_and_save_user(fwd_peer, client, cursor, users_data_cache, avatars_dir, fallback_name=fwd_name)
                            
                            # Resolve saved_from_peer (for Saved Messages forwards)
                            saved_peer = getattr(fwd, 'saved_from_peer', None)
                            if saved_peer:
                                saved_name = getattr(fwd, 'saved_from_name', None)
                                users_data_cache = await fetch_and_save_user(saved_peer, client, cursor, users_data_cache, avatars_dir, fallback_name=saved_name)

                            fwd_data = {
                                "from_id_raw": getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None),
                                "from_name": fwd_name, "from_id": fwd_id,
                                "date": getattr(fwd, 'date', None),
                                "story_id": getattr(fwd, 'story_id', None),
                                "channel_post": getattr(fwd, 'channel_post', None),
                                "post_author": getattr(fwd, 'post_author', None),
                                "header_from_name": getattr(fwd, 'from_name', None),
                                "saved_from_msg_id": getattr(fwd, 'saved_from_msg_id', None),
                                "saved_out": getattr(fwd, 'saved_out', None),
                                "saved_from_id": getattr(fwd, 'saved_from_id', None),
                                "saved_from_name": getattr(fwd, 'saved_from_name', None),
                                "saved_date": getattr(fwd, 'saved_date', None),
                                "saved_from_peer": getattr(fwd, 'saved_from_peer', None),
                                "imported": getattr(fwd, 'imported', None),
                                "psa_type": getattr(fwd, 'psa_type', None),
                            }
                        except Exception as e_fwd:
                            logging.debug(f"[core] Forward parsing error on msg #{getattr(message, 'id', '?')}: {e_fwd}")

                    if action_data:
                        act_cls = type(action_data).__name__
                        if act_cls in ('MessageActionStarGift', 'MessageActionStarGiftUnique'):
                            act_dict = action_data.to_dict() if hasattr(action_data, 'to_dict') else {}
                            gift_obj = getattr(action_data, 'gift', None)
                            if gift_obj and getattr(gift_obj, 'sticker', None):
                                gifts_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "star_gifts")
                                os.makedirs(gifts_dir, exist_ok=True)
                                gift_id = getattr(gift_obj, 'id', message.id)
                                gift_fn = f"gift_{gift_id}.webp"
                                gift_abs = os.path.join(gifts_dir, gift_fn)
                                if not os.path.exists(gift_abs):
                                    try:
                                        await client.download_media(gift_obj.sticker, file=gift_abs)
                                    except Exception as e_gift:
                                        logging.debug(f"[core] Gift sticker download error: {e_gift}")
                                if os.path.exists(gift_abs):
                                    act_dict['local_gift_path'] = os.path.relpath(gift_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            action_data = act_dict

                        act_users = getattr(action_data, 'users', None)
                        act_users_list = list(act_users) if isinstance(act_users, (list, tuple, set)) else []
                        act_uid = getattr(action_data, 'user_id', None)
                        uids_to_resolve = act_users_list + ([act_uid] if act_uid else [])
                        for uid in uids_to_resolve:
                            if uid:
                                users_data_cache = await fetch_and_save_user(uid, client, cursor, users_data_cache, avatars_dir)

                    if getattr(message, 'via_bot_id', None):
                        users_data_cache = await fetch_and_save_user(message.via_bot_id, client, cursor, users_data_cache, avatars_dir)

                    if message.sender_id:
                        users_data_cache = await fetch_and_save_user(message.sender_id, client, cursor, users_data_cache, avatars_dir)

                    if getattr(message, 'via_business_bot_id', None):
                        users_data_cache = await fetch_and_save_user(message.via_business_bot_id, client, cursor, users_data_cache, avatars_dir)

                    if getattr(message, 'saved_peer_id', None):
                        _, sp_id = await get_entity_name(client, message.saved_peer_id)
                        if sp_id:
                            users_data_cache = await fetch_and_save_user(sp_id, client, cursor, users_data_cache, avatars_dir)

                    reply_to_obj = getattr(message, 'reply_to', None)
                    reply_to_msg_id = getattr(message, 'reply_to_msg_id', None) if reply_to_obj else None
                    reply_to_story_id = getattr(reply_to_obj, 'story_id', None) if reply_to_obj else None
                    reply_to_peer = (getattr(reply_to_obj, 'reply_to_peer_id', None) or getattr(reply_to_obj, 'peer', None)) if reply_to_obj else None

                    message_data = {
                        'id': message.id, 'sender_id': getattr(message, 'sender_id', None), 'date': getattr(message, 'date', None),
                        'text': getattr(message, 'raw_text', None) or '', 'entities': getattr(message, 'entities', None), 'group_id': getattr(message, 'grouped_id', None),
                        'reply_to_msg_id': reply_to_msg_id,
                        'reply_to_story_id': reply_to_story_id,
                        'edited_date': getattr(message, 'edit_date', None) if not getattr(message, 'edit_hide', False) else None,
                        'thumbnail_local_path': thumbnail_local_path, 'media_original_name': media_original_name,
                        'media_mime_type': media_mime_type, 'media_skipped': media_skipped,
                        'media_local_path': local_media_path,
                        'file_size': file_size or 0,
                        'forward_info': fwd_data, 'service_action': action_data, 'poll_data': poll_data,
                        'reply_snippet': None, 'reply_sender_id': None,
                        'reactions': getattr(message, 'reactions', None),
                        'is_media_spoiler': (getattr(message, 'spoiler', False) or (getattr(message, 'media', None) and getattr(message.media, 'spoiler', False))),
                        'media_other': media_other_data, 'views': getattr(message, 'views', None),
                        'via_bot_id': getattr(message, 'via_bot_id', None), 'post_author': getattr(message, 'post_author', None),
                        'is_pinned': 1 if getattr(message, 'pinned', False) else 0, 'reply_markup': getattr(message, 'reply_markup', None),
                        # --- Group A: Message-level flags ---
                        'is_outgoing': 1 if getattr(message, 'out', False) else 0,
                        'is_mentioned': 1 if getattr(message, 'mentioned', False) else 0,
                        'is_post': 1 if getattr(message, 'post', False) else 0,
                        'invert_media': 1 if getattr(message, 'invert_media', False) else 0,
                        'is_legacy': 1 if getattr(message, 'legacy', False) else 0,
                        'replies': getattr(message, 'replies', None),
                        'restriction_reason': getattr(message, 'restriction_reason', None),
                        # --- Group C: Reply header enrichment ---
                        'reply_to_top_id': getattr(reply_to_obj, 'reply_to_top_id', None) if reply_to_obj else None,
                        'is_forum_topic': 1 if (reply_to_obj and getattr(reply_to_obj, 'forum_topic', False)) else 0,
                        'reply_to_peer_id': reply_to_peer,
                        'quote_text': getattr(reply_to_obj, 'quote_text', None) if reply_to_obj else None,
                        'quote_entities': getattr(reply_to_obj, 'quote_entities', None) if reply_to_obj else None,
                        'quote_offset': getattr(reply_to_obj, 'quote_offset', None) if reply_to_obj else None,
                        'reply_from': getattr(reply_to_obj, 'reply_from', None) if reply_to_obj else None,
                        'reply_media': getattr(reply_to_obj, 'reply_media', None) if reply_to_obj else None,
                        # --- Group D: Media sub-fields ---
                        'is_voice': is_voice, 'is_round_video': is_round_video,
                        'video_cover_path': video_cover_path, 'media_type': media_type_str,
                        'duration': media_duration_val, 'waveform_data': media_waveform_json,
                        # --- Medium priority fields ---
                        'saved_peer_id': getattr(message, 'saved_peer_id', None),
                        'via_business_bot_id': getattr(message, 'via_business_bot_id', None),
                        'video_timestamp': video_timestamp_val,
                        # --- Low priority fields ---
                        'from_scheduled': from_scheduled_val,
                        'forwards': forwards_val,
                    }
                    
                    if message_count % 3 == 0 or unique_filename:
                        preview_txt = ""
                        if unique_filename: preview_txt = f"file - {unique_filename}"
                        elif message.raw_text: preview_txt = f"msg - {message.raw_text[:40].replace(chr(10), ' ')}..."
                        else: preview_txt = "msg - [Media]"
                        if resume_stage:
                            notify(f"Resuming: {len(new_messages_list)}/{stage_target} messages...", len(new_messages_list), stage_target, last_item=preview_txt, phase='resume')
                        else:
                            msg_label = f"Phase 1: {message_count}/{stage_target} messages..." if stage_target > 0 else f"Phase 1: {message_count} messages..."
                            notify(msg_label, message_count, stage_target, last_item=preview_txt, phase=1)
                    
                    # В обычном режиме (не ремонт) мы попадаем сюда только для новых сообщений благодаря offset_id.
                    # Но в режиме ремонта мы проходим всё.
                    
                    # Enrich reply snippet on the fly
                    reply_id = message_data.get('reply_to_msg_id')
                    if reply_id:
                        reply_original_msg = recent_messages_map.get(reply_id) or fetch_message_from_db(cursor, reply_id, chat_id)
                        if reply_original_msg:
                            orig_text = (reply_original_msg.get('text') or '').strip()
                            orig_mtype = reply_original_msg.get('media_type')
                            orig_grp = reply_original_msg.get('group_id')
                            orig_name = reply_original_msg.get('media_original_name')
                            media_snip = ""
                            if orig_grp:
                                try:
                                    cursor.execute("SELECT message_id FROM tbl_messages WHERE group_id = ? ORDER BY message_id ASC", (orig_grp,))
                                    album_mids = [r[0] for r in cursor.fetchall()]
                                    if reply_id in album_mids:
                                        b_type = (orig_mtype or 'Photo').capitalize()
                                        media_snip = f"{b_type} ({album_mids.index(reply_id) + 1}/{len(album_mids)})" if len(album_mids) > 1 else b_type
                                except Exception:
                                    pass
                            if not media_snip and orig_mtype:
                                if orig_mtype == 'photo': media_snip = "Photo"
                                elif orig_mtype == 'video': media_snip = "Video"
                                elif orig_mtype in ('file', 'document'): media_snip = f"Document: {orig_name}" if orig_name else "Document"
                                elif orig_mtype == 'audio': media_snip = f"Audio: {orig_name}" if orig_name else "Audio"
                                else: media_snip = orig_mtype.capitalize()

                            if media_snip and orig_text:
                                message_data['reply_snippet'] = f"{media_snip}: {orig_text}"[:50]
                            elif media_snip:
                                message_data['reply_snippet'] = media_snip[:50]
                            elif orig_text:
                                message_data['reply_snippet'] = orig_text[:50]
                            else:
                                message_data['reply_snippet'] = "[Media]"

                            orig_sender = reply_original_msg.get('sender_id')
                            rfwd = reply_original_msg.get('forward_info')
                            if rfwd:
                                if isinstance(rfwd, str):
                                    try: rfwd = json.loads(rfwd)
                                    except Exception: rfwd = None
                                if isinstance(rfwd, dict):
                                    fwd_id = rfwd.get('from_id') or rfwd.get('sender_id')
                                    if not fwd_id and isinstance(rfwd.get('from_id_raw'), dict):
                                        fwd_id = rfwd['from_id_raw'].get('user_id') or rfwd['from_id_raw'].get('channel_id') or rfwd['from_id_raw'].get('chat_id')
                                    if fwd_id:
                                        orig_sender = fwd_id
                            message_data['reply_sender_id'] = orig_sender
                    elif message_data.get('reply_to_story_id'):
                        st_id = message_data['reply_to_story_id']
                        message_data['reply_snippet'] = f"Story #{st_id}"

                    recent_messages_map[message_data['id']] = message_data
                    if len(recent_messages_map) > 500:
                        oldest_k = next(iter(recent_messages_map))
                        del recent_messages_map[oldest_k]

                    # Сохраняем данные сообщения + объект Telethon для Фазы 2
                    idx = len(new_messages_list)
                    new_messages_list.append(message_data)
                    unflushed_messages.append(message_data)
                    custom_emoji_ids.update(extract_custom_emoji_ids_from_message(message))
                    
                    # Если файл нужно скачать — добавляем в очередь загрузки
                    # В режиме ремонта сюда попадут даже старые сообщения, если их файлов нет на диске
                    if media_id and not media_skipped and unique_filename and file_path and not os.path.exists(file_path):
                        download_queue.append((idx, message, file_path))

                    # Потоковый коммит каждые 100 сообщений: нулевая потеря данных при сбоях
                    if len(unflushed_messages) >= 100:
                        db.flush_messages_batch(conn, cursor, chat_id, unflushed_messages)
                        unflushed_messages.clear()
                        db.recalculate_chat_bounds(cursor, chat_id, server_top_id=top_id, backup_status='running')
                
                if cutoff_reached:
                    break

                if reverse_fetch:
                    if current_offset_id <= prev_offset_id:
                        break
                    if top_id and current_offset_id >= top_id:
                        break

            except FloodWaitError as e:
                wait_seconds = e.seconds + 5
                notify(f"Limit! Waiting {wait_seconds}s...", message_count, 0 if is_range_limited else total_messages)
                await asyncio.sleep(wait_seconds)
            except Exception as e:
                raise e
        if unflushed_messages:
            db.flush_messages_batch(conn, cursor, chat_id, unflushed_messages)
            unflushed_messages.clear()
            db.recalculate_chat_bounds(cursor, chat_id, server_top_id=top_id, backup_status='running')

        if is_range_limited:
            total_messages = message_count
        else:
            total_messages = max(total_messages, message_count)
        
        # =============================================
        # PHASE 2: PARALLEL MEDIA DOWNLOAD
        # Semaphore limits concurrency to N_WORKERS.
        # FloodWait triggers global pause across all workers.
        # =============================================
        custom_emoji_cfg = settings.get('custom_emoji') or settings.get('custom_emojis') or {'enabled': True}
        if custom_emoji_ids and custom_emoji_cfg.get('enabled', True):
            try:
                await async_fetch_and_save_custom_emojis(client, custom_emoji_ids, conn)
            except Exception as e_ce:
                logging.error(f"Error fetching custom emojis: {e_ce}")

        if download_queue:
            n_workers = core.DOWNLOAD_WORKERS
            download_total = len(download_queue)
            download_done = 0
            download_errors = 0
            db_write_lock = asyncio.Lock()
            notify(f"Phase 2: Downloading {download_total} new files (0/{download_total}) [{n_workers} threads]...", 0, download_total, phase=2)
            
            sem = asyncio.Semaphore(n_workers)
            flood_event = asyncio.Event()
            flood_event.set()  # Изначально открыт (не заблокирован)
            
            async def download_one(idx, msg_obj, fpath):
                nonlocal download_done, download_errors
                async with sem:
                    if core.GLOBAL_ABORT:
                        download_errors += 1
                        return
                        
                    await flood_event.wait()  # Ждем, если глобальный FloodWait активен
                    actual_fpath = fpath
                    is_stk = new_messages_list[idx].get('media_type') == 'sticker'
                    if is_stk:
                        actual_fpath = os.path.join(helpers.get_shared_stickers_dir(), os.path.basename(fpath))

                    try:
                        if os.path.exists(actual_fpath):
                            raw_path = actual_fpath
                        else:
                            async with db_write_lock:
                                existing_src = helpers.find_existing_local_media(cursor, os.path.basename(fpath), new_messages_list[idx].get('file_size'))
                            if existing_src and helpers.try_create_hardlink(existing_src, actual_fpath):
                                raw_path = actual_fpath
                            else:
                                raw_path = await helpers.download_media_robust(
                                    client, msg_obj, actual_fpath, chat_entity=chat_entity
                                )
                        if raw_path:
                            rel_path = os.path.relpath(raw_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                            new_messages_list[idx]['media_local_path'] = rel_path
                            new_messages_list[idx]['media_skipped'] = None
                            async with db_write_lock:
                                cursor.execute("UPDATE tbl_messages SET media_local_path = ?, media_skipped = NULL WHERE chat_id = ? AND message_id = ?", (rel_path, chat_id, msg_obj.id))
                                conn.commit()
                        else:
                            clean_reason = "Unavailable on Telegram (purged or expired)"
                            new_messages_list[idx]['media_skipped'] = clean_reason
                            async with db_write_lock:
                                cursor.execute("UPDATE tbl_messages SET media_skipped = ? WHERE chat_id = ? AND message_id = ?", (clean_reason, chat_id, msg_obj.id))
                                conn.commit()
                    except FloodWaitError as e:
                        # Глобальная пауза: блокируем ВСЕ воркеры
                        flood_event.clear()
                        wait_secs = e.seconds + 5
                        notify(f"Phase 2: FloodWait {wait_secs}s — pausing all downloads...", download_done, download_total, phase=2)
                        try: await asyncio.sleep(wait_secs)
                        except asyncio.CancelledError: pass
                        flood_event.set()  # Разблокируем
                        # Повторяем попытку после паузы
                        try:
                            raw_path = await helpers.download_media_robust(
                                client, msg_obj, actual_fpath, chat_entity=chat_entity
                            )
                            if raw_path:
                                rel_path = os.path.relpath(raw_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                                new_messages_list[idx]['media_local_path'] = rel_path
                                new_messages_list[idx]['media_skipped'] = None
                                async with db_write_lock:
                                    cursor.execute("UPDATE tbl_messages SET media_local_path = ?, media_skipped = NULL WHERE chat_id = ? AND message_id = ?", (rel_path, chat_id, msg_obj.id))
                                    conn.commit()
                            else:
                                clean_reason = "Unavailable on Telegram (purged or expired)"
                                new_messages_list[idx]['media_skipped'] = clean_reason
                                async with db_write_lock:
                                    cursor.execute("UPDATE tbl_messages SET media_skipped = ? WHERE chat_id = ? AND message_id = ?", (clean_reason, chat_id, msg_obj.id))
                                    conn.commit()
                        except Exception as retry_e:
                            _code, _label, _perm = helpers.classify_media_download_error(retry_e)
                            err_reason = f"Unavailable on Telegram ({_label})" if _perm else f"Failed: {_label}"
                            new_messages_list[idx]['media_skipped'] = err_reason
                            async with db_write_lock:
                                cursor.execute("UPDATE tbl_messages SET media_skipped = ? WHERE chat_id = ? AND message_id = ?", (err_reason, chat_id, msg_obj.id))
                                conn.commit()
                            download_errors += 1
                    except asyncio.TimeoutError:
                        err_reason = "Failed: Telegram Server Timeout (180s)"
                        new_messages_list[idx]['media_skipped'] = err_reason
                        async with db_write_lock:
                            cursor.execute("UPDATE tbl_messages SET media_skipped = ? WHERE chat_id = ? AND message_id = ?", (err_reason, chat_id, msg_obj.id))
                            conn.commit()
                        download_errors += 1
                    except Exception as e_dl:
                        _code, _label, _perm = helpers.classify_media_download_error(e_dl)
                        err_reason = f"Unavailable on Telegram ({_label})" if _perm else f"Failed: {_label}"
                        new_messages_list[idx]['media_skipped'] = err_reason
                        async with db_write_lock:
                            cursor.execute("UPDATE tbl_messages SET media_skipped = ? WHERE chat_id = ? AND message_id = ?", (err_reason, chat_id, msg_obj.id))
                            conn.commit()
                        download_errors += 1
                    finally:
                        download_done += 1
                        if download_done % 5 == 0 or download_done == download_total:
                            fname = os.path.basename(fpath)
                            remaining_files = max(0, download_total - download_done)
                            notify(
                                f"Phase 2: ({download_done}/{remaining_files}) files [{download_total} new]...",
                                download_done, download_total,
                                last_item=f"dl - {fname}",
                                phase=2
                            )
            
            # Запускаем задачи батчами по 500 (Semaphore ограничит конкурентность воркеров, батчинг защитит RAM)
            BATCH_SIZE = 500
            for b_idx in range(0, len(download_queue), BATCH_SIZE):
                if core.GLOBAL_ABORT:
                    break
                batch_tasks = [
                    download_one(idx, msg_obj, fpath)
                    for idx, msg_obj, fpath in download_queue[b_idx:b_idx + BATCH_SIZE]
                ]
                await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            if core.GLOBAL_ABORT:
                notify("Stopping upon request...", message_count, total_messages, status="error")
                raise Exception("Archiving cancelled by user.")
            
            err_suffix = f" ({download_errors} errors)" if download_errors else ""
            notify(f"Phase 2: Complete — {download_total} files{err_suffix}", download_total, download_total, phase=2)

        # Сохранение в БД
        if new_messages_list:
            notify(f"Saving {len(new_messages_list)} messages...", total_messages, total_messages, phase=3)
            
            new_messages_map = {msg['id']: msg for msg in new_messages_list}
            for msg_data in new_messages_list:
                reply_id = msg_data.get('reply_to_msg_id')
                if reply_id and not msg_data.get('reply_snippet'):
                    reply_original_msg = new_messages_map.get(reply_id) or fetch_message_from_db(cursor, reply_id, chat_id)
                    if reply_original_msg:
                        orig_text = (reply_original_msg.get('text') or '').strip()
                        orig_mtype = reply_original_msg.get('media_type')
                        orig_grp = reply_original_msg.get('group_id')
                        orig_name = reply_original_msg.get('media_original_name')
                        media_snip = ""
                        if orig_grp:
                            try:
                                album_mids = [m['id'] for m in new_messages_list if m.get('group_id') == orig_grp]
                                if not album_mids:
                                    cursor.execute("SELECT message_id FROM tbl_messages WHERE group_id = ? ORDER BY message_id ASC", (orig_grp,))
                                    album_mids = [r[0] for r in cursor.fetchall()]
                                else:
                                    album_mids.sort()
                                if reply_id in album_mids:
                                    b_type = (orig_mtype or 'Photo').capitalize()
                                    if len(album_mids) > 1:
                                        media_snip = f"{b_type} ({album_mids.index(reply_id) + 1}/{len(album_mids)})"
                                    else:
                                        media_snip = b_type
                            except Exception:
                                pass
                        if not media_snip and orig_mtype:
                            if orig_mtype == 'photo': media_snip = "Photo"
                            elif orig_mtype == 'video': media_snip = "Video"
                            elif orig_mtype in ('file', 'document'): media_snip = f"Document: {orig_name}" if orig_name else "Document"
                            elif orig_mtype == 'audio': media_snip = f"Audio: {orig_name}" if orig_name else "Audio"
                            else: media_snip = orig_mtype.capitalize()

                        if media_snip and orig_text:
                            msg_data['reply_snippet'] = f"{media_snip}: {orig_text}"[:50]
                        elif media_snip:
                            msg_data['reply_snippet'] = media_snip[:50]
                        elif orig_text:
                            msg_data['reply_snippet'] = orig_text[:50]
                        else:
                            msg_data['reply_snippet'] = "[Media]"
                        orig_sender = reply_original_msg.get('sender_id')
                        rfwd = reply_original_msg.get('forward_info')
                        if rfwd:
                            if isinstance(rfwd, str):
                                try: rfwd = json.loads(rfwd)
                                except Exception: rfwd = None
                            if isinstance(rfwd, dict):
                                fwd_id = rfwd.get('from_id') or rfwd.get('sender_id')
                                if not fwd_id and isinstance(rfwd.get('from_id_raw'), dict):
                                    fwd_id = rfwd['from_id_raw'].get('user_id') or rfwd['from_id_raw'].get('channel_id') or rfwd['from_id_raw'].get('chat_id')
                                if fwd_id:
                                    orig_sender = fwd_id
                        msg_data['reply_sender_id'] = orig_sender
                elif msg_data.get('reply_to_story_id'):
                    st_id = msg_data['reply_to_story_id']
            reply_updates = [
                (msg.get('reply_snippet'), msg.get('reply_sender_id'), chat_id, msg['id'])
                for msg in new_messages_list
                if msg.get('reply_to_msg_id') or msg.get('reply_to_story_id')
            ]
            if reply_updates:
                cursor.executemany(
                    "UPDATE tbl_messages SET reply_snippet = ?, reply_sender_id = ? WHERE chat_id = ? AND message_id = ?",
                    reply_updates
                )
            
            # Watermark Guard: Only update last_backup_offset_id if full sweep was performed or latest resume
            should_update_watermark = (range_mode == 'all' or resume_stage == 'latest')
            if should_update_watermark and new_messages_list:
                max_msg_id = max(m['id'] for m in new_messages_list)
                cursor.execute("UPDATE tbl_chats SET last_backup_offset_id = MAX(COALESCE(last_backup_offset_id, 0), ?) WHERE chat_id = ?", (max_msg_id, chat_id))
            conn.commit()
            link_migrated_chats(conn)

            # Update bounds
            if resume_stage == 'latest':
                cursor.execute("UPDATE tbl_chats SET has_newer_gap = 0 WHERE chat_id = ?", (chat_id,))
            elif resume_stage == 'older':
                cursor.execute("UPDATE tbl_chats SET has_older_gap = 0 WHERE chat_id = ?", (chat_id,))
            elif range_mode == 'all':
                cursor.execute("UPDATE tbl_chats SET has_newer_gap = 0, backup_status = 'complete' WHERE chat_id = ?", (chat_id,))
            db.recalculate_chat_bounds(cursor, chat_id, server_top_id=top_id, backup_status='complete' if not resume_stage else None)
            conn.commit()
            
            # =============================================
            # PHASE 3: SCAVENGER — Resolve unknown users & missing custom emojis
            # =============================================
            placeholders = [uid for uid, info in users_data_cache.items() if "[ID:" in (info.get('first_name') or '') or "ID:" in (info.get('first_name') or '')]
            if placeholders:
                notify(f"Phase 3: Resolving {len(placeholders)} placeholder names...", message_count, total_messages, phase=3)
                cursor.execute("SELECT forward_info_json FROM tbl_messages WHERE chat_id = ? AND forward_info_json IS NOT NULL", (chat_id,))
                rows = cursor.fetchall()
                attempted_scavenge_ids = set()
                for row in rows:
                    try:
                        fwd = json.loads(row[0])
                        fid = fwd.get('from_id')
                        raw = fwd.get('from_id_raw') or {}
                        saved_raw = fwd.get('saved_from_peer') or {}
                        
                        target_id = fid or raw.get('channel_id') or raw.get('user_id') or raw.get('chat_id')
                        if target_id is None or target_id in attempted_scavenge_ids or str(target_id) in attempted_scavenge_ids:
                            continue
                        if target_id in placeholders or str(target_id) in placeholders:
                            attempted_scavenge_ids.add(target_id)
                            attempted_scavenge_ids.add(str(target_id))
                            from telethon.tl.types import PeerChannel, PeerUser, PeerChat
                            peer = None
                            if raw.get('_') == 'PeerChannel' or saved_raw.get('_') == 'PeerChannel' or str(target_id).startswith('-100'):
                                cid_num = raw.get('channel_id') or saved_raw.get('channel_id') or int(str(target_id).replace('-100', ''))
                                peer = PeerChannel(int(cid_num))
                            elif raw.get('_') == 'PeerChat' or saved_raw.get('_') == 'PeerChat' or (str(target_id).startswith('-') and not str(target_id).startswith('-100')):
                                gid_num = raw.get('chat_id') or saved_raw.get('chat_id') or int(str(target_id).replace('-', ''))
                                peer = PeerChat(int(gid_num))
                            elif raw.get('_') == 'PeerUser' or saved_raw.get('_') == 'PeerUser':
                                uid_num = raw.get('user_id') or saved_raw.get('user_id') or int(target_id)
                                peer = PeerUser(int(uid_num))
                            else:
                                if str(target_id).startswith('-100'): peer = PeerChannel(int(str(target_id)[4:]))
                                elif str(target_id).startswith('-'): peer = PeerChat(int(str(target_id)[1:]))
                                else:
                                    try: peer = int(target_id)
                                    except Exception: peer = target_id
                            
                            fb_name = fwd.get('from_name') or fwd.get('header_from_name') or fwd.get('saved_from_name')
                            users_data_cache = await fetch_and_save_user(peer, client, cursor, users_data_cache, avatars_dir, fallback_name=fb_name)
                            cached_name = users_data_cache.get(target_id, {}).get('first_name') or (users_data_cache.get(int(target_id), {}).get('first_name') if str(target_id).lstrip('-').isdigit() else '')
                            if cached_name and "[ID:" not in cached_name and "ID:" not in cached_name:
                                if target_id in placeholders: placeholders.remove(target_id)
                                if str(target_id) in placeholders: placeholders.remove(str(target_id))
                    except Exception as e:
                        logging.debug(f"[core] Scavenger peer resolution warning: {e}")

            # Phase 3B: Scavenge and fetch any missing custom emojis for this chat
            try:
                cursor.execute("""
                    SELECT entities_json, reactions_json FROM tbl_messages 
                    WHERE chat_id = ? AND (entities_json LIKE '%MessageEntityCustomEmoji%' OR reactions_json LIKE '%ReactionCustomEmoji%')
                """, (chat_id,))
                msg_emoji_rows = cursor.fetchall()
                chat_emoji_ids = set()
                for me_row in msg_emoji_rows:
                    if me_row[0]:
                        try:
                            for ent in json.loads(me_row[0]):
                                if ent.get('_') == 'MessageEntityCustomEmoji' and ent.get('document_id'):
                                    chat_emoji_ids.add(ent['document_id'])
                        except Exception: pass
                    if me_row[1]:
                        try:
                            rx_data = json.loads(me_row[1])
                            for r_item in (rx_data.get('results', []) + rx_data.get('recent_reactions', [])):
                                rx_obj = r_item.get('reaction', {})
                                if rx_obj.get('_') == 'ReactionCustomEmoji' and rx_obj.get('document_id'):
                                    chat_emoji_ids.add(rx_obj['document_id'])
                        except Exception: pass
                
                if chat_emoji_ids:
                    custom_emoji_cfg = settings.get('custom_emoji') or settings.get('custom_emojis') or {'enabled': True}
                    if custom_emoji_cfg.get('enabled', True):
                        await async_fetch_and_save_custom_emojis(client, chat_emoji_ids, conn)
            except Exception as e_ces:
                logging.debug(f"Custom emoji scavenger error: {e_ces}")

            final_total = max(message_count, stage_target if stage_target > 0 else total_messages)
            notify("Архивация завершена!", message_count, final_total, status="done")
            cursor.execute("SELECT migrated_to_chat_id FROM tbl_chats WHERE chat_id = ?", (chat_id,))
            mig_result = cursor.fetchone()
            return f"Готово! (Скачано {len(new_messages_list)})", mig_result[0] if mig_result else None

        else:
            notify("Готово! (Новых нет)", total_messages, total_messages, "done")
            cursor.execute("SELECT migrated_to_chat_id FROM tbl_chats WHERE chat_id = ?", (chat_id,))
            mig_result = cursor.fetchone()
            return "Готово! (Новых нет)", mig_result[0] if mig_result else None

    except Exception as e_main:
        backup_exception = e_main
        raise e_main
    finally:
        core.IS_BULK_ARCHIVING = False
        try:
            if 'unflushed_messages' in locals() and unflushed_messages:
                db.flush_messages_batch(conn, cursor, chat_id, unflushed_messages, users_data_cache)
                unflushed_messages.clear()
            if 'chat_id' in locals() and cursor and conn:
                h_status = 'complete'
                h_reason = None
                if core.GLOBAL_ABORT:
                    h_status = 'halted'
                    h_reason = 'user_abort'
                elif backup_exception:
                    h_status = 'halted'
                    h_reason = str(backup_exception)
                db.recalculate_chat_bounds(
                    cursor, chat_id,
                    server_top_id=top_id if 'top_id' in locals() else None,
                    backup_status=h_status if (core.GLOBAL_ABORT or backup_exception) else None,
                    halt_reason=h_reason
                )
                conn.commit()
        except Exception as e_fin:
            logging.debug(f"[backup finally flush error]: {e_fin}")
        if conn: conn.close()
        if client_was_created and client:
            await client.disconnect()


# ── Re-export extracted dialog & catch-up engines for 100% backward compatibility ──
from .dialogs import async_fetch_and_cache_dialogs, async_quick_fetch_and_cache_dialogs
from .catchup import async_download_missing_chat_media, async_resume_chat_download, async_reconcile_chat_gaps
