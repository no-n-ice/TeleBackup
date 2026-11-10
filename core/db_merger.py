#!/usr/bin/env python3
"""
core/db_merger.py — Non-destructive transactional merger for multi-machine/seed SQLite databases.
Includes WAL-safe backup, scoped atomic message/FTS5 chunking, zero-syscall media set diffing,
and live background progress reporting.
"""
import os
import re
import time
import shutil
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePath
from .db_messages import recalculate_chat_bounds

_SAFE_COL_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_BLOCKED_MEDIA_SYNC_EXTS = frozenset({
    '.session', '.session-journal', '.db', '.sqlite', '.sqlite3', '.wal', '.shm',
    '.ini', '.env', '.pem', '.key', '.pfx', '.p12', '.kdbx', '.py', '.pyc', '.pyo',
})

MERGE_STATUS_LOCK = threading.Lock()
MERGE_STATUS = {
    "status": "idle",
    "stage": "",
    "percent": 0,
    "detail": "",
    "db_stats": {"messages_merged": 0, "messages_total": 0, "chats_merged": 0, "chats_total": 0},
    "media_stats": {"copied": 0, "total": 0, "bytes_copied": 0, "total_bytes": 0, "skipped": 0},
    "snapshot_backup": None,
    "error": None,
    "started_at": None,
    "finished_at": None
}

def get_merge_status():
    with MERGE_STATUS_LOCK:
        return dict(MERGE_STATUS)

def _update_merge_status(stage, current, total, detail, percent):
    with MERGE_STATUS_LOCK:
        MERGE_STATUS["status"] = "running"
        MERGE_STATUS["stage"] = stage
        MERGE_STATUS["percent"] = min(100, max(0, percent))
        MERGE_STATUS["detail"] = detail
        if stage == "messages":
            MERGE_STATUS["db_stats"]["messages_merged"] = current
            MERGE_STATUS["db_stats"]["messages_total"] = total
        elif stage == "media_copy":
            MERGE_STATUS["media_stats"]["copied"] = current
            MERGE_STATUS["media_stats"]["total"] = total

def _is_subpath(child_real, parent_real):
    try:
        return os.path.commonpath([parent_real, child_real]) == parent_real
    except ValueError:
        return False

def _validate_merge_media_dir(source_media_dir, project_root, media_dest_root):
    if '..' in PurePath(source_media_dir).parts:
        raise ValueError("Relative parent traversal ('..') in source_media_dir is not allowed")
    if os.path.islink(source_media_dir):
        raise ValueError("Symlink source_media_dir is not allowed")
    real_src_media = os.path.realpath(os.path.abspath(source_media_dir))
    if not os.path.isdir(real_src_media):
        raise FileNotFoundError(f"Source media directory not found: {source_media_dir}")

    is_drive_root = os.path.splitdrive(real_src_media)[1] in ('\\', '/', '')
    blocked_prefixes = (
        'c:\\windows', 'c:\\program files', 'c:\\program files (x86)', 'c:\\programdata',
        '/usr', '/etc', '/bin', '/sbin', '/var', '/dev', '/proc', '/sys', '/boot', '/root',
    )
    lower_media = real_src_media.lower()
    if is_drive_root or any(lower_media == b or lower_media.startswith(b + ('\\' if '\\' in b else '/')) for b in blocked_prefixes):
        raise ValueError("Cannot sync media from system directory or drive root")

    for part in PurePath(real_src_media).parts:
        if part.startswith('.') and part not in ('.', '..'):
            raise ValueError("Cannot sync media from hidden dot-directories")

    real_proj = os.path.realpath(project_root)
    real_dest = os.path.realpath(media_dest_root)
    if real_src_media in (real_proj, real_dest):
        raise ValueError("Cannot sync media from the active project root or active media_backup directory")
    if _is_subpath(real_src_media, real_dest):
        raise ValueError("Cannot sync media from inside the active media_backup directory")
    return real_src_media

def merge_database_file(source_db_path, source_media_dir=None, progress_callback=None):
    """
    Non-destructive transactional merger for multi-machine/seed SQLite databases.
    Applies defensive column intersection, Anti-Interference profile protection,
    atomic transaction with scoped chunking, O(log N) FTS5 sync, and fast O(1) media sync.
    """
    import core
    from . import db as _db
    DB_FILE = _db.DB_FILE
    PROJECT_ROOT = _db.PROJECT_ROOT
    flush_live_db_to_main = _db.flush_live_db_to_main

    if getattr(core, 'IS_BULK_ARCHIVING', False):
        raise ValueError("Cannot merge database while bulk archiving is active.")

    if '..' in PurePath(source_db_path).parts:
        raise ValueError("Relative parent traversal ('..') in source_db_path is not allowed")
    if os.path.islink(source_db_path):
        raise ValueError("Symlink database files are not allowed")
    if not os.path.exists(source_db_path):
        raise FileNotFoundError(f"Source database file not found: {source_db_path}")

    real_src_db = os.path.realpath(os.path.abspath(source_db_path))
    if not os.path.isfile(real_src_db):
        raise FileNotFoundError(f"Source database file not found: {source_db_path}")

    real_main_db = os.path.realpath(DB_FILE)
    real_live_db = os.path.realpath(_db.LIVE_DB_FILE)
    from . import ABS_MEDIA_BACKUP_DIR
    media_dest_root = ABS_MEDIA_BACKUP_DIR or os.path.join(PROJECT_ROOT, "media_backup")
    real_media_root = os.path.realpath(media_dest_root)
    real_default_media_root = os.path.realpath(os.path.join(PROJECT_ROOT, "media_backup"))

    if real_src_db in (real_main_db, real_live_db):
        raise ValueError("Cannot merge the active TeleBackup database into itself")
    if real_src_db.lower().endswith('.session'):
        raise ValueError("Refusing to merge a Telethon .session file as an archive database")
    if _is_subpath(real_src_db, real_media_root) or _is_subpath(real_src_db, real_default_media_root):
        raise ValueError("Refusing to merge a database file located inside media_backup")

    # Validate SQLite3 magic header and TeleBackup schema in strict read-only mode
    with open(real_src_db, 'rb') as f_hdr:
        if f_hdr.read(16) != b"SQLite format 3\x00":
            raise ValueError("Source file is not a valid SQLite 3 database")

    src_ro_uri = Path(real_src_db).as_uri() + "?mode=ro"
    ro_check_conn = sqlite3.connect(src_ro_uri, uri=True, timeout=10.0)
    try:
        ro_cur = ro_check_conn.cursor()
        ro_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tbl_chats', 'tbl_messages')")
        found_tables = {r[0] for r in ro_cur.fetchall()}
        if not {'tbl_chats', 'tbl_messages'}.issubset(found_tables):
            raise ValueError("Source database is missing required TeleBackup tables (tbl_chats, tbl_messages)")
    finally:
        ro_check_conn.close()

    validated_src_media = None
    if source_media_dir:
        validated_src_media = _validate_merge_media_dir(source_media_dir, PROJECT_ROOT, media_dest_root)

    # 1. Pre-flight snapshot & live buffer flush
    flush_live_db_to_main()
    core.IS_DB_MERGING = True

    snapshot_bak = f"{DB_FILE}.pre_merge_{int(time.time())}.bak"
    main_conn = None
    cursor = None

    try:
        if progress_callback:
            progress_callback("snapshot", 0, 100, "Starting WAL-safe snapshot backup...", 2)

        main_conn = sqlite3.connect(Path(real_main_db).as_uri(), uri=True, check_same_thread=False, timeout=60.0)
        main_conn.row_factory = sqlite3.Row
        main_conn.execute('PRAGMA journal_mode=WAL;')
        main_conn.execute('PRAGMA synchronous=NORMAL;')
        main_conn.execute('PRAGMA busy_timeout=60000;')

        # WAL-safe backup with exact page progress
        bak_conn = sqlite3.connect(snapshot_bak)
        def _backup_progress(status, remaining, total):
            if total > 0 and progress_callback:
                done = total - remaining
                pct = int(15 * (done / total))
                progress_callback("snapshot", done, total, f"Creating safety snapshot: {done:,}/{total:,} pages ({pct}%)", pct)
        main_conn.backup(bak_conn, pages=4000, progress=_backup_progress)
        bak_conn.close()

        cursor = main_conn.cursor()
        cursor.execute("ATTACH DATABASE ? AS src", (src_ro_uri,))
        cursor.execute("BEGIN IMMEDIATE")

        def _get_common_cols(table_name):
            if not _SAFE_COL_RE.match(table_name):
                return []
            cursor.execute(f"PRAGMA main.table_info({table_name})")
            m_cols = {r[1] for r in cursor.fetchall() if isinstance(r[1], str) and _SAFE_COL_RE.match(r[1])}
            cursor.execute(f"PRAGMA src.table_info({table_name})")
            s_cols = {r[1] for r in cursor.fetchall() if isinstance(r[1], str) and _SAFE_COL_RE.match(r[1])}
            return list(m_cols & s_cols)

        if progress_callback:
            progress_callback("schema", 15, 100, "Merging chats and users...", 15)

        # Merge tbl_chats
        chat_cols = _get_common_cols('tbl_chats')
        if chat_cols:
            cols_sql = ', '.join(chat_cols)
            chat_updates = []
            if 'chat_name' in chat_cols:
                chat_updates.append("chat_name = COALESCE(tbl_chats.chat_name, excluded.chat_name)")
            if 'last_backup_offset_id' in chat_cols:
                chat_updates.append("last_backup_offset_id = MAX(COALESCE(tbl_chats.last_backup_offset_id, 0), COALESCE(excluded.last_backup_offset_id, 0))")
            if 'min_downloaded_id' in chat_cols:
                chat_updates.append("""min_downloaded_id = CASE
                    WHEN tbl_chats.min_downloaded_id IS NULL OR tbl_chats.min_downloaded_id = 0 THEN excluded.min_downloaded_id
                    WHEN excluded.min_downloaded_id IS NULL OR excluded.min_downloaded_id = 0 THEN tbl_chats.min_downloaded_id
                    ELSE MIN(tbl_chats.min_downloaded_id, excluded.min_downloaded_id)
                END""")
            if 'max_downloaded_id' in chat_cols:
                chat_updates.append("max_downloaded_id = MAX(COALESCE(tbl_chats.max_downloaded_id, 0), COALESCE(excluded.max_downloaded_id, 0))")
            if 'server_top_id' in chat_cols:
                chat_updates.append("server_top_id = MAX(COALESCE(tbl_chats.server_top_id, 0), COALESCE(excluded.server_top_id, 0))")

            chat_update_sql = ("DO UPDATE SET " + ",\n".join(chat_updates)) if chat_updates else "DO NOTHING"
            chat_update_where = """WHERE (
                (tbl_chats.chat_name IS NULL AND excluded.chat_name IS NOT NULL)
                OR COALESCE(excluded.last_backup_offset_id, 0) > COALESCE(tbl_chats.last_backup_offset_id, 0)
                OR (excluded.min_downloaded_id IS NOT NULL AND (tbl_chats.min_downloaded_id IS NULL OR excluded.min_downloaded_id < tbl_chats.min_downloaded_id))
                OR COALESCE(excluded.max_downloaded_id, 0) > COALESCE(tbl_chats.max_downloaded_id, 0)
                OR COALESCE(excluded.server_top_id, 0) > COALESCE(tbl_chats.server_top_id, 0)
            )"""

            cursor.execute(f"""
                INSERT INTO tbl_chats ({cols_sql})
                SELECT {cols_sql} FROM src.tbl_chats WHERE 1=1
                ON CONFLICT(chat_id) {chat_update_sql} {chat_update_where}
            """)

        # Merge tbl_users (Anti-Interference Guarantee + WHERE change filter)
        user_cols = _get_common_cols('tbl_users')
        if user_cols:
            cols_sql = ', '.join(user_cols)
            user_updates = []
            user_where_clauses = []
            for col in ['first_name', 'last_name', 'username', 'avatar_path', 'about', 'full_profile_json', 'birthday_json', 'business_json', 'gifts_json', 'phone']:
                if col in user_cols:
                    user_updates.append(f"{col} = COALESCE(tbl_users.{col}, excluded.{col})")
                    user_where_clauses.append(f"(tbl_users.{col} IS NULL AND excluded.{col} IS NOT NULL)")
            user_update_sql = ("DO UPDATE SET " + ",\n".join(user_updates)) if user_updates else "DO NOTHING"
            user_update_where = ("WHERE " + " OR ".join(user_where_clauses)) if user_where_clauses else ""

            cursor.execute(f"""
                INSERT INTO tbl_users ({cols_sql})
                SELECT {cols_sql} FROM src.tbl_users WHERE 1=1
                ON CONFLICT(user_id) {user_update_sql} {user_update_where}
            """)

        # Pre-count source messages to allow granular progress
        cursor.execute("SELECT chat_id, COUNT(*), MIN(message_id), MAX(message_id) FROM src.tbl_messages GROUP BY chat_id")
        src_chats = cursor.fetchall()
        total_src_msgs = sum(r[1] for r in src_chats) if src_chats else 0
        processed_msgs = 0

        # Merge tbl_messages (with non-destructive enrichment & WHERE filter)
        msg_cols = _get_common_cols('tbl_messages')
        if msg_cols and src_chats:
            cols_sql = ', '.join(msg_cols)
            msg_updates = []
            msg_where_clauses = []
            for enrich_col in ['media_local_path', 'thumbnail_local_path', 'video_cover_path', 'transcription_text', 'transcription_lang', 'waveform_data']:
                if enrich_col in msg_cols:
                    msg_updates.append(f"{enrich_col} = COALESCE(tbl_messages.{enrich_col}, excluded.{enrich_col})")
                    msg_where_clauses.append(f"(tbl_messages.{enrich_col} IS NULL AND excluded.{enrich_col} IS NOT NULL)")
            if 'file_size' in msg_cols:
                msg_updates.append("file_size = MAX(COALESCE(tbl_messages.file_size, 0), COALESCE(excluded.file_size, 0))")
                msg_where_clauses.append("COALESCE(excluded.file_size, 0) > COALESCE(tbl_messages.file_size, 0)")
            if 'duration' in msg_cols:
                msg_updates.append("duration = MAX(COALESCE(tbl_messages.duration, 0), COALESCE(excluded.duration, 0))")
                msg_where_clauses.append("COALESCE(excluded.duration, 0) > COALESCE(tbl_messages.duration, 0)")

            msg_update_sql = ("DO UPDATE SET " + ",\n".join(msg_updates)) if msg_updates else "DO NOTHING"
            msg_update_where = ("WHERE " + " OR ".join(msg_where_clauses)) if msg_where_clauses else ""

            # Sliced atomic upsert & FTS5 sync per chat in 25,000 ID chunks
            CHUNK_SIZE = 25000
            for c_idx, c_row in enumerate(src_chats, start=1):
                c_id = c_row[0]
                c_min = c_row[2] or 1
                c_max = c_row[3] or 1

                curr_start = c_min
                while curr_start <= c_max:
                    curr_end = curr_start + CHUNK_SIZE - 1

                    # 1. Upsert message slice
                    cursor.execute(f"""
                        INSERT INTO tbl_messages ({cols_sql})
                        SELECT {cols_sql} FROM src.tbl_messages
                        WHERE chat_id = ? AND message_id BETWEEN ? AND ?
                        ON CONFLICT(message_id, chat_id) {msg_update_sql} {msg_update_where}
                    """, (c_id, curr_start, curr_end))

                    # 2. Sync FTS5 for this slice with unary-plus affinity stripping
                    if 'text' in msg_cols:
                        tr_expr = "COALESCE(m.transcription_text, '')" if 'transcription_text' in msg_cols else "''"
                        tr_cond = "OR (m.transcription_text IS NOT NULL AND m.transcription_text != '')" if 'transcription_text' in msg_cols else ""
                        cursor.execute(f"""
                            INSERT INTO tbl_messages_fts (message_id, chat_id, text)
                            SELECT m.message_id, m.chat_id, TRIM(COALESCE(m.text, '') || ' ' || {tr_expr})
                            FROM src.tbl_messages m
                            WHERE m.chat_id = ? AND m.message_id BETWEEN ? AND ?
                              AND ((m.text IS NOT NULL AND m.text != '') {tr_cond})
                              AND NOT EXISTS (
                                  SELECT 1 FROM tbl_messages_fts_content f
                                  WHERE f.c1 = +m.chat_id AND f.c0 = +m.message_id
                              )
                        """, (c_id, curr_start, curr_end))

                    processed_msgs = min(total_src_msgs, processed_msgs + CHUNK_SIZE)
                    if total_src_msgs > 0 and progress_callback:
                        pct = 20 + int(50 * (processed_msgs / total_src_msgs))
                        progress_callback(
                            "messages",
                            processed_msgs,
                            total_src_msgs,
                            f"Merging messages & FTS: {processed_msgs:,} / {total_src_msgs:,} (Chat {c_idx}/{len(src_chats)})",
                            pct
                        )

                    curr_start = curr_end + 1

        if progress_callback:
            progress_callback("aux", processed_msgs, total_src_msgs, "Merging participants and auxiliary tables...", 70)

        # Merge tbl_chat_participants (preserving earliest entered_at, latest status/rank)
        part_cols = _get_common_cols('tbl_chat_participants')
        if part_cols:
            cols_sql = ', '.join(part_cols)
            part_updates = []
            part_where_clauses = []
            for col in ['status', 'admin_rank', 'exit_reason', 'kicked_by_user_id', 'invited_by_user_id', 'updated_at']:
                if col in part_cols:
                    part_updates.append(f"{col} = COALESCE(excluded.{col}, tbl_chat_participants.{col})")
                    part_where_clauses.append(f"(tbl_chat_participants.{col} IS NULL AND excluded.{col} IS NOT NULL)")
            for col in ['entered_at', 'first_seen_at']:
                if col in part_cols:
                    part_updates.append(f"{col} = COALESCE(tbl_chat_participants.{col}, excluded.{col})")
                    part_where_clauses.append(f"(excluded.{col} IS NOT NULL AND (tbl_chat_participants.{col} IS NULL OR excluded.{col} < tbl_chat_participants.{col}))")
            if 'left_at' in part_cols:
                part_updates.append("left_at = COALESCE(excluded.left_at, tbl_chat_participants.left_at)")
            part_update_sql = ("DO UPDATE SET " + ",\n".join(part_updates)) if part_updates else "DO NOTHING"
            part_update_where = ("WHERE " + " OR ".join(part_where_clauses)) if part_where_clauses else ""

            try:
                cursor.execute(f"""
                    INSERT INTO tbl_chat_participants ({cols_sql})
                    SELECT {cols_sql} FROM src.tbl_chat_participants WHERE 1=1
                    ON CONFLICT(chat_id, user_id) {part_update_sql} {part_update_where}
                """)
            except Exception as part_err:
                logging.warning(f"[DB Merge] tbl_chat_participants merge note: {part_err}")

        # Merge tbl_participant_events (excluding auto-increment primary key 'id')
        try:
            pe_cols = [c for c in _get_common_cols('tbl_participant_events') if c != 'id']
            if pe_cols:
                cols_sql = ', '.join(pe_cols)
                cursor.execute(f"""
                    INSERT INTO tbl_participant_events ({cols_sql})
                    SELECT {cols_sql} FROM src.tbl_participant_events s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM tbl_participant_events m
                        WHERE m.chat_id = s.chat_id
                          AND m.date = s.date
                          AND m.event_type = s.event_type
                          AND COALESCE(m.user_id, 0) = COALESCE(s.user_id, 0)
                    )
                """)
        except Exception as pe_err:
            logging.warning(f"[DB Merge] tbl_participant_events merge note: {pe_err}")

        # Merge Auxiliary tables
        aux_tables = [
            'tbl_bookmarks', 'tbl_custom_emoji', 'tbl_admin_log',
            'tbl_stars_transactions', 'tbl_sponsored_messages',
            'tbl_folders', 'tbl_folder_chats'
        ]
        for t in aux_tables:
            try:
                t_cols = _get_common_cols(t)
                if t_cols:
                    cols_sql = ', '.join(t_cols)
                    cursor.execute(f"INSERT OR IGNORE INTO {t} ({cols_sql}) SELECT {cols_sql} FROM src.{t}")
            except Exception as aux_err:
                logging.warning(f"[DB Merge] Aux table {t} merge note: {aux_err}")

        if progress_callback:
            progress_callback("bounds", processed_msgs, total_src_msgs, "Recalculating affected chat bounds...", 75)

        # Recompute bounds ONLY for affected chats
        for c_row in src_chats:
            try:
                recalculate_chat_bounds(cursor, c_row[0])
            except Exception:
                pass

        cursor.execute("COMMIT")
        cursor.execute("DETACH DATABASE src")
        core.IS_DB_MERGING = False

        # Fast Media Sync via os.scandir set diffing + 1MB sequential buffer
        media_copied = 0
        media_skipped = 0
        media_bytes_copied = 0

        if validated_src_media and os.path.isdir(validated_src_media):
            if progress_callback:
                progress_callback("media_scan", 0, 0, "Scanning media folders...", 80)

            os.makedirs(media_dest_root, exist_ok=True)
            real_dest_root = os.path.realpath(media_dest_root)
            copy_queue = []
            total_bytes_to_copy = 0

            def _scan_dir(current_src_dir, current_rel_path):
                nonlocal media_skipped, total_bytes_to_copy
                dest_dir = os.path.join(media_dest_root, current_rel_path) if current_rel_path else media_dest_root

                existing_in_dest = set()
                if os.path.isdir(dest_dir):
                    try:
                        with os.scandir(dest_dir) as d_entries:
                            for de in d_entries:
                                if de.is_file(follow_symlinks=False):
                                    existing_in_dest.add(de.name)
                    except Exception:
                        pass

                try:
                    with os.scandir(current_src_dir) as entries:
                        for entry in entries:
                            if entry.name.startswith('.'):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                sub_rel = os.path.join(current_rel_path, entry.name) if current_rel_path else entry.name
                                if '..' in PurePath(sub_rel).parts:
                                    continue
                                _scan_dir(entry.path, sub_rel)
                            elif entry.is_file(follow_symlinks=False):
                                ext_low = os.path.splitext(entry.name)[1].lower()
                                if ext_low in _BLOCKED_MEDIA_SYNC_EXTS:
                                    continue
                                if entry.name in existing_in_dest:
                                    media_skipped += 1
                                    continue
                                
                                try:
                                    f_size = entry.stat(follow_symlinks=False).st_size
                                except Exception:
                                    f_size = 0
                                dst_file = os.path.join(dest_dir, entry.name)
                                copy_queue.append((entry.path, dst_file, f_size))
                                total_bytes_to_copy += f_size
                except Exception as scan_err:
                    logging.warning(f"[DB Merge] Media scan warning in {current_src_dir}: {scan_err}")

            _scan_dir(validated_src_media, "")

            total_files_to_copy = len(copy_queue)
            for idx, (s_path, d_path, f_sz) in enumerate(copy_queue, start=1):
                try:
                    os.makedirs(os.path.dirname(d_path), exist_ok=True)
                    with open(s_path, 'rb') as fsrc, open(d_path, 'wb') as fdst:
                        shutil.copyfileobj(fsrc, fdst, length=1048576)
                    try:
                        shutil.copystat(s_path, d_path)
                    except Exception:
                        pass
                    media_copied += 1
                    media_bytes_copied += f_sz
                except Exception as copy_err:
                    logging.warning(f"[DB Merge] File copy failed {s_path} -> {d_path}: {copy_err}")

                if progress_callback and (idx % 10 == 0 or idx == total_files_to_copy):
                    pct = 80 + int(19 * (idx / max(1, total_files_to_copy)))
                    mb_copied = media_bytes_copied / (1024 * 1024)
                    mb_total = total_bytes_to_copy / (1024 * 1024)
                    progress_callback(
                        "media_copy",
                        idx,
                        total_files_to_copy,
                        f"Copying media: {idx:,} / {total_files_to_copy:,} files ({mb_copied:.1f} MB / {mb_total:.1f} MB) • {media_skipped:,} skipped",
                        pct
                    )

        if progress_callback:
            progress_callback("done", 100, 100, "Database merged successfully!", 100)

        return {
            "status": "ok",
            "snapshot_backup": snapshot_bak,
            "media_copied": media_copied,
            "media_skipped": media_skipped,
            "media_bytes_copied": media_bytes_copied
        }

    except Exception as e:
        core.IS_DB_MERGING = False
        if cursor:
            try: cursor.execute("ROLLBACK")
            except Exception: pass
            try: cursor.execute("DETACH DATABASE src")
            except Exception: pass
        raise e

    finally:
        core.IS_DB_MERGING = False
        if cursor:
            try: cursor.close()
            except Exception: pass
        if main_conn:
            try: main_conn.close()
            except Exception: pass
        import gc
        gc.collect()

def start_merge_background(source_db_path, source_media_dir=None):
    """Launches database merger on background thread with live state reporting."""
    with MERGE_STATUS_LOCK:
        if MERGE_STATUS["status"] == "running":
            raise ValueError("Another database merge is already in progress.")
        MERGE_STATUS.update({
            "status": "running",
            "stage": "starting",
            "percent": 0,
            "detail": "Initializing background merger...",
            "db_stats": {"messages_merged": 0, "messages_total": 0, "chats_merged": 0, "chats_total": 0},
            "media_stats": {"copied": 0, "total": 0, "bytes_copied": 0, "total_bytes": 0, "skipped": 0},
            "snapshot_backup": None,
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None
        })

    def _worker():
        try:
            res = merge_database_file(
                source_db_path,
                source_media_dir=source_media_dir,
                progress_callback=_update_merge_status
            )
            with MERGE_STATUS_LOCK:
                MERGE_STATUS["status"] = "completed"
                MERGE_STATUS["percent"] = 100
                MERGE_STATUS["stage"] = "done"
                MERGE_STATUS["detail"] = f"Database merged successfully! Backup snapshot: {res.get('snapshot_backup')}"
                MERGE_STATUS["snapshot_backup"] = res.get("snapshot_backup")
                MERGE_STATUS["media_stats"]["copied"] = res.get("media_copied", 0)
                MERGE_STATUS["media_stats"]["skipped"] = res.get("media_skipped", 0)
                MERGE_STATUS["media_stats"]["bytes_copied"] = res.get("media_bytes_copied", 0)
                MERGE_STATUS["finished_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logging.error(f"[DB Merger] Background merge failed: {e}", exc_info=True)
            with MERGE_STATUS_LOCK:
                MERGE_STATUS["status"] = "error"
                MERGE_STATUS["error"] = str(e)
                MERGE_STATUS["detail"] = f"Merge error: {e}"
                MERGE_STATUS["finished_at"] = datetime.now(timezone.utc).isoformat()

    t = threading.Thread(target=_worker, daemon=True, name="DBMergerThread")
    t.start()
    return {"status": "started"}
