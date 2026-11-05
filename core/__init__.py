#!/usr/bin/env python3
"""
core/__init__.py — Config hub, constants, shared runtime flags, and package root.
"""
import logging
import os
import configparser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- Configuration Loading ---
config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config_path = os.path.join(PROJECT_ROOT, 'config.ini')
if not os.path.exists(config_path):
    config_path = 'config.ini'

if os.path.exists(config_path):
    try:
        config.read(config_path, encoding='utf-8')
    except Exception as e:
        print(f"[Core] Warning: Error reading config.ini ({e}). Using default settings where possible.")

# Telegram section
try:
    raw_api_id = config.get('Telegram', 'api_id', fallback='').strip()
    API_ID = int(raw_api_id) if raw_api_id else 0
except Exception:
    API_ID = 0

API_HASH = config.get('Telegram', 'api_hash', fallback='').strip()
SESSION_FILE = config.get('Telegram', 'session_name', fallback='backup_session').strip() or 'backup_session'

# Backup section
MEDIA_BACKUP_DIR = config.get('Backup', 'media_dir', fallback='media_backup').strip() or 'media_backup'

try:
    raw_max_size = config.get('Backup', 'max_file_size_mb', fallback='10').strip()
    MAX_FILE_SIZE_MB = int(raw_max_size) if raw_max_size else 10
except Exception:
    MAX_FILE_SIZE_MB = 10

try:
    raw_workers = config.get('Backup', 'download_workers', fallback='3').strip()
    DOWNLOAD_WORKERS = int(raw_workers) if raw_workers else 3
except Exception:
    DOWNLOAD_WORKERS = 3

DOWNLOAD_WORKERS = max(1, min(DOWNLOAD_WORKERS, 10))
ABS_MEDIA_BACKUP_DIR = os.path.join(PROJECT_ROOT, MEDIA_BACKUP_DIR) if not os.path.isabs(MEDIA_BACKUP_DIR) else os.path.abspath(MEDIA_BACKUP_DIR)
MAX_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024 if MAX_FILE_SIZE_MB > 0 else 0

GLOBAL_ABORT = False
IS_BULK_ARCHIVING = False
IS_DB_MERGING = False

import threading
_ABORT_LOCK = threading.Lock()
CANCELLED_CHATS = set()

def is_abort_requested(chat_id=None):
    """Checks whether global abort or per-chat cancellation has been requested."""
    if GLOBAL_ABORT:
        return True
    if chat_id is not None:
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            cid = chat_id
        with _ABORT_LOCK:
            return cid in CANCELLED_CHATS or str(cid) in CANCELLED_CHATS
    return False

def request_abort(chat_id=None):
    """Requests abort globally (chat_id=None) or for a specific chat."""
    global GLOBAL_ABORT
    with _ABORT_LOCK:
        if chat_id is None:
            GLOBAL_ABORT = True
        else:
            try:
                cid = int(chat_id)
            except (ValueError, TypeError):
                cid = chat_id
            CANCELLED_CHATS.add(cid)
            CANCELLED_CHATS.add(str(cid))

def clear_abort(chat_id=None):
    """Clears abort state globally (chat_id=None) or for a specific chat."""
    global GLOBAL_ABORT
    with _ABORT_LOCK:
        if chat_id is None:
            GLOBAL_ABORT = False
            CANCELLED_CHATS.clear()
        else:
            try:
                cid = int(chat_id)
            except (ValueError, TypeError):
                cid = chat_id
            CANCELLED_CHATS.discard(cid)
            CANCELLED_CHATS.discard(str(cid))

def restrict_file_permissions(fpath):
    """Best-effort restriction of sensitive file permissions (0o600 on POSIX)."""
    if not fpath or not os.path.exists(fpath):
        return
    try:
        os.chmod(fpath, 0o600)
    except Exception:
        pass

def load_or_generate_secret(filename, length=32):
    fpath = os.path.join(PROJECT_ROOT, filename)
    try:
        if os.path.exists(fpath):
            restrict_file_permissions(fpath)
            with open(fpath, 'r', encoding='utf-8') as f:
                val = f.read().strip()
                if val:
                    return val
        import secrets
        token = secrets.token_hex(length)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(token)
        restrict_file_permissions(fpath)
        return token
    except Exception:
        import secrets
        return secrets.token_hex(length)

# Re-export DB functions for backwards compatibility
from .db import (
    init_db, get_db_conn, get_live_db_conn, init_live_db, flush_live_db_to_main,
    run_db_migration, link_migrated_chats, get_chat_pipeline, load_all_users_from_db,
    get_offset_id, fetch_message_from_db, flush_messages_batch, recalculate_chat_bounds, delete_fts_messages,
    get_chat_sync_bounds, backfill_all_chat_bounds, DB_FILE, CACHE_FILE
)
from .helpers import (
    dt_converter, sanitize_filename, get_chat_id_variants, are_chat_ids_equal,
    get_entity_name, fetch_and_save_user, normalize_id, safe_fetch_full_user
)
from . import db
from . import helpers

def __getattr__(name):
    # Dynamic re-exports to eliminate circular imports between core, backup, live, and emoji
    import importlib
    if name in ('LIVE_CLIENT', 'LIVE_STARTING', 'LIVE_CALLBACK', 'LIVE_TASK', 'LIVE_REFRESH_TASK', 'start_live_archiver',
                'stop_live_archiver', 'auto_catchup_archived_chats',
                'is_chat_archived_in_main', 'is_message_already_logged', 'ACTIVE_USER_STATUS'):
        live_mod = importlib.import_module('.live', package=__name__)
        return getattr(live_mod, name)
    if name in ('async_run_backup', 'async_download_missing_chat_media', 'async_fetch_and_cache_dialogs', 'async_quick_fetch_and_cache_dialogs', 'async_resume_chat_download', 'async_reconcile_chat_gaps'):
        backup_mod = importlib.import_module('.backup', package=__name__)
        return getattr(backup_mod, name)
    if name in ('extract_custom_emoji_ids_from_message', 'async_fetch_and_save_custom_emojis'):
        emoji_mod = importlib.import_module('.emoji', package=__name__)
        return getattr(emoji_mod, name)
    if name == 'CACHE_FILE':
        return db.get_dialog_cache_file()
    if hasattr(db, name):
        return getattr(db, name)
    if hasattr(helpers, name):
        return getattr(helpers, name)
    backup_mod = importlib.import_module('.backup', package=__name__)
    if hasattr(backup_mod, name):
        return getattr(backup_mod, name)
    live_mod = importlib.import_module('.live', package=__name__)
    if hasattr(live_mod, name):
        return getattr(live_mod, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

