#!/usr/bin/env python3
"""
routes/viewer.py — Read-only viewer API routes (chats, messages, search, media, stats).
"""
import os
import re
import json
import logging
from pathlib import Path, PurePath
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_from_directory
import shutil
import asyncio
import time
import core
from core import db
from core import helpers
from core.helpers import dt_converter, normalize_id, get_chat_id_variants
from core import worker

MESSAGES_PER_PAGE = 50

_GLOBAL_DIR_STATS_CACHE = {'size': 0, 'files': 0, 'timestamp': 0}
_AVATAR_MTIME_CACHE = {}

viewer_bp = Blueprint('viewer', __name__)

@viewer_bp.route('/')
def index():
    # Файл теперь называется index.html без цифры 5
    return render_template('index.html')



_SAFE_INLINE_MEDIA_EXTS = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif', '.ico',
    '.mp4', '.webm', '.mov', '.ogv', '.m4v',
    '.ogg', '.oga', '.opus', '.mp3', '.m4a', '.wav', '.flac', '.aac',
    '.tgs',
})
_RASTER_THUMB_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif', '.ico', '.tif', '.tiff'})
_MEDIA_SANDBOX_CSP = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
_MAX_IMAGE_PIXELS = 64_000_000

def _is_path_within(child_path, root_path):
    try:
        real_root = os.path.normcase(os.path.realpath(root_path))
        real_child = os.path.normcase(os.path.realpath(child_path))
        return os.path.commonpath([real_root, real_child]) == real_root
    except Exception:
        return False

def _send_sandboxed_media(base_dir, rel_filename):
    clean_rel = str(rel_filename or '').replace('\\', '/').lstrip('/')
    target_path = os.path.normpath(os.path.join(base_dir, clean_rel))
    if not _is_path_within(target_path, core.ABS_MEDIA_BACKUP_DIR):
        return ("Forbidden", 403)
    real_target = os.path.realpath(target_path)
    if not os.path.isfile(real_target):
        return ("File not found", 404)
    ext = os.path.splitext(real_target)[1].lower()
    force_attachment = ext not in _SAFE_INLINE_MEDIA_EXTS
    resp = send_from_directory(base_dir, clean_rel, as_attachment=force_attachment)
    resp.headers['Content-Security-Policy'] = _MEDIA_SANDBOX_CSP
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp

@viewer_bp.route('/data/<path:filename>')
def serve_media(filename):
    return _send_sandboxed_media(core.ABS_MEDIA_BACKUP_DIR, filename)

@viewer_bp.route('/data_thumb/<path:filename>')
def serve_thumbnail(filename):
    """
    Serves downscaled cached thumbnails (~260px) to prevent massive DOM memory usage
    when browsing the chat photo grid.
    """
    clean_rel = str(filename or '').replace('\\', '/').lstrip('/')
    if not clean_rel:
        return ("File not found", 404)
    orig_path = os.path.normpath(os.path.join(core.ABS_MEDIA_BACKUP_DIR, clean_rel))
    if not _is_path_within(orig_path, core.ABS_MEDIA_BACKUP_DIR):
        return ("Forbidden", 403)
    if not os.path.isfile(os.path.realpath(orig_path)):
        return ("File not found", 404)

    if clean_rel.endswith('_thumb.jpg') or '/.thumbs/' in clean_rel or clean_rel.startswith('.thumbs/'):
        return _send_sandboxed_media(core.ABS_MEDIA_BACKUP_DIR, clean_rel)

    ext = os.path.splitext(orig_path)[1].lower()
    if ext not in _RASTER_THUMB_EXTS:
        return _send_sandboxed_media(core.ABS_MEDIA_BACKUP_DIR, clean_rel)

    rel_parent = os.path.dirname(clean_rel).strip('/')
    thumb_dir = os.path.normpath(os.path.join(core.ABS_MEDIA_BACKUP_DIR, ".thumbs", rel_parent)) if rel_parent else os.path.join(core.ABS_MEDIA_BACKUP_DIR, ".thumbs")
    if not _is_path_within(thumb_dir, core.ABS_MEDIA_BACKUP_DIR):
        return ("Forbidden", 403)
    base_name = os.path.splitext(os.path.basename(clean_rel))[0]
    thumb_filename = f"{base_name}.webp"
    thumb_path = os.path.join(thumb_dir, thumb_filename)
    rel_thumb = f".thumbs/{rel_parent}/{thumb_filename}" if rel_parent else f".thumbs/{thumb_filename}"

    if os.path.isfile(thumb_path):
        return _send_sandboxed_media(core.ABS_MEDIA_BACKUP_DIR, rel_thumb)

    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        os.makedirs(thumb_dir, exist_ok=True)
        with Image.open(orig_path) as img:
            img.thumbnail((260, 260), Image.Resampling.LANCZOS)
            img.save(thumb_path, "WEBP", quality=80)
        return _send_sandboxed_media(core.ABS_MEDIA_BACKUP_DIR, rel_thumb)
    except Exception as e:
        logging.debug(f"[serve_thumbnail] fallback to original for {filename}: {e}")
        return _send_sandboxed_media(core.ABS_MEDIA_BACKUP_DIR, clean_rel)

@viewer_bp.route('/api/check_chat_updates')
def check_chat_updates():
    chat_id_str = request.args.get('chat_id')
    if not chat_id_str:
        return jsonify({'error': 'chat_id missing'}), 400
    try:
        chat_id_int = int(chat_id_str)
    except ValueError:
        return jsonify({'error': 'invalid chat_id'}), 400

    conn = core.get_db_conn()
    cursor = conn.cursor()
    chat_variants = list(helpers.get_chat_id_variants(chat_id_int))
    placeholders = ','.join('?' for _ in chat_variants)
    cursor.execute(f"SELECT MAX(message_id) as max_id FROM tbl_messages WHERE chat_id IN ({placeholders})", chat_variants)
    row = cursor.fetchone()
    db_max_id = (row['max_id'] or 0) if row else 0

    cursor.execute(f"SELECT last_backup_offset_id FROM tbl_chats WHERE chat_id IN ({placeholders})", chat_variants)
    c_row = cursor.fetchone()
    if c_row and c_row['last_backup_offset_id'] and c_row['last_backup_offset_id'] > db_max_id:
        db_max_id = c_row['last_backup_offset_id']
    conn.close()

    client = getattr(core, 'LIVE_CLIENT', None)
    if not client or not client.is_connected() or not worker.LIVE_LOOP or not worker.LIVE_LOOP.is_running():
        return jsonify({'has_updates': False, 'new_count': 0, 'db_max_id': db_max_id})

    try:
        chat_entity = None
        for v in chat_variants:
            try:
                chat_entity = asyncio.run_coroutine_threadsafe(client.get_entity(v), worker.LIVE_LOOP).result(timeout=2.5)
                if chat_entity: break
            except Exception:
                pass

        if not chat_entity:
            return jsonify({'has_updates': False, 'new_count': 0, 'db_max_id': db_max_id})

        future = asyncio.run_coroutine_threadsafe(client.get_messages(chat_entity, limit=1), worker.LIVE_LOOP)
        latest_msgs = future.result(timeout=3.5)
        latest_id = latest_msgs[0].id if (latest_msgs and len(latest_msgs) > 0) else 0

        delta = max(0, latest_id - db_max_id)
        has_updates = (latest_id > db_max_id)
        return jsonify({
            'has_updates': has_updates,
            'new_count': delta,
            'latest_id': latest_id,
            'db_max_id': db_max_id
        })
    except Exception as e:
        logging.debug(f"[check_chat_updates] error: {e}")
        return jsonify({'has_updates': False, 'new_count': 0, 'db_max_id': db_max_id})

@viewer_bp.route('/api/sync_chat_delta', methods=['POST'])
def sync_chat_delta():
    data = request.json or {}
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    chat_id = int(chat_id)

    conn = core.get_db_conn()
    cur = conn.cursor()
    chat_variants = list(helpers.get_chat_id_variants(chat_id))
    ph = ','.join('?' for _ in chat_variants)
    try:
        cur.execute(f"SELECT archive_settings_json, last_backup_offset_id FROM tbl_chats WHERE chat_id IN ({ph})", chat_variants)
        row = cur.fetchone()
    except Exception:
        try:
            cur.execute(f"SELECT last_backup_offset_id FROM tbl_chats WHERE chat_id IN ({ph})", chat_variants)
            row = cur.fetchone()
        except Exception:
            row = None
    cur.execute(f"SELECT MAX(message_id) as max_id FROM tbl_messages WHERE chat_id IN ({ph})", chat_variants)
    m_row = cur.fetchone()
    conn.close()

    db_max_id = (m_row['max_id'] or 0) if m_row else 0
    if row and row['last_backup_offset_id'] and row['last_backup_offset_id'] > db_max_id:
        db_max_id = row['last_backup_offset_id']

    # Fast guard: if LIVE_CLIENT connected, check if Telegram has any new messages
    client = getattr(core, 'LIVE_CLIENT', None)
    if client and client.is_connected() and worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
        try:
            chat_entity = None
            for v in chat_variants:
                try:
                    chat_entity = asyncio.run_coroutine_threadsafe(client.get_entity(v), worker.LIVE_LOOP).result(timeout=2.0)
                    if chat_entity: break
                except Exception:
                    pass
            if chat_entity:
                top_msgs = asyncio.run_coroutine_threadsafe(client.get_messages(chat_entity, limit=1), worker.LIVE_LOOP).result(timeout=2.5)
                latest_id = top_msgs[0].id if (top_msgs and len(top_msgs) > 0) else 0
                if latest_id and latest_id <= db_max_id:
                    return jsonify({
                        'ok': True,
                        'already_synced': True,
                        'new_count': 0,
                        'latest_id': latest_id,
                        'db_max_id': db_max_id,
                        'message': 'Chat is already up to date'
                    })
        except Exception as e_chk:
            logging.debug(f"[sync_chat_delta] fast-check error: {e_chk}")

    settings = {}
    if row and row['archive_settings_json']:
        try: settings = json.loads(row['archive_settings_json'])
        except Exception: pass
    if not settings:
        settings = core.db.get_global_settings()
    settings['is_delta'] = True

    with worker.status_lock:
        if chat_id in worker.ARCHIVE_STATUS and worker.ARCHIVE_STATUS[chat_id].get('status') in ('running', 'queued'):
            return jsonify({'ok': True, 'message': 'Sync already in progress'})
        worker.ARCHIVE_STATUS[chat_id] = {
            "status": "queued",
            "processed": 0,
            "total": 0,
            "status_text": "Queued (Sync Delta)..."
        }
    worker.ARCHIVE_QUEUE.put({"id": chat_id, "total": 0, "settings": settings})
    init_msg = json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)})
    for q in list(worker.ARCHIVE_LISTENERS):
        try: q.put_nowait(init_msg)
        except Exception: pass

    return jsonify({'ok': True, 'message': 'Sync started'})

# --- Фон чата ---

@viewer_bp.route('/api/get_bg')
def get_bg():
    """Проверяет наличие пользовательского фона чата."""
    bg_path = os.path.join(core.ABS_MEDIA_BACKUP_DIR, 'custom_background.jpg')
    if os.path.exists(bg_path):
        return jsonify({'path': '/data/custom_background.jpg'})
    return jsonify({'path': None})

@viewer_bp.route('/api/upload_bg', methods=['POST'])
def upload_bg():
    """Загружает пользовательский фон чата (перезаписывает старый)."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        with Image.open(f.stream) as img:
            if (img.format or '').upper() not in {'JPEG', 'PNG', 'WEBP', 'GIF', 'BMP'}:
                return jsonify({'error': 'Unsupported image format'}), 400
            clean_img = ImageOps.exif_transpose(img).convert('RGB')
        os.makedirs(core.ABS_MEDIA_BACKUP_DIR, exist_ok=True)
        save_path = os.path.join(core.ABS_MEDIA_BACKUP_DIR, 'custom_background.jpg')
        clean_img.save(save_path, format='JPEG', quality=90)
    except Exception:
        return jsonify({'error': 'Invalid or corrupted image file'}), 400
    return jsonify({'path': '/data/custom_background.jpg'})

@viewer_bp.route('/api/delete_bg', methods=['POST'])
def delete_bg():
    """Удаляет пользовательский фон чата."""
    bg_path = os.path.join(core.ABS_MEDIA_BACKUP_DIR, 'custom_background.jpg')
    if os.path.exists(bg_path):
        os.remove(bg_path)
    return jsonify({'ok': True})

@viewer_bp.route('/api/chats')
def get_chats():
    core.flush_live_db_to_main()
    conn = core.get_db_conn()
    cursor = conn.cursor()
    # Показываем чат только если он самый актуальный (нигде не является old_id для успешной миграции)
    cursor.execute("""
        SELECT c1.chat_id, c1.chat_name, c1.safe_folder_name, c1.chat_avatar_path,
               c1.chat_about, c1.chat_creation_date, c1.linked_chat_id, c1.admin_list_json,
               c1.color_json, c1.profile_color_json, c1.chat_type,
               c1.wallpaper_path, c1.wallpaper_json, c1.pinned_msg_ids_json,
               c1.emoji_status_document_id, c1.emoji_status_json, c1.stats_json,
               u.is_bot AS user_is_bot
        FROM tbl_chats c1
        LEFT JOIN tbl_chats c2 ON c1.migrated_to_chat_id = c2.chat_id
        LEFT JOIN tbl_users u ON c1.chat_id = u.user_id
        WHERE (c1.migrated_to_chat_id IS NULL OR c2.chat_id IS NULL)
          AND c1.chat_name IS NOT NULL
          AND ((c1.last_backup_offset_id IS NOT NULL AND c1.last_backup_offset_id > 0) OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = c1.chat_id))
    """)
    chats = cursor.fetchall()
    conn.close()
    
    chat_list = []
    for c in chats:
        c_type = c['chat_type']
        if (not c_type or c_type == 'user') and c['user_is_bot']:
            c_type = 'bot'
        chat_entry = {
            "chat_id": c['chat_id'], "chat_name": c['chat_name'], 
            "safe_folder_name": c['safe_folder_name'], "chat_avatar_path": c['chat_avatar_path'],
            "chat_about": c['chat_about'], "chat_creation_date": c['chat_creation_date'],
            "linked_chat_id": c['linked_chat_id'], "chat_type": c_type or 'user',
            "wallpaper_path": c['wallpaper_path'] if 'wallpaper_path' in c.keys() else None,
            "emoji_status_document_id": c['emoji_status_document_id'] if 'emoji_status_document_id' in c.keys() else None
        }
        if c['admin_list_json']:
            try: chat_entry['admin_list'] = json.loads(c['admin_list_json'])
            except json.JSONDecodeError: chat_entry['admin_list'] = None
        else: chat_entry['admin_list'] = None

        for col in ['color_json', 'profile_color_json', 'wallpaper_json', 'pinned_msg_ids_json', 'emoji_status_json', 'stats_json']:
            if col in c.keys():
                clean = col.replace('_json', '')
                if c[col]:
                    try: chat_entry[clean] = json.loads(c[col])
                    except json.JSONDecodeError: chat_entry[clean] = None
                else: chat_entry[clean] = None

        chat_list.append(chat_entry)
    return jsonify(chat_list)

@viewer_bp.route('/api/users')
def get_users():
    core.flush_live_db_to_main()
    chat_id_str = request.args.get('chat_id')
    if not chat_id_str: return jsonify({"error": "chat_id is required"}), 400
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    pipeline = core.get_chat_pipeline(cursor, int(chat_id_str))
    placeholders = ','.join('?' for _ in pipeline)
    
    cursor.execute(f"""
        SELECT u.* FROM tbl_users u
        WHERE u.user_id IN (
            SELECT DISTINCT sender_id FROM tbl_messages WHERE chat_id IN ({placeholders}) AND sender_id IS NOT NULL
            UNION
            SELECT DISTINCT reply_sender_id FROM tbl_messages WHERE chat_id IN ({placeholders}) AND reply_sender_id IS NOT NULL
        )
    """, pipeline + pipeline)
    
    users_data = {}
    for r in cursor.fetchall():
        u_id = r['user_id']
        u_entry = {
            "first_name": r['first_name'], "last_name": r['last_name'],
            "username": r['username'], "avatar_path": r['avatar_path'],
            "is_bot": r['is_bot'] if 'is_bot' in r.keys() else 0,
            "is_premium": r['is_premium'] if 'is_premium' in r.keys() else 0,
            "is_restricted": r['is_restricted'] if 'is_restricted' in r.keys() else 0,
            "verified": r['verified'] if 'verified' in r.keys() else 0,
            "lang_code": r['lang_code'] if 'lang_code' in r.keys() else None,
            "is_scam": r['is_scam'] if 'is_scam' in r.keys() else 0,
            "is_fake": r['is_fake'] if 'is_fake' in r.keys() else 0,
            "usernames": json.loads(r['usernames_json']) if (r['usernames_json'] if 'usernames_json' in r.keys() else None) else None,
            "about": r['about'] if 'about' in r.keys() else None,
            "emoji_status_document_id": r['emoji_status_document_id'] if 'emoji_status_document_id' in r.keys() else None,
            "emoji_status": json.loads(r['emoji_status_json']) if (r['emoji_status_json'] if 'emoji_status_json' in r.keys() else None) else None,
            "birthday": json.loads(r['birthday_json']) if (r['birthday_json'] if 'birthday_json' in r.keys() else None) else None,
            "business": json.loads(r['business_json']) if (r['business_json'] if 'business_json' in r.keys() else None) else None,
            "gifts": json.loads(r['gifts_json']) if (r['gifts_json'] if 'gifts_json' in r.keys() else None) else None,
            "full_profile_updated_at": r['full_profile_updated_at'] if 'full_profile_updated_at' in r.keys() else None
        }
        if r['avatar_path']:
            rel_av = r['avatar_path']
            cached_mtime = _AVATAR_MTIME_CACHE.get(rel_av)
            if cached_mtime is None:
                try:
                    av_abs = os.path.join(core.ABS_MEDIA_BACKUP_DIR, rel_av)
                    cached_mtime = int(os.path.getmtime(av_abs)) if os.path.exists(av_abs) else 0
                except Exception:
                    cached_mtime = 0
                if len(_AVATAR_MTIME_CACHE) > 10000:
                    _AVATAR_MTIME_CACHE.clear()
                _AVATAR_MTIME_CACHE[rel_av] = cached_mtime
            if cached_mtime > 0:
                u_entry['avatar_mtime'] = cached_mtime
        live_status = getattr(core, 'ACTIVE_USER_STATUS', {}).get(u_id)
        if live_status:
            u_entry['live_status'] = live_status
        users_data[u_id] = u_entry
    conn.close()
    return jsonify(users_data)

_ALBUM_POS_CACHE = {}

def _get_album_position(cursor, group_id, reply_msg_id):
    """Returns (1-based index, total_count) of reply_msg_id within an album/media group."""
    if not group_id or not reply_msg_id or not cursor:
        return None, None
    key = (group_id, reply_msg_id)
    if key not in _ALBUM_POS_CACHE:
        try:
            cursor.execute(
                "SELECT message_id FROM tbl_messages WHERE group_id = ? ORDER BY message_id ASC",
                (group_id,)
            )
            rows = cursor.fetchall()
            all_ids = [r[0] for r in rows]
            total = len(all_ids)
            for idx, mid in enumerate(all_ids, 1):
                _ALBUM_POS_CACHE[(group_id, mid)] = (idx, total)
        except Exception:
            return None, None
        if len(_ALBUM_POS_CACHE) > 5000:
            _ALBUM_POS_CACHE.clear()
    return _ALBUM_POS_CACHE.get(key, (None, None))

def _format_reply_snippet(msg_dict, cursor=None):
    """
    Computes a precise snippet for a replied message.
    Supports exact media type, album index (e.g. Photo (6/10)), filenames, captions,
    and falls back to '[Unavailable]' only when the message is not found in DB.
    """
    text = (msg_dict.get('dynamic_reply_text') or '').strip()
    m_type = msg_dict.get('dynamic_reply_media_type')
    group_id = msg_dict.get('dynamic_reply_group_id')
    reply_msg_id = msg_dict.get('reply_to_msg_id')
    orig_name = msg_dict.get('dynamic_reply_media_name')
    is_voice = msg_dict.get('dynamic_reply_is_voice')
    is_round = msg_dict.get('dynamic_reply_is_round')

    # If replied message is completely missing from DB:
    if (msg_dict.get('dynamic_reply_sender_id') is None and 
        not text and not m_type and 
        msg_dict.get('dynamic_reply_forward_info_json') is None and
        msg_dict.get('dynamic_reply_post_author_name') is None):
        existing_snip = msg_dict.get('reply_snippet')
        if existing_snip and existing_snip not in ('[Media or unavailable]', '[Медиа]', '[Media]', '...', '[Unavailable]'):
            return existing_snip
        return "[Unavailable]"

    media_label = ""
    # 1. Album batch item with exact position
    if group_id and cursor and reply_msg_id:
        pos, total = _get_album_position(cursor, group_id, reply_msg_id)
        base_type = "Photo"
        if m_type == 'video':
            base_type = "Video"
        elif m_type == 'audio':
            base_type = "Audio"
        elif m_type in ('document', 'file'):
            base_type = "Document"
        elif m_type:
            base_type = m_type.capitalize()

        if pos and total and total > 1:
            media_label = f"{base_type} ({pos}/{total})"
        elif pos and total:
            media_label = base_type

    # 2. Standalone media item
    if not media_label and m_type:
        if m_type == 'photo':
            media_label = "Photo"
        elif m_type == 'video':
            media_label = "Video"
        elif is_voice or m_type == 'voice':
            media_label = "Voice message"
        elif is_round or m_type in ('round', 'round_video'):
            media_label = "Video message"
        elif m_type == 'sticker':
            media_label = "Sticker"
        elif m_type == 'gif':
            media_label = "GIF"
        elif m_type == 'audio':
            media_label = f"Audio: {orig_name}" if orig_name else "Audio"
        elif m_type in ('file', 'document'):
            media_label = f"Document: {orig_name}" if orig_name else "Document"
        elif m_type == 'poll':
            media_label = "Poll"
        elif m_type == 'contact':
            media_label = "Contact"
        elif m_type == 'location':
            media_label = "Location"
        elif m_type == 'story':
            media_label = "Story"
        else:
            media_label = m_type.capitalize()

    # 3. Combine with text caption if present
    if media_label:
        if text:
            return f"{media_label}: {text[:50]}"
        return media_label
    elif text:
        return text[:50]

    return "Message"

def _enrich_reply_info(msg_dict, peer_ids=None, cursor=None):
    """Resolves reply snippet, original sender ID and forwarded author name for replies."""
    if msg_dict.get('reply_to_msg_id'):
        curr_snip = msg_dict.get('reply_snippet')
        if not curr_snip or curr_snip in ('[Media or unavailable]', '[Медиа]', '[Media]', '...'):
            msg_dict['reply_snippet'] = _format_reply_snippet(msg_dict, cursor)
        elif msg_dict.get('dynamic_reply_group_id') and cursor:
            # Re-evaluate album items so exact batch index is computed
            msg_dict['reply_snippet'] = _format_reply_snippet(msg_dict, cursor)

        if msg_dict.get('dynamic_reply_media_type'):
            msg_dict['reply_media_type'] = msg_dict['dynamic_reply_media_type']
        if msg_dict.get('dynamic_reply_thumb_path'):
            msg_dict['reply_thumbnail_path'] = msg_dict['dynamic_reply_thumb_path']
        elif msg_dict.get('dynamic_reply_media_path'):
            msg_dict['reply_media_path'] = msg_dict['dynamic_reply_media_path']

        if msg_dict.get('dynamic_reply_sender_id') and not msg_dict.get('reply_sender_id'):
            msg_dict['reply_sender_id'] = msg_dict['dynamic_reply_sender_id']

        raw_rfwd = msg_dict.get('dynamic_reply_forward_info_json')
        if raw_rfwd:
            try:
                rfwd = json.loads(raw_rfwd) if isinstance(raw_rfwd, str) else raw_rfwd
                if isinstance(rfwd, dict):
                    orig_name = rfwd.get('from_name') or rfwd.get('header_from_name') or rfwd.get('post_author')
                    orig_id = rfwd.get('from_id') or rfwd.get('sender_id')
                    if not orig_id and isinstance(rfwd.get('from_id_raw'), dict):
                        orig_id = rfwd['from_id_raw'].get('user_id') or rfwd['from_id_raw'].get('channel_id') or rfwd['from_id_raw'].get('chat_id')
                    if orig_name:
                        msg_dict['reply_original_fwd_name'] = orig_name
                        msg_dict['reply_sender_first_name'] = orig_name
                        msg_dict['reply_sender_last_name'] = None
                    if orig_id:
                        msg_dict['reply_sender_id'] = orig_id
                        if peer_ids is not None:
                            peer_ids.add(orig_id)
                    elif orig_name:
                        msg_dict['reply_sender_id'] = None
            except Exception:
                pass
        elif msg_dict.get('dynamic_reply_post_author_name'):
            author = msg_dict['dynamic_reply_post_author_name']
            msg_dict['reply_original_fwd_name'] = author
            msg_dict['reply_sender_first_name'] = author
            msg_dict['reply_sender_last_name'] = None

def _collect_peer_ids_from_msg(msg_dict, peer_ids):
    if msg_dict.get('sender_id'): peer_ids.add(msg_dict['sender_id'])
    if msg_dict.get('reply_sender_id'): peer_ids.add(msg_dict['reply_sender_id'])
    if msg_dict.get('forward_info'):
        f = msg_dict['forward_info']
        for k in ['from_id', 'chat_id', 'sender_id', 'saved_from_id']:
            if f.get(k): peer_ids.add(f[k])
        raw_f = f.get('from_id_raw')
        if isinstance(raw_f, dict):
            for rk in ['channel_id', 'user_id', 'chat_id']:
                if raw_f.get(rk): peer_ids.add(raw_f[rk])
        raw_s = f.get('saved_from_peer')
        if isinstance(raw_s, dict):
            for sk in ['channel_id', 'user_id', 'chat_id']:
                if raw_s.get(sk): peer_ids.add(raw_s[sk])
    if msg_dict.get('via_bot_id'): peer_ids.add(msg_dict['via_bot_id'])
    if msg_dict.get('service_action') and isinstance(msg_dict['service_action'], dict):
        sa = msg_dict['service_action']
        if sa.get('user_id'): peer_ids.add(sa['user_id'])
        if sa.get('inviter_id'): peer_ids.add(sa['inviter_id'])
        if sa.get('users') and isinstance(sa['users'], list):
            for u in sa['users']:
                if u: peer_ids.add(u)

def _resolve_peers_map(cursor, peer_ids):
    users_map = {}
    if not peer_ids:
        return users_map
    ids_list = [str(pid) for pid in peer_ids if pid]
    if not ids_list:
        return users_map

    expanded_ids = set(ids_list)
    for pid in ids_list:
        try:
            n = int(pid)
            clean_n = abs(n)
            expanded_ids.add(str(clean_n))
            expanded_ids.add(str(-clean_n))
            if clean_n < 2000000000000:
                expanded_ids.add(str(-1000000000000 - clean_n))
        except Exception:
            pass

    expanded_list = list(expanded_ids)
    placeholders_u = ','.join('?' for _ in expanded_list)
    cursor.execute(f"SELECT * FROM tbl_users WHERE user_id IN ({placeholders_u})", expanded_list)
    for u in cursor.fetchall():
        u_dict = dict(u)
        if u_dict.get('emoji_status_document_id'):
            u_dict['emoji_status_document_id'] = str(u_dict['emoji_status_document_id'])
        uid_str = str(u['user_id'])
        clean_uid = uid_str.replace('-100', '').replace('-', '')
        for k in (uid_str, clean_uid, f"-{clean_uid}", f"-100{clean_uid}"):
            if k in expanded_ids:
                users_map[k] = u_dict

    placeholders_c = ','.join('?' for _ in expanded_list)
    cursor.execute(f"SELECT chat_id, chat_name, chat_avatar_path, emoji_status_document_id FROM tbl_chats WHERE chat_id IN ({placeholders_c})", expanded_list)
    for c in cursor.fetchall():
        cid = c['chat_id']
        cid_str = str(cid)
        clean_id = cid_str.replace('-100', '').replace('-', '')
        variations = {cid_str, str(abs(cid)), clean_id, f"-100{clean_id}", f"-{clean_id}"}
        status_id = str(c['emoji_status_document_id']) if c['emoji_status_document_id'] else None
        for key in variations:
            if key in expanded_ids and key not in users_map:
                users_map[key] = {
                    "user_id": c['chat_id'],
                    "first_name": c['chat_name'],
                    "avatar_path": c['chat_avatar_path'],
                    "emoji_status_document_id": status_id,
                    "is_chat": True
                }
    return users_map

def _extract_and_stringify_custom_emojis(messages):
    custom_emoji_ids = set()
    for msg in messages:
        if msg.get('entities') and isinstance(msg['entities'], list):
            for e in msg['entities']:
                if isinstance(e, dict) and e.get('_') == 'MessageEntityCustomEmoji' and e.get('document_id'):
                    custom_emoji_ids.add(e['document_id'])
                    e['document_id'] = str(e['document_id'])
        if msg.get('quote_entities') and isinstance(msg['quote_entities'], list):
            for e in msg['quote_entities']:
                if isinstance(e, dict) and e.get('_') == 'MessageEntityCustomEmoji' and e.get('document_id'):
                    custom_emoji_ids.add(e['document_id'])
                    e['document_id'] = str(e['document_id'])
        if msg.get('reactions') and isinstance(msg['reactions'], dict):
            rxns = msg['reactions']
            for r in (rxns.get('results', []) + rxns.get('recent_reactions', [])):
                rx = r.get('reaction', {})
                if rx.get('_') == 'ReactionCustomEmoji' and rx.get('document_id'):
                    custom_emoji_ids.add(rx['document_id'])
                    rx['document_id'] = str(rx['document_id'])
    return custom_emoji_ids

def _resolve_custom_emoji_map(cursor, custom_emoji_ids):
    if not custom_emoji_ids:
        return {}
    custom_emoji_map = db.get_custom_emojis_by_ids(cursor, custom_emoji_ids)
    string_map = {}
    for k, v in custom_emoji_map.items():
        v_dict = dict(v)
        if 'document_id' in v_dict:
            v_dict['document_id'] = str(v_dict['document_id'])
        string_map[str(k)] = v_dict

    missing_ids = [cid for cid in custom_emoji_ids if str(cid) not in string_map]
    if missing_ids and getattr(core, 'LIVE_CLIENT', None):
        try:
            if core.LIVE_CLIENT.is_connected():
                import asyncio
                from core.worker import LIVE_LOOP
                from core.emoji import async_fetch_and_save_custom_emojis
                asyncio.run_coroutine_threadsafe(
                    async_fetch_and_save_custom_emojis(core.LIVE_CLIENT, missing_ids),
                    LIVE_LOOP
                )
        except Exception:
            pass
    return string_map

async def async_resolve_missing_forward_channels(client, chat_id, missing_channel_msgs):
    """
    Resolves forwarded channel info and avatars by fetching the forwarded message
    via Telegram MTProto where the chats vector is automatically populated.
    missing_channel_msgs: list of (message_id, channel_id)
    """
    if not missing_channel_msgs or not client:
        return
    conn = None
    try:
        conn = core.get_db_conn()
        cur = conn.cursor()
        # Get all pipeline chat_id variants for correct DB lookups
        try:
            pipeline = core.get_chat_pipeline(cur, int(chat_id))
        except Exception:
            pipeline = list(helpers.get_chat_id_variants(chat_id))
        if not pipeline:
            pipeline = [chat_id]
        ph = ','.join('?' for _ in pipeline)

        avatars_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "avatars_global")
        os.makedirs(avatars_dir, exist_ok=True)
        from core import helpers

        for mid, cid in missing_channel_msgs:
            try:
                channel_ent = None
                cid_int = int(cid)
                # Try bare ID first (as stored in from_id_raw.channel_id)
                try:
                    from telethon.tl.types import PeerChannel
                    channel_ent = await client.get_entity(PeerChannel(cid_int))
                except Exception as e1:
                    logging.debug(f"[FwdResolver] get_entity(PeerChannel({cid_int})) failed: {e1}")
                    # Fallback: fetch the original message from our archived chat and extract chats vector
                    try:
                        tg_msgs = await client.get_messages(chat_id, ids=int(mid))
                        if tg_msgs:
                            # Single message object
                            msg_obj = tg_msgs if not isinstance(tg_msgs, list) else (tg_msgs[0] if tg_msgs else None)
                            if msg_obj:
                                fwd_obj = getattr(msg_obj, 'forward', None)
                                if fwd_obj and getattr(fwd_obj, 'chat', None):
                                    channel_ent = fwd_obj.chat
                                elif hasattr(msg_obj, '_chats') and msg_obj._chats:
                                    for ch in msg_obj._chats.values():
                                        if getattr(ch, 'id', None) == cid_int:
                                            channel_ent = ch
                                            break
                    except Exception as e2:
                        logging.debug(f"[FwdResolver] get_messages fallback failed for msg {mid}: {e2}")

                if channel_ent:
                    u_cache = {}
                    await helpers.fetch_and_save_user(channel_ent, client, cur, u_cache, avatars_dir)
                    ch_title = getattr(channel_ent, 'title', None) or getattr(channel_ent, 'first_name', None)
                    if ch_title:
                        # Update forward_info_json for ALL pipeline chat_id variants
                        cur.execute(f"SELECT chat_id, forward_info_json FROM tbl_messages WHERE message_id = ? AND chat_id IN ({ph})",
                                    [mid] + pipeline)
                        rows = cur.fetchall()
                        for row in rows:
                            if row['forward_info_json']:
                                try:
                                    f_dict = json.loads(row['forward_info_json'])
                                    f_dict['from_name'] = ch_title
                                    cur.execute("UPDATE tbl_messages SET forward_info_json = ? WHERE message_id = ? AND chat_id = ?",
                                                (json.dumps(f_dict), mid, row['chat_id']))
                                except Exception:
                                    pass
                    conn.commit()
                    logging.debug(f"[FwdResolver] Resolved channel {cid_int} -> '{ch_title}' for msg {mid}")
                else:
                    logging.debug(f"[FwdResolver] Could not resolve channel {cid_int} for msg {mid}")
            except Exception as e:
                logging.debug(f"[async_resolve_missing_forward_channels] Failed msg {mid}, cid {cid}: {e}")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

@viewer_bp.route('/api/messages')
def get_messages():
    core.flush_live_db_to_main()
    chat_id_str = request.args.get('chat_id')
    page = request.args.get('page', 1, type=int)
    if not chat_id_str: return jsonify({"error": "chat_id is required"}), 400
    
    offset = (page - 1) * MESSAGES_PER_PAGE
    conn = core.get_db_conn()
    cursor = conn.cursor()
    pipeline = core.get_chat_pipeline(cursor, int(chat_id_str))
    placeholders = ','.join('?' for _ in pipeline)
    
    # ДОБАВЛЕН LEFT JOIN: Вытягиваем текст, автора и медиа оригинального сообщения на лету!
    cursor.execute(f"""
        SELECT m.*, 
               rm.sender_id as dynamic_reply_sender_id, 
               rm.text as dynamic_reply_text,
               rm.forward_info_json as dynamic_reply_forward_info_json,
               rm.post_author_name as dynamic_reply_post_author_name,
               rm.media_type as dynamic_reply_media_type,
               rm.group_id as dynamic_reply_group_id,
               rm.media_original_name as dynamic_reply_media_name,
               rm.is_voice as dynamic_reply_is_voice,
               rm.is_round_video as dynamic_reply_is_round,
               rm.thumbnail_local_path as dynamic_reply_thumb_path,
               rm.media_local_path as dynamic_reply_media_path,
               ru.first_name as reply_sender_first_name,
               ru.last_name as reply_sender_last_name,
               u.first_name as sender_first_name,
               u.last_name as sender_last_name,
               u.avatar_path as sender_avatar_path
        FROM tbl_messages m
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id
        LEFT JOIN tbl_messages rm ON m.reply_to_msg_id = rm.message_id AND rm.chat_id IN ({placeholders})
        LEFT JOIN tbl_users ru ON rm.sender_id = ru.user_id
        WHERE m.chat_id IN ({placeholders}) 
        ORDER BY m.date DESC, m.message_id DESC 
        LIMIT ? OFFSET ?
    """, pipeline + pipeline + [MESSAGES_PER_PAGE + 1, offset])
    
    results = [dict(r) for r in cursor.fetchall()]
    
    has_older_same_date = False
    if len(results) > MESSAGES_PER_PAGE:
        oldest_in_page = results[MESSAGES_PER_PAGE - 1]
        older_message = results[MESSAGES_PER_PAGE]
        if oldest_in_page['date'][:10] == older_message['date'][:10]:
            has_older_same_date = True
        results = results[:MESSAGES_PER_PAGE]
        
    messages = []
    peer_ids = set()
    _linked_cache = {}
    for msg_dict in results:
        _enrich_reply_info(msg_dict, peer_ids, cursor)
                
        for key in ['entities_json', 'forward_info_json', 'service_action_json', 'poll_data_json', 'reactions_json', 'media_other_json', 'reply_markup_json', 'replies_json', 'restriction_reason_json', 'reply_to_peer_id_json', 'quote_entities_json', 'reply_from_json', 'reply_media_json', 'saved_peer_id_json', 'edit_history_json']:
            clean_key = key.replace('_json', '')
            if msg_dict[key]:
                try: msg_dict[clean_key] = json.loads(msg_dict[key])
                except json.JSONDecodeError: msg_dict[clean_key] = None
            else: msg_dict[clean_key] = None
            
        # Collect IDs for resolution
        _collect_peer_ids_from_msg(msg_dict, peer_ids)
        if msg_dict.get('reply_from'):
            rf = msg_dict['reply_from']
        _enrich_channel_post(cursor, msg_dict, _linked_cache)
        messages.append(msg_dict)
    
    # Resolve all collected peer IDs
    users_map = _resolve_peers_map(cursor, peer_ids)

    # Resolve custom emoji IDs for this page and stringify for JS 64-bit int precision
    custom_emoji_ids = _extract_and_stringify_custom_emojis(messages)
    custom_emoji_map = _resolve_custom_emoji_map(cursor, custom_emoji_ids)

    # Scavenge any missing forwarded channels on this page in the background
    missing_channel_msgs = []
    for msg in messages:
        fwd = msg.get('forward_info')
        if fwd:
            cid = None
            if fwd.get('from_id_raw') and isinstance(fwd['from_id_raw'], dict) and fwd['from_id_raw'].get('channel_id'):
                cid = fwd['from_id_raw']['channel_id']
            elif fwd.get('from_id'):
                cid = fwd['from_id']
            if cid:
                # Trigger resolution if channel has no name OR is not in users_map at all
                has_name = bool(fwd.get('from_name') or fwd.get('header_from_name'))
                cid_str = str(abs(int(cid)))
                in_users_map = (cid_str in users_map or f"-100{cid_str}" in users_map
                                or f"-{cid_str}" in users_map or str(cid) in users_map)
                if not has_name or not in_users_map:
                    missing_channel_msgs.append((msg['message_id'], cid))

    if missing_channel_msgs and getattr(core, 'LIVE_CLIENT', None):
        try:
            if core.LIVE_CLIENT.is_connected():
                import asyncio
                from core.worker import LIVE_LOOP
                asyncio.run_coroutine_threadsafe(
                    async_resolve_missing_forward_channels(core.LIVE_CLIENT, int(chat_id_str), missing_channel_msgs),
                    LIVE_LOOP
                )
        except Exception:
            pass
                
    show_sponsored = request.args.get('show_sponsored') == '1'
    sponsored_messages = []
    if show_sponsored:
        try:
            sponsored_messages = db.get_sponsored_messages(cursor, int(chat_id_str))
        except Exception:
            pass

    sync_bounds = {}
    try:
        sync_bounds = db.get_chat_sync_bounds(cursor, int(chat_id_str))
    except Exception as e:
        logging.debug(f"[api/messages] sync_bounds fetch error: {e}")

    conn.close()

    return jsonify({
        "messages": messages,
        "users": users_map,
        "custom_emoji": custom_emoji_map,
        "has_older_same_date": has_older_same_date,
        "sponsored_messages": sponsored_messages,
        "sync_bounds": sync_bounds
    })

@viewer_bp.route('/api/chat_sync_bounds')
def get_chat_sync_bounds_route():
    chat_id_str = request.args.get('chat_id')
    if not chat_id_str:
        return jsonify({"error": "chat_id required"}), 400
    conn = core.get_db_conn()
    cursor = conn.cursor()
    try:
        bounds = db.get_chat_sync_bounds(cursor, int(chat_id_str))
        return jsonify(bounds)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@viewer_bp.route('/api/single_message')
def get_single_message():
    core.flush_live_db_to_main()
    chat_id_str = request.args.get('chat_id')
    message_id_str = request.args.get('message_id')
    if not chat_id_str or not message_id_str: return jsonify({"error": "Missing params"}), 400

    conn = core.get_db_conn()
    cursor = conn.cursor()
    variants = list(get_chat_id_variants(chat_id_str))
    placeholders = ','.join('?' for _ in variants)

    cursor.execute(f"""
        SELECT m.*, 
               rm.sender_id as dynamic_reply_sender_id, 
               rm.text as dynamic_reply_text,
               rm.forward_info_json as dynamic_reply_forward_info_json,
               rm.post_author_name as dynamic_reply_post_author_name,
               rm.media_type as dynamic_reply_media_type,
               rm.group_id as dynamic_reply_group_id,
               rm.media_original_name as dynamic_reply_media_name,
               rm.is_voice as dynamic_reply_is_voice,
               rm.is_round_video as dynamic_reply_is_round,
               rm.thumbnail_local_path as dynamic_reply_thumb_path,
               rm.media_local_path as dynamic_reply_media_path,
               ru.first_name as reply_sender_first_name,
               ru.last_name as reply_sender_last_name,
               u.first_name as sender_first_name,
               u.last_name as sender_last_name,
               u.avatar_path as sender_avatar_path
        FROM tbl_messages m
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id
        LEFT JOIN tbl_messages rm ON m.reply_to_msg_id = rm.message_id AND rm.chat_id IN ({placeholders})
        LEFT JOIN tbl_users ru ON rm.sender_id = ru.user_id
        WHERE m.chat_id IN ({placeholders}) AND m.message_id = ?
    """, variants + variants + [int(message_id_str)])

    row = cursor.fetchone()
    if not row:
        # Check if message is currently in the live buffer DB before doing any flush or wait
        live_found = False
        try:
            live_conn = core.get_live_db_conn()
            live_cur = live_conn.cursor()
            live_cur.execute(f"SELECT 1 FROM tbl_messages WHERE chat_id IN ({placeholders}) AND message_id = ?", variants + [int(message_id_str)])
            live_found = live_cur.fetchone() is not None
            live_conn.close()
        except Exception:
            pass

        if live_found:
            core.flush_live_db_to_main()
            cursor.execute(f"""
                SELECT m.*, 
                       rm.sender_id as dynamic_reply_sender_id, 
                       rm.text as dynamic_reply_text,
                       rm.forward_info_json as dynamic_reply_forward_info_json,
                       rm.post_author_name as dynamic_reply_post_author_name,
                       rm.media_type as dynamic_reply_media_type,
                       rm.group_id as dynamic_reply_group_id,
                       rm.media_original_name as dynamic_reply_media_name,
                       rm.is_voice as dynamic_reply_is_voice,
                       rm.is_round_video as dynamic_reply_is_round,
                       rm.thumbnail_local_path as dynamic_reply_thumb_path,
                       rm.media_local_path as dynamic_reply_media_path,
                       ru.first_name as reply_sender_first_name,
                       ru.last_name as reply_sender_last_name,
                       u.first_name as sender_first_name,
                       u.last_name as sender_last_name,
                       u.avatar_path as sender_avatar_path
                FROM tbl_messages m
                LEFT JOIN tbl_users u ON m.sender_id = u.user_id
                LEFT JOIN tbl_messages rm ON m.reply_to_msg_id = rm.message_id AND rm.chat_id IN ({placeholders})
                LEFT JOIN tbl_users ru ON rm.sender_id = ru.user_id
                WHERE m.chat_id IN ({placeholders}) AND m.message_id = ?
            """, variants + variants + [int(message_id_str)])
            row = cursor.fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "Message not found"}), 404

    msg_dict = dict(row)
    peer_ids = set()
    _enrich_reply_info(msg_dict, peer_ids, cursor)
    for key in ['entities_json', 'forward_info_json', 'service_action_json', 'poll_data_json', 'reactions_json', 'media_other_json', 'reply_markup_json', 'replies_json', 'restriction_reason_json', 'reply_to_peer_id_json', 'quote_entities_json', 'reply_from_json', 'reply_media_json', 'saved_peer_id_json', 'edit_history_json']:
        clean_key = key.replace('_json', '')
        if msg_dict[key]:
            try: msg_dict[clean_key] = json.loads(msg_dict[key])
            except json.JSONDecodeError: msg_dict[clean_key] = None
        else: msg_dict[clean_key] = None

    _collect_peer_ids_from_msg(msg_dict, peer_ids)
    users_map = _resolve_peers_map(cursor, peer_ids)

    custom_emoji_ids = _extract_and_stringify_custom_emojis([msg_dict])
    custom_emoji_map = _resolve_custom_emoji_map(cursor, custom_emoji_ids)

    conn.close()
    return jsonify({"message": msg_dict, "users": users_map, "custom_emoji": custom_emoji_map})


@viewer_bp.route('/api/message_page')
def get_message_page():
    chat_id_str = request.args.get('chat_id')
    message_id = request.args.get('message_id')
    if not chat_id_str or not message_id: return jsonify({"error": "Missing params"}), 400
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT date FROM tbl_messages WHERE message_id = ? AND chat_id = ?", (message_id, chat_id_str))
    res = cursor.fetchone()
    if not res:
        conn.close()
        return jsonify({"error": "Message not found"}), 404
        
    target_date = res['date']
    pipeline = core.get_chat_pipeline(cursor, int(chat_id_str))
    placeholders = ','.join('?' for _ in pipeline)
    
    cursor.execute(f"""
        SELECT COUNT(message_id) FROM tbl_messages 
        WHERE chat_id IN ({placeholders}) AND (date > ? OR (date = ? AND message_id > ?))
    """, pipeline + [target_date, target_date, message_id])
    
    newer_count = cursor.fetchone()[0]
    conn.close()
    
    page = (newer_count // MESSAGES_PER_PAGE) + 1
    return jsonify({ "target_message_id": int(message_id), "target_date": target_date, "page": page })

def _enrich_channel_post(cursor, msg_dict, linked_cache=None):
    """Enriches a discussion group message with media/content from the original main channel post."""
    fwd = msg_dict.get('forward_info')
    if not fwd or not isinstance(fwd, dict):
        return
    sf_id = fwd.get('saved_from_msg_id') or fwd.get('channel_post')
    if not sf_id:
        return
    
    msg_dict['is_channel_post'] = True

    chan_id = None
    if fwd.get('from_id_raw') and isinstance(fwd['from_id_raw'], dict) and fwd['from_id_raw'].get('_') == 'PeerChannel':
        raw_c = fwd['from_id_raw'].get('channel_id')
        if raw_c:
            chan_id = raw_c if raw_c < 0 else -1000000000000 - raw_c
    elif fwd.get('saved_from_peer') and isinstance(fwd['saved_from_peer'], dict) and fwd['saved_from_peer'].get('_') == 'PeerChannel':
        raw_c = fwd['saved_from_peer'].get('channel_id')
        if raw_c:
            chan_id = raw_c if raw_c < 0 else -1000000000000 - raw_c

    cid = msg_dict.get('chat_id')
    if not chan_id and cid:
        if linked_cache is not None and cid in linked_cache:
            chan_id = linked_cache[cid]
        else:
            try:
                cursor.execute("SELECT linked_chat_id FROM tbl_chats WHERE chat_id = ?", (cid,))
                row = cursor.fetchone()
                if row and row['linked_chat_id']:
                    l_id = row['linked_chat_id']
                    chan_id = l_id if l_id < 0 else -1000000000000 - l_id
                if linked_cache is not None:
                    linked_cache[cid] = chan_id
            except Exception:
                pass

    if chan_id:
        try:
            cursor.execute("""
                SELECT media_local_path, thumbnail_local_path, media_type, is_voice, is_round_video, text
                FROM tbl_messages 
                WHERE chat_id = ? AND message_id = ? AND media_local_path IS NOT NULL
            """, (chan_id, sf_id))
            cp_row = cursor.fetchone()
            if cp_row:
                if not msg_dict.get('media_local_path') and cp_row['media_local_path']:
                    msg_dict['media_local_path'] = cp_row['media_local_path']
                if not msg_dict.get('thumbnail_local_path') and cp_row['thumbnail_local_path']:
                    msg_dict['thumbnail_local_path'] = cp_row['thumbnail_local_path']
                if not msg_dict.get('media_type') and cp_row['media_type']:
                    msg_dict['media_type'] = cp_row['media_type']
                if msg_dict.get('is_voice') is None or msg_dict.get('is_voice') == 0:
                    msg_dict['is_voice'] = cp_row['is_voice']
                if msg_dict.get('is_round_video') is None or msg_dict.get('is_round_video') == 0:
                    msg_dict['is_round_video'] = cp_row['is_round_video']
        except Exception as e:
            logging.debug(f"[_enrich_channel_post] error: {e}")

# --- Поиск ---
def _run_search_query(where_clause, params, order_by, limit=100, requires_fts=False):
    conn = core.get_db_conn()
    cursor = conn.cursor()
    sql = """
        SELECT m.*, u.first_name as sender_first_name, u.last_name as sender_last_name, 
               u.avatar_path as sender_avatar_path, c.chat_name, c.chat_avatar_path,
               ru.first_name as reply_sender_first_name, ru.last_name as reply_sender_last_name,
               rm.sender_id as dynamic_reply_sender_id,
               rm.text as dynamic_reply_text,
               rm.forward_info_json as dynamic_reply_forward_info_json,
               rm.post_author_name as dynamic_reply_post_author_name,
               rm.media_type as dynamic_reply_media_type,
               rm.group_id as dynamic_reply_group_id,
               rm.media_original_name as dynamic_reply_media_name,
               rm.is_voice as dynamic_reply_is_voice,
               rm.is_round_video as dynamic_reply_is_round,
               rm.thumbnail_local_path as dynamic_reply_thumb_path,
               rm.media_local_path as dynamic_reply_media_path
        FROM tbl_messages m
        JOIN tbl_chats c ON m.chat_id = c.chat_id
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id 
        LEFT JOIN tbl_messages rm ON m.reply_to_msg_id = rm.message_id AND rm.chat_id = m.chat_id
        LEFT JOIN tbl_users ru ON rm.sender_id = ru.user_id
    """
    if requires_fts:
         sql += " JOIN tbl_messages_fts fts ON m.message_id = fts.message_id AND m.chat_id = fts.chat_id "
    sql += f" WHERE {where_clause} ORDER BY {order_by} LIMIT {limit}"
    
    try:
        cursor.execute(sql, tuple(params))
        messages = cursor.fetchall()
    except Exception as e:
        print(f"[Search Error] {e}")
        conn.close()
        return []

    msg_list = []
    for r in messages:
        d = dict(r)
        _enrich_reply_info(d, None, cursor)
        for k in ['entities_json', 'forward_info_json', 'service_action_json', 'poll_data_json', 'reactions_json', 'media_other_json', 'reply_markup_json', 'replies_json', 'restriction_reason_json', 'reply_to_peer_id_json', 'quote_entities_json', 'reply_from_json', 'reply_media_json', 'saved_peer_id_json']:
            clean_k = k.replace('_json', '')
            if d[k]:
                try: d[clean_k] = json.loads(d[k])
                except: d[clean_k] = None
            else: d[clean_k] = None
            del d[k]
        _enrich_channel_post(cursor, d)
        msg_list.append(d)
    _extract_and_stringify_custom_emojis(msg_list)
    conn.close()
    return msg_list


def get_chat_pipeline_cached(chat_id):
    conn = db.get_db_conn()
    cur = conn.cursor()
    pipeline = core.get_chat_pipeline(cur, int(chat_id))
    conn.close()
    return pipeline

@viewer_bp.route('/api/search')
def search_messages():
    query = (request.args.get('query') or request.args.get('q') or '').strip()
    raw_chat_id = request.args.get('chat_id')
    chat_id = None
    if raw_chat_id and raw_chat_id.strip() and raw_chat_id.strip() not in ('null', 'undefined', '0', ''):
        try:
            chat_id = int(raw_chat_id.strip())
        except ValueError:
            chat_id = None
    date_start = request.args.get('date_start', '').strip()
    date_end = request.args.get('date_end', '').strip()
    media_filter = request.args.get('media_type', '').strip()
    from_user = request.args.get('from_user', '').strip()

    where_clauses = []
    params = []
    requires_fts = False
    fts_match_expr = None

    if query:
        import re
        tokens = re.findall(r'\w+', query)
        if tokens:
            fts_match_expr = " ".join(f'"{t}"' for t in tokens)
            where_clauses.append("fts.text MATCH ?")
            params.append(fts_match_expr)
            requires_fts = True
        else:
            where_clauses.append("(m.text LIKE ? OR m.transcription_text LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
            requires_fts = False

    if chat_id:
        chat_id_int = chat_id
        pipeline = get_chat_pipeline_cached(chat_id_int)
        placeholders = ','.join('?' for _ in pipeline)
        where_clauses.append(f"m.chat_id IN ({placeholders})")
        params.extend(pipeline)

    if date_start:
        where_clauses.append("m.date >= ?")
        params.append(date_start)

    if date_end:
        where_clauses.append("m.date <= ?")
        params.append(date_end + " 23:59:59")

    if media_filter:
        if media_filter == 'media':
            where_clauses.append("m.media_type IS NOT NULL AND m.media_type != ''")
        else:
            where_clauses.append("m.media_type = ?")
            params.append(media_filter)

    if from_user:
        where_clauses.append("(m.sender_id = ? OR m.sender_id IN (SELECT user_id FROM tbl_users WHERE first_name LIKE ? OR username LIKE ?))")
        try:
            u_id = int(from_user)
        except ValueError:
            u_id = -1
        params.extend([u_id, f"%{from_user}%", f"%{from_user}%"])

    if not where_clauses:
        return jsonify([])

    where_sql = " AND ".join(where_clauses)
    results = _run_search_query(where_sql, params, order_by="m.date DESC", limit=100, requires_fts=requires_fts)
    if not results and requires_fts and fts_match_expr:
        # Fallback to direct LIKE in case tokens were not indexed in FTS
        like_clauses = [c if c != "fts.text MATCH ?" else "(m.text LIKE ? OR m.transcription_text LIKE ?)" for c in where_clauses]
        like_params = []
        for p in params:
            if p == fts_match_expr:
                like_params.extend([f"%{query}%", f"%{query}%"])
            else:
                like_params.append(p)
        results = _run_search_query(" AND ".join(like_clauses), like_params, order_by="m.date DESC", limit=100, requires_fts=False)
    return jsonify(results)

@viewer_bp.route('/api/search_by_date')
def search_by_date():
    chat_id_str = request.args.get('chat_id')
    date_str = request.args.get('date', '')
    if not date_str: return jsonify({"error": "date is required"}), 400

    base_where, base_params = " 1=1 ", []
    if chat_id_str:
        base_where += " AND m.chat_id = ? "
        base_params.append(int(chat_id_str))

    where_clause = base_where + " AND m.date >= ? AND m.date < ? "
    msgs = _run_search_query(where_clause, base_params + [date_str, f"{date_str}~"], "m.date DESC", requires_fts=False)
    
    notification = None
    if not msgs:
        conn = core.get_db_conn()
        c = conn.cursor()
        c.execute(f"SELECT MIN(date) as min_d, MAX(date) as max_d FROM tbl_messages WHERE {'chat_id=?' if chat_id_str else '1=1'}", base_params)
        bounds = c.fetchone()
        conn.close()
        
        if bounds and bounds['min_d']:
            min_d, max_d = bounds['min_d'][:10], bounds['max_d'][:10]
            if date_str < min_d:
                notification, order = "too_old", "m.date ASC"
            elif date_str > max_d:
                notification, order = "too_new", "m.date DESC"
            else:
                notification = "empty_day"
                
            if notification in ("too_old", "too_new"):
                msgs = _run_search_query(base_where, base_params, order, 50, requires_fts=False)
                if notification == "too_old": msgs = msgs[::-1]

    return jsonify({"messages": msgs, "notification": notification})

@viewer_bp.route('/api/media_counts')
def get_chat_media_counts():
    chat_id_str = request.args.get('chat_id')
    if not chat_id_str:
        return jsonify({"photos": 0, "videos": 0, "gifs": 0, "voices": 0, "audio": 0, "files": 0, "links": 0, "surveys": 0})
    try:
        chat_id_int = int(chat_id_str)
    except (ValueError, TypeError):
        return jsonify({"photos": 0, "videos": 0, "gifs": 0, "voices": 0, "audio": 0, "files": 0, "links": 0, "surveys": 0})

    conn = core.get_db_conn()
    cursor = conn.cursor()
    pipeline = core.get_chat_pipeline(cursor, chat_id_int)
    placeholders = ','.join('?' for _ in pipeline)

    query = f"""
        SELECT 
            SUM(CASE WHEN media_type = 'photo' OR (media_type IS NULL AND media_mime_type LIKE 'image/%' AND media_mime_type NOT IN ('image/webp', 'image/gif') AND media_local_path IS NOT NULL AND REPLACE(media_local_path, '\\', '/') NOT LIKE '%/stickers/%') THEN 1 ELSE 0 END) as photos,
            SUM(CASE WHEN media_type = 'video' OR (media_type IS NULL AND media_mime_type LIKE 'video/%' AND media_local_path IS NOT NULL AND REPLACE(media_local_path, '\\', '/') NOT LIKE '%/gifs/%') THEN 1 ELSE 0 END) as videos,
            SUM(CASE WHEN media_type = 'gif' OR (media_type IS NULL AND (media_mime_type = 'image/gif' OR REPLACE(media_local_path, '\\', '/') LIKE '%/gifs/%') AND media_local_path IS NOT NULL) THEN 1 ELSE 0 END) as gifs,
            SUM(CASE WHEN media_type = 'voice' OR (media_type IS NULL AND (media_mime_type IN ('audio/ogg', 'audio/oga') OR is_voice = 1) AND media_local_path IS NOT NULL) THEN 1 ELSE 0 END) as voices,
            SUM(CASE WHEN media_type = 'audio' OR (media_type IS NULL AND media_mime_type LIKE 'audio/%' AND media_mime_type NOT IN ('audio/ogg', 'audio/oga') AND (is_voice IS NULL OR is_voice = 0) AND media_local_path IS NOT NULL) THEN 1 ELSE 0 END) as audio,
            SUM(CASE WHEN media_type IN ('document', 'file') OR (media_type IS NULL AND media_mime_type NOT LIKE 'image/%' AND media_mime_type NOT LIKE 'video/%' AND media_mime_type NOT LIKE 'audio/%' AND media_mime_type NOT IN ('application/x-tgsticker') AND media_local_path IS NOT NULL AND REPLACE(media_local_path, '\\', '/') NOT LIKE '%/stickers/%') THEN 1 ELSE 0 END) as files,
            SUM(CASE WHEN text LIKE '%http://%' OR text LIKE '%https://%' OR entities_json LIKE '%MessageEntityUrl%' OR entities_json LIKE '%MessageEntityTextUrl%' THEN 1 ELSE 0 END) as links,
            SUM(CASE WHEN poll_data_json IS NOT NULL OR media_type = 'poll' THEN 1 ELSE 0 END) as surveys
        FROM tbl_messages
        WHERE chat_id IN ({placeholders})
    """
    cursor.execute(query, pipeline)
    row = cursor.fetchone()
    conn.close()

    if row:
        return jsonify({
            "photos": row[0] or 0,
            "videos": row[1] or 0,
            "gifs": row[2] or 0,
            "voices": row[3] or 0,
            "audio": row[4] or 0,
            "files": row[5] or 0,
            "links": row[6] or 0,
            "surveys": row[7] or 0
        })
    return jsonify({"photos": 0, "videos": 0, "gifs": 0, "voices": 0, "audio": 0, "files": 0, "links": 0, "surveys": 0})

@viewer_bp.route('/api/media')
def get_chat_media():
    chat_id_str = request.args.get('chat_id')
    m_type = request.args.get('type', 'photos')
    page = request.args.get('page', 1, type=int)

    if not chat_id_str: return jsonify({"items": [], "has_more": False})

    limit = 60
    offset = (page - 1) * limit

    conn = core.get_db_conn()
    cursor = conn.cursor()
    pipeline = core.get_chat_pipeline(cursor, int(chat_id_str))
    placeholders = ','.join('?' for _ in pipeline)

    if m_type == 'links':
        query = f"""
            SELECT message_id, chat_id, date, text, entities_json, media_other_json
            FROM tbl_messages
            WHERE chat_id IN ({placeholders})
              AND (text LIKE '%http://%' OR text LIKE '%https://%' OR entities_json LIKE '%MessageEntityUrl%' OR entities_json LIKE '%MessageEntityTextUrl%')
            ORDER BY date DESC LIMIT {limit + 1} OFFSET {offset}
        """
        cursor.execute(query, pipeline)
        raw_rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        has_more = len(raw_rows) > limit
        if has_more: raw_rows = raw_rows[:limit]
        for item in raw_rows:
            for k in ['entities_json', 'media_other_json']:
                if item.get(k):
                    try: item[k.replace('_json', '')] = json.loads(item[k])
                    except: item[k.replace('_json', '')] = None
                item.pop(k, None)
        return jsonify({"items": raw_rows, "has_more": has_more})

    elif m_type == 'surveys':
        query = f"""
            SELECT message_id, chat_id, date, text, poll_data_json
            FROM tbl_messages
            WHERE chat_id IN ({placeholders})
              AND (poll_data_json IS NOT NULL OR media_type = 'poll')
            ORDER BY date DESC LIMIT {limit + 1} OFFSET {offset}
        """
        cursor.execute(query, pipeline)
        raw_rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        has_more = len(raw_rows) > limit
        if has_more: raw_rows = raw_rows[:limit]
        for item in raw_rows:
            if item.get('poll_data_json'):
                try: item['poll_data'] = json.loads(item['poll_data_json'])
                except: item['poll_data'] = None
            item.pop('poll_data_json', None)
        return jsonify({"items": raw_rows, "has_more": has_more})

    # File / Media based queries
    base_query = f"SELECT message_id, chat_id, date, media_local_path, thumbnail_local_path, media_mime_type, media_original_name, file_size FROM tbl_messages WHERE chat_id IN ({placeholders}) AND media_local_path IS NOT NULL AND REPLACE(media_local_path, '\\', '/') NOT LIKE '%/stickers/%'"

    if m_type == 'photos':
        base_query += " AND (media_type = 'photo' OR (media_type IS NULL AND media_mime_type LIKE 'image/%' AND media_mime_type NOT IN ('image/webp', 'image/gif')))"
    elif m_type == 'videos':
        base_query += " AND (media_type IN ('video', 'round', 'video_note') OR (media_type IS NULL AND media_mime_type LIKE 'video/%' AND REPLACE(media_local_path, '\\', '/') NOT LIKE '%/gifs/%'))"
    elif m_type == 'gifs':
        base_query += " AND (media_type = 'gif' OR (media_type IS NULL AND (media_mime_type = 'image/gif' OR REPLACE(media_local_path, '\\', '/') LIKE '%/gifs/%')))"
    elif m_type == 'voices':
        base_query += " AND (media_type = 'voice' OR (media_type IS NULL AND (media_mime_type IN ('audio/ogg', 'audio/oga') OR is_voice = 1)))"
    elif m_type == 'audio':
        base_query += " AND (media_type = 'audio' OR (media_type IS NULL AND media_mime_type LIKE 'audio/%' AND media_mime_type NOT IN ('audio/ogg', 'audio/oga') AND (is_voice IS NULL OR is_voice = 0)))"
    elif m_type == 'files':
        base_query += " AND (media_type IN ('document', 'file') OR (media_type IS NULL AND media_mime_type NOT LIKE 'image/%' AND media_mime_type NOT LIKE 'video/%' AND media_mime_type NOT LIKE 'audio/%' AND media_mime_type NOT IN ('application/x-tgsticker')))"

    base_query += f" ORDER BY date DESC LIMIT {limit + 1} OFFSET {offset}"

    cursor.execute(base_query, pipeline)
    results = [dict(r) for r in cursor.fetchall()]
    conn.close()

    has_more = len(results) > limit
    if has_more:
        results = results[:limit]

    media_backup_root = core.ABS_MEDIA_BACKUP_DIR
    for item in results:
        p = item.get('media_local_path')
        sz = item.get('file_size') or 0
        if not sz and p:
            fp = os.path.join(media_backup_root, p)
            if os.path.exists(fp):
                try: sz = os.path.getsize(fp)
                except Exception: sz = 0
        item['size_bytes'] = sz

    return jsonify({"items": results, "has_more": has_more})

_DANGEROUS_EXPORT_EXTS = frozenset({
    '.exe', '.bat', '.cmd', '.com', '.scr', '.pif', '.vbs', '.vbe',
    '.js', '.jse', '.wsf', '.wsh', '.ps1', '.psm1', '.msi', '.msp',
    '.hta', '.cpl', '.reg', '.lnk', '.scf', '.url', '.dll', '.sys',
    '.sh', '.bash', '.zsh', '.command', '.desktop', '.appimage', '.jar',
})

def _validate_batch_export_target(target_dir):
    if '..' in PurePath(target_dir).parts:
        return None, "Relative parent traversal ('..') is not allowed"
    abs_target = os.path.abspath(target_dir)
    real_target = os.path.realpath(abs_target)

    blocked_prefixes = (
        'c:\\windows', 'c:\\program files', 'c:\\program files (x86)', 'c:\\programdata',
        '/usr', '/etc', '/bin', '/sbin', '/var', '/dev', '/proc', '/sys', '/boot',
        '/lib', '/lib64', '/system', '/library',
    )
    is_drive_root = os.path.splitdrive(real_target)[1] in ('\\', '/', '')
    real_lower = real_target.lower()
    if is_drive_root or any(real_lower == b or real_lower.startswith(b + ('\\' if '\\' in b else '/')) for b in blocked_prefixes):
        return None, "Cannot export to system directory or drive root"

    # Block hidden dot-directories (.ssh, .git, .gnupg, .config, etc.)
    for p_check in (abs_target, real_target):
        for part in PurePath(p_check).parts:
            if part.startswith('.') and part not in ('.', '..'):
                return None, "Cannot export into hidden dot-directories"

    # Block OS autostart / persistence / application data directories
    norm_slash = real_lower.replace('\\', '/')
    blocked_substrings = (
        '/appdata/',
        '/start menu/programs/startup',
        '/library/launchagents',
        '/library/launchdaemons',
        '/autostart',
    )
    if any(sub in (norm_slash + '/') for sub in blocked_substrings):
        return None, "Cannot export into Startup or application configuration directories"

    # Block TeleBackup project root except dedicated exports subdirectories
    real_project = os.path.realpath(core.PROJECT_ROOT)
    try:
        if os.path.commonpath([real_project, real_target]) == real_project:
            allowed_project_subdirs = (
                os.path.realpath(os.path.join(real_project, 'exports')),
                os.path.realpath(os.path.join(real_project, 'db_exports')),
            )
            if not any(
                os.path.commonpath([allowed_sub, real_target]) == allowed_sub
                for allowed_sub in allowed_project_subdirs
            ):
                return None, "Cannot export directly into the TeleBackup application directory (use an external folder or exports/)"
    except ValueError:
        pass

    return real_target, None

@viewer_bp.route('/api/export_media_batch', methods=['POST'])
def export_media_batch():
    data = request.get_json(silent=True) or {}
    message_ids = data.get('message_ids', [])
    chat_id = data.get('chat_id')
    target_dir = (data.get('target_dir') or '').strip()

    if not message_ids or not isinstance(message_ids, list):
        return jsonify({"error": "No message IDs provided"}), 400
    if not target_dir:
        return jsonify({"error": "Target directory path is required"}), 400

    abs_target, target_err = _validate_batch_export_target(target_dir)
    if target_err:
        return jsonify({"error": target_err}), 403

    try:
        os.makedirs(abs_target, exist_ok=True)
    except Exception as e:
        return jsonify({"error": f"Cannot create directory '{abs_target}': {e}"}), 400

    # Re-verify after directory creation to guard against symlink race
    abs_target, target_err = _validate_batch_export_target(abs_target)
    if target_err:
        return jsonify({"error": target_err}), 403

    conn = core.get_db_conn()
    cursor = conn.cursor()
    placeholders = ','.join('?' for _ in message_ids)

    if chat_id:
        pipeline = core.get_chat_pipeline(cursor, int(chat_id))
        chat_placeholders = ','.join('?' for _ in pipeline)
        cursor.execute(f"""
            SELECT message_id, chat_id, media_local_path, media_original_name
            FROM tbl_messages
            WHERE message_id IN ({placeholders}) AND chat_id IN ({chat_placeholders}) AND media_local_path IS NOT NULL
        """, list(message_ids) + list(pipeline))
    else:
        cursor.execute(f"""
            SELECT message_id, chat_id, media_local_path, media_original_name
            FROM tbl_messages
            WHERE message_id IN ({placeholders}) AND media_local_path IS NOT NULL
        """, list(message_ids))
    rows = cursor.fetchall()
    conn.close()

    copied = 0
    errors = []
    for r in rows:
        rel_path = r['media_local_path']
        if not rel_path:
            continue
        src = os.path.join(core.ABS_MEDIA_BACKUP_DIR, rel_path)
        if os.path.islink(src) or not _is_path_within(src, core.ABS_MEDIA_BACKUP_DIR):
            continue
        real_src = os.path.realpath(src)
        if not os.path.isfile(real_src):
            continue
        orig_name = r['media_original_name'] or os.path.basename(rel_path)
        safe_name = os.path.basename(helpers.sanitize_filename(orig_name)) or "media_file"
        ext_lower = os.path.splitext(safe_name.rstrip('. '))[1].lower()
        if ext_lower in _DANGEROUS_EXPORT_EXTS:
            safe_name = f"{safe_name}.safe_download"
        dst_filename = f"{int(r['message_id'])}_{safe_name}"
        dst = os.path.join(abs_target, dst_filename)
        if os.path.islink(dst) or not _is_path_within(dst, abs_target):
            continue
        try:
            shutil.copy2(real_src, dst)
            copied += 1
        except Exception as err:
            errors.append(str(err))

    return jsonify({
        "ok": True,
        "copied_count": copied,
        "target_dir": abs_target,
        "errors": errors[:5]
    })
    

# ==========================================
# БЛОК 2: АРХИВАЦИЯ И ВОКЕР (ЗАПИСЬ БД)
# ==========================================


@viewer_bp.route('/api/find_comment_root', methods=['GET'])
def find_comment_root():
    raw_chat_id = request.args.get('discussion_chat_id')
    channel_msg_id = request.args.get('channel_msg_id')
    if not raw_chat_id or not channel_msg_id:
        return jsonify({'error': 'Missing parameters'}), 400
        
    chat_variants = set(helpers.get_chat_id_variants(raw_chat_id))

    conn = core.get_db_conn()
    cursor = conn.cursor()

    # Automatically resolve linked discussion chat ID if raw_chat_id is a channel
    # Automatically resolve linked discussion chat ID if raw_chat_id is a channel or discussion group
    try:
        raw_vars = helpers.get_chat_id_variants(raw_chat_id)
        ph_v = ','.join('?' for _ in raw_vars)
        cursor.execute(f"SELECT chat_id, linked_chat_id FROM tbl_chats WHERE chat_id IN ({ph_v}) OR linked_chat_id IN ({ph_v})", (*raw_vars, *raw_vars))
        for r_row in cursor.fetchall():
            if r_row['chat_id']: chat_variants.update(helpers.get_chat_id_variants(r_row['chat_id']))
            if r_row['linked_chat_id']: chat_variants.update(helpers.get_chat_id_variants(r_row['linked_chat_id']))
    except Exception as e:
        logging.debug(f"[find_comment_root] Linked chat resolution error: {e}")

    chat_variants_list = list(chat_variants)
    placeholders = ','.join('?' for _ in chat_variants_list)
    msg_id_int = int(channel_msg_id)
    msg_id_str = str(channel_msg_id)

    # 1. Primary search with chat_id variants (forwarded channel post)
    cursor.execute(f"""
        SELECT message_id, chat_id FROM tbl_messages 
        WHERE chat_id IN ({placeholders})
        AND forward_info_json IS NOT NULL
        AND (
            json_extract(forward_info_json, '$.saved_from_msg_id') = ?
            OR json_extract(forward_info_json, '$.saved_from_msg_id') = ?
            OR json_extract(forward_info_json, '$.channel_post') = ?
            OR json_extract(forward_info_json, '$.channel_post') = ?
        )
        LIMIT 1
    """, (*chat_variants_list, msg_id_int, msg_id_str, msg_id_int, msg_id_str))
    row = cursor.fetchone()

    # 2. Direct message ID search in discussion group
    if not row:
        cursor.execute(f"""
            SELECT message_id, chat_id FROM tbl_messages 
            WHERE chat_id IN ({placeholders})
            AND message_id = ?
            LIMIT 1
        """, (*chat_variants_list, msg_id_int))
        row = cursor.fetchone()

    # 3. Global fallback search across all messages if chat_id variants didn't match
    if not row:
        cursor.execute("""
            SELECT message_id, chat_id FROM tbl_messages 
            WHERE forward_info_json IS NOT NULL
            AND (
                json_extract(forward_info_json, '$.saved_from_msg_id') = ?
                OR json_extract(forward_info_json, '$.saved_from_msg_id') = ?
                OR json_extract(forward_info_json, '$.channel_post') = ?
                OR json_extract(forward_info_json, '$.channel_post') = ?
            )
            LIMIT 1
        """, (msg_id_int, msg_id_str, msg_id_int, msg_id_str))
        row = cursor.fetchone()

    conn.close()
    
    if row:
        return jsonify({
            'discussion_msg_id': row['message_id'],
            'message_id': row['message_id'],
            'chat_id': row['chat_id']
        })
    return jsonify({'error': 'Not found'}), 404

@viewer_bp.route('/api/comments', methods=['GET'])
def get_comments():
    core.flush_live_db_to_main()
    chat_id_str = request.args.get('chat_id') or request.args.get('discussion_chat_id')
    root_msg_id = request.args.get('root_msg_id')
    if not chat_id_str or not root_msg_id:
        return jsonify({"error": "chat_id and root_msg_id required"}), 400

    root_id = int(root_msg_id)
    conn = core.get_db_conn()
    cursor = conn.cursor()
    pipeline = core.get_chat_pipeline(cursor, int(chat_id_str))
    placeholders = ','.join('?' for _ in pipeline)

    query_params = pipeline + pipeline + [root_id, root_id, root_id]
    cursor.execute(f"""
        SELECT m.*,
               rm.sender_id as dynamic_reply_sender_id, 
               rm.text as dynamic_reply_text,
               rm.forward_info_json as dynamic_reply_forward_info_json,
               rm.post_author_name as dynamic_reply_post_author_name,
               rm.media_type as dynamic_reply_media_type,
               rm.group_id as dynamic_reply_group_id,
               rm.media_original_name as dynamic_reply_media_name,
               rm.is_voice as dynamic_reply_is_voice,
               rm.is_round_video as dynamic_reply_is_round,
               rm.thumbnail_local_path as dynamic_reply_thumb_path,
               rm.media_local_path as dynamic_reply_media_path,
               ru.first_name as reply_sender_first_name,
               ru.last_name as reply_sender_last_name,
               u.first_name as sender_first_name,
               u.last_name as sender_last_name,
               u.avatar_path as sender_avatar_path
        FROM tbl_messages m
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id
        LEFT JOIN tbl_messages rm ON m.reply_to_msg_id = rm.message_id AND rm.chat_id IN ({placeholders})
        LEFT JOIN tbl_users ru ON rm.sender_id = ru.user_id
        WHERE m.chat_id IN ({placeholders})
          AND (m.reply_to_top_id = ? OR m.reply_to_msg_id = ?)
          AND m.message_id != ?
        ORDER BY m.date ASC, m.message_id ASC
        LIMIT 100
    """, query_params)

    results = [dict(r) for r in cursor.fetchall()]

    messages = []
    peer_ids = set()
    for msg_dict in results:
        _enrich_reply_info(msg_dict, peer_ids, cursor)
        for key in ['entities_json', 'forward_info_json', 'service_action_json', 'poll_data_json', 'reactions_json', 'media_other_json', 'reply_markup_json', 'replies_json', 'restriction_reason_json', 'reply_to_peer_id_json', 'quote_entities_json', 'reply_from_json', 'reply_media_json', 'saved_peer_id_json', 'edit_history_json']:
            clean_key = key.replace('_json', '')
            if msg_dict.get(key):
                try: msg_dict[clean_key] = json.loads(msg_dict[key])
                except json.JSONDecodeError: msg_dict[clean_key] = None
            else: msg_dict[clean_key] = None

        _collect_peer_ids_from_msg(msg_dict, peer_ids)
        messages.append(msg_dict)

    users_map = _resolve_peers_map(cursor, peer_ids)

    custom_emoji_ids = _extract_and_stringify_custom_emojis(messages)
    custom_emoji_map = _resolve_custom_emoji_map(cursor, custom_emoji_ids)

    conn.close()
    return jsonify({"messages": messages, "users": users_map, "custom_emoji": custom_emoji_map})

@viewer_bp.route('/api/tools/sync_custom_emojis', methods=['POST'])
def sync_custom_emojis():
    if not getattr(core, 'LIVE_CLIENT', None) or not core.LIVE_CLIENT.is_connected():
        return jsonify({"status": "error", "message": "Live client not connected"}), 503
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT entities_json, reactions_json FROM tbl_messages
        WHERE entities_json LIKE '%MessageEntityCustomEmoji%' OR reactions_json LIKE '%ReactionCustomEmoji%'
    """)
    rows = cursor.fetchall()
    all_emoji_ids = set()
    for r in rows:
        if r[0]:
            try:
                for ent in json.loads(r[0]):
                    if ent.get('_') == 'MessageEntityCustomEmoji' and ent.get('document_id'):
                        all_emoji_ids.add(ent['document_id'])
            except Exception: pass
        if r[1]:
            try:
                rx_data = json.loads(r[1])
                for item in (rx_data.get('results', []) + rx_data.get('recent_reactions', [])):
                    rx_obj = item.get('reaction', {})
                    if rx_obj.get('_') == 'ReactionCustomEmoji' and rx_obj.get('document_id'):
                        all_emoji_ids.add(rx_obj['document_id'])
            except Exception: pass

    existing_map = db.get_custom_emojis_by_ids(cursor, all_emoji_ids)
    missing_ids = [cid for cid in all_emoji_ids if str(cid) not in existing_map]
    
    if missing_ids:
        import asyncio
        from core.worker import LIVE_LOOP
        from core.emoji import async_fetch_and_save_custom_emojis
        asyncio.run_coroutine_threadsafe(
            async_fetch_and_save_custom_emojis(core.LIVE_CLIENT, missing_ids),
            LIVE_LOOP
        )
    conn.close()
    return jsonify({
        "status": "ok",
        "total_referenced": len(all_emoji_ids),
        "already_cached": len(existing_map),
        "queued_for_download": len(missing_ids)
    })



# ==========================================
# ADVANCED STATS, BOOKMARKS, EXPORT & DIFF
# ==========================================


@viewer_bp.route('/api/bookmarks', methods=['GET'])
def get_bookmarks_api():
    chat_id = request.args.get('chat_id', type=int)
    conn = db.get_db_conn()
    cur = conn.cursor()
    bookmarks = db.get_bookmarks(cur, chat_id)
    conn.close()
    return jsonify(bookmarks)

@viewer_bp.route('/api/bookmark', methods=['POST'])
def add_bookmark_api():
    data = request.get_json(silent=True) or {}
    message_id = data.get('message_id')
    chat_id = data.get('chat_id')
    note = data.get('note', '')
    if not message_id or not chat_id:
        return jsonify({'error': 'Missing message_id or chat_id'}), 400

    conn = db.get_db_conn()
    cur = conn.cursor()
    db.add_bookmark(cur, message_id, chat_id, note)
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@viewer_bp.route('/api/bookmark', methods=['DELETE'])
def remove_bookmark_api():
    data = request.get_json(silent=True) or {}
    message_id = data.get('message_id') or request.args.get('message_id', type=int)
    chat_id = data.get('chat_id') or request.args.get('chat_id', type=int)
    if not message_id or not chat_id:
        return jsonify({'error': 'Missing message_id or chat_id'}), 400

    conn = db.get_db_conn()
    cur = conn.cursor()
    db.remove_bookmark(cur, message_id, chat_id)
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@viewer_bp.route('/api/diff')
def chat_diff():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400

    pipeline = get_chat_pipeline_cached(chat_id)
    placeholders = ','.join('?' for _ in pipeline)

    conn = db.get_db_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT message_id, chat_id, sender_id, text, date, edited_date, edit_history_json
        FROM tbl_messages
        WHERE chat_id IN ({placeholders}) AND (edited_date IS NOT NULL OR edit_history_json IS NOT NULL)
        ORDER BY edited_date DESC LIMIT 100
    """, pipeline)
    edits = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({'edits': edits})

@viewer_bp.route('/api/export')
def export_chat_api():
    chat_id = request.args.get('chat_id', type=int)
    interval = request.args.get('interval', 'month')
    fmt = request.args.get('format', 'md')
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400

    pipeline = get_chat_pipeline_cached(chat_id)
    placeholders = ','.join('?' for _ in pipeline)

    conn = db.get_db_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT m.message_id, m.date, m.sender_id, m.text,
               u.first_name, u.last_name, u.username
        FROM tbl_messages m
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id
        WHERE m.chat_id IN ({placeholders})
        ORDER BY m.date ASC
    """, pipeline)
    messages = cur.fetchall()
    conn.close()

    lines = [f"# Telegram Backup Export — Chat ID {chat_id}", f"Generated: {datetime.now().isoformat()}", ""]
    for msg in messages:
        dt = msg['date'] or ''
        sender = msg['first_name'] or msg['username'] or str(msg['sender_id'] or 'Unknown')
        text = msg['text'] or '[Media / No text]'
        lines.append(f"[{dt}] **{sender}**: {text}\n")

    safe_interval = re.sub(r'[^A-Za-z0-9_-]', '', interval or '')[:24] or 'month'
    safe_fmt = 'md' if fmt == 'md' else 'txt'
    content = "\n".join(lines)
    filename = f"export_{chat_id}_{safe_interval}.{safe_fmt}"
    
    from flask import Response
    return Response(
        content,
        mimetype="text/markdown" if safe_fmt == "md" else "text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


