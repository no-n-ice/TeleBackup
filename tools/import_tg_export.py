#!/usr/bin/env python3
"""
tools/import_tg_export.py — Imports official Telegram Desktop JSON exports (result.json)
into TeleBackup database and media structure.

Usage:
    python tools/import_tg_export.py "C:/path/to/TelegramExport_2025"             # Dry-run preview
    python tools/import_tg_export.py "C:/path/to/TelegramExport_2025" --execute   # Commits import
"""

import os
import sys
import json
import argparse
import sqlite3
import shutil
from pathlib import PurePath
from datetime import datetime

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import core
from core import helpers

def clean_sender_id(raw_id):
    if not raw_id:
        return 0
    s = str(raw_id).strip()
    if s.startswith('user'):
        s = s[4:]
    elif s.startswith('channel'):
        s = '-' + s[7:]
    try:
        return int(s)
    except ValueError:
        return 0

def flatten_tgd_text(raw_text):
    """
    TGD text field can be either a plain string or a list of items:
    ["plain ", {"type": "bold", "text": "bold text"}, ...]
    """
    if isinstance(raw_text, str):
        return raw_text
    if isinstance(raw_text, list):
        out = []
        for item in raw_text:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and 'text' in item:
                out.append(str(item['text']))
        return ''.join(out)
    return ''

def detect_media_type(msg):
    m_type = msg.get('media_type')
    if m_type == 'voice_message':
        return 'voice'
    if m_type == 'video_message':
        return 'round_video'
    if m_type == 'animation':
        return 'gif'
    if m_type == 'video_file':
        return 'video'
    if m_type == 'audio_file':
        return 'audio'
    if m_type == 'sticker':
        return 'sticker'

    photo = msg.get('photo')
    if photo:
        return 'photo'

    file_p = msg.get('file')
    if file_p:
        ext = os.path.splitext(file_p)[1].lower()
        if ext in ('.mp4', '.mov', '.avi', '.mkv'):
            return 'video'
        if ext in ('.mp3', '.ogg', '.m4a', '.flac', '.wav'):
            return 'audio'
        if ext in ('.webp', '.tgs'):
            return 'sticker'
        if ext in ('.jpg', '.jpeg', '.png', '.gif'):
            return 'photo'
        return 'document'

    if msg.get('poll'):
        return 'poll'
    if msg.get('location_information'):
        return 'geo'

    return None

def _is_safe_subpath(child_path, parent_real):
    try:
        return os.path.commonpath([parent_real, os.path.realpath(child_path)]) == parent_real
    except Exception:
        return False

def import_tg_export(export_dir, custom_chat_id=None, execute=False, use_hardlinks=True):
    if not export_dir or '..' in PurePath(export_dir).parts or os.path.islink(export_dir):
        print("❌ Invalid or symlink export path refused.")
        return False

    abs_input = os.path.abspath(export_dir)
    if os.path.isfile(abs_input) and abs_input.lower().endswith('.json'):
        json_path = abs_input
        export_dir = os.path.dirname(abs_input)
    else:
        export_dir = abs_input
        json_path = os.path.join(export_dir, 'result.json')

    if os.path.islink(json_path) or not os.path.isfile(json_path):
        print(f"❌ Could not find result.json in: {export_dir}")
        return False

    real_export_dir = os.path.realpath(export_dir)
    real_project = os.path.realpath(core.PROJECT_ROOT)
    real_media_backup = os.path.realpath(core.ABS_MEDIA_BACKUP_DIR)
    if real_export_dir in (real_project, real_media_backup):
        print("❌ Refusing to import directly from active TeleBackup project or media_backup root.")
        return False

    print(f"[*] Reading {json_path}...")
    with open(json_path, 'r', encoding='utf-8', errors='replace') as f:
        data = json.load(f)

    chat_name = data.get('name') or "Imported Chat"
    raw_chat_id = data.get('id')
    chat_type = data.get('type')

    # Telegram Desktop export IDs: channels often don't have -100 prefix
    if custom_chat_id:
        chat_id = int(custom_chat_id)
    elif raw_chat_id:
        chat_id = int(raw_chat_id)
        if chat_type in ('public_channel', 'private_channel', 'public_supergroup', 'private_group'):
            if chat_id > 0:
                chat_id = int(f"-100{chat_id}")
    else:
        chat_id = -1009999999999

    messages = data.get('messages', [])
    print(f"[*] Chat Title: {chat_name}")
    print(f"[*] Target Chat ID: {chat_id} (Type: {chat_type})")
    print(f"[*] Messages in Export: {len(messages):,}")

    safe_chat_name = helpers.sanitize_folder_name(chat_name)
    safe_folder_name = f"{safe_chat_name}_{chat_id}"
    dest_chat_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, safe_folder_name)

    # Scan messages & users
    unique_users = {}
    db_records = []
    files_to_copy = []
    min_msg_id = None
    max_msg_id = None

    for m in messages:
        msg_id = m.get('id')
        if not msg_id:
            continue

        if min_msg_id is None or msg_id < min_msg_id:
            min_msg_id = msg_id
        if max_msg_id is None or msg_id > max_msg_id:
            max_msg_id = msg_id

        # Sender extraction
        sender_id = clean_sender_id(m.get('from_id') or m.get('actor_id'))
        sender_name = m.get('from') or m.get('actor')
        if sender_id and sender_name and sender_id not in unique_users:
            unique_users[sender_id] = sender_name

        text = flatten_tgd_text(m.get('text'))
        date_iso = m.get('date', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
        # Standardize ISO date format: 2023-01-01T12:00:00 -> 2023-01-01 12:00:00
        date_str = date_iso.replace('T', ' ')

        media_type = detect_media_type(m)
        media_src_rel = m.get('file') or m.get('photo')
        thumb_src_rel = m.get('thumbnail')

        media_local_path = None
        thumbnail_local_path = None
        file_size = 0

        # Physical file mapping (confined strictly inside real_export_dir)
        if media_src_rel and isinstance(media_src_rel, str):
            src_abs = os.path.join(real_export_dir, media_src_rel.replace('/', os.sep))
            if not os.path.islink(src_abs) and _is_safe_subpath(src_abs, real_export_dir) and os.path.isfile(src_abs):
                try: file_size = os.path.getsize(src_abs)
                except Exception: pass

                dest_datatype = media_type or 'documents'
                clean_base = helpers.sanitize_filename(os.path.basename(media_src_rel)) or 'media_file'
                dest_fn = f"{int(msg_id)} - {clean_base}"
                dest_abs = os.path.join(dest_chat_dir, dest_datatype, dest_fn)
                dest_rel = f"{safe_folder_name}/{dest_datatype}/{dest_fn}"

                files_to_copy.append((os.path.realpath(src_abs), dest_abs))
                media_local_path = dest_rel

        if thumb_src_rel and isinstance(thumb_src_rel, str):
            thumb_src_abs = os.path.join(real_export_dir, thumb_src_rel.replace('/', os.sep))
            if not os.path.islink(thumb_src_abs) and _is_safe_subpath(thumb_src_abs, real_export_dir) and os.path.isfile(thumb_src_abs):
                dest_datatype = media_type or 'documents'
                clean_thumb = helpers.sanitize_filename(os.path.basename(thumb_src_rel)) or 'thumb.jpg'
                dest_thumb_fn = f"{int(msg_id)} - {clean_thumb}"
                dest_thumb_abs = os.path.join(dest_chat_dir, dest_datatype, 'thumbs', dest_thumb_fn)
                dest_thumb_rel = f"{safe_folder_name}/{dest_datatype}/thumbs/{dest_thumb_fn}"

                files_to_copy.append((os.path.realpath(thumb_src_abs), dest_thumb_abs))
                thumbnail_local_path = dest_thumb_rel

        reply_to_id = m.get('reply_to_message_id')
        forward_info_json = json.dumps({'from': m.get('forwarded_from')}, ensure_ascii=False) if m.get('forwarded_from') else None
        entities_json = json.dumps(m.get('text_entities', []), ensure_ascii=False) if m.get('text_entities') else None

        db_records.append((
            msg_id, chat_id, sender_id, date_str, text,
            media_type, media_local_path, thumbnail_local_path,
            os.path.basename(media_src_rel) if media_src_rel else None,
            file_size, reply_to_id, forward_info_json, entities_json
        ))

    print(f"[*] Detected {len(unique_users)} distinct users/senders.")
    print(f"[*] Media files to transfer: {len(files_to_copy):,}")
    print(f"[*] ID Range: #{min_msg_id} - #{max_msg_id}")

    if not execute:
        print("\n[DRY RUN PREVIEW] No changes written to database or disk.")
        print(f"Run with --execute to import into TeleBackup.")
        return True

    print("\n[EXECUTE] Connecting to database...")
    conn = sqlite3.connect(core.DB_FILE)
    cursor = conn.cursor()

    # 1. Populate / Update Chat Entry
    cursor.execute("""
        INSERT INTO tbl_chats (chat_id, chat_name, safe_folder_name, chat_type, backup_status, min_downloaded_id, max_downloaded_id)
        VALUES (?, ?, ?, ?, 'complete', ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            chat_name = excluded.chat_name,
            safe_folder_name = COALESCE(tbl_chats.safe_folder_name, excluded.safe_folder_name),
            backup_status = 'complete',
            min_downloaded_id = MIN(COALESCE(tbl_chats.min_downloaded_id, excluded.min_downloaded_id), excluded.min_downloaded_id),
            max_downloaded_id = MAX(COALESCE(tbl_chats.max_downloaded_id, excluded.max_downloaded_id), excluded.max_downloaded_id)
    """, (chat_id, chat_name, safe_folder_name, chat_type or 'imported', min_msg_id or 1, max_msg_id or 1))

    # 2. Populate Users
    user_batch = [(uid, uname, '') for uid, uname in unique_users.items()]
    cursor.executemany("""
        INSERT OR IGNORE INTO tbl_users (user_id, first_name, last_name)
        VALUES (?, ?, ?)
    """, user_batch)

    # 3. Batch Insert Messages
    print(f"[*] Inserting {len(db_records):,} messages...")
    cursor.executemany("""
        INSERT OR IGNORE INTO tbl_messages (
            message_id, chat_id, sender_id, date, text,
            media_type, media_local_path, thumbnail_local_path,
            media_original_name, file_size, reply_to_msg_id,
            forward_info_json, entities_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, db_records)

    conn.commit()
    conn.close()

    # 4. Transfer media files
    print(f"[*] Transferring {len(files_to_copy):,} media files to archive directory...")
    transferred = 0
    for src, dst in files_to_copy:
        try:
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if use_hardlinks and helpers.try_create_hardlink(src, dst):
                    pass
                else:
                    shutil.copyfile(src, dst)
                transferred += 1
        except Exception as e:
            print(f"  [!] Transfer error for {src}: {e}")

    print(f"✅ Import successfully completed! Transferred {transferred:,} files and inserted {len(db_records):,} messages.")
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Import Telegram Desktop JSON export into TeleBackup.")
    parser.add_argument('export_dir', help="Path to folder containing result.json")
    parser.add_argument('--chat-id', type=int, default=None, help="Optional custom Telegram chat_id override")
    parser.add_argument('--execute', action='store_true', help="Execute import (default is dry-run)")
    parser.add_argument('--no-links', action='store_true', help="Do not attempt hardlinks (always copy)")
    args = parser.parse_args()

    import_tg_export(
        export_dir=args.export_dir,
        custom_chat_id=args.chat_id,
        execute=args.execute,
        use_hardlinks=not args.no_links
    )
