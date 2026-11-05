#!/usr/bin/env python3
"""
core/db_messages.py — Message fetching, batch flushing, media asset querying, sender aggregation & chat sync bounds.
"""
import os
import re
import json
import sqlite3
import logging
from datetime import datetime, timezone
from .db_chats import get_chat_pipeline

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _get_db_conn(timeout=60.0):
    from . import db as _db
    return _db.get_db_conn(timeout=timeout)

def _delete_fts_messages(cursor, chat_id, message_ids=None):
    from . import db as _db
    return _db._delete_fts_messages(cursor, chat_id, message_ids=message_ids)

def fetch_message_from_db(cursor, message_id, chat_id):

    try:

        cursor.execute(

            "SELECT sender_id, text, media_original_name, media_skipped, poll_data_json, service_action_json, media_type, group_id FROM tbl_messages WHERE message_id = ? AND chat_id = ?",

            (message_id, chat_id)

        )

        row = cursor.fetchone()

        if row:

            return {

                'sender_id': row['sender_id'], 'text': row['text'], 

                'media_original_name': row['media_original_name'], 'media_skipped': row['media_skipped'], 

                'poll_data': json.loads(row['poll_data_json']) if row['poll_data_json'] else None,

                'service_action': json.loads(row['service_action_json']) if row['service_action_json'] else None,

                'media_type': row['media_type'] if 'media_type' in row.keys() else None,

                'group_id': row['group_id'] if 'group_id' in row.keys() else None

            }

    except sqlite3.Error: pass

    return None



def get_chat_media_assets(cursor, chat_id, verify_disk=False):

    """Returns list of media assets for a chat across its migration pipeline with computed file sizes."""

    try:

        pipeline = get_chat_pipeline(cursor, chat_id)

    except Exception:

        pipeline = [chat_id]



    all_ids = set()

    for pid in pipeline:

        all_ids.add(pid)

        try:

            from .helpers import get_chat_id_variants

            all_ids.update(get_chat_id_variants(pid))

        except Exception:

            pass

    if not all_ids:

        all_ids.add(chat_id)



    placeholders = ','.join('?' for _ in all_ids)

    cursor.execute(f"""

        SELECT message_id, chat_id, sender_id, via_bot_id, date, media_type, media_original_name,

               media_mime_type, media_local_path, thumbnail_local_path, media_skipped,

               COALESCE(

                   NULLIF(file_size, 0),

                   CAST(json_extract(media_other_json, '$.document.size') AS INTEGER),

                   CAST(json_extract(media_other_json, '$.size') AS INTEGER),

                   0

               ) AS size_bytes,

               COALESCE(

                   json_extract(forward_info_json, '$.from_id'),

                   json_extract(forward_info_json, '$.from_id_raw.user_id'),

                   json_extract(forward_info_json, '$.from_id_raw.channel_id')

               ) AS fwd_from_id

        FROM tbl_messages

        WHERE chat_id IN ({placeholders})

          AND (media_local_path IS NOT NULL 

               OR media_skipped IS NOT NULL 

               OR media_type IN ('photo', 'video', 'document', 'audio', 'voice', 'video_note', 'sticker', 'gif', 'paid_media', 'invoice', 'story', 'media'))

        ORDER BY message_id DESC

    """, list(all_ids))

    rows = cursor.fetchall()



    media_backup_root = os.path.join(PROJECT_ROOT, "media_backup")

    assets = []

    for r in rows:

        item = dict(r)

        size_bytes = item.get('size_bytes') or 0

        media_path = item.get('media_local_path')



        # Only touch disk if verify_disk=True or downloaded file has 0 recorded size in DB

        if media_path and (verify_disk or size_bytes == 0):

            full_path = os.path.join(media_backup_root, media_path)

            try:

                if os.path.exists(full_path):

                    disk_size = os.path.getsize(full_path)

                    if disk_size > 0:

                        size_bytes = disk_size

            except Exception:

                pass



        item['size_bytes'] = size_bytes

        item['file_size'] = size_bytes

        assets.append(item)



    return assets





def get_chat_senders(cursor, chat_id):

    """Returns list of distinct message senders/bots in chat with message counts, media counts, and excluded stats."""

    try:

        pipeline = get_chat_pipeline(cursor, chat_id)

    except Exception:

        pipeline = [chat_id]



    all_ids = set()

    for pid in pipeline:

        all_ids.add(pid)

        try:

            from .helpers import get_chat_id_variants

            all_ids.update(get_chat_id_variants(pid))

        except Exception:

            pass

    if not all_ids:

        all_ids.add(chat_id)



    placeholders = ','.join('?' for _ in all_ids)

    all_ids_list = list(all_ids)

    cursor.execute(f"""

        WITH raw_senders AS (

            SELECT 

                m.sender_id AS uid,

                0 AS is_via_bot,

                m.message_id,

                m.file_size,

                m.media_mime_type,

                m.media_local_path,

                m.media_skipped,

                m.media_type,

                NULL AS fwd_name

            FROM tbl_messages m

            WHERE m.chat_id IN ({placeholders}) AND m.sender_id IS NOT NULL



            UNION ALL



            SELECT 

                m.via_bot_id AS uid,

                1 AS is_via_bot,

                m.message_id,

                m.file_size,

                m.media_mime_type,

                m.media_local_path,

                m.media_skipped,

                m.media_type,

                NULL AS fwd_name

            FROM tbl_messages m

            WHERE m.chat_id IN ({placeholders}) AND m.via_bot_id IS NOT NULL



            UNION ALL



            SELECT 

                COALESCE(

                    json_extract(m.forward_info_json, '$.from_id'),

                    json_extract(m.forward_info_json, '$.from_id_raw.user_id'),

                    json_extract(m.forward_info_json, '$.from_id_raw.channel_id')

                ) AS uid,

                0 AS is_via_bot,

                m.message_id,

                m.file_size,

                m.media_mime_type,

                m.media_local_path,

                m.media_skipped,

                m.media_type,

                json_extract(m.forward_info_json, '$.from_name') AS fwd_name

            FROM tbl_messages m

            WHERE m.chat_id IN ({placeholders}) AND m.forward_info_json IS NOT NULL

              AND COALESCE(

                    json_extract(m.forward_info_json, '$.from_id'),

                    json_extract(m.forward_info_json, '$.from_id_raw.user_id'),

                    json_extract(m.forward_info_json, '$.from_id_raw.channel_id')

              ) IS NOT NULL



            UNION ALL



            SELECT 

                c.chat_id AS uid,

                CASE WHEN u.is_bot = 1 THEN 1 ELSE 0 END AS is_via_bot,

                NULL AS message_id,

                0 AS file_size,

                NULL AS media_mime_type,

                NULL AS media_local_path,

                NULL AS media_skipped,

                NULL AS media_type,

                NULL AS fwd_name

            FROM tbl_chats c

            LEFT JOIN tbl_users u ON c.chat_id = u.user_id

            WHERE c.chat_id IN ({placeholders}) AND c.chat_id > 0

        )

        SELECT 

            s.uid AS user_id,

            COALESCE(u.first_name, MAX(s.fwd_name), '') AS first_name,

            COALESCE(u.last_name, '') AS last_name,

            u.username,

            u.avatar_path,

            CASE WHEN MAX(s.is_via_bot) = 1 THEN 1 ELSE COALESCE(u.is_bot, 0) END AS is_bot,

            COUNT(s.message_id) AS total_messages,

            COUNT(CASE WHEN s.media_mime_type IS NOT NULL OR s.media_local_path IS NOT NULL OR s.media_skipped IS NOT NULL OR s.media_type IS NOT NULL THEN 1 END) AS media_messages,

            COALESCE(SUM(CASE WHEN s.media_mime_type IS NOT NULL OR s.media_local_path IS NOT NULL OR s.media_skipped IS NOT NULL OR s.media_type IS NOT NULL THEN s.file_size ELSE 0 END), 0) AS total_media_bytes,

            COALESCE(SUM(CASE WHEN s.media_skipped LIKE '%user_rule_excluded%' THEN s.file_size ELSE 0 END), 0) AS excluded_bytes,

            COALESCE(SUM(CASE WHEN s.media_local_path IS NOT NULL THEN s.file_size ELSE 0 END), 0) AS downloaded_bytes

        FROM raw_senders s

        LEFT JOIN tbl_users u ON s.uid = u.user_id

        GROUP BY s.uid

        ORDER BY is_bot DESC, media_messages DESC, total_messages DESC

    """, all_ids_list * 4)

    rows = cursor.fetchall()

    senders = []

    for r in rows:

        d = dict(r)

        d['sender_id'] = d['user_id']

        name_parts = [d.get('first_name', ''), d.get('last_name', '')]

        full_name = ' '.join(p for p in name_parts if p).strip()

        if not full_name:

            full_name = f"@{d['username']}" if d.get('username') else f"User {d['user_id']}"

        d['display_name'] = full_name

        d['name'] = full_name

        senders.append(d)

    return senders



def flush_messages_batch(conn, cursor, chat_id, messages_list, users_cache=None):

    """

    Атомарно сохраняет пачку сообщений в tbl_messages и tbl_messages_fts,

    а также обновляет tbl_users при наличии users_cache, сразу фиксируя транзакцию.

    """

    if not messages_list:

        return 0



    from .helpers import dt_converter

    

    messages_to_save = []

    fts_messages_to_save = []

    

    for msg in messages_list:

        m_id = msg.get('id') if 'id' in msg else msg.get('message_id')

        if m_id is None:

            continue

            

        date_val = msg.get('date')

        if date_val is not None:

            date_val = dt_converter(date_val)

            

        edited_date_val = msg.get('edited_date')

        if edited_date_val is not None:

            edited_date_val = dt_converter(edited_date_val)

            

        def _ser(field_name):

            val = msg.get(field_name)

            if val is None:

                val = msg.get(field_name + '_json')

            if val is None:

                return None

            if isinstance(val, str):

                return val

            try:

                return json.dumps(val, default=dt_converter)

            except Exception:

                return str(val)



        messages_to_save.append((

            m_id,

            chat_id,

            msg.get('sender_id'),

            date_val,

            msg.get('text'),

            msg.get('reply_to_msg_id'),

            msg.get('reply_sender_id'),

            msg.get('reply_snippet'),

            edited_date_val,

            msg.get('media_local_path'),

            msg.get('thumbnail_local_path'),

            msg.get('media_original_name'),

            msg.get('media_mime_type'),

            msg.get('media_skipped'),

            msg.get('group_id'),

            _ser('forward_info'),

            _ser('service_action'),

            _ser('poll_data'),

            _ser('entities'),

            msg.get('is_media_spoiler', 0),

            _ser('reactions'),

            _ser('media_other'),

            msg.get('views') if 'views' in msg else msg.get('views_count'),

            msg.get('via_bot_id'),

            msg.get('post_author') if 'post_author' in msg else msg.get('post_author_name'),

            _ser('reply_markup'),

            msg.get('is_pinned', 0),

            msg.get('is_outgoing', 0),

            msg.get('is_mentioned', 0),

            msg.get('is_post', 0),

            msg.get('invert_media', 0),

            msg.get('is_legacy', 0),

            _ser('replies'),

            _ser('restriction_reason'),

            msg.get('reply_to_top_id'),

            msg.get('is_forum_topic', 0),

            _ser('reply_to_peer_id'),

            msg.get('quote_text'),

            _ser('quote_entities'),

            msg.get('quote_offset'),

            _ser('reply_from'),

            _ser('reply_media'),

            msg.get('is_voice', 0),

            msg.get('is_round_video', 0),

            msg.get('video_cover_path'),

            msg.get('media_type'),

            _ser('saved_peer_id'),

            msg.get('via_business_bot_id'),

            msg.get('video_timestamp'),

            msg.get('from_scheduled', 0),

            msg.get('forwards', 0),

            _ser('edit_history'),

            msg.get('file_size', 0),

            msg.get('reply_to_story_id'),

            msg.get('duration', 0),

            msg.get('transcription_lang'),

            msg.get('waveform_data')

        ))

        

        text_val = msg.get('text')

        if text_val:

            fts_messages_to_save.append((m_id, chat_id, text_val))



    _msg_cols = """message_id, chat_id, sender_id, date, text, reply_to_msg_id, reply_sender_id, reply_snippet,

        edited_date, media_local_path, thumbnail_local_path, media_original_name, media_mime_type,

        media_skipped, group_id, forward_info_json, service_action_json, poll_data_json, entities_json,

        is_media_spoiler, reactions_json, media_other_json, views_count, via_bot_id, post_author_name,

        reply_markup_json, is_pinned, is_outgoing, is_mentioned, is_post, invert_media, is_legacy,

        replies_json, restriction_reason_json, reply_to_top_id, is_forum_topic, reply_to_peer_id_json,

        quote_text, quote_entities_json, quote_offset, reply_from_json, reply_media_json,

        is_voice, is_round_video, video_cover_path, media_type,

        saved_peer_id_json, via_business_bot_id, video_timestamp,

        from_scheduled, forwards, edit_history_json, file_size, reply_to_story_id,

        duration, transcription_lang, waveform_data"""

        

    cursor.executemany(f"INSERT OR REPLACE INTO tbl_messages ({_msg_cols}) VALUES ({','.join(['?']*57)})", messages_to_save)

    

    if fts_messages_to_save:
        fts_ids = [item[0] for item in fts_messages_to_save]
        _delete_fts_messages(cursor, chat_id, fts_ids)
        cursor.executemany("INSERT INTO tbl_messages_fts (message_id, chat_id, text) VALUES (?, ?, ?)", fts_messages_to_save)

        

    if users_cache:

        for u_id, u_info in users_cache.items():

            if not isinstance(u_info, dict):

                continue

            cursor.execute("""

                INSERT INTO tbl_users (

                    user_id, first_name, last_name, username, avatar_path, is_bot,

                    is_premium, is_restricted, verified, usernames_json, lang_code, is_scam, is_fake

                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(user_id) DO UPDATE SET

                    first_name=excluded.first_name,

                    last_name=excluded.last_name,

                    username=excluded.username,

                    avatar_path=COALESCE(excluded.avatar_path, tbl_users.avatar_path),

                    is_bot=excluded.is_bot,

                    is_premium=excluded.is_premium,

                    is_restricted=excluded.is_restricted,

                    verified=excluded.verified,

                    usernames_json=COALESCE(excluded.usernames_json, tbl_users.usernames_json),

                    lang_code=COALESCE(excluded.lang_code, tbl_users.lang_code),

                    is_scam=excluded.is_scam,

                    is_fake=excluded.is_fake

            """, (

                u_id,

                u_info.get('first_name'),

                u_info.get('last_name'),

                u_info.get('username'),

                u_info.get('avatar_path'),

                1 if u_info.get('is_bot') else 0,

                1 if u_info.get('is_premium') else 0,

                1 if u_info.get('is_restricted') else 0,

                1 if u_info.get('verified') else 0,

                json.dumps(u_info.get('usernames_json')) if isinstance(u_info.get('usernames_json'), list) else u_info.get('usernames_json'),

                u_info.get('lang_code'),

                1 if u_info.get('is_scam') else 0,

                1 if u_info.get('is_fake') else 0

            ))

            

    conn.commit()

    return len(messages_to_save)





def recalculate_chat_bounds(cursor, chat_id, server_top_id=None, backup_status=None, halt_reason=None):

    """

    Пересчитывает границы скачанных сообщений для чата:

    min_downloaded_id, max_downloaded_id, server_top_id, has_older_gap, has_newer_gap, backup_status.

    """

    try:

        chat_id = int(chat_id)

    except (ValueError, TypeError):

        pass



    cursor.execute("""

        SELECT 

            (SELECT MIN(message_id) FROM tbl_messages WHERE chat_id = ?) AS min_id,

            (SELECT MAX(message_id) FROM tbl_messages WHERE chat_id = ?) AS max_id

    """, (chat_id, chat_id))

    row = cursor.fetchone()

    min_id = (row['min_id'] or 0) if row else 0

    max_id = (row['max_id'] or 0) if row else 0



    cursor.execute("""

        SELECT min_downloaded_id, max_downloaded_id, server_top_id, 

               has_older_gap, has_newer_gap, backup_status, last_backup_offset_id

        FROM tbl_chats WHERE chat_id = ?

    """, (chat_id,))

    chat_row = cursor.fetchone()



    cur_server_top = 0

    cur_status = 'complete'

    if chat_row:

        cur_server_top = chat_row['server_top_id'] or 0

        cur_status = chat_row['backup_status'] or 'complete'

        

    if server_top_id is not None and server_top_id > 0:

        cur_server_top = max(cur_server_top, server_top_id)

        

    if backup_status is not None:

        cur_status = backup_status

        

    if chat_row and chat_row['last_backup_offset_id'] and chat_row['last_backup_offset_id'] > max_id:

        max_id = chat_row['last_backup_offset_id']



    # Older gap logic: if min_id > 1 and (cur_status in ('halted', 'interrupted') or has_older_gap was explicitly set)

    has_older = 0

    if min_id > 1:

        if cur_status in ('halted', 'interrupted'):

            has_older = 1

        elif chat_row and chat_row['has_older_gap']:

            has_older = 1



    # Newer gap logic: if server_top_id > max_id

    has_newer = 0

    if cur_server_top > 0 and cur_server_top > max_id:

        has_newer = 1



    now_iso = datetime.utcnow().isoformat()

    update_fields = [

        "min_downloaded_id = ?",

        "max_downloaded_id = ?",

        "server_top_id = ?",

        "has_older_gap = ?",

        "has_newer_gap = ?",

        "backup_status = ?"

    ]

    params = [min_id, max_id, cur_server_top, has_older, has_newer, cur_status]

    

    if halt_reason is not None:

        update_fields.append("last_halt_reason = ?")

        update_fields.append("last_halt_date = ?")

        params.extend([halt_reason, now_iso])



    params.append(chat_id)

    cursor.execute(f"UPDATE tbl_chats SET {', '.join(update_fields)} WHERE chat_id = ?", params)

    

    return {

        "min_downloaded_id": min_id,

        "max_downloaded_id": max_id,

        "server_top_id": cur_server_top,

        "has_older_gap": bool(has_older),

        "has_newer_gap": bool(has_newer),

        "backup_status": cur_status

    }





def backfill_all_chat_bounds(cursor):

    """

    Однократный безопасный бэкфилл границ для всех существующих чатов.

    """

    cursor.execute("SELECT chat_id, min_downloaded_id, max_downloaded_id, backup_status FROM tbl_chats")

    chats = cursor.fetchall()

    for c in chats:

        cid = c['chat_id']

        cursor.execute("SELECT MIN(message_id) AS min_id, MAX(message_id) AS max_id, COUNT(*) AS cnt FROM tbl_messages WHERE chat_id = ?", (cid,))

        m = cursor.fetchone()

        if m and m['cnt'] > 0:

            min_id = m['min_id'] or 0

            max_id = m['max_id'] or 0

            if not c['min_downloaded_id'] or not c['max_downloaded_id']:

                cursor.execute("""

                    UPDATE tbl_chats 

                    SET min_downloaded_id = ?, 

                        max_downloaded_id = MAX(COALESCE(max_downloaded_id, 0), ?),

                        backup_status = COALESCE(backup_status, 'complete')

                    WHERE chat_id = ?

                """, (min_id, max_id, cid))





def get_chat_sync_bounds(cursor, chat_id):

    """Возвращает метаданные границ синхронизации чата."""

    try:

        chat_id = int(chat_id)

    except (ValueError, TypeError):

        pass

    pipeline = get_chat_pipeline(cursor, chat_id)

    ph = ','.join('?' for _ in pipeline)

    

    cursor.execute(f"""

        SELECT 

            MIN(min_downloaded_id) as min_id,

            MAX(max_downloaded_id) as max_id,

            MAX(server_top_id) as top_id,

            MAX(has_older_gap) as older_gap,

            MAX(has_newer_gap) as newer_gap,

            backup_status,

            last_halt_reason,

            last_halt_date

        FROM tbl_chats 

        WHERE chat_id IN ({ph})

    """, pipeline)

    row = cursor.fetchone()

    

    cursor.execute(f"""

        SELECT MIN(date) as oldest_date, MAX(date) as newest_date,

               MIN(message_id) as db_min_id, MAX(message_id) as db_max_id

        FROM tbl_messages 

        WHERE chat_id IN ({ph})

    """, pipeline)

    m_row = cursor.fetchone()

    

    db_min = (m_row['db_min_id'] or 0) if m_row else 0

    db_max = (m_row['db_max_id'] or 0) if m_row else 0

    oldest_date = m_row['oldest_date'] if m_row else None

    newest_date = m_row['newest_date'] if m_row else None

    

    min_cached = row['min_id'] if (row and row['min_id']) else db_min

    max_cached = row['max_id'] if (row and row['max_id']) else db_max

    server_top = (row['top_id'] or 0) if row else 0

    has_older = bool(row['older_gap']) if row else False

    has_newer = bool(row['newer_gap']) if row else False

    status = (row['backup_status'] or 'complete') if row else 'complete'

    halt_reason = row['last_halt_reason'] if row else None

    halt_date = row['last_halt_date'] if row else None



    if min_cached > 1 and status in ('halted', 'interrupted'):

        has_older = True

    if server_top > max_cached:

        has_newer = True



    return {

        "min_cached_id": min_cached,

        "max_cached_id": max_cached,

        "server_top_id": server_top,

        "has_older_gap": has_older,

        "has_newer_gap": has_newer,

        "backup_status": status,

        "last_halt_reason": halt_reason,

        "last_halt_date": halt_date,

        "oldest_cached_date": oldest_date,

        "newest_cached_date": newest_date

    }


