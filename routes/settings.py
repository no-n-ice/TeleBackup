#!/usr/bin/env python3
"""
routes/settings.py — Chat and global settings API endpoints.
"""
import os
import json
import time
import sqlite3
import logging
from pathlib import PurePath
from flask import Blueprint, request, jsonify
import core
from core import db, helpers

settings_bp = Blueprint('settings', __name__)

def _execute_with_retry(fn, max_retries=5, base_delay=0.2):
    """Executes a database write function with retry on SQLite lock contention."""
    last_err = None
    for attempt in range(max_retries):
        conn = None
        try:
            conn = db.get_db_conn()
            cur = conn.cursor()
            res = fn(cur)
            conn.commit()
            return res
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                if attempt < max_retries - 1:
                    time.sleep(base_delay * (1.5 ** attempt))
                    continue
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    if last_err:
        raise last_err

@settings_bp.route('/api/chat_settings', methods=['GET'])
@settings_bp.route('/api/get_chat_settings', methods=['GET'])
def get_chat_settings_api():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    conn = None
    try:
        conn = db.get_db_conn()
        cur = conn.cursor()
        chat_settings = db.get_chat_settings(cur, chat_id)
        global_settings = db.get_global_settings(cur) or {}
        effective_settings = chat_settings if chat_settings is not None else global_settings
        effective_settings = helpers.normalize_media_settings(effective_settings or {})
        return jsonify(effective_settings)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

def _normalize_rules_dict(cur, user_rules):
    if not isinstance(user_rules, dict):
        return {}
    resolved = {}
    for k, v in user_rules.items():
        if not k:
            continue
        clean_k = str(k).strip()
        if clean_k.startswith('@'):
            clean_k = clean_k[1:]
        if clean_k.isdigit():
            try:
                cur.execute("SELECT user_id, first_name, username, is_bot FROM tbl_users WHERE user_id = ? LIMIT 1", (int(clean_k),))
                row = cur.fetchone()
                if row and isinstance(v, dict):
                    if not v.get('user_name') or v.get('user_name') == f"User {clean_k}":
                        v['user_name'] = row['first_name'] or (f"@{row['username']}" if row['username'] else f"User {clean_k}")
                    if 'is_bot' not in v or not v.get('is_bot'):
                        v['is_bot'] = bool(row['is_bot'])
            except Exception:
                pass
            resolved[clean_k] = v
        else:
            try:
                cur.execute("SELECT user_id, first_name, username, is_bot FROM tbl_users WHERE LOWER(username) = LOWER(?) LIMIT 1", (clean_k,))
                row = cur.fetchone()
                if row:
                    uid = str(row['user_id'])
                    if isinstance(v, dict) and not v.get('user_name'):
                        v['user_name'] = row['first_name'] or f"@{row['username']}"
                        v['is_bot'] = bool(row['is_bot'])
                    resolved[uid] = v
                else:
                    resolved[clean_k] = v
            except Exception:
                resolved[clean_k] = v
    return resolved

@settings_bp.route('/api/save_chat_settings', methods=['POST'])
def save_chat_settings_api():
    data = request.json or {}
    chat_id = data.get('chat_id')
    settings = data.get('settings')
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    if isinstance(settings, dict) and 'user_rules' in settings:
        conn = None
        try:
            conn = db.get_db_conn()
            cur = conn.cursor()
            settings['user_rules'] = _normalize_rules_dict(cur, settings['user_rules'])
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    try:
        _execute_with_retry(lambda cur: db.save_chat_settings(cur, chat_id, settings))
        return jsonify({"status": "ok", "settings": settings})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@settings_bp.route('/api/save_global_settings', methods=['POST'])
def save_global_settings_api():
    settings = request.json
    if not settings:
        return jsonify({'error': 'settings missing'}), 400
    try:
        _execute_with_retry(lambda cur: db.save_global_settings(cur, settings))
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@settings_bp.route('/api/chat_media_assets', methods=['GET'])
def get_chat_media_assets_api():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    conn = None
    try:
        conn = db.get_db_conn()
        cur = conn.cursor()
        assets = db.get_chat_media_assets(cur, chat_id)
        return jsonify({'chat_id': chat_id, 'assets': assets})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@settings_bp.route('/api/chat_senders', methods=['GET'])
def get_chat_senders_api():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    conn = None
    try:
        conn = db.get_db_conn()
        cur = conn.cursor()
        senders = db.get_chat_senders(cur, chat_id)
        return jsonify({'chat_id': chat_id, 'senders': senders})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@settings_bp.route('/api/estimate_media_catchup', methods=['POST'])
def estimate_media_catchup_api():
    data = request.json or {}
    chat_id = data.get('chat_id')
    settings = data.get('settings')
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400

    conn = None
    try:
        conn = db.get_db_conn()
        cur = conn.cursor()
        if not settings:
            chat_s = db.get_chat_settings(cur, chat_id)
            settings = chat_s if chat_s is not None else db.get_global_settings(cur)
        settings = helpers.normalize_media_settings(settings or {})
        if settings.get('user_rules'):
            settings['user_rules'] = _normalize_rules_dict(cur, settings['user_rules'])
        assets = db.get_chat_media_assets(cur, chat_id)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    applicable_count = 0
    estimated_bytes = 0
    items_by_type = {}
    missing_excluded_count = 0
    missing_excluded_bytes = 0
    total_excluded_count = 0
    total_excluded_bytes = 0

    user_rules = settings.get('user_rules') or {}

    for a in assets:
        m_type = a.get('media_type') or 'document'
        if m_type in ('web_page', 'webpage', 'poll', 'dice', 'geo', 'geolive', 'contact', 'venue', 'game') and not a.get('media_skipped'):
            continue
        cfg_key = 'documents'
        if m_type in ('photo',): cfg_key = 'photos'
        elif m_type in ('video', 'gif'): cfg_key = 'videos'
        elif m_type in ('voice', 'audio'): cfg_key = 'voice'
        elif m_type in ('video_note',): cfg_key = 'video_notes'
        elif m_type in ('sticker',): cfg_key = 'documents'

        size = a.get('size_bytes', 0)

        # Check user/bot exclusion rules (sender, via_bot, or forward origin)
        s_id = str(a.get('sender_id')) if a.get('sender_id') else None
        v_id = str(a.get('via_bot_id')) if a.get('via_bot_id') else None
        f_id = str(a.get('fwd_from_id')) if a.get('fwd_from_id') else None
        if not f_id and a.get('forward_info_json'):
            try:
                fj = json.loads(a['forward_info_json']) if isinstance(a['forward_info_json'], str) else a['forward_info_json']
                raw_fid = fj.get('from_id') or (fj.get('from_id_raw') or {}).get('user_id') or (fj.get('from_id_raw') or {}).get('channel_id')
                if raw_fid:
                    f_id = str(raw_fid)
            except Exception:
                pass

        u_rule = user_rules.get(s_id) or (user_rules.get(v_id) if v_id else None) or (user_rules.get(f_id) if f_id else None)
        
        is_user_excluded = False
        if u_rule:
            mode = u_rule.get('mode', 'exclude_all')
            if mode == 'exclude_all':
                is_user_excluded = True
            elif mode == 'custom':
                u_cfg = u_rule.get(cfg_key) or {}
                if not u_cfg.get('enabled', True):
                    is_user_excluded = True
                elif u_cfg.get('max_mb', 0) > 0 and size > (u_cfg.get('max_mb') * 1024 * 1024):
                    is_user_excluded = True

        if is_user_excluded or (a.get('media_skipped') and 'user_rule_excluded' in a.get('media_skipped')):
            total_excluded_count += 1
            total_excluded_bytes += size
            if not a.get('media_local_path'):
                missing_excluded_count += 1
                missing_excluded_bytes += size
            continue

        if a.get('media_local_path'):
            continue

        skip_val = a.get('media_skipped')
        if skip_val:
            if helpers.is_media_skip_permanent(skip_val):
                total_excluded_count += 1
                total_excluded_bytes += size
                if not a.get('media_local_path'):
                    missing_excluded_count += 1
                    missing_excluded_bytes += size
                continue
            if not data.get('bypass_limits') and helpers.is_media_skip_download_failed(skip_val):
                continue

        cfg = settings.get(cfg_key) or {}
        if not cfg.get('enabled', True):
            continue

        max_mb = cfg.get('max_mb', 0)
        if max_mb and max_mb > 0 and size > (max_mb * 1024 * 1024):
            continue

        applicable_count += 1
        estimated_bytes += size

        items_by_type[cfg_key] = items_by_type.get(cfg_key, 0) + 1

    excl_count = missing_excluded_count if missing_excluded_count > 0 else total_excluded_count
    excl_bytes = missing_excluded_bytes if missing_excluded_count > 0 else total_excluded_bytes

    return jsonify({
        "applicable_count": applicable_count,
        "estimated_bytes": estimated_bytes,
        "items_by_type": items_by_type,
        "excluded_by_rules_count": excl_count,
        "excluded_by_rules_bytes": excl_bytes,
        "missing_excluded_count": missing_excluded_count,
        "missing_excluded_bytes": missing_excluded_bytes,
        "total_excluded_count": total_excluded_count,
        "total_excluded_bytes": total_excluded_bytes
    })

@settings_bp.route('/api/settings/merge_db', methods=['POST'])
def handle_merge_db():
    """Non-destructive multi-machine database and media merger."""
    payload = request.get_json(silent=True) or {}
    source_db_path = (payload.get('source_db_path') or '').strip()
    source_media_dir = (payload.get('source_media_dir') or '').strip() or None
    async_mode = bool(payload.get('async_mode', False))
    
    if not source_db_path:
        return jsonify({'error': 'source_db_path is required'}), 400
    if '..' in PurePath(source_db_path).parts:
        return jsonify({'error': "Relative parent traversal ('..') in source_db_path is not allowed"}), 400
    if source_media_dir and '..' in PurePath(source_media_dir).parts:
        return jsonify({'error': "Relative parent traversal ('..') in source_media_dir is not allowed"}), 400
    if not os.path.isabs(source_db_path):
        source_db_path = os.path.join(core.PROJECT_ROOT, source_db_path)
    if not os.path.exists(source_db_path):
        return jsonify({'error': f'Source database file not found: {source_db_path}'}), 404
        
    try:
        if async_mode:
            res = db.start_merge_background(source_db_path, source_media_dir=source_media_dir)
            return jsonify(res)
        res = db.merge_database_file(source_db_path, source_media_dir=source_media_dir)
        return jsonify(res)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logging.error(f"[Settings] Database merge failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@settings_bp.route('/api/settings/merge_db/status', methods=['GET'])
def handle_merge_db_status():
    """Returns real-time background database merge progress snapshot."""
    return jsonify(db.get_merge_status())
