#!/usr/bin/env python3
"""
core/catchup.py — Missing media catch-up downloader, dual-strategy resume, and O(1) HOR gap reconciliation.
"""
import os
import re
import time
import json
import logging
import sqlite3
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
from .backup import async_run_backup
import core

async def async_download_missing_chat_media(chat_id, settings, progress_callback=None, client=None, bypass_limits=False, include_msg_ids=None, priority_msg_ids=None):
    """
    Downloads missing media files for existing messages in tbl_messages that match active settings.
    Pre-filters in DB/Python before Telegram RPCs and downloads in parallel worker pool.
    """
    conn = get_db_conn()
    cursor = conn.cursor()
    client_was_created = False

    try:
        try:
            pipeline = core.db.get_chat_pipeline(cursor, chat_id)
        except Exception:
            pipeline = [chat_id]

        chat_variants = []
        seen_v = set()
        for pid in [chat_id] + [p for p in pipeline if p != chat_id]:
            for v in [pid] + list(get_chat_id_variants(pid)):
                if v not in seen_v:
                    seen_v.add(v)
                    chat_variants.append(v)

        placeholders = ','.join('?' for _ in chat_variants)

        # 1. Resolve settings from DB if not provided
        if not settings:
            try:
                cursor.execute(f"SELECT archive_settings_json FROM tbl_chats WHERE chat_id IN ({placeholders})", chat_variants)
                s_row = cursor.fetchone()
            except sqlite3.OperationalError:
                s_row = None
            if s_row and s_row['archive_settings_json']:
                try:
                    settings = json.loads(s_row['archive_settings_json'])
                except Exception:
                    settings = {}
            if not settings:
                settings = core.db.get_global_settings(cursor)
        settings = normalize_media_settings(settings or {})

        # 2. Query missing media rows from SQLite BEFORE touching Telegram API
        if include_msg_ids:
            msg_ids_list = [int(mid) for mid in include_msg_ids if mid]
            if not msg_ids_list:
                if progress_callback:
                    progress_callback(chat_id, "No messages specified.", 0, 0, status="done", phase="catchup")
                return "No messages specified."

            rows = []
            chunk_size = 500
            for i in range(0, len(msg_ids_list), chunk_size):
                chunk = msg_ids_list[i:i + chunk_size]
                m_ph = ','.join('?' for _ in chunk)
                cursor.execute(f"""
                    SELECT message_id, chat_id, sender_id, via_bot_id, media_type,
                           media_original_name, media_mime_type, media_skipped,
                           COALESCE(
                               NULLIF(file_size, 0),
                               CAST(json_extract(media_other_json, '$.document.size') AS INTEGER),
                               CAST(json_extract(media_other_json, '$.size') AS INTEGER),
                               0
                           ) AS est_size,
                           COALESCE(
                               json_extract(forward_info_json, '$.from_id'),
                               json_extract(forward_info_json, '$.from_id_raw.user_id'),
                               json_extract(forward_info_json, '$.from_id_raw.channel_id')
                           ) AS fwd_from_id
                    FROM tbl_messages
                    WHERE chat_id IN ({placeholders})
                      AND message_id IN ({m_ph})
                      AND (media_local_path IS NULL OR media_local_path = '')
                    ORDER BY message_id DESC
                """, (*chat_variants, *chunk))
                rows.extend(cursor.fetchall())
        else:
            cursor.execute(f"""
                SELECT message_id, chat_id, sender_id, via_bot_id, media_type,
                       media_original_name, media_mime_type, media_skipped,
                       COALESCE(
                           NULLIF(file_size, 0),
                           CAST(json_extract(media_other_json, '$.document.size') AS INTEGER),
                           CAST(json_extract(media_other_json, '$.size') AS INTEGER),
                           0
                       ) AS est_size,
                       COALESCE(
                           json_extract(forward_info_json, '$.from_id'),
                           json_extract(forward_info_json, '$.from_id_raw.user_id'),
                           json_extract(forward_info_json, '$.from_id_raw.channel_id')
                       ) AS fwd_from_id
                FROM tbl_messages
                WHERE chat_id IN ({placeholders})
                  AND (media_local_path IS NULL OR media_local_path = '')
                  AND (media_skipped IS NOT NULL OR media_type IN ('photo', 'video', 'document', 'audio', 'voice', 'video_note', 'sticker', 'gif', 'paid_media', 'invoice', 'story', 'media'))
                ORDER BY message_id DESC
            """, chat_variants)
            rows = cursor.fetchall()

        if not rows:
            if progress_callback:
                progress_callback(chat_id, "No missing media to download.", 0, 0, status="done", phase="catchup")
            return "No missing media found."

        # 3. Pre-filter in Python using DB metadata to eliminate wasted Telegram get_messages() RPC batches
        p_set = set(int(mid) for mid in (priority_msg_ids or []) if mid)
        user_rules = settings.get('user_rules') or {}

        if not bypass_limits and not include_msg_ids:
            filtered_rows = []
            for r in rows:
                mid = r['message_id']
                if mid in p_set:
                    filtered_rows.append(r)
                    continue

                skip = r['media_skipped']
                if skip and (helpers.is_media_skip_permanent(skip) or helpers.is_media_skip_download_failed(skip)):
                    continue

                m_type_raw = (r['media_type'] or 'document').lower()
                if m_type_raw in ('web_page', 'webpage', 'poll', 'dice', 'geo', 'geolive', 'contact', 'venue', 'game') and not skip:
                    continue

                cfg_key = 'documents'
                if m_type_raw == 'photo':
                    cfg_key = 'photos'
                elif m_type_raw in ('video', 'gif'):
                    cfg_key = 'videos'
                elif m_type_raw in ('voice', 'audio'):
                    cfg_key = 'voice'
                elif m_type_raw == 'video_note':
                    cfg_key = 'video_notes'

                est_size = r['est_size'] or 0

                # Check user/bot exclusion rules
                s_id = str(r['sender_id']) if r['sender_id'] else None
                v_id = str(r['via_bot_id']) if r['via_bot_id'] else None
                f_id = str(r['fwd_from_id']) if r['fwd_from_id'] else None
                u_rule = user_rules.get(s_id) or (user_rules.get(v_id) if v_id else None) or (user_rules.get(f_id) if f_id else None)
                if u_rule:
                    r_mode = u_rule.get('mode', 'exclude_all')
                    if r_mode == 'exclude_all':
                        continue
                    elif r_mode == 'custom':
                        u_cfg = u_rule.get(cfg_key) or {}
                        if not u_cfg.get('enabled', True):
                            continue
                        if u_cfg.get('max_mb', 0) > 0 and est_size > (u_cfg.get('max_mb') * 1024 * 1024):
                            continue

                # Check category enabled & max_mb
                cfg = settings.get(cfg_key) or {}
                if not cfg.get('enabled', True):
                    continue
                max_mb = cfg.get('max_mb', 0)
                if max_mb and max_mb > 0:
                    if est_size > (max_mb * 1024 * 1024):
                        continue
                    if est_size == 0 and skip:
                        m_match = re.search(r'(?:Больше|лимит)\s+(\d+(?:\.\d+)?)\s*(?:МБ|MB)', str(skip), re.IGNORECASE)
                        if m_match and float(m_match.group(1)) >= float(max_mb):
                            continue

                filtered_rows.append(r)

            rows = filtered_rows
            if not rows:
                if progress_callback:
                    progress_callback(chat_id, "No eligible missing media items match current settings.", 0, 0, status="done", phase="catchup")
                return "No eligible missing media items."

        if priority_msg_ids:
            rows = sorted(rows, key=lambda r: (0 if r['message_id'] in p_set else 1, -r['message_id']))

        row_meta_map = {r['message_id']: dict(r) for r in rows}
        missing_msg_ids = [r['message_id'] for r in rows]
        total_items = len(missing_msg_ids)

        if progress_callback:
            progress_callback(chat_id, f"Found {total_items} eligible media items. Connecting...", 0, total_items, status="running", phase="catchup")

        # 4. Connect Telethon client and resolve entity ONLY when there is actual work to do
        if client and client.is_connected():
            pass
        elif getattr(core, 'LIVE_CLIENT', None) and core.LIVE_CLIENT.is_connected():
            client = core.LIVE_CLIENT
        else:
            client = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
            client_was_created = True

        if client_was_created:
            await client.connect()

        client.flood_sleep_threshold = max(getattr(client, 'flood_sleep_threshold', 60), 120)

        if not await client.is_user_authorized():
            raise Exception("Client not authorized.")

        chat_entity = None
        for variant in chat_variants:
            try:
                chat_entity = await client.get_entity(variant)
                if chat_entity:
                    break
            except Exception:
                pass

        if not chat_entity:
            from telethon.tl.types import PeerChannel, PeerChat, PeerUser
            for variant in chat_variants:
                try:
                    str_id = str(variant).replace('-100', '').replace('-', '')
                    num_id = int(str_id)
                    for peer_cls in (PeerChannel, PeerChat, PeerUser):
                        try:
                            chat_entity = await client.get_entity(peer_cls(num_id))
                            if chat_entity:
                                break
                        except Exception:
                            pass
                    if chat_entity:
                        break
                except Exception:
                    pass

        if not chat_entity:
            try:
                await client.get_dialogs(limit=100)
                for variant in chat_variants:
                    try:
                        chat_entity = await client.get_entity(variant)
                        if chat_entity:
                            break
                    except Exception:
                        pass
            except Exception:
                pass

        if not chat_entity:
            try:
                chat_entity = await client.get_entity(chat_id)
            except Exception as e_res:
                cat, reason = helpers.classify_telegram_entity_error(e_res)
                status_to_set = cat or 'inaccessible'
                cursor.execute("""
                    UPDATE tbl_chats 
                    SET backup_status = ?, last_halt_reason = ?, last_halt_date = ? 
                    WHERE chat_id = ?
                """, (status_to_set, reason, datetime.now().isoformat(), chat_id))
                conn.commit()
                err_text = f"Chat {status_to_set} in Telegram: {reason}"
                if progress_callback:
                    progress_callback(chat_id, f"Chat {status_to_set}: {reason}", 0, total_items, status="error", phase="catchup")
                return err_text

        cursor.execute(f"SELECT safe_folder_name FROM tbl_chats WHERE chat_id IN ({placeholders})", chat_variants)
        row = cursor.fetchone()
        safe_folder = row['safe_folder_name'] if row and row['safe_folder_name'] else f"Chat_{chat_id}"
        chat_media_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, safe_folder)
        ensure_chat_media_subdirs(chat_media_dir)

        # 5. Parallel download worker pool setup
        n_workers = max(1, getattr(core, 'DOWNLOAD_WORKERS', 3))
        sem = asyncio.Semaphore(n_workers)
        flood_event = asyncio.Event()
        flood_event.set()
        db_lock = asyncio.Lock()

        downloaded_count = 0
        processed_count = 0

        async def _download_catchup_item(msg):
            nonlocal downloaded_count, processed_count
            async with sem:
                if getattr(core, 'GLOBAL_ABORT', False):
                    return
                await flood_event.wait()

                if not msg or not getattr(msg, 'media', None):
                    processed_count += 1
                    return

                db_meta = row_meta_map.get(msg.id) or {}
                unique_fn = helpers.build_canonical_media_filename(msg, db_meta.get('media_original_name'))

                try:
                    file_size = 0
                    if getattr(msg, 'document', None):
                        file_size = getattr(msg.document, 'size', 0)
                    elif getattr(msg, 'photo', None) and getattr(msg.photo, 'sizes', None):
                        last_sz = msg.photo.sizes[-1]
                        file_size = getattr(last_sz, 'size', getattr(last_sz, 'file_size', 0))

                    # Correct priority order: check video_note BEFORE video
                    m_type = 'photos' if getattr(msg, 'photo', None) else 'documents'
                    if getattr(msg, 'video_note', None):
                        m_type = 'video_notes'
                    elif getattr(msg, 'voice', None) or getattr(msg, 'audio', None):
                        m_type = 'voice'
                    elif getattr(msg, 'video', None) or getattr(msg, 'gif', None):
                        m_type = 'videos'

                    if not bypass_limits:
                        s_id = getattr(msg, 'sender_id', None)
                        v_id = getattr(msg, 'via_bot_id', None)
                        f_id = get_message_forward_sender_id(msg)
                        r_key = (
                            str(s_id) if s_id and str(s_id) in user_rules
                            else (str(v_id) if v_id and str(v_id) in user_rules
                            else (str(f_id) if f_id and str(f_id) in user_rules else None))
                        )
                        if r_key:
                            u_rule = user_rules[r_key]
                            r_mode = u_rule.get('mode', 'exclude_all')
                            if r_mode == 'exclude_all':
                                processed_count += 1
                                return
                            elif r_mode == 'custom':
                                u_cfg = u_rule.get(m_type) or {}
                                if not u_cfg.get('enabled', True):
                                    processed_count += 1
                                    return
                                if u_cfg.get('max_mb', 0) > 0 and file_size > (u_cfg.get('max_mb') * 1024 * 1024):
                                    processed_count += 1
                                    return

                        cfg = settings.get(m_type) or {}
                        if not cfg.get('enabled', True):
                            processed_count += 1
                            return
                        max_mb = cfg.get('max_mb', 0)
                        if max_mb and max_mb > 0 and file_size > (max_mb * 1024 * 1024):
                            if file_size > 0:
                                async with db_lock:
                                    cursor.execute(f"""
                                        UPDATE tbl_messages
                                        SET file_size = ?, media_skipped = ?
                                        WHERE chat_id IN ({placeholders}) AND message_id = ?
                                    """, (file_size, f"Больше {max_mb} МБ", *chat_variants, msg.id))
                            processed_count += 1
                            if progress_callback:
                                progress_callback(
                                    chat_id,
                                    f"Skipped oversized {unique_fn} ({processed_count}/{total_items})...",
                                    processed_count, total_items, status="running", phase="catchup"
                                )
                            return

                    is_photo = bool(getattr(msg, 'photo', None))
                    mime_type = getattr(getattr(msg, 'file', None), 'mime_type', None)
                    attrs = getattr(getattr(msg, 'document', None), 'attributes', None)

                    target_dir = get_media_target_dir(
                        chat_media_dir,
                        mime_type=mime_type,
                        is_photo=is_photo,
                        attributes=attrs,
                        filename=unique_fn,
                        is_voice=(m_type == 'voice'),
                        is_round_video=(m_type == 'video_notes')
                    )
                    target_file = os.path.join(target_dir, unique_fn)
                    legacy_file = os.path.join(chat_media_dir, unique_fn)

                    is_stk = (getattr(msg, 'document', None) and any('Sticker' in getattr(a, '__class__', type(a)).__name__ for a in (msg.document.attributes or [])))
                    actual_target = os.path.join(helpers.get_shared_stickers_dir(), unique_fn) if is_stk else target_file
                    os.makedirs(os.path.dirname(actual_target), exist_ok=True)

                    last_dl_report = 0.0

                    def on_dl_chunk(current_bytes, total_bytes):
                        if getattr(core, 'GLOBAL_ABORT', False):
                            raise asyncio.CancelledError("Media download aborted by user")
                        nonlocal last_dl_report
                        now = time.time()
                        if now - last_dl_report > 0.4 or (total_bytes and current_bytes >= total_bytes):
                            last_dl_report = now
                            c_mb = current_bytes / (1024 * 1024)
                            t_mb = (total_bytes / (1024 * 1024)) if total_bytes else 0
                            pct = int((current_bytes / total_bytes) * 100) if total_bytes else 0
                            if progress_callback:
                                progress_callback(
                                    chat_id,
                                    f"Downloading {unique_fn} ({c_mb:.1f}/{t_mb:.1f} MB, {pct}%)...",
                                    processed_count,
                                    total_items,
                                    status="running",
                                    last_item=unique_fn,
                                    phase="catchup",
                                    bytes_current=current_bytes,
                                    bytes_total=total_bytes
                                )

                    if os.path.exists(actual_target) and os.path.getsize(actual_target) > 0:
                        dl_path = actual_target
                    elif os.path.exists(legacy_file) and os.path.getsize(legacy_file) > 0:
                        dl_path = legacy_file
                    else:
                        async with db_lock:
                            existing_src = helpers.find_existing_local_media(cursor, unique_fn, file_size)
                        if existing_src and helpers.try_create_hardlink(existing_src, actual_target):
                            dl_path = actual_target
                        else:
                            try:
                                dl_path = await helpers.download_media_robust(
                                    client, msg, actual_target,
                                    chat_entity=chat_entity,
                                    progress_callback=on_dl_chunk
                                )
                            except FloodWaitError as fw:
                                flood_event.clear()
                                wait_s = fw.seconds + 5
                                if progress_callback:
                                    progress_callback(chat_id, f"FloodWait {wait_s}s — pausing workers...", processed_count, total_items, status="running", phase="catchup")
                                await asyncio.sleep(wait_s)
                                flood_event.set()
                                dl_path = await helpers.download_media_robust(
                                    client, msg, actual_target,
                                    chat_entity=chat_entity,
                                    progress_callback=on_dl_chunk
                                )

                    if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
                        return
                    processed_count += 1
                    if dl_path and os.path.exists(dl_path):
                        rel_path = os.path.relpath(dl_path, core.ABS_MEDIA_BACKUP_DIR).replace('\\\\', '/')
                        if not file_size:
                            try:
                                file_size = os.path.getsize(dl_path)
                            except Exception:
                                pass
                        async with db_lock:
                            cursor.execute(f"""
                                UPDATE tbl_messages
                                SET media_local_path = ?, media_original_name = COALESCE(media_original_name, ?), media_skipped = NULL, file_size = ?
                                WHERE chat_id IN ({placeholders}) AND message_id = ?
                            """, (rel_path, unique_fn, file_size, *chat_variants, msg.id))
                            conn.commit()
                        downloaded_count += 1
                        if progress_callback:
                            progress_callback(
                                chat_id,
                                f"Downloaded {downloaded_count}/{total_items}: {unique_fn}",
                                processed_count,
                                total_items,
                                status="running",
                                last_item=unique_fn,
                                phase="catchup",
                                downloaded_id=msg.id
                            )
                    else:
                        clean_reason = "Unavailable on Telegram (purged or expired)"
                        async with db_lock:
                            cursor.execute(f"""
                                UPDATE tbl_messages
                                SET media_skipped = ?
                                WHERE chat_id IN ({placeholders}) AND message_id = ?
                            """, (clean_reason, *chat_variants, msg.id))
                            conn.commit()
                        if progress_callback:
                            progress_callback(
                                chat_id,
                                f"Unavailable: {unique_fn} ({clean_reason})",
                                processed_count,
                                total_items,
                                status="running",
                                last_item=unique_fn,
                                phase="catchup",
                                failed_id=msg.id,
                                error_text=clean_reason
                            )
                except asyncio.CancelledError:
                    return
                except Exception as dl_err:
                    if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
                        return
                    processed_count += 1
                    code, clean_label, is_perm = helpers.classify_media_download_error(dl_err)
                    err_msg = f"Unavailable on Telegram ({clean_label})" if is_perm else f"Failed: {clean_label}"
                    async with db_lock:
                        try:
                            cursor.execute(f"""
                                UPDATE tbl_messages
                                SET media_skipped = ?
                                WHERE chat_id IN ({placeholders}) AND message_id = ?
                            """, (err_msg, *chat_variants, msg.id))
                            conn.commit()
                        except Exception:
                            pass
                    if progress_callback:
                        progress_callback(
                            chat_id,
                            f"Error downloading {unique_fn}: {clean_label}",
                            processed_count,
                            total_items,
                            status="running",
                            last_item=unique_fn,
                            phase="catchup",
                            failed_id=msg.id,
                            error_text=clean_label
                        )

        # 6. Process in 100-message chunks with parallel workers inside each chunk
        for i in range(0, len(missing_msg_ids), 100):
            if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
                break
            batch_ids = missing_msg_ids[i:i + 100]
            try:
                tg_msgs = await client.get_messages(chat_entity, ids=batch_ids)
            except Exception as e:
                logging.debug(f"[download_missing_chat_media] get_messages error: {e}")
                continue

            valid_msgs = [m for m in (tg_msgs or []) if m and getattr(m, 'media', None)]
            skipped_in_batch = len(batch_ids) - len(valid_msgs)
            if skipped_in_batch > 0:
                processed_count += skipped_in_batch

            if valid_msgs:
                tasks = [asyncio.create_task(_download_catchup_item(m)) for m in valid_msgs]
                while tasks and not all(t.done() for t in tasks):
                    if getattr(core, 'GLOBAL_ABORT', False):
                        for t in tasks:
                            if not t.done():
                                t.cancel()
                        break
                    await asyncio.sleep(0.15)
                await asyncio.gather(*tasks, return_exceptions=True)
                async with db_lock:
                    conn.commit()

            if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
                break

        if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
            if progress_callback:
                progress_callback(chat_id, "Media catch-up stopped by user.", processed_count, total_items, status="error", phase="catchup")
            return "Stopped by user."

        if progress_callback:
            progress_callback(chat_id, f"Media catchup complete: {downloaded_count} files downloaded.", total_items, total_items, status="done", last_item=None, phase="catchup")
        return f"Downloaded {downloaded_count} files."
    finally:
        conn.close()
        if client_was_created and client:
            await client.disconnect()
            await asyncio.sleep(0.05)


async def async_resume_chat_download(chat_id, strategy, settings, total_messages, progress_callback=None):
    """
    Dual-Strategy Resume Engine:
    - latest_first: (1) sync latest to server_top_id -> (2) backfill older history to day 1
    - older_first:  (1) backfill older history to day 1 -> (2) sync latest to server_top_id
    """
    core.GLOBAL_ABORT = False
    core.IS_BULK_ARCHIVING = True

    def notify(text, processed=None, total=None, status="running", last_item=None, phase=None):
        if progress_callback:
            progress_callback(chat_id, text, processed, total, status, last_item, phase)

    conn = get_db_conn()
    cur = conn.cursor()
    bounds = db.get_chat_sync_bounds(cur, chat_id)
    cached_row = cur.execute("SELECT COUNT(*) FROM tbl_messages WHERE chat_id = ?", (chat_id,)).fetchone()
    cached_count = cached_row[0] if cached_row else 0
    conn.close()
    remaining_messages = max(0, total_messages - cached_count)

    mig_id = None
    if strategy == 'latest_first':
        notify("Resume Stage 1/3: Downloading latest remaining messages...", 0, remaining_messages, phase='resume')
        res1, mig = await async_run_backup(
            chat_id, settings, total_messages, progress_callback,
            resume_stage='latest', resume_boundary_id=bounds['max_cached_id']
        )
        if mig: mig_id = mig
        if core.GLOBAL_ABORT:
            notify("Resume stopped by user.", 0, remaining_messages, status="error")
            return "Resume halted by user", mig_id

        # Refresh bounds for stage 2
        conn = get_db_conn()
        cur = conn.cursor()
        bounds = db.get_chat_sync_bounds(cur, chat_id)
        cached_row = cur.execute("SELECT COUNT(*) FROM tbl_messages WHERE chat_id = ?", (chat_id,)).fetchone()
        cached_count = cached_row[0] if cached_row else 0
        conn.close()
        remaining_messages = max(0, total_messages - cached_count)

        notify("Resume Stage 2/3: Backfilling older remaining messages...", 0, remaining_messages, phase='resume')
        res2, mig2 = await async_run_backup(
            chat_id, settings, total_messages, progress_callback,
            resume_stage='older', resume_boundary_id=bounds['min_cached_id']
        )
        if mig2: mig_id = mig2
        if core.GLOBAL_ABORT:
            notify("Resume stopped by user.", 0, remaining_messages, status="error")
            return "Resume halted by user", mig_id

    else: # older_first
        notify("Resume Stage 1/3: Completing older remaining messages...", 0, remaining_messages, phase='resume')
        res1, mig = await async_run_backup(
            chat_id, settings, total_messages, progress_callback,
            resume_stage='older', resume_boundary_id=bounds['min_cached_id']
        )
        if mig: mig_id = mig
        if core.GLOBAL_ABORT:
            notify("Resume stopped by user.", 0, remaining_messages, status="error")
            return "Resume halted by user", mig_id

        conn = get_db_conn()
        cur = conn.cursor()
        bounds = db.get_chat_sync_bounds(cur, chat_id)
        cached_row = cur.execute("SELECT COUNT(*) FROM tbl_messages WHERE chat_id = ?", (chat_id,)).fetchone()
        cached_count = cached_row[0] if cached_row else 0
        conn.close()
        remaining_messages = max(0, total_messages - cached_count)

        notify("Resume Stage 2/3: Catching up latest remaining messages...", 0, remaining_messages, phase='resume')
        res2, mig2 = await async_run_backup(
            chat_id, settings, total_messages, progress_callback,
            resume_stage='latest', resume_boundary_id=bounds['max_cached_id']
        )
        if mig2: mig_id = mig2
        if core.GLOBAL_ABORT:
            notify("Resume stopped by user.", 0, remaining_messages, status="error")
            return "Resume halted by user", mig_id

    # Stage 3: Check and catch-up missing media for cached messages
    if not core.GLOBAL_ABORT:
        conn = get_db_conn()
        cur = conn.cursor()
        chat_variants = list(get_chat_id_variants(chat_id))
        ph = ','.join('?' for _ in chat_variants)
        cur.execute(f"""
            SELECT COUNT(*) FROM tbl_messages
            WHERE chat_id IN ({ph}) AND media_local_path IS NULL AND (media_type IS NOT NULL OR media_skipped IS NOT NULL)
        """, chat_variants)
        row_c = cur.fetchone()
        missing_count = row_c[0] if row_c else 0
        conn.close()

        if missing_count > 0:
            notify(f"Resume Stage 3/3: Catching up {missing_count} missing media files...", 0, missing_count, phase="catchup")
            try:
                await async_download_missing_chat_media(chat_id, settings, progress_callback)
            except Exception as e_m:
                logging.warning(f"[Resume] Media catch-up warning: {e_m}")

    if core.GLOBAL_ABORT:
        notify("Resume stopped by user.", 0, total_messages, status="error")
        return "Resume halted by user", mig_id

    conn = get_db_conn()
    cur = conn.cursor()
    db.recalculate_chat_bounds(cur, chat_id, backup_status='complete')
    cur.execute("UPDATE tbl_chats SET has_older_gap = 0, has_newer_gap = 0, backup_status = 'complete' WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

    notify("Resume complete!", total_messages, total_messages, status="done")
    return f"Resume complete ({res1} | {res2})", mig_id



async def async_reconcile_chat_gaps(chat_id, settings=None, progress_callback=None, client=None):
    """
    True O(1) Hierarchical Range (HOR) gap reconciliation using Telegram MTProto add_offset.
    Checks chat completeness in 1 query, skips 500-message blocks in 1 query via add_offset,
    and performs targeted bulk fetches for missing messages.
    """
    def notify(text, processed=None, total=None, status="running", last_item=None, phase="reconcile"):
        if progress_callback:
            progress_callback(chat_id, text, processed=processed, total=total, status=status, last_item=last_item, phase=phase)

    client_was_created = False
    curr_loop = None
    try:
        curr_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    if client and client.is_connected():
        pass
    elif getattr(core, 'LIVE_CLIENT', None) and core.LIVE_CLIENT.is_connected() and getattr(core.LIVE_CLIENT, 'loop', None) == curr_loop:
        client = core.LIVE_CLIENT
    else:
        client = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
        client_was_created = True
        await client.connect()

    conn = db.get_db_conn()
    cursor = conn.cursor()

    try:
        notify("Connecting to Telegram...", status="running")

        chat_variants = helpers.get_chat_id_variants(chat_id)
        chat_entity = None
        for variant in chat_variants:
            try:
                chat_entity = await client.get_entity(variant)
                if chat_entity:
                    break
            except Exception:
                pass

        if not chat_entity:
            from telethon.tl.types import PeerChannel, PeerChat, PeerUser
            for variant in chat_variants:
                try:
                    str_id = str(variant).replace('-100', '').replace('-', '')
                    num_id = int(str_id)
                    for peer_cls in (PeerChannel, PeerChat, PeerUser):
                        try:
                            chat_entity = await client.get_entity(peer_cls(num_id))
                            if chat_entity:
                                break
                        except Exception:
                            pass
                    if chat_entity:
                        break
                except Exception:
                    pass

        if not chat_entity:
            try:
                chat_entity = await client.get_entity(chat_id)
            except Exception as e_res:
                notify(f"Could not access chat {chat_id}: {e_res}", status="error")
                return {"status": "error", "message": str(e_res)}

        # 1. Fast Global Check (1 request)
        cursor.execute("SELECT COUNT(*) FROM tbl_messages WHERE chat_id = ? AND is_deleted = 0", (chat_id,))
        local_count = cursor.fetchone()[0]

        server_history = await client.get_messages(chat_entity, limit=0)
        server_total = getattr(server_history, 'total', 0) or 0

        bounds = db.get_chat_sync_bounds(cursor, chat_id)
        if local_count >= server_total and server_total > 0 and not bounds['has_older_gap'] and not bounds['has_newer_gap']:
            notify(f"100% in sync! Local: {local_count}, Server: {server_total}. Zero missing messages.", processed=local_count, total=server_total, status="done")
            return {"status": "ok", "missing_found": 0, "fast_exit": True}

        notify(f"Analyzing message ranges (Local: {local_count}, Server: {server_total})...", processed=local_count, total=server_total, status="running")

        cursor.execute("SELECT MAX(message_id) FROM tbl_messages WHERE chat_id = ? AND is_deleted = 0", (chat_id,))
        max_local_row = cursor.fetchone()
        max_local_id = (max_local_row[0] or 0) if max_local_row else 0

        bucket_size = 500
        bucket_max_id = max(max_local_id, server_total)
        total_missing_fetched = 0
        aborted = False

        curr_top = bucket_max_id
        while curr_top >= 1:
            if getattr(core, 'GLOBAL_ABORT', False):
                notify("Stopped by user", status="error")
                aborted = True
                break

            cursor.execute("""
                SELECT message_id FROM tbl_messages
                WHERE chat_id = ? AND is_deleted = 0 AND message_id <= ?
                ORDER BY message_id DESC
                LIMIT 1 OFFSET ?
            """, (chat_id, curr_top, bucket_size - 1))
            local_500th_row = cursor.fetchone()
            local_500th_id = local_500th_row[0] if local_500th_row else None

            remote_probe = await client.get_messages(chat_entity, limit=1, offset_id=curr_top + 1, add_offset=bucket_size - 1)
            remote_probe_msg = remote_probe[0] if remote_probe else None

            if remote_probe_msg and local_500th_id and remote_probe_msg.id == local_500th_id:
                curr_top = local_500th_id - 1
                notify(f"Verified block down to #{local_500th_id} in 1 query (in sync)", status="running")
                continue

            min_bound = (local_500th_id - 1) if local_500th_id else 0
            remote_ids = []
            try:
                async for m in client.iter_messages(chat_entity, limit=bucket_size, offset_id=curr_top + 1, min_id=min_bound):
                    if m:
                        remote_ids.append(m.id)
            except Exception as e:
                logging.warning(f"[reconcile] iter_messages exception: {e}")

            if not remote_ids:
                if min_bound <= 0 or curr_top <= min_bound:
                    break
                curr_top = min_bound
                continue

            cursor.execute("""
                SELECT message_id FROM tbl_messages
                WHERE chat_id = ? AND is_deleted = 0 AND message_id BETWEEN ? AND ?
            """, (chat_id, min(remote_ids), max(remote_ids)))
            local_present = {r[0] for r in cursor.fetchall()}
            missing_ids = [mid for mid in remote_ids if mid not in local_present]

            if missing_ids:
                notify(f"Found {len(missing_ids)} missing messages in block #{min(remote_ids)}-#{max(remote_ids)}. Fetching...", status="running")
                for i in range(0, len(missing_ids), 100):
                    chunk = missing_ids[i:i + 100]
                    try:
                        msgs = await client.get_messages(chat_entity, ids=chunk)
                    except Exception:
                        msgs = []
                    batch_to_save = []
                    for m in msgs:
                        if not m:
                            continue
                        batch_to_save.append({
                            'id': m.id,
                            'message_id': m.id,
                            'date': m.date,
                            'text': m.raw_text or '',
                            'sender_id': getattr(m, 'sender_id', None),
                            'reply_to_msg_id': getattr(getattr(m, 'reply_to', None), 'reply_to_msg_id', None),
                            'edited_date': getattr(m, 'edit_date', None),
                            'group_id': getattr(m, 'grouped_id', None),
                            'entities': [e.to_dict() for e in m.entities] if getattr(m, 'entities', None) else None,
                            'views_count': getattr(m, 'views', None),
                            'forwards': getattr(m, 'forwards', 0) or 0,
                            'via_bot_id': getattr(m, 'via_bot_id', None),
                            'post_author_name': getattr(m, 'post_author', None),
                            'is_pinned': 1 if getattr(m, 'pinned', False) else 0,
                            'is_outgoing': 1 if getattr(m, 'out', False) else 0,
                            'is_post': 1 if getattr(m, 'post', False) else 0,
                            'file_size': getattr(getattr(m, 'file', None), 'size', 0) or 0,
                            'media_mime_type': getattr(getattr(m, 'file', None), 'mime_type', None),
                            'media_original_name': getattr(getattr(m, 'file', None), 'name', None),
                        })
                    if batch_to_save:
                        db.flush_messages_batch(conn, cursor, chat_id, batch_to_save)
                        conn.commit()
                        total_missing_fetched += len(batch_to_save)

            next_top = min(remote_ids) - 1
            if next_top >= curr_top:
                next_top = curr_top - 1
            curr_top = next_top

        if not aborted:
            cursor.execute("UPDATE tbl_chats SET has_older_gap = 0, backup_status = 'complete' WHERE chat_id = ?", (chat_id,))
        db.recalculate_chat_bounds(cursor, chat_id)
        conn.commit()

        notify(f"Reconciliation finished. Synchronized {total_missing_fetched} missing messages.", status="done")
        return {"status": "ok", "missing_fetched": total_missing_fetched}
    finally:
        conn.close()
        if client_was_created and client:
            await client.disconnect()
            await asyncio.sleep(0.05)
