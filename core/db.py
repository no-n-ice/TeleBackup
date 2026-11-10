import sqlite3

import os

import re

import json

import logging

from datetime import datetime, timezone

# Decoupled from telethon



PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(PROJECT_ROOT, "telegram_backup.db")

LIVE_DB_FILE = os.path.join(PROJECT_ROOT, "live_stream_backup.db")

def get_dialog_cache_file():

    """Returns per-session dialog cache path to prevent cross-account chat leaks."""

    try:

        from . import SESSION_FILE

        if SESSION_FILE and SESSION_FILE != 'backup_session':

            return os.path.join(PROJECT_ROOT, f"dialog_cache_{SESSION_FILE}.json")

    except Exception:

        pass

    return os.path.join(PROJECT_ROOT, "dialog_cache.json")



CACHE_FILE = get_dialog_cache_file()



_LIVE_DB_INITIALIZED = False

_LIVE_BUFFER_DIRTY = True  # True on startup so leftover rows in live_stream_backup.db are flushed once



def mark_live_buffer_dirty():

    global _LIVE_BUFFER_DIRTY

    _LIVE_BUFFER_DIRTY = True





def get_db_conn(timeout=60.0):

    """Возвращает оптимизированное подключение к основной БД."""

    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=timeout)

    conn.row_factory = sqlite3.Row

    conn.execute('PRAGMA journal_mode=WAL;')

    conn.execute('PRAGMA synchronous=NORMAL;')

    conn.execute('PRAGMA busy_timeout=60000;')

    return conn



def get_live_db_conn(timeout=60.0):

    """Возвращает подключение к изолированной буферной БД для Live Update."""

    conn = sqlite3.connect(LIVE_DB_FILE, check_same_thread=False, timeout=timeout)

    conn.row_factory = sqlite3.Row

    conn.execute('PRAGMA journal_mode=WAL;')

    conn.execute('PRAGMA synchronous=NORMAL;')

    conn.execute('PRAGMA busy_timeout=60000;')

    return conn



def init_live_db(cursor):

    """Initializes tables in live buffer DB once per process lifetime."""

    global _LIVE_DB_INITIALIZED

    if _LIVE_DB_INITIALIZED and os.path.exists(LIVE_DB_FILE):

        return

    init_db(cursor)

    run_db_migration(cursor)

    _LIVE_DB_INITIALIZED = True



def delete_fts_messages(cursor, chat_id, message_ids):
    """
    O(log N) indexed removal of FTS5 entries prior to re-indexing.
    Bypasses the O(N) full table scan of 'WHERE chat_id = ? AND message_id IN (...)'
    by resolving rowids via idx_fts_content_chat_msg on tbl_messages_fts_content.
    """
    if not message_ids:
        return
    if isinstance(message_ids, (int, str)):
        message_ids = [message_ids]
    
    # Try fast rowid seek via shadow table index first
    try:
        ph = ','.join('?' for _ in message_ids)
        c_id_int = int(chat_id)
        m_ids_int = [int(m) for m in message_ids]
        cursor.execute(f"SELECT id FROM tbl_messages_fts_content WHERE c1 = ? AND c0 IN ({ph})", [c_id_int] + m_ids_int)
        rowids = [r[0] for r in cursor.fetchall()]
        if rowids:
            ph_r = ','.join('?' for _ in rowids)
            cursor.execute(f"DELETE FROM tbl_messages_fts WHERE rowid IN ({ph_r})", rowids)
        return
    except Exception:
        pass
    
    # Standard virtual table delete fallback
    try:
        ph = ','.join('?' for _ in message_ids)
        cursor.execute(f"DELETE FROM tbl_messages_fts WHERE chat_id = ? AND message_id IN ({ph})", [int(chat_id)] + [int(m) for m in message_ids])
    except Exception:
        pass

def flush_live_db_to_main():

    """Safely merges accumulated records from live_stream_backup.db into main telegram_backup.db."""

    global _LIVE_BUFFER_DIRTY

    import core as _core_mod
    if getattr(_core_mod, 'IS_DB_MERGING', False):
        return

    if not _LIVE_BUFFER_DIRTY or not os.path.exists(LIVE_DB_FILE):

        return

    main_conn = None

    live_conn = None

    try:

        live_conn = get_live_db_conn()

        live_cursor = live_conn.cursor()



        # Check if there is anything to transfer before acquiring main DB connection

        try:

            live_cursor.execute("SELECT 1 FROM tbl_messages LIMIT 1")

            has_msgs = live_cursor.fetchone()

            live_cursor.execute("SELECT 1 FROM tbl_chats LIMIT 1")

            has_chats = live_cursor.fetchone()

            live_cursor.execute("SELECT 1 FROM tbl_users LIMIT 1")

            has_users = live_cursor.fetchone()

            if not has_msgs and not has_chats and not has_users:

                _LIVE_BUFFER_DIRTY = False

                return

        except Exception:

            return



        main_conn = get_db_conn()

        main_cursor = main_conn.cursor()



        live_cursor.execute("SELECT * FROM tbl_chats")

        chats = live_cursor.fetchall()

        for c in chats:

            c_dict = dict(c)

            main_cursor.execute(

                "INSERT OR IGNORE INTO tbl_chats (chat_id, chat_name, safe_folder_name) VALUES (?, ?, ?)",

                (c_dict['chat_id'], c_dict['chat_name'], c_dict['safe_folder_name'])

            )



        live_cursor.execute("SELECT * FROM tbl_users")

        users = live_cursor.fetchall()

        for u in users:

            u_dict = dict(u)

            main_cursor.execute(

                """INSERT INTO tbl_users (

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

                    is_fake=excluded.is_fake""",

                (

                    u_dict['user_id'], u_dict['first_name'], u_dict['last_name'], u_dict['username'],

                    u_dict['avatar_path'], u_dict['is_bot'], u_dict['is_premium'], u_dict['is_restricted'],

                    u_dict['verified'], u_dict['usernames_json'], u_dict['lang_code'], u_dict['is_scam'], u_dict['is_fake']

                )

            )



        live_cursor.execute("SELECT * FROM tbl_messages")
        msgs = live_cursor.fetchall()
        fts_items = []
        for m in msgs:
            m_dict = dict(m)
            col_names = list(m_dict.keys())
            placeholders = ','.join('?' for _ in col_names)
            col_sql = ','.join(col_names)
            vals = [m_dict[k] for k in col_names]
            main_cursor.execute(f"INSERT OR REPLACE INTO tbl_messages ({col_sql}) VALUES ({placeholders})", vals)
            t_text = m_dict.get('text') or ''
            tr_text = m_dict.get('transcription_text') or ''
            comb = (t_text + ' ' + tr_text).strip()
            if comb:
                fts_items.append((m_dict['message_id'], m_dict['chat_id'], comb))

        if fts_items:
            fts_by_chat = {}
            for m_id, c_id, txt in fts_items:
                fts_by_chat.setdefault(c_id, []).append(m_id)
            for c_id, m_ids in fts_by_chat.items():
                delete_fts_messages(main_cursor, c_id, m_ids)
            main_cursor.executemany("INSERT INTO tbl_messages_fts (message_id, chat_id, text) VALUES (?, ?, ?)", fts_items)

        live_cursor.execute("SELECT * FROM tbl_custom_emoji")
        emojis = live_cursor.fetchall()
        for e in emojis:
            e_dict = dict(e)
            main_cursor.execute(
                """INSERT OR REPLACE INTO tbl_custom_emoji (
                    document_id, file_path, mime_type, alt_emoji, created_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (e_dict['document_id'], e_dict['file_path'], e_dict['mime_type'], e_dict['alt_emoji'], e_dict['created_at'])
            )

        main_conn.commit()

        # Delete ONLY the rows that were transferred, avoiding race condition on newly arrived live messages
        if msgs:
            for m in msgs:
                live_cursor.execute("DELETE FROM tbl_messages WHERE chat_id = ? AND message_id = ?", (m['chat_id'], m['message_id']))
        if users:
            for u in users:
                live_cursor.execute("DELETE FROM tbl_users WHERE user_id = ?", (u['user_id'],))
        if chats:
            for c in chats:
                live_cursor.execute("DELETE FROM tbl_chats WHERE chat_id = ?", (c['chat_id'],))
        if emojis:
            for e in emojis:
                live_cursor.execute("DELETE FROM tbl_custom_emoji WHERE document_id = ?", (e['document_id'],))
        live_conn.commit()

        # Check if new live records arrived in buffer during transfer
        live_cursor.execute("SELECT 1 FROM tbl_messages LIMIT 1")
        rem_m = live_cursor.fetchone()
        live_cursor.execute("SELECT 1 FROM tbl_chats LIMIT 1")
        rem_c = live_cursor.fetchone()
        live_cursor.execute("SELECT 1 FROM tbl_users LIMIT 1")
        rem_u = live_cursor.fetchone()
        _LIVE_BUFFER_DIRTY = bool(rem_m or rem_c or rem_u)

        if len(msgs) > 0:
            print(f"[DB Sync] Successfully transferred {len(msgs)} live messages to main database.")

    except Exception as e:

        if main_conn:

            try: main_conn.rollback()

            except Exception as e:

                logging.error('Exception caught', exc_info=True)

        if live_conn:

            try: live_conn.rollback()

            except Exception as e:

                logging.error('Exception caught', exc_info=True)

        print(f"[DB Sync Error] Error merging Live DB: {e}")

    finally:

        if main_conn:

            try: main_conn.close()

            except Exception as e:

                logging.error('Exception caught', exc_info=True)

        if live_conn:

            try: live_conn.close()

            except Exception as e:

                logging.error('Exception caught', exc_info=True)





def init_db(cursor):

    """Создает структуру таблиц в БД, если они не существуют."""

    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_chats (

        chat_id INTEGER PRIMARY KEY,

        chat_name TEXT,

        safe_folder_name TEXT,

        last_backup_offset_id INTEGER DEFAULT 0,

        chat_avatar_path TEXT,

        migrated_to_chat_id INTEGER,

        chat_about TEXT,

        chat_creation_date TEXT,

        linked_chat_id INTEGER,

        admin_list_json TEXT,

        color_json TEXT,

        profile_color_json TEXT,

        chat_type TEXT,

        backup_status TEXT DEFAULT 'complete',

        min_downloaded_id INTEGER DEFAULT 0,

        max_downloaded_id INTEGER DEFAULT 0,

        server_top_id INTEGER DEFAULT 0,

        has_older_gap INTEGER DEFAULT 0,

        has_newer_gap INTEGER DEFAULT 0,

        last_halt_reason TEXT,

        last_halt_date TEXT,

        resume_strategy TEXT,

        archive_settings_json TEXT DEFAULT NULL

    )''')

    

    try:

        cursor.execute("PRAGMA table_info(tbl_chats)")

        columns = [row[1] for row in cursor.fetchall()]

        chat_init_cols = {

            'archive_settings_json': 'TEXT DEFAULT NULL',

            'migrated_to_chat_id': 'INTEGER',

            'chat_about': 'TEXT', 'chat_creation_date': 'TEXT',

            'linked_chat_id': 'INTEGER', 'admin_list_json': 'TEXT',

            'color_json': 'TEXT', 'profile_color_json': 'TEXT',

            'chat_type': 'TEXT',

            'wallpaper_path': 'TEXT', 'wallpaper_json': 'TEXT',

            'pinned_msg_ids_json': 'TEXT',

            'emoji_status_document_id': 'INTEGER', 'emoji_status_json': 'TEXT',

            'stats_json': 'TEXT', 'stats_updated_at': 'TEXT',

            'archive_range_mode': "TEXT DEFAULT 'all'",

            'archive_range_start': 'TEXT',

            'archive_range_end': 'TEXT',

            'archive_range_days': 'INTEGER',

            'archive_direction': "TEXT DEFAULT 'backward'",

            'backup_status': "TEXT DEFAULT 'complete'",

            'min_downloaded_id': 'INTEGER DEFAULT 0',

            'max_downloaded_id': 'INTEGER DEFAULT 0',

            'server_top_id': 'INTEGER DEFAULT 0',

            'has_older_gap': 'INTEGER DEFAULT 0',

            'has_newer_gap': 'INTEGER DEFAULT 0',

            'last_halt_reason': 'TEXT',

            'last_halt_date': 'TEXT',

            'resume_strategy': 'TEXT',

            'participants_count': 'INTEGER DEFAULT 0',

            'track_participants': 'INTEGER DEFAULT 0',

            'participants_synced_at': 'TEXT'

        }

        for col, col_type in chat_init_cols.items():

            if col not in columns:

                cursor.execute(f"ALTER TABLE tbl_chats ADD COLUMN {col} {col_type}")

    except Exception as e:

        print(f"[DB] Error migrating tbl_chats: {e}")



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_users (

        user_id INTEGER PRIMARY KEY,

        first_name TEXT,

        last_name TEXT,

        username TEXT,

        avatar_path TEXT,

        is_bot INTEGER DEFAULT 0,

        is_premium INTEGER DEFAULT 0,

        is_restricted INTEGER DEFAULT 0,

        verified INTEGER DEFAULT 0,

        usernames_json TEXT,

        lang_code TEXT,

        is_scam INTEGER DEFAULT 0,

        is_fake INTEGER DEFAULT 0,

        rank TEXT,

        is_creator BOOLEAN DEFAULT 0,

        about TEXT,

        birthday_json TEXT,

        business_json TEXT,

        full_profile_json TEXT,

        full_profile_updated_at TEXT,

        emoji_status_document_id INTEGER,

        emoji_status_json TEXT,

        gifts_json TEXT

    )''')



    try:

        cursor.execute("PRAGMA table_info(tbl_users)")

        u_cols = [row[1] for row in cursor.fetchall()]

        user_init_cols = {

            'rank': 'TEXT', 'is_creator': 'BOOLEAN DEFAULT 0',

            'about': 'TEXT', 'birthday_json': 'TEXT',

            'business_json': 'TEXT', 'full_profile_json': 'TEXT',

            'full_profile_updated_at': 'TEXT',

            'emoji_status_document_id': 'INTEGER', 'emoji_status_json': 'TEXT',

            'gifts_json': 'TEXT',

            'phone': 'TEXT',

            'profile_photos_json': 'TEXT',

            'stories_json': 'TEXT',

            'personal_channel_id': 'INTEGER',

            'personal_channel_title': 'TEXT'

        }

        for col, col_type in user_init_cols.items():

            if col not in u_cols:

                cursor.execute(f"ALTER TABLE tbl_users ADD COLUMN {col} {col_type}")

    except Exception as e:

        print(f"[DB] Error migrating tbl_users: {e}")



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_messages (

        message_id INTEGER NOT NULL,

        chat_id INTEGER NOT NULL,

        sender_id INTEGER,

        date TEXT,

        text TEXT,

        reply_to_msg_id INTEGER,

        reply_sender_id INTEGER,

        reply_snippet TEXT,

        edited_date TEXT,

        media_local_path TEXT,

        thumbnail_local_path TEXT,

        media_original_name TEXT,

        media_mime_type TEXT,

        media_skipped TEXT,

        group_id INTEGER,

        forward_info_json TEXT,

        service_action_json TEXT,

        poll_data_json TEXT,

        entities_json TEXT,

        is_media_spoiler INTEGER DEFAULT 0,

        reactions_json TEXT,

        media_other_json TEXT,

        views_count INTEGER,

        via_bot_id INTEGER,

        post_author_name TEXT,

        reply_markup_json TEXT,

        is_pinned INTEGER DEFAULT 0,

        is_outgoing INTEGER DEFAULT 0,

        is_mentioned INTEGER DEFAULT 0,

        is_post INTEGER DEFAULT 0,

        invert_media INTEGER DEFAULT 0,

        is_legacy INTEGER DEFAULT 0,

        replies_json TEXT,

        restriction_reason_json TEXT,

        reply_to_top_id INTEGER,

        is_forum_topic INTEGER DEFAULT 0,

        reply_to_peer_id_json TEXT,

        quote_text TEXT,

        quote_entities_json TEXT,

        quote_offset INTEGER,

        reply_from_json TEXT,

        reply_media_json TEXT,

        is_voice INTEGER DEFAULT 0,

        is_round_video INTEGER DEFAULT 0,

        video_cover_path TEXT,

        media_type TEXT,

        saved_peer_id_json TEXT,

        via_business_bot_id INTEGER,

        video_timestamp INTEGER,

        from_scheduled INTEGER DEFAULT 0,

        forwards INTEGER,

        is_deleted INTEGER DEFAULT 0,

        edit_history_json TEXT,

        reply_to_story_id INTEGER,

        transcription_text TEXT DEFAULT NULL,

        file_size INTEGER DEFAULT 0,

        duration INTEGER DEFAULT 0,

        transcription_lang TEXT DEFAULT NULL,

        waveform_data TEXT DEFAULT NULL,

        PRIMARY KEY (message_id, chat_id)

    )''')

    

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON tbl_messages (chat_id, date DESC, message_id DESC)')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_sender ON tbl_messages (chat_id, sender_id)')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_media ON tbl_messages (chat_id, media_type)')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_msgid ON tbl_messages (chat_id, message_id DESC)')

    cursor.execute('''

        CREATE INDEX IF NOT EXISTS idx_messages_missing_media 

        ON tbl_messages (chat_id, message_id DESC) 

        WHERE media_local_path IS NULL OR media_local_path = ''

    ''')

    cursor.execute('''

        CREATE INDEX IF NOT EXISTS idx_messages_group_id 

        ON tbl_messages (group_id, message_id ASC) 

        WHERE group_id IS NOT NULL

    ''')

    cursor.execute('DROP INDEX IF EXISTS idx_messages_media_name')

    cursor.execute('''

        CREATE INDEX IF NOT EXISTS idx_messages_media_name 

        ON tbl_messages (media_original_name, file_size, chat_id, media_local_path)

        WHERE media_original_name IS NOT NULL

    ''')

    cursor.execute('''

        CREATE INDEX IF NOT EXISTS idx_messages_chat_reply_top 

        ON tbl_messages (chat_id, reply_to_top_id) 

        WHERE reply_to_top_id IS NOT NULL

    ''')

    cursor.execute('''

        CREATE INDEX IF NOT EXISTS idx_messages_chat_reply_msg 

        ON tbl_messages (chat_id, reply_to_msg_id) 

        WHERE reply_to_msg_id IS NOT NULL

    ''')



    cursor.execute('''

    CREATE VIRTUAL TABLE IF NOT EXISTS tbl_messages_fts USING fts5(

        message_id UNINDEXED, 

        chat_id UNINDEXED, 

        text

    )''')



    

    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_bookmarks (

        message_id INTEGER NOT NULL,

        chat_id INTEGER NOT NULL,

        created_at TEXT,

        note TEXT,

        PRIMARY KEY (message_id, chat_id)

    )''')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_custom_emoji (

        document_id INTEGER PRIMARY KEY,

        file_path TEXT,

        mime_type TEXT,

        alt_emoji TEXT,

        created_at TEXT

    )''')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_admin_log (

        event_id INTEGER PRIMARY KEY,

        chat_id INTEGER NOT NULL,

        date TEXT,

        user_id INTEGER,

        action_type TEXT,

        action_json TEXT,

        prev_value_json TEXT,

        new_value_json TEXT

    )''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_admin_log_chat_date ON tbl_admin_log(chat_id, date)')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_stars_transactions (

        tx_id TEXT PRIMARY KEY,

        chat_id INTEGER NOT NULL,

        date TEXT,

        stars INTEGER,

        title TEXT,

        description TEXT,

        raw_json TEXT

    )''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stars_tx_chat_date ON tbl_stars_transactions(chat_id, date)')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_sponsored_messages (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        message_id INTEGER,

        chat_id INTEGER NOT NULL,

        title TEXT,

        text TEXT,

        url TEXT,

        button_text TEXT,

        sponsor_info TEXT,

        additional_info TEXT,

        date TEXT,

        UNIQUE(chat_id, message_id)

    )''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sponsored_chat ON tbl_sponsored_messages(chat_id)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_chat_participants (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT DEFAULT 'active',
        entered_at TEXT,
        first_seen_at TEXT NOT NULL,
        left_at TEXT,
        exit_reason TEXT,
        kicked_by_user_id INTEGER,
        invited_by_user_id INTEGER,
        admin_rank TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_participants_status ON tbl_chat_participants(chat_id, status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_participants_left ON tbl_chat_participants(chat_id, left_at DESC)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_participant_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id INTEGER,
        event_type TEXT NOT NULL,
        date TEXT NOT NULL,
        actor_user_id INTEGER,
        exit_reason TEXT,
        participants_count INTEGER,
        source TEXT NOT NULL,
        external_event_id INTEGER,
        raw_json TEXT
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_part_events_chat_date ON tbl_participant_events(chat_id, date DESC)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_folders (
        folder_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        emoticon TEXT,
        color INTEGER DEFAULT 0,
        source TEXT DEFAULT 'custom',
        pinned_peers_json TEXT,
        include_peers_json TEXT,
        exclude_peers_json TEXT,
        filter_flags_json TEXT,
        order_index INTEGER DEFAULT 0,
        updated_at TEXT
    )''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_folder_chats (
        folder_id TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        is_pinned INTEGER DEFAULT 0,
        PRIMARY KEY (folder_id, chat_id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_folder_chats_chat ON tbl_folder_chats(chat_id)')

    try:
        cursor.execute('''
        UPDATE tbl_chats 
        SET chat_type = CASE 
            WHEN EXISTS (SELECT 1 FROM tbl_users u WHERE u.user_id = tbl_chats.chat_id AND u.is_bot = 1) THEN 'bot'
            ELSE 'user'
        END
        WHERE chat_id > 0 AND (chat_type = 'group' OR chat_type IS NULL)
        ''')
    except Exception:
        pass

    try:
        if hasattr(cursor, 'connection') and cursor.connection:
            cursor.connection.commit()
    except Exception:
        pass



def run_db_migration(cursor):
    for col, col_type in [
        ('participants_count', 'INTEGER DEFAULT 0'),
        ('track_participants', 'INTEGER DEFAULT 0'),
        ('participants_synced_at', 'TEXT')
    ]:
        try:
            cursor.execute(f"ALTER TABLE tbl_chats ADD COLUMN {col} {col_type}")
        except Exception:
            pass

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_chat_participants (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT DEFAULT 'active',
        entered_at TEXT,
        first_seen_at TEXT NOT NULL,
        left_at TEXT,
        exit_reason TEXT,
        kicked_by_user_id INTEGER,
        invited_by_user_id INTEGER,
        admin_rank TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_participants_status ON tbl_chat_participants(chat_id, status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_participants_left ON tbl_chat_participants(chat_id, left_at DESC)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_participant_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id INTEGER,
        event_type TEXT NOT NULL,
        date TEXT NOT NULL,
        actor_user_id INTEGER,
        exit_reason TEXT,
        participants_count INTEGER,
        source TEXT NOT NULL,
        external_event_id INTEGER,
        raw_json TEXT
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_part_events_chat_date ON tbl_participant_events(chat_id, date DESC)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_folders (
        folder_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        emoticon TEXT,
        color INTEGER DEFAULT 0,
        source TEXT DEFAULT 'custom',
        pinned_peers_json TEXT,
        include_peers_json TEXT,
        exclude_peers_json TEXT,
        filter_flags_json TEXT,
        order_index INTEGER DEFAULT 0,
        updated_at TEXT
    )''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tbl_folder_chats (
        folder_id TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        is_pinned INTEGER DEFAULT 0,
        PRIMARY KEY (folder_id, chat_id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_folder_chats_chat ON tbl_folder_chats(chat_id)')

    try:
        cursor.execute('''
        UPDATE tbl_chats 
        SET chat_type = CASE 
            WHEN EXISTS (SELECT 1 FROM tbl_users u WHERE u.user_id = tbl_chats.chat_id AND u.is_bot = 1) THEN 'bot'
            ELSE 'user'
        END
        WHERE chat_id > 0 AND (chat_type = 'group' OR chat_type IS NULL)
        ''')
    except Exception:
        pass

    try:
        if hasattr(cursor, 'connection') and cursor.connection:
            cursor.connection.commit()
    except Exception:
        pass

    try:

        cursor.execute("ALTER TABLE tbl_chats ADD COLUMN archive_settings_json TEXT DEFAULT NULL")

    except Exception:

        pass



    try:

        cursor.execute("UPDATE tbl_chats SET archive_settings_json = wallpaper_path, wallpaper_path = NULL, archive_direction = 'backward' WHERE wallpaper_path LIKE '{%'")

        cursor.execute("UPDATE tbl_chats SET archive_settings_json = NULL, archive_direction = 'backward' WHERE archive_settings_json = 'backward'")

    except Exception:

        pass

        

    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_global_settings (

        key TEXT PRIMARY KEY,

        value TEXT

    )''')



    

    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_bookmarks (

        message_id INTEGER NOT NULL,

        chat_id INTEGER NOT NULL,

        created_at TEXT,

        note TEXT,

        PRIMARY KEY (message_id, chat_id)

    )''')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_custom_emoji (

        document_id INTEGER PRIMARY KEY,

        file_path TEXT,

        mime_type TEXT,

        alt_emoji TEXT,

        created_at TEXT

    )''')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_admin_log (

        event_id INTEGER PRIMARY KEY,

        chat_id INTEGER NOT NULL,

        date TEXT,

        user_id INTEGER,

        action_type TEXT,

        action_json TEXT,

        prev_value_json TEXT,

        new_value_json TEXT

    )''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_admin_log_chat_date ON tbl_admin_log(chat_id, date)')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_stars_transactions (

        tx_id TEXT PRIMARY KEY,

        chat_id INTEGER NOT NULL,

        date TEXT,

        stars INTEGER,

        title TEXT,

        description TEXT,

        raw_json TEXT

    )''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stars_tx_chat_date ON tbl_stars_transactions(chat_id, date)')



    cursor.execute('''

    CREATE TABLE IF NOT EXISTS tbl_sponsored_messages (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        message_id INTEGER,

        chat_id INTEGER NOT NULL,

        title TEXT,

        text TEXT,

        url TEXT,

        button_text TEXT,

        sponsor_info TEXT,

        additional_info TEXT,

        date TEXT,

        UNIQUE(chat_id, message_id)

    )''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sponsored_chat ON tbl_sponsored_messages(chat_id)')



    """Проверяет и добавляет недостающие столбцы в существующую БД."""

    try:

        cursor.execute("PRAGMA table_info(tbl_messages)")

        columns = [row[1] for row in cursor.fetchall()]

        

        cols_to_add = {

            'reactions_json': 'TEXT', 'is_media_spoiler': 'INTEGER DEFAULT 0',

            'media_other_json': 'TEXT', 'views_count': 'INTEGER',

            'via_bot_id': 'INTEGER', 'post_author_name': 'TEXT',

            'reply_markup_json': 'TEXT', 'is_pinned': 'INTEGER DEFAULT 0',

            'is_outgoing': 'INTEGER DEFAULT 0', 'is_mentioned': 'INTEGER DEFAULT 0',

            'is_post': 'INTEGER DEFAULT 0', 'invert_media': 'INTEGER DEFAULT 0',

            'is_legacy': 'INTEGER DEFAULT 0', 'replies_json': 'TEXT',

            'restriction_reason_json': 'TEXT', 'reply_to_top_id': 'INTEGER',

            'is_forum_topic': 'INTEGER DEFAULT 0', 'reply_to_peer_id_json': 'TEXT',

            'quote_text': 'TEXT', 'quote_entities_json': 'TEXT',

            'quote_offset': 'INTEGER', 'reply_from_json': 'TEXT',

            'reply_media_json': 'TEXT', 'is_voice': 'INTEGER DEFAULT 0',

            'is_round_video': 'INTEGER DEFAULT 0', 'video_cover_path': 'TEXT',

            'media_type': 'TEXT',

            'saved_peer_id_json': 'TEXT', 'via_business_bot_id': 'INTEGER',

            'video_timestamp': 'INTEGER',

            'from_scheduled': 'INTEGER DEFAULT 0', 'forwards': 'INTEGER',

            'is_deleted': 'INTEGER DEFAULT 0',

            'edit_history_json': 'TEXT',

            'reply_to_story_id': 'INTEGER',

            'file_size': 'INTEGER DEFAULT 0',

            'duration': 'INTEGER DEFAULT 0',

            'transcription_lang': 'TEXT DEFAULT NULL',

            'waveform_data': 'TEXT DEFAULT NULL'

        }



        for col, col_type in cols_to_add.items():

            if col not in columns:

                cursor.execute(f"ALTER TABLE tbl_messages ADD COLUMN {col} {col_type}")



        # Backfill file_size for existing records if needed (only once)

        try:

            cursor.execute("SELECT value FROM tbl_global_settings WHERE key = 'file_size_backfilled'")

            b_flag = cursor.fetchone()

            b_val = (b_flag['value'] if hasattr(b_flag, 'keys') else b_flag[0]) if b_flag else None

            if b_val != '1':

                media_backup_root = os.path.join(PROJECT_ROOT, "media_backup")

                cursor.execute("""

                    SELECT message_id, chat_id, media_local_path, media_skipped 

                    FROM tbl_messages 

                    WHERE (file_size IS NULL OR file_size = 0) 

                      AND (media_local_path IS NOT NULL OR media_skipped IS NOT NULL)

                """)

                bf_rows = cursor.fetchall()

                for r in bf_rows:

                    f_size = 0

                    m_path = r['media_local_path']

                    if m_path:

                        fp = os.path.join(media_backup_root, m_path)

                        if os.path.exists(fp):

                            try:

                                f_size = os.path.getsize(fp)

                            except Exception:

                                pass

                    elif r['media_skipped']:

                        ms = str(r['media_skipped'])

                        m_match = re.search(r'(?:Больше|лимит)\s+(\d+(?:\.\d+)?)\s*МБ', ms, re.IGNORECASE)

                        if m_match:

                            f_size = int(float(m_match.group(1)) * 1024 * 1024)

                    if f_size > 0:

                        cursor.execute("UPDATE tbl_messages SET file_size = ? WHERE message_id = ? AND chat_id = ?",

                                       (f_size, r['message_id'], r['chat_id']))

                cursor.execute("INSERT OR REPLACE INTO tbl_global_settings (key, value) VALUES ('file_size_backfilled', '1')")

        except Exception as e_bf:

            logging.debug(f"[DB] file_size backfill notice: {e_bf}")



        # Clean up legacy raw download errors into properly classified status

        try:

            cursor.execute("""

                UPDATE tbl_messages

                SET media_skipped = 'Failed: Telegram Server Timeout (internal issue)'

                WHERE media_skipped LIKE '%Request was unsuccessful%'

            """)

            cursor.execute("""

                UPDATE tbl_messages

                SET media_skipped = 'Unavailable on Telegram (purged or expired)'

                WHERE media_skipped = 'Unavailable on Telegram'

            """)

        except Exception as e_clean:

            logging.debug(f"[DB] media_skipped cleanup notice: {e_clean}")

    except sqlite3.Error as e:

        print(f"[DB] Could not perform migration of tbl_messages: {e}")

    # --- Migration tbl_users ---

    try:

        cursor.execute("PRAGMA table_info(tbl_users)")

        cols_users = [row[1] for row in cursor.fetchall()]

        user_additions = [

            ('rank', 'TEXT'),

            ('is_creator', 'BOOLEAN DEFAULT 0'),

            ('about', 'TEXT'),

            ('birthday_json', 'TEXT'),

            ('business_json', 'TEXT'),

            ('full_profile_json', 'TEXT'),

            ('full_profile_updated_at', 'TEXT'),

            ('emoji_status_document_id', 'INTEGER'),

            ('emoji_status_json', 'TEXT'),

            ('gifts_json', 'TEXT'),

            ('phone', 'TEXT'),

            ('profile_photos_json', 'TEXT'),

            ('stories_json', 'TEXT'),

            ('personal_channel_id', 'INTEGER'),

            ('personal_channel_title', 'TEXT')

        ]

        for col_name, col_type in user_additions:

            if col_name not in cols_users:

                cursor.execute(f"ALTER TABLE tbl_users ADD COLUMN {col_name} {col_type}")

        try:

            cursor.execute("""

                UPDATE tbl_users

                SET avatar_path = json_extract(profile_photos_json, '$[0]')

                WHERE profile_photos_json IS NOT NULL

                  AND json_extract(profile_photos_json, '$[0]') IS NOT NULL

            """)

        except Exception:

            pass

    except sqlite3.Error as e:

        print(f"[DB] Could not perform migration of tbl_users: {e}")

    # --- Migration tbl_chats ---

    try:

        cursor.execute("PRAGMA table_info(tbl_chats)")

        columns = [row[1] for row in cursor.fetchall()]

        chat_cols = {

            'chat_about': 'TEXT', 'chat_creation_date': 'TEXT', 'linked_chat_id': 'INTEGER', 'admin_list_json': 'TEXT',

            'color_json': 'TEXT', 'profile_color_json': 'TEXT', 'chat_type': 'TEXT',

            'wallpaper_path': 'TEXT', 'wallpaper_json': 'TEXT',

            'pinned_msg_ids_json': 'TEXT',

            'emoji_status_document_id': 'INTEGER', 'emoji_status_json': 'TEXT',

            'stats_json': 'TEXT', 'stats_updated_at': 'TEXT',

            'archive_range_mode': "TEXT DEFAULT 'all'",

            'archive_range_start': 'TEXT',

            'archive_range_end': 'TEXT',

            'archive_range_days': 'INTEGER',

            'archive_direction': "TEXT DEFAULT 'backward'",

            'backup_status': "TEXT DEFAULT 'complete'",

            'min_downloaded_id': 'INTEGER DEFAULT 0',

            'max_downloaded_id': 'INTEGER DEFAULT 0',

            'server_top_id': 'INTEGER DEFAULT 0',

            'has_older_gap': 'INTEGER DEFAULT 0',

            'has_newer_gap': 'INTEGER DEFAULT 0',

            'last_halt_reason': 'TEXT',

            'last_halt_date': 'TEXT',

            'resume_strategy': 'TEXT',

            'participants_count': 'INTEGER DEFAULT 0',

            'track_participants': 'INTEGER DEFAULT 0',

            'participants_synced_at': 'TEXT'

        }

        for col, col_type in chat_cols.items():

            if col not in columns:

                cursor.execute(f"ALTER TABLE tbl_chats ADD COLUMN {col} {col_type}")



        # Backfill bounds for existing chats if needed (only once)

        try:

            cursor.execute("SELECT value FROM tbl_global_settings WHERE key = 'chat_bounds_backfilled'")

            b_flag = cursor.fetchone()

            b_val = (b_flag['value'] if hasattr(b_flag, 'keys') else b_flag[0]) if b_flag else None

            if b_val != '1':

                from .db_chats import backfill_all_chat_bounds
                backfill_all_chat_bounds(cursor)

                cursor.execute("INSERT OR REPLACE INTO tbl_global_settings (key, value) VALUES ('chat_bounds_backfilled', '1')")

        except Exception as e_bf:

            logging.debug(f"[DB] chat bounds backfill notice: {e_bf}")

    except sqlite3.Error as e:

        print(f"[DB] Could not perform migration of tbl_chats: {e}")



    # --- Performance Indexes on tbl_messages ---

    try:

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON tbl_messages (chat_id, date DESC, message_id DESC)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_sender ON tbl_messages (chat_id, sender_id)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_media ON tbl_messages (chat_id, media_type)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_media_name ON tbl_messages (media_original_name, file_size)")

    except Exception as e_idx:

        logging.debug(f"[DB] Index migration notice: {e_idx}")



    # --- Transcription Text Column ---

    try:

        cursor.execute("PRAGMA table_info(tbl_messages)")

        msg_cols = [row[1] for row in cursor.fetchall()]

        if 'transcription_text' not in msg_cols:

            cursor.execute("ALTER TABLE tbl_messages ADD COLUMN transcription_text TEXT DEFAULT NULL")

    except Exception as e_tr:

        logging.debug(f"[DB] transcription_text column migration notice: {e_tr}")



    try:

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_msgid ON tbl_messages (chat_id, message_id DESC)')

        cursor.execute('''

            CREATE INDEX IF NOT EXISTS idx_messages_missing_media 

            ON tbl_messages (chat_id, message_id DESC) 

            WHERE media_local_path IS NULL OR media_local_path = ''

        ''')

        cursor.execute('''

            CREATE INDEX IF NOT EXISTS idx_messages_group_id 

            ON tbl_messages (group_id, message_id ASC) 

            WHERE group_id IS NOT NULL

        ''')

        cursor.execute('DROP INDEX IF EXISTS idx_messages_media_name')

        cursor.execute('''

            CREATE INDEX IF NOT EXISTS idx_messages_media_name 

            ON tbl_messages (media_original_name, file_size, chat_id, media_local_path)

            WHERE media_original_name IS NOT NULL

        ''')

        cursor.execute('''

            CREATE INDEX IF NOT EXISTS idx_messages_chat_reply_top 

            ON tbl_messages (chat_id, reply_to_top_id) 

            WHERE reply_to_top_id IS NOT NULL

        ''')

        cursor.execute('''

            CREATE INDEX IF NOT EXISTS idx_messages_chat_reply_msg 

            ON tbl_messages (chat_id, reply_to_msg_id) 

            WHERE reply_to_msg_id IS NOT NULL

        ''')

    except Exception as e_idx:

        logging.debug(f"[DB] index migration notice: {e_idx}")

    try:
        if hasattr(cursor, 'connection') and cursor.connection:
            cursor.connection.commit()
    except Exception:
        pass




# ── Re-export domain submodules for 100% backward compatibility ──
from .db_chats import *
from .db_messages import *
from .db_merger import *
from .db_participants import *
from .folders import *
