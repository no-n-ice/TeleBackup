#!/usr/bin/env python3
"""
core/db_chats.py — Chat, User, Settings, Custom Emoji, Bookmarks, Admin Log, Stars & Sponsored Message DB helpers.
"""
import os
import re
import json
import sqlite3
import logging
from datetime import datetime, timezone

def _get_project_root():
    from . import db as _db
    return _db.PROJECT_ROOT

def _get_db_conn(timeout=60.0):
    from . import db as _db
    return _db.get_db_conn(timeout=timeout)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def link_migrated_chats(conn):

    """Восстанавливает связи миграции чатов на основе сервисных сообщений."""

    cursor = conn.cursor()

    try:

        count = 0

        cursor.execute("SELECT chat_id, service_action_json FROM tbl_messages WHERE service_action_json IS NOT NULL AND service_action_json LIKE '%MessageActionChatMigrateTo%'")

        for row in cursor.fetchall():

            try:

                action = json.loads(row['service_action_json'])

                if action.get('_') == 'MessageActionChatMigrateTo' and action.get('channel_id'):

                    new_id = int(f"-100{action['channel_id']}")

                    cursor.execute("UPDATE tbl_chats SET migrated_to_chat_id = ? WHERE chat_id = ?", (new_id, row['chat_id']))

                    count += 1

            except Exception as e:

                logging.error('Exception caught', exc_info=True)

            

        cursor.execute("SELECT chat_id, service_action_json FROM tbl_messages WHERE service_action_json IS NOT NULL AND service_action_json LIKE '%MessageActionChannelMigrateFrom%'")

        for row in cursor.fetchall():

            try:

                action = json.loads(row['service_action_json'])

                if action.get('_') == 'MessageActionChannelMigrateFrom' and action.get('chat_id'):

                    old_id = action.get('chat_id')

                    if old_id > 0: old_id = -old_id 

                    cursor.execute("UPDATE tbl_chats SET migrated_to_chat_id = ? WHERE chat_id = ?", (row['chat_id'], old_id))

                    count += 1

            except Exception as e:

                logging.error('Exception caught', exc_info=True)

        conn.commit()

    except Exception as e:

        print(f"[DB] Error linking migrated chats: {e}")



def get_chat_pipeline(cursor, chat_id):

    """Возвращает список всех связанных ID чатов (предков и потомков)."""

    pipeline_ids = set([chat_id])

    curr_id = chat_id

    while True:

        cursor.execute("SELECT chat_id FROM tbl_chats WHERE migrated_to_chat_id = ?", (curr_id,))

        row = cursor.fetchone()

        if row:

            curr_id = row[0]

            pipeline_ids.add(curr_id)

        else: break



    curr_id = chat_id

    while True:

        cursor.execute("SELECT migrated_to_chat_id FROM tbl_chats WHERE chat_id = ?", (curr_id,))

        row = cursor.fetchone()

        if row and row[0]:

            curr_id = row[0]

            pipeline_ids.add(curr_id)

        else: break

    return list(pipeline_ids)



def load_all_users_from_db(cursor):

    """Загружает всех пользователей в словарь-кэш."""

    users_cache = {}

    try:

        cursor.execute("SELECT user_id, first_name, last_name, username, avatar_path, is_bot, is_premium, is_restricted, verified, usernames_json, lang_code, is_scam, is_fake FROM tbl_users")

        for row in cursor.fetchall():

            users_cache[row['user_id']] = {

                "first_name": row['first_name'], "last_name": row['last_name'],

                "username": row['username'], "avatar_path": row['avatar_path'],

                "is_bot": row['is_bot'], "is_premium": row['is_premium'],

                "is_restricted": row['is_restricted'],

                "verified": row['verified'],

                "usernames_json": row['usernames_json'],

                "lang_code": row['lang_code'],

                "is_scam": row['is_scam'] if 'is_scam' in row.keys() else 0,

                "is_fake": row['is_fake'] if 'is_fake' in row.keys() else 0

            }

    except sqlite3.Error: pass

    return users_cache



def get_offset_id(cursor, chat_id):

    offset = 0

    try:

        cursor.execute("SELECT last_backup_offset_id FROM tbl_chats WHERE chat_id = ?", (chat_id,))

        res = cursor.fetchone()

        if res and res[0]: offset = res[0]

    except sqlite3.Error: pass

    

    if offset == 0:

        try:

            cursor.execute("SELECT MAX(message_id) FROM tbl_messages WHERE chat_id = ?", (chat_id,))

            res = cursor.fetchone()

            if res and res[0]: offset = res[0]

        except sqlite3.Error: pass

    return offset



def get_chat_settings(cursor, chat_id):

    try:

        chat_id = int(chat_id)

    except (ValueError, TypeError):

        pass

    try:
        cursor.execute("SELECT archive_settings_json FROM tbl_chats WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row['archive_settings_json']:
            try:
                return json.loads(row['archive_settings_json'])
            except Exception:
                return None
    except Exception:
        pass

    try:

        pipeline = get_chat_pipeline(cursor, chat_id)

        for pid in pipeline:

            if pid != chat_id:

                cursor.execute("SELECT archive_settings_json FROM tbl_chats WHERE chat_id = ?", (pid,))

                p_row = cursor.fetchone()

                if p_row and p_row['archive_settings_json']:

                    try:

                        return json.loads(p_row['archive_settings_json'])

                    except Exception:

                        pass

    except Exception:

        pass

    return None



def save_chat_settings(cursor, chat_id, settings_dict):

    try:

        chat_id = int(chat_id)

    except (ValueError, TypeError):

        pass

    if settings_dict is None:

        cursor.execute("""

            UPDATE tbl_chats SET 

                archive_settings_json = NULL,

                archive_range_mode = 'all',

                archive_range_start = NULL,

                archive_range_end = NULL,

                archive_range_days = NULL,

                archive_direction = 'backward'

            WHERE chat_id = ?

        """, (chat_id,))

    else:

        settings_json = json.dumps(settings_dict)

        r_mode = settings_dict.get('archive_range_mode') or 'all'

        r_start = settings_dict.get('archive_range_start')

        r_end = settings_dict.get('archive_range_end')

        r_days = settings_dict.get('archive_range_days')

        r_dir = settings_dict.get('archive_direction') or 'backward'



        cursor.execute("""

            INSERT INTO tbl_chats (

                chat_id, archive_settings_json, archive_range_mode,

                archive_range_start, archive_range_end, archive_range_days, archive_direction

            ) VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(chat_id) DO UPDATE SET

                archive_settings_json = excluded.archive_settings_json,

                archive_range_mode = excluded.archive_range_mode,

                archive_range_start = excluded.archive_range_start,

                archive_range_end = excluded.archive_range_end,

                archive_range_days = excluded.archive_range_days,

                archive_direction = excluded.archive_direction

        """, (chat_id, settings_json, r_mode, r_start, r_end, r_days, r_dir))



def get_global_settings(cursor=None):

    close_conn = False

    if cursor is None:

        conn = _get_db_conn()

        cursor = conn.cursor()

        close_conn = True

    try:

        cursor.execute("SELECT value FROM tbl_global_settings WHERE key = 'archive_settings'")

        row = cursor.fetchone()

        if row and row['value']:

            try:

                return json.loads(row['value'])

            except Exception:

                return None

        return None

    finally:

        if close_conn:

            conn.close()



def save_global_settings(cursor, settings_dict):

    settings_json = json.dumps(settings_dict)

    cursor.execute("INSERT OR REPLACE INTO tbl_global_settings (key, value) VALUES ('archive_settings', ?)", (settings_json,))



def get_whisper_settings(cursor=None):

    close_conn = False

    if cursor is None:

        conn = _get_db_conn()

        cursor = conn.cursor()

        close_conn = True

    try:

        cursor.execute("SELECT value FROM tbl_global_settings WHERE key = 'whisper_config'")

        row = cursor.fetchone()

        if row and row['value']:

            try:

                return json.loads(row['value'])

            except Exception:

                return None

        return None

    finally:

        if close_conn:

            conn.close()



def save_whisper_settings(cursor, settings_dict):

    settings_json = json.dumps(settings_dict)

    cursor.execute("INSERT OR REPLACE INTO tbl_global_settings (key, value) VALUES ('whisper_config', ?)", (settings_json,))



def upsert_custom_emoji(cursor, document_id, file_path, mime_type, alt_emoji):

    import datetime

    now_str = datetime.datetime.now().isoformat()

    cursor.execute("""

        INSERT OR REPLACE INTO tbl_custom_emoji (document_id, file_path, mime_type, alt_emoji, created_at)

        VALUES (?, ?, ?, ?, ?)

    """, (int(document_id), file_path, mime_type, alt_emoji, now_str))



def get_custom_emojis_by_ids(cursor, doc_ids):

    if not doc_ids:

        return {}

    valid_ids = []

    for d in doc_ids:

        try:

            valid_ids.append(int(d))

        except (ValueError, TypeError):

            pass

    if not valid_ids:

        return {}

    placeholders = ','.join('?' for _ in valid_ids)

    cursor.execute(f"SELECT document_id, file_path, mime_type, alt_emoji FROM tbl_custom_emoji WHERE document_id IN ({placeholders})", valid_ids)

    rows = cursor.fetchall()

    result = {}

    for r in rows:

        result[str(r['document_id'])] = {

            'document_id': r['document_id'],

            'file_path': r['file_path'],

            'mime_type': r['mime_type'],

            'alt_emoji': r['alt_emoji']

        }

    return result







def add_bookmark(cursor, message_id, chat_id, note=""):

    from datetime import datetime, timezone, timezone

    now = datetime.now(timezone.utc).isoformat()

    cursor.execute(

        "INSERT OR REPLACE INTO tbl_bookmarks (message_id, chat_id, created_at, note) VALUES (?, ?, ?, ?)",

        (message_id, chat_id, now, note)

    )



def remove_bookmark(cursor, message_id, chat_id):

    cursor.execute(

        "DELETE FROM tbl_bookmarks WHERE message_id = ? AND chat_id = ?",

        (message_id, chat_id)

    )



def get_bookmarks(cursor, chat_id=None):

    if chat_id:

        cursor.execute(

            """SELECT b.message_id, b.chat_id, b.created_at, b.note, m.text, m.date, m.sender_id, m.media_type, m.media_local_path

               FROM tbl_bookmarks b

               LEFT JOIN tbl_messages m ON b.message_id = m.message_id AND b.chat_id = m.chat_id

               WHERE b.chat_id = ?

               ORDER BY b.created_at DESC""",

            (chat_id,)

        )

    else:

        cursor.execute(

            """SELECT b.message_id, b.chat_id, b.created_at, b.note, m.text, m.date, m.sender_id, m.media_type, m.media_local_path

               FROM tbl_bookmarks b

               LEFT JOIN tbl_messages m ON b.message_id = m.message_id AND b.chat_id = m.chat_id

               ORDER BY b.created_at DESC"""

        )

    return [dict(row) for row in cursor.fetchall()]





def save_admin_log_events(cursor, chat_id, events):

    """Saves a batch of admin log events into tbl_admin_log."""

    if not events:

        return

    for ev in events:

        cursor.execute("""

            INSERT OR REPLACE INTO tbl_admin_log (

                event_id, chat_id, date, user_id, action_type, action_json, prev_value_json, new_value_json

            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)

        """, (

            ev.get('event_id'), chat_id, ev.get('date'), ev.get('user_id'),

            ev.get('action_type'), ev.get('action_json'),

            ev.get('prev_value_json'), ev.get('new_value_json')

        ))



def get_admin_log_events(cursor, chat_id, limit=50, offset=0, action_type=None, min_id=None):

    """Retrieves admin log events for a chat with optional action_type filter and pipeline support."""

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

    query = f"""

        SELECT event_id, chat_id, date, user_id, action_type, action_json, prev_value_json, new_value_json

        FROM tbl_admin_log

        WHERE chat_id IN ({placeholders})

    """

    params = list(all_ids)



    if action_type:

        query += " AND action_type = ?"

        params.append(action_type)



    if min_id and min_id > 0:

        query += " AND event_id > ?"

        params.append(min_id)



    query += " ORDER BY date DESC, event_id DESC LIMIT ? OFFSET ?"

    params.extend([limit, offset or 0])



    cursor.execute(query, params)

    events = [dict(r) for r in cursor.fetchall()]

    if not events:

        msg_query = f"""

            SELECT m.message_id as event_id, m.chat_id, m.date, m.sender_id as user_id,

                   m.service_action_json as action_json,

                   u.first_name as admin_first_name, u.last_name as admin_last_name,

                   c.chat_name

            FROM tbl_messages m

            LEFT JOIN tbl_users u ON m.sender_id = u.user_id

            LEFT JOIN tbl_chats c ON m.chat_id = c.chat_id

            WHERE m.chat_id IN ({placeholders}) AND m.service_action_json IS NOT NULL

        """

        msg_params = list(all_ids)

        if min_id and min_id > 0:

            msg_query += " AND m.message_id > ?"

            msg_params.append(min_id)

        msg_query += " ORDER BY m.date DESC LIMIT ? OFFSET ?"

        msg_params.extend([limit, offset or 0])

        cursor.execute(msg_query, msg_params)

        cols = [d[0] for d in cursor.description] if cursor.description else []

        for r in cursor.fetchall():

            row = dict(r) if hasattr(r, 'keys') else dict(zip(cols, r))

            try:

                import json

                act = json.loads(row['action_json']) if row['action_json'] else {}

                raw_type = act.get('_', 'ServiceAction')

                clean_type = raw_type.replace('MessageAction', '')

                if action_type and action_type.lower() not in clean_type.lower():

                    continue

                admin_first = row['admin_first_name'] or row['chat_name'] or 'Channel/Group'

                admin_last = row['admin_last_name'] or ''

                old_val = None

                new_val = None

                if 'title' in act:

                    new_val = act['title']

                events.append({

                    'event_id': row['event_id'],

                    'chat_id': row['chat_id'],

                    'date': row['date'],

                    'user_id': row['user_id'],

                    'admin_first_name': admin_first,

                    'admin_last_name': admin_last,

                    'action_type': clean_type,

                    'action_json': row['action_json'],

                    'old_value': old_val,

                    'new_value': new_val

                })

            except Exception:

                pass

    return events



def save_stars_transactions(cursor, chat_id, transactions):

    """Saves stars transaction ledger items into tbl_stars_transactions."""

    if not transactions:

        return

    for tx in transactions:

        cursor.execute("""

            INSERT OR REPLACE INTO tbl_stars_transactions (

                tx_id, chat_id, date, stars, title, description, raw_json

            ) VALUES (?, ?, ?, ?, ?, ?, ?)

        """, (

            tx.get('tx_id'), chat_id, tx.get('date'), tx.get('stars'),

            tx.get('title'), tx.get('description'), tx.get('raw_json')

        ))



def get_stars_transactions(cursor, chat_id, limit=50, offset=0):

    """Retrieves stars transactions for a chat."""

    cursor.execute("""

        SELECT tx_id, chat_id, date, stars, title, description, raw_json

        FROM tbl_stars_transactions

        WHERE chat_id = ?

        ORDER BY date DESC

        LIMIT ? OFFSET ?

    """, (chat_id, limit, offset))

    return [dict(r) for r in cursor.fetchall()]



def get_user_full_profile(cursor, user_id):

    """Retrieves stored full profile details for a user with parsed JSON objects."""

    cursor.execute("""

        SELECT user_id, first_name, last_name, username, avatar_path,

               is_bot, is_premium, is_restricted, verified, lang_code,

               rank, is_creator, about, birthday_json, business_json,

               full_profile_json, full_profile_updated_at,

               emoji_status_document_id, emoji_status_json, gifts_json,

               phone, profile_photos_json, stories_json, personal_channel_id, personal_channel_title

        FROM tbl_users

        WHERE user_id = ?

    """, (user_id,))

    row = cursor.fetchone()

    if not row:

        # Check if user_id refers to a channel or chat in tbl_chats

        from .helpers import get_chat_id_variants

        variants = list(get_chat_id_variants(user_id))

        placeholders = ','.join('?' for _ in variants)

        cursor.execute(f"""

            SELECT chat_id, chat_name, safe_folder_name, chat_avatar_path,

                   chat_about, chat_creation_date, linked_chat_id, admin_list_json,

                   chat_type, wallpaper_path, emoji_status_document_id, emoji_status_json

            FROM tbl_chats

            WHERE chat_id IN ({placeholders})

        """, variants)

        chat_row = cursor.fetchone()

        if chat_row:

            cd = dict(chat_row) if hasattr(chat_row, 'keys') else dict(zip([c[0] for c in cursor.description], chat_row))

            admin_list = []

            if cd.get('admin_list_json'):

                try:

                    admin_list = json.loads(cd['admin_list_json'])

                except Exception:

                    admin_list = []

            photos = []

            if cd.get('chat_avatar_path'):

                photos = [cd['chat_avatar_path']]

            media_dir = os.path.join(PROJECT_ROOT, "media_backup")

            try:

                from . import ABS_MEDIA_BACKUP_DIR

                media_dir = ABS_MEDIA_BACKUP_DIR

            except Exception:

                pass

            return {

                'user_id': cd['chat_id'],

                'first_name': cd['chat_name'] or 'Channel',

                'last_name': '',

                'username': None,

                'avatar_path': cd.get('chat_avatar_path'),

                'avatar_mtime': int(os.path.getmtime(os.path.join(media_dir, cd['chat_avatar_path']))) if cd.get('chat_avatar_path') and os.path.exists(os.path.join(media_dir, cd['chat_avatar_path'])) else None,

                'is_channel': True,

                'chat_type': cd.get('chat_type') or 'channel',

                'is_bot': 0,

                'is_premium': 0,

                'is_creator': 0,

                'is_restricted': 0,

                'verified': 0,

                'about': cd.get('chat_about'),

                'creation_date': cd.get('chat_creation_date'),

                'linked_chat_id': cd.get('linked_chat_id'),

                'admin_list': admin_list,

                'emoji_status_document_id': cd.get('emoji_status_document_id'),

                'emoji_status': json.loads(cd['emoji_status_json']) if cd.get('emoji_status_json') else None,

                'birthday': None,

                'business': None,

                'gifts': [],

                'phone': None,

                'profile_photos': photos,

                'stories': [],

                'personal_channel_id': None,

                'personal_channel_title': None,

                'full_profile_updated_at': None

            }

        return None

    d = dict(row) if hasattr(row, 'keys') else dict(zip([c[0] for c in cursor.description], row))

    for k in ['birthday_json', 'business_json', 'full_profile_json', 'emoji_status_json', 'gifts_json', 'profile_photos_json', 'stories_json']:

        clean_k = k.replace('_json', '')

        if d.get(k):

            try:

                d[clean_k] = json.loads(d[k])

            except Exception:

                d[clean_k] = None

        else:

            d[clean_k] = None



    if d.get('profile_photos') and len(d['profile_photos']) > 0:

        latest_photo = d['profile_photos'][0]

        if latest_photo:

            d['avatar_path'] = latest_photo



    if d.get('avatar_path'):

        try:

            media_dir = os.path.join(PROJECT_ROOT, "media_backup")

            try:

                from . import ABS_MEDIA_BACKUP_DIR

                media_dir = ABS_MEDIA_BACKUP_DIR

            except Exception:

                pass

            av_abs = os.path.join(media_dir, d['avatar_path'])

            if os.path.exists(av_abs):

                d['avatar_mtime'] = int(os.path.getmtime(av_abs))

        except Exception:

            pass



    return d



def save_sponsored_messages(cursor, chat_id, messages):

    """Saves sponsored messages into tbl_sponsored_messages."""

    if not messages:

        return

    for sm in messages:

        cursor.execute("""

            INSERT OR REPLACE INTO tbl_sponsored_messages (

                message_id, chat_id, title, text, url, button_text, sponsor_info, additional_info, date

            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

        """, (

            sm.get('message_id'), chat_id, sm.get('title'), sm.get('text'),

            sm.get('url'), sm.get('button_text'), sm.get('sponsor_info'),

            sm.get('additional_info'), sm.get('date')

        ))





def get_sponsored_messages(cursor, chat_id):

    """Retrieves sponsored messages for a chat."""

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

        SELECT id, message_id, chat_id, title, text, url, button_text, sponsor_info, additional_info, date

        FROM tbl_sponsored_messages

        WHERE chat_id IN ({placeholders})

        ORDER BY id DESC

    """, list(all_ids))

    return [dict(r) for r in cursor.fetchall()]


