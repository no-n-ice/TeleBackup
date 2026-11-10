#!/usr/bin/env python3
"""
routes/auth.py — Ultra-secure Telegram authentication, sessions management, status detection, and login API.
"""
import os
import re
import sys
import time
import json
import sqlite3
import secrets
import asyncio
import logging
import threading
import subprocess
import configparser
import io
from pathlib import Path
import qrcode
import qrcode.image.svg
from flask import Blueprint, request, jsonify
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeEmptyError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    PhoneNumberBannedError,
    PasswordHashInvalidError
)
import core
from core import worker

auth_bp = Blueprint('auth', __name__)

# Persistent Cryptographic CSRF Token across restarts
AUTH_CSRF_TOKEN = core.load_or_generate_secret('.csrf_token', 32)

_SESSION_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_RESERVED_SESSION_NAMES = frozenset({'telegram_backup', 'live_stream_backup', 'config', 'sessions_meta'})

def _is_valid_session_name(name):
    if not name or not isinstance(name, str):
        return False
    clean = name[:-8] if name.endswith('.session') else name
    return bool(_SESSION_NAME_RE.fullmatch(clean)) and clean.lower() not in _RESERVED_SESSION_NAMES

def _mask_phone(phone):
    """Masks phone for safe persistence: e.g. +380***06"""
    if not phone or len(str(phone)) < 5:
        return phone or ''
    p = str(phone).strip()
    prefix = "+" if p.startswith("+") else ""
    p_clean = p.lstrip("+")
    if len(p_clean) < 5:
        return phone
    return f"{prefix}{p_clean[:3]}***{p_clean[-2:]}"

_LAST_CODE_REQUEST = 0

# Ephemeral in-memory auth state (300 seconds TTL, zero disk persistence)
_AUTH_STATE = {
    'client': None,
    'loop': None,
    'phone': None,
    'phone_code_hash': None,
    'target_session': None,
    'expires_at': 0,
    'attempts': 0
}

# Ephemeral in-memory QR auth state
_QR_STATE = {
    'client': None,
    'loop': None,
    'qr': None,
    'target_session': None,
    'expires_at': 0,
    'done': False,
    'user': None,
    'error': None,
    'need_2fa': False
}

# Session cache for quick status checks (60 seconds)
_STATUS_CACHE = {
    'last_check': 0,
    'authorized': False,
    'user': None
}

def _reset_qr_state():
    global _QR_STATE
    if _QR_STATE['client'] and _QR_STATE['loop']:
        try:
            if _QR_STATE['client'].is_connected():
                _QR_STATE['loop'].run_until_complete(_QR_STATE['client'].disconnect())
        except Exception:
            pass
    _QR_STATE = {
        'client': None,
        'loop': None,
        'qr': None,
        'target_session': None,
        'expires_at': 0,
        'done': False,
        'user': None,
        'error': None,
        'need_2fa': False
    }

def _reset_auth_state():
    global _AUTH_STATE
    if _AUTH_STATE['client'] and _AUTH_STATE['loop']:
        try:
            if _AUTH_STATE['client'].is_connected():
                _AUTH_STATE['loop'].run_until_complete(_AUTH_STATE['client'].disconnect())
        except Exception:
            pass
    _AUTH_STATE = {
        'client': None,
        'loop': None,
        'phone': None,
        'phone_code_hash': None,
        'target_session': None,
        'expires_at': 0,
        'attempts': 0
    }
    _reset_qr_state()

def verify_csrf():
    token = request.headers.get('X-TeleBackup-Auth-Token') or (request.get_json(silent=True) or {}).get('auth_csrf_token')
    if not token or not secrets.compare_digest(token, AUTH_CSRF_TOKEN):
        return False
    return True

# -------------------------------------------------------------------------
# SESSIONS REGISTRY & PERSISTENCE
# -------------------------------------------------------------------------

def load_sessions_registry():
    """Returns all session metadata stored in tbl_global_settings."""
    try:
        con = core.get_db_conn()
        cur = con.cursor()
        cur.execute("SELECT value FROM tbl_global_settings WHERE key = 'sessions_registry'")
        row = cur.fetchone()
        con.close()
        if row and row['value']:
            return json.loads(row['value'])
    except Exception:
        pass
    return {}

def save_sessions_registry(registry):
    """Persists sessions registry dictionary to tbl_global_settings."""
    try:
        con = core.get_db_conn()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO tbl_global_settings (key, value) VALUES ('sessions_registry', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (json.dumps(registry),))
        con.commit()
        con.close()
    except Exception:
        pass

def update_session_meta(session_name, meta):
    """Updates metadata for a given session in the registry."""
    reg = load_sessions_registry()
    existing = reg.get(session_name, {})
    existing.update(meta)
    existing['updated_at'] = time.time()
    reg[session_name] = existing
    save_sessions_registry(reg)

def remove_session_meta(session_name):
    """Removes a session from the registry."""
    reg = load_sessions_registry()
    if session_name in reg:
        del reg[session_name]
        save_sessions_registry(reg)

def has_persistent_session(session_name=None):
    """Fast, local non-network check for valid persistent session credentials in SQLite."""
    target = (session_name or core.SESSION_FILE or '').strip()
    base_name = target[:-8] if target.endswith('.session') else target
    if not _is_valid_session_name(base_name):
        return False
    session_file = os.path.join(core.PROJECT_ROOT, f"{base_name}.session")
    if os.path.islink(session_file) or not os.path.isfile(session_file):
        return False
    try:
        ro_uri = Path(os.path.realpath(session_file)).as_uri() + "?mode=ro"
        con = sqlite3.connect(ro_uri, uri=True, timeout=0.5)
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM sessions WHERE auth_key IS NOT NULL")
        sess_count = cur.fetchone()[0]
        entity_count = 0
        try:
            cur.execute("SELECT count(*) FROM entities")
            entity_count = cur.fetchone()[0]
        except Exception:
            pass
        con.close()
        return sess_count > 0 and entity_count > 0
    except Exception:
        return os.path.isfile(session_file) and os.path.getsize(session_file) > 2048

def scan_all_sessions():
    """Scans PROJECT_ROOT for all *.session files and builds comprehensive session items."""
    active_name = getattr(core, 'SESSION_FILE', 'backup_session')
    registry = load_sessions_registry()
    session_files = []

    for entry in os.listdir(core.PROJECT_ROOT):
        if entry.endswith('.session') and not entry.endswith('-shm') and not entry.endswith('-wal') and not entry.endswith('-journal'):
            s_name = entry[:-8]
            if _is_valid_session_name(s_name):
                session_files.append(s_name)

    if _is_valid_session_name(active_name) and active_name not in session_files:
        session_files.append(active_name)

    results = []
    for s_name in set(session_files):
        is_active = (s_name == active_name)
        has_auth = has_persistent_session(s_name)
        meta = registry.get(s_name, {})

        if is_active:
            live_user = _STATUS_CACHE.get('user') or get_persisted_user()
            if live_user:
                for k, v in live_user.items():
                    if v:
                        meta[k] = v

        user_id = meta.get('id') or meta.get('user_id')
        name = meta.get('first_name') or meta.get('name') or s_name
        if meta.get('last_name'):
            name = f"{name} {meta['last_name']}".strip()
        username = meta.get('username') or ''
        phone = _mask_phone(meta.get('phone') or '')

        # Resolve avatar
        avatar_url = meta.get('avatar_url')
        if not avatar_url and user_id:
            av_rel = f"avatars_global/{user_id}.jpg"
            av_abs = os.path.join(core.ABS_MEDIA_BACKUP_DIR, av_rel)
            if os.path.exists(av_abs):
                avatar_url = f"/data/{av_rel}"

        results.append({
            'session_name': s_name,
            'is_active': is_active,
            'has_auth': has_auth,
            'user_id': user_id,
            'name': name,
            'username': username,
            'phone': phone,
            'avatar_url': avatar_url
        })

    results.sort(key=lambda x: (not x['is_active'], x['session_name'].lower()))
    return results

def get_persisted_user():
    """Reads previously saved authorized user profile from database."""
    try:
        con = core.get_db_conn()
        cur = con.cursor()
        cur.execute("SELECT value FROM tbl_global_settings WHERE key = 'auth_user_cache'")
        row = cur.fetchone()
        con.close()
        if row and row['value']:
            return json.loads(row['value'])
    except Exception:
        pass
    return None

def save_persisted_user(user_data):
    """Persists authorized user profile to database."""
    if not user_data:
        return
    try:
        con = core.get_db_conn()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO tbl_global_settings (key, value) VALUES ('auth_user_cache', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (json.dumps(user_data),))
        con.commit()
        con.close()
    except Exception:
        pass

def trigger_server_restart():
    """Spawns fresh app.py process and cleanly terminates current process."""
    def _do_restart():
        time.sleep(0.5)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

        if os.name == 'nt':
            flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) or subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([sys.executable, 'app.py'], cwd=core.PROJECT_ROOT, creationflags=flags)
        else:
            subprocess.Popen([sys.executable, 'app.py'], cwd=core.PROJECT_ROOT)

        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_do_restart, daemon=True).start()

# -------------------------------------------------------------------------
# STATUS CHECK & SESSIONS ENDPOINTS
# -------------------------------------------------------------------------

async def _check_authorized():
    if not (core.API_ID and core.API_HASH):
        return False, None

    if not has_persistent_session():
        return False, None

    # 1. Check live client first if present on loop
    if getattr(core, 'LIVE_CLIENT', None):
        client = core.LIVE_CLIENT
        try:
            if not client.is_connected():
                try:
                    await asyncio.wait_for(client.connect(), timeout=3.0)
                except Exception:
                    return has_persistent_session(), None
            if await client.is_user_authorized():
                me = None
                try:
                    me = await asyncio.wait_for(client.get_me(), timeout=3.0)
                except Exception:
                    pass
                return True, me
        except Exception as e_live:
            if "locked" in str(e_live).lower() or has_persistent_session():
                return True, None
        return has_persistent_session(), None

    # If LiveArchiver is currently initializing its connection, avoid SQLite session lock race
    if getattr(core, 'LIVE_STARTING', False):
        return True, None

    # 2. Ephemeral client check with lock protection (only if LIVE_CLIENT is not registered)
    acquired = worker.TELETHON_LOCK.acquire(blocking=True, timeout=2.0)
    if not acquired:
        return True, None

    test_client = TelegramClient(os.path.join(core.PROJECT_ROOT, core.SESSION_FILE), core.API_ID, core.API_HASH)
    try:
        await asyncio.wait_for(test_client.connect(), timeout=5.0)
        auth = await asyncio.wait_for(test_client.is_user_authorized(), timeout=5.0)
        me = None
        if auth:
            try:
                me = await asyncio.wait_for(test_client.get_me(), timeout=4.0)
            except Exception:
                pass
        return auth, me
    except Exception as e:
        if "locked" in str(e).lower() or has_persistent_session():
            return True, None
        return False, None
    finally:
        try:
            await test_client.disconnect()
            await asyncio.sleep(0.05)
        except Exception:
            pass
        worker.TELETHON_LOCK.release()

@auth_bp.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Returns current Telegram session authorization state and masked user info."""
    configured = bool(core.API_ID and core.API_HASH)
    if not configured:
        return jsonify({
            'authorized': False,
            'configured': False,
            'reason': 'config_missing'
        })

    has_session = has_persistent_session()
    if not has_session:
        _STATUS_CACHE['authorized'] = False
        _STATUS_CACHE['user'] = None
        return jsonify({
            'authorized': False,
            'configured': True,
            'user': None
        })

    force_refresh = request.args.get('refresh') == '1'
    now = time.time()
    
    if not force_refresh and (now - _STATUS_CACHE['last_check'] < 60) and _STATUS_CACHE['authorized']:
        user_info = _STATUS_CACHE['user'] or get_persisted_user()
        return jsonify({
            'authorized': True,
            'configured': True,
            'user': user_info
        })

    try:
        if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
            future = asyncio.run_coroutine_threadsafe(_check_authorized(), worker.LIVE_LOOP)
            is_auth, me = future.result(timeout=6)
        else:
            is_auth, me = asyncio.run(_check_authorized())
    except Exception:
        is_auth = has_persistent_session()
        me = None

    user_data = _STATUS_CACHE.get('user') or get_persisted_user()
    if is_auth and me:
        phone_str = getattr(me, 'phone', '') or ''
        masked_phone = f"+{phone_str[:3]}***{phone_str[-2:]}" if len(phone_str) >= 5 else None
        user_data = {
            'id': me.id,
            'first_name': getattr(me, 'first_name', '') or '',
            'last_name': getattr(me, 'last_name', '') or '',
            'username': getattr(me, 'username', '') or '',
            'phone': masked_phone
        }
        save_persisted_user(user_data)
        update_session_meta(core.SESSION_FILE, user_data)

    _STATUS_CACHE['last_check'] = now
    _STATUS_CACHE['authorized'] = is_auth
    _STATUS_CACHE['user'] = user_data

    return jsonify({
        'authorized': is_auth,
        'configured': True,
        'user': user_data
    })

@auth_bp.route('/api/auth/sessions', methods=['GET'])
def get_sessions():
    """Returns all saved Telegram sessions with active status and user metadata."""
    sessions = scan_all_sessions()
    return jsonify({
        'active_session': getattr(core, 'SESSION_FILE', 'backup_session'),
        'sessions': sessions
    })

@auth_bp.route('/api/auth/switch_session', methods=['POST'])
def switch_session():
    """Switches active session in config.ini and restarts app.py."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    if getattr(core, 'IS_BULK_ARCHIVING', False):
        return jsonify({'error': 'Cannot switch session while a backup is actively running.'}), 400

    data = request.get_json(silent=True) or {}
    target_session = str(data.get('session_name', '')).strip()

    if not target_session:
        return jsonify({'error': 'No session_name specified'}), 400
    if not _is_valid_session_name(target_session):
        return jsonify({'error': 'Invalid session_name format'}), 400

    if not has_persistent_session(target_session):
        return jsonify({'error': f'Session file for "{target_session}" not found or unauthorized.'}), 404

    # Update config.ini
    try:
        config_path = os.path.join(core.PROJECT_ROOT, 'config.ini')
        cfg = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        cfg.read(config_path, encoding='utf-8')
        if not cfg.has_section('Telegram'):
            cfg.add_section('Telegram')
        cfg.set('Telegram', 'session_name', target_session)
        with open(config_path, 'w', encoding='utf-8') as f:
            cfg.write(f)
        core.restrict_file_permissions(config_path)
    except Exception as e:
        return jsonify({'error': f'Failed to update config.ini: {e}'}), 500

    core.SESSION_FILE = target_session
    _STATUS_CACHE['authorized'] = False
    _STATUS_CACHE['user'] = None

    # Disconnect live client if connected
    if getattr(core, 'LIVE_CLIENT', None):
        try:
            if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
                asyncio.run_coroutine_threadsafe(core.LIVE_CLIENT.disconnect(), worker.LIVE_LOOP)
        except Exception:
            pass

    trigger_server_restart()
    return jsonify({'ok': True, 'restarting': True, 'target_session': target_session})

@auth_bp.route('/api/auth/shutdown_session', methods=['POST'])
def shutdown_session():
    """Shuts down or deletes a session."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    data = request.get_json(silent=True) or {}
    target_session = str(data.get('session_name', '')).strip()
    delete_file = bool(data.get('delete_file', False))

    if not target_session:
        return jsonify({'error': 'No session_name specified'}), 400
    if not _is_valid_session_name(target_session):
        return jsonify({'error': 'Invalid session_name format'}), 400

    is_active = (target_session == getattr(core, 'SESSION_FILE', 'backup_session'))

    if is_active:
        if getattr(core, 'IS_BULK_ARCHIVING', False):
            return jsonify({'error': 'Cannot shutdown session while backup is running.'}), 400

        # Disconnect live client
        if getattr(core, 'LIVE_CLIENT', None):
            try:
                if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
                    asyncio.run_coroutine_threadsafe(core.LIVE_CLIENT.disconnect(), worker.LIVE_LOOP)
            except Exception:
                pass

        _STATUS_CACHE['authorized'] = False
        _STATUS_CACHE['user'] = None

        if delete_file:
            remove_session_meta(target_session)
            for ext in ['.session', '.session-shm', '.session-wal', '.session-journal']:
                fp = os.path.join(core.PROJECT_ROOT, f"{target_session}{ext}")
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
            trigger_server_restart()
            return jsonify({'ok': True, 'deleted': True, 'restarting': True})

        return jsonify({'ok': True, 'shutdown': True})
    else:
        # Inactive session: delete files and remove from registry
        remove_session_meta(target_session)
        for ext in ['.session', '.session-shm', '.session-wal', '.session-journal']:
            fp = os.path.join(core.PROJECT_ROOT, f"{target_session}{ext}")
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass
        return jsonify({'ok': True, 'deleted': True})

def _parse_sent_code(res):
    """Parses SentCode/SentCodePaymentRequired Telethon response into detailed user-facing delivery metadata."""
    if not res:
        return {
            'channel': 'unknown',
            'title': 'Unknown Channel',
            'desc': 'Verification code requested from Telegram.',
            'warning': None,
            'length': None,
            'timeout': 60
        }

    res_type_name = type(res).__name__
    if res_type_name == 'SentCodePaymentRequired':
        curr = getattr(res, 'currency', '')
        amt = getattr(res, 'amount', 0)
        return {
            'channel': 'payment_required',
            'title': 'Telegram Fee / Payment Required',
            'desc': f'Telegram requires payment of {amt} {curr} to send login code to this number.',
            'warning': 'Telegram withheld the code pending fee payment.',
            'length': None,
            'timeout': 60
        }

    code_type_obj = getattr(res, 'type', None)
    type_name = type(code_type_obj).__name__ if code_type_obj else res_type_name
    length = getattr(code_type_obj, 'length', None)
    length_str = f" ({length} digits)" if length else ""
    timeout = getattr(res, 'timeout', 60) or 60
    next_type = type(getattr(res, 'next_type', None)).__name__ if getattr(res, 'next_type', None) else None

    if type_name == 'SentCodeTypeApp':
        return {
            'channel': 'app',
            'title': 'Telegram App (In-App Chat)',
            'length': length,
            'desc': f'Telegram sent the {length or 5}-digit code directly inside your active Telegram app (chat from official "Telegram" service account, ID 777000).',
            'warning': 'NOT sent via SMS! Check the Telegram app on your phone or PC. If not in main chat list, check your "Archived Chats" folder.',
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeSms':
        return {
            'channel': 'sms',
            'title': 'SMS Text Message',
            'length': length,
            'desc': f'Code sent via standard SMS text message{length_str} to your cellular phone carrier inbox.',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeEmailCode':
        pattern = getattr(code_type_obj, 'email_pattern', 'registered email')
        return {
            'channel': 'email',
            'title': 'Email Inbox',
            'length': length,
            'desc': f'Code sent to your Telegram linked email: {pattern}{length_str}. Check inbox and spam.',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeSetUpEmailRequired':
        return {
            'channel': 'email_setup_required',
            'title': 'Login Email Required by Telegram (NO CODE SENT)',
            'desc': 'Telegram refused to dispatch a code. Your Telegram account requires setting up a Login Email in the official mobile app before 3rd-party logins are allowed.',
            'warning': 'Open Telegram mobile app → Settings → Privacy & Security → Login Email. Add and verify your email address, then try again.',
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeFragmentSms':
        url = getattr(code_type_obj, 'url', '')
        return {
            'channel': 'fragment',
            'title': 'Fragment Anonymous Number',
            'length': length,
            'desc': f'Code sent to your Fragment portal{length_str}: {url}',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeMissedCall':
        prefix = getattr(code_type_obj, 'prefix', '')
        return {
            'channel': 'missed_call',
            'title': 'Missed Call Verification',
            'length': length,
            'desc': f'Telegram is making a missed call from a number starting with {prefix}. Enter the last {length or 5} digits of the caller number.',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeCall':
        return {
            'channel': 'call',
            'title': 'Voice Phone Call',
            'length': length,
            'desc': f'Telegram is calling your phone to dictate the {length or 5}-digit code.',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }
    elif type_name == 'SentCodeTypeFlashCall':
        pattern = getattr(code_type_obj, 'pattern', '')
        return {
            'channel': 'flash_call',
            'title': 'Flash Call Verification',
            'length': length,
            'desc': f'Telegram is making a flash call matching pattern: {pattern}',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }
    else:
        return {
            'channel': 'other',
            'title': type_name,
            'length': length,
            'desc': f'Code dispatched via {type_name}{length_str}.',
            'warning': None,
            'timeout': timeout,
            'next_type': next_type
        }

# -------------------------------------------------------------------------
# RESILIENT LOGIN FLOW
# -------------------------------------------------------------------------

@auth_bp.route('/api/auth/send_code', methods=['POST'])
def send_code():
    """Initiates login by sending verification code via Telegram."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    global _LAST_CODE_REQUEST
    now = time.time()
    if now - _LAST_CODE_REQUEST < 15:
        return jsonify({'error': 'Please wait 15 seconds between code requests'}), 429
    _LAST_CODE_REQUEST = now

    if not (core.API_ID and core.API_HASH):
        return jsonify({'error': 'api_id and api_hash not configured in config.ini'}), 400

    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    session_alias = str(data.get('session_name', '')).strip()
    force_sms = bool(data.get('force_sms', False))

    if not phone or len(phone) < 5:
        return jsonify({'error': 'Please enter a valid phone number with country code'}), 400
    if session_alias and not _is_valid_session_name(session_alias):
        return jsonify({'error': 'Invalid session_name format'}), 400

    _reset_auth_state()

    # Create isolated session file so existing active session is not corrupted during login
    safe_phone = "".join(c for c in phone if c.isdigit())[:20]
    if not safe_phone:
        return jsonify({'error': 'Phone number must contain digits'}), 400
    target_session = session_alias or f"session_{safe_phone}"
    if not _is_valid_session_name(target_session):
        return jsonify({'error': 'Invalid session_name format'}), 400

    # If target session file exists but is NOT authorized, delete stale file to ensure clean handshake
    if not has_persistent_session(target_session):
        for ext in ['.session', '.session-shm', '.session-wal', '.session-journal']:
            fp = os.path.join(core.PROJECT_ROOT, f"{target_session}{ext}")
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass

    loop = asyncio.new_event_loop()
    session_abs_prefix = os.path.join(core.PROJECT_ROOT, target_session)
    client = TelegramClient(session_abs_prefix, core.API_ID, core.API_HASH, loop=loop)

    try:
        loop.run_until_complete(client.connect())
        core.restrict_file_permissions(f"{session_abs_prefix}.session")
        res = loop.run_until_complete(client.send_code_request(phone, force_sms=force_sms))

        delivery_info = _parse_sent_code(res)
        print(f"[AUTH] send_code for {_mask_phone(phone)}: res={res}, delivery={delivery_info['title']} ({delivery_info['channel']})", flush=True)

        _AUTH_STATE['client'] = client
        _AUTH_STATE['loop'] = loop
        _AUTH_STATE['phone'] = phone
        _AUTH_STATE['phone_code_hash'] = getattr(res, 'phone_code_hash', None)
        _AUTH_STATE['target_session'] = target_session
        _AUTH_STATE['expires_at'] = time.time() + 300
        _AUTH_STATE['attempts'] = 0

        return jsonify({
            'ok': True,
            'timeout': delivery_info.get('timeout', 300),
            'session_name': target_session,
            'delivery': delivery_info,
            'message': f"Verification code dispatched via {delivery_info['title']}"
        })
    except PhoneNumberInvalidError:
        _reset_auth_state()
        return jsonify({'error': 'The phone number is invalid. Check country code.'}), 400
    except PhoneNumberUnoccupiedError:
        _reset_auth_state()
        return jsonify({'error': 'Phone number is not registered on Telegram. Sign up on mobile app first.'}), 400
    except PhoneNumberBannedError:
        _reset_auth_state()
        return jsonify({'error': 'This phone number has been banned by Telegram.'}), 403
    except FloodWaitError as e:
        _reset_auth_state()
        return jsonify({'error': f'Telegram flood wait: please wait {e.seconds} seconds'}), 429
    except Exception as e:
        _reset_auth_state()
        print(f"[AUTH ERROR] send_code failed for {_mask_phone(phone)}: {type(e).__name__}: {e}", flush=True)
        return jsonify({'error': f'Failed to send code: {type(e).__name__} ({e})'}), 500

@auth_bp.route('/api/auth/resend_code', methods=['POST'])
def resend_code():
    """Requests resending verification code via SMS or next verification channel."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    if not _AUTH_STATE['client'] or not _AUTH_STATE['loop'] or time.time() > _AUTH_STATE['expires_at']:
        _reset_auth_state()
        return jsonify({'error': 'Login session expired. Please request a new code.'}), 408

    client = _AUTH_STATE['client']
    loop = _AUTH_STATE['loop']
    phone = _AUTH_STATE['phone']
    phone_code_hash = _AUTH_STATE['phone_code_hash']

    try:
        from telethon.tl.functions.auth import ResendCodeRequest
        res = loop.run_until_complete(
            client(ResendCodeRequest(phone_number=phone, phone_code_hash=phone_code_hash))
        )
        delivery_info = _parse_sent_code(res)
        _AUTH_STATE['phone_code_hash'] = getattr(res, 'phone_code_hash', phone_code_hash)
        print(f"[AUTH] resend_code: res={res}, delivery={delivery_info['title']}", flush=True)
        return jsonify({
            'ok': True,
            'delivery': delivery_info,
            'message': f"Code resent via {delivery_info['title']}"
        })
    except FloodWaitError as e:
        return jsonify({'error': f'Telegram flood wait: please wait {e.seconds} seconds'}), 429
    except Exception as e:
        # Fallback to force_sms via send_code_request
        try:
            res = loop.run_until_complete(client.send_code_request(phone, force_sms=True))
            delivery_info = _parse_sent_code(res)
            _AUTH_STATE['phone_code_hash'] = getattr(res, 'phone_code_hash', phone_code_hash)
            print(f"[AUTH] resend_code fallback (force_sms): res={res}, delivery={delivery_info['title']}", flush=True)
            return jsonify({
                'ok': True,
                'delivery': delivery_info,
                'message': f"Code resent via {delivery_info['title']}"
            })
        except Exception as e2:
            return jsonify({'error': f'Failed to resend code: {type(e2).__name__} ({e2})'}), 500

@auth_bp.route('/api/auth/qr_start', methods=['POST'])
def qr_start():
    """Initiates instant QR code login via Telegram MTProto."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    if not (core.API_ID and core.API_HASH):
        return jsonify({'error': 'api_id and api_hash not configured in config.ini'}), 400

    data = request.get_json(silent=True) or {}
    session_alias = str(data.get('session_name', '')).strip()
    if session_alias and not _is_valid_session_name(session_alias):
        return jsonify({'error': 'Invalid session_name format'}), 400

    _reset_auth_state()
    _reset_qr_state()

    target_session = session_alias or f"session_qr_{int(time.time())}"
    if not _is_valid_session_name(target_session):
        return jsonify({'error': 'Invalid session_name format'}), 400

    # Clean stale unauthenticated session file
    if not has_persistent_session(target_session):
        for ext in ['.session', '.session-shm', '.session-wal', '.session-journal']:
            fp = os.path.join(core.PROJECT_ROOT, f"{target_session}{ext}")
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass

    loop = asyncio.new_event_loop()
    session_abs_prefix = os.path.join(core.PROJECT_ROOT, target_session)
    client = TelegramClient(session_abs_prefix, core.API_ID, core.API_HASH, loop=loop)

    try:
        loop.run_until_complete(client.connect())
        core.restrict_file_permissions(f"{session_abs_prefix}.session")
        qr = loop.run_until_complete(client.qr_login())

        # Generate SVG in-memory
        img = qrcode.make(qr.url, image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        svg_content = buf.getvalue().decode('utf-8')

        _QR_STATE['client'] = client
        _QR_STATE['loop'] = loop
        _QR_STATE['qr'] = qr
        _QR_STATE['target_session'] = target_session
        _QR_STATE['expires_at'] = qr.expires.timestamp() if qr.expires else (time.time() + 60)
        _QR_STATE['done'] = False
        _QR_STATE['user'] = None
        _QR_STATE['error'] = None
        _QR_STATE['need_2fa'] = False

        # Start background wait thread
        def _wait_thread_func(c, q, s_name, lp):
            try:
                user = lp.run_until_complete(q.wait())
                me = user
                if not me:
                    try:
                        me = lp.run_until_complete(c.get_me())
                    except Exception:
                        pass

                user_data = {
                    'id': getattr(me, 'id', 0),
                    'first_name': getattr(me, 'first_name', '') or '',
                    'last_name': getattr(me, 'last_name', '') or '',
                    'username': getattr(me, 'username', '') or '',
                    'phone': _mask_phone(getattr(me, 'phone', ''))
                }

                update_session_meta(s_name, user_data)
                save_persisted_user(user_data)

                try:
                    config_path = os.path.join(core.PROJECT_ROOT, 'config.ini')
                    cfg = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
                    cfg.read(config_path, encoding='utf-8')
                    if not cfg.has_section('Telegram'):
                        cfg.add_section('Telegram')
                    cfg.set('Telegram', 'session_name', s_name)
                    with open(config_path, 'w', encoding='utf-8') as f:
                        cfg.write(f)
                    core.SESSION_FILE = s_name
                except Exception:
                    pass

                _QR_STATE['done'] = True
                _QR_STATE['user'] = user_data
                _STATUS_CACHE['authorized'] = True
                _STATUS_CACHE['last_check'] = time.time()
                _STATUS_CACHE['user'] = user_data

                print(f"[AUTH] QR login success: user={user_data.get('username') or user_data.get('id')}", flush=True)
                trigger_server_restart()

            except SessionPasswordNeededError:
                print("[AUTH] QR login requires 2FA password", flush=True)
                _QR_STATE['need_2fa'] = True
                _AUTH_STATE['client'] = c
                _AUTH_STATE['loop'] = lp
                _AUTH_STATE['target_session'] = s_name
                _AUTH_STATE['expires_at'] = time.time() + 300
                _AUTH_STATE['attempts'] = 0
            except asyncio.TimeoutError:
                _QR_STATE['done'] = True
                _QR_STATE['error'] = 'QR code expired. Click "Refresh QR Code" to generate a new one.'
            except Exception as e:
                _QR_STATE['done'] = True
                _QR_STATE['error'] = f"QR login failed: {type(e).__name__} ({e})"

        threading.Thread(target=_wait_thread_func, args=(client, qr, target_session, loop), daemon=True).start()

        return jsonify({
            'ok': True,
            'svg': svg_content,
            'url': qr.url,
            'expires_in': int(_QR_STATE['expires_at'] - time.time()),
            'session_name': target_session
        })
    except Exception as e:
        _reset_qr_state()
        return jsonify({'error': f'Failed to generate QR code: {type(e).__name__} ({e})'}), 500

@auth_bp.route('/api/auth/qr_status', methods=['GET'])
def qr_status():
    """Polls QR login confirmation status."""
    return jsonify({
        'done': _QR_STATE['done'],
        'authorized': bool(_QR_STATE['user']),
        'user': _QR_STATE['user'],
        'need_2fa': _QR_STATE['need_2fa'],
        'error': _QR_STATE['error'],
        'restarting': bool(_QR_STATE['user'])
    })

@auth_bp.route('/api/auth/sign_in', methods=['POST'])
def sign_in():
    """Submits verification code to complete login or initiate 2FA. Crash-proof for non-password accounts."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    data = request.get_json(silent=True) or {}
    code = str(data.get('code', '')).strip()

    if not code:
        return jsonify({'error': 'Please enter the verification code'}), 400

    if not _AUTH_STATE['client'] or not _AUTH_STATE['loop'] or time.time() > _AUTH_STATE['expires_at']:
        _reset_auth_state()
        return jsonify({'error': 'Login session expired. Please request a new code.'}), 408

    if _AUTH_STATE['attempts'] >= 3:
        _reset_auth_state()
        return jsonify({'error': 'Too many failed attempts. Login cancelled.'}), 429

    _AUTH_STATE['attempts'] += 1
    client = _AUTH_STATE['client']
    loop = _AUTH_STATE['loop']
    target_session = _AUTH_STATE.get('target_session') or core.SESSION_FILE

    try:
        user = loop.run_until_complete(
            client.sign_in(
                phone=_AUTH_STATE['phone'],
                code=code,
                phone_code_hash=_AUTH_STATE['phone_code_hash']
            )
        )

        # Login complete WITHOUT 2FA password
        me = user
        if not me:
            try:
                me = loop.run_until_complete(client.get_me())
            except Exception:
                pass

        user_data = {
            'id': getattr(me, 'id', 0),
            'first_name': getattr(me, 'first_name', '') or '',
            'last_name': getattr(me, 'last_name', '') or '',
            'username': getattr(me, 'username', '') or '',
            'phone': _mask_phone(_AUTH_STATE.get('phone', ''))
        }

        update_session_meta(target_session, user_data)
        save_persisted_user(user_data)
        _reset_auth_state()

        # Update config.ini to make new session active
        try:
            config_path = os.path.join(core.PROJECT_ROOT, 'config.ini')
            cfg = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
            cfg.read(config_path, encoding='utf-8')
            if not cfg.has_section('Telegram'):
                cfg.add_section('Telegram')
            cfg.set('Telegram', 'session_name', target_session)
            with open(config_path, 'w', encoding='utf-8') as f:
                cfg.write(f)
            core.SESSION_FILE = target_session
        except Exception:
            pass

        _STATUS_CACHE['authorized'] = True
        _STATUS_CACHE['last_check'] = time.time()
        _STATUS_CACHE['user'] = user_data

        trigger_server_restart()
        return jsonify({'ok': True, 'user': user_data, 'session_name': target_session, 'restarting': True})

    except SessionPasswordNeededError:
        # 2FA password required
        return jsonify({'ok': True, 'need_2fa': True, 'message': '2FA cloud password required'})
    except PhoneCodeInvalidError:
        return jsonify({'error': 'Invalid verification code'}), 400
    except PhoneCodeExpiredError:
        _reset_auth_state()
        return jsonify({'error': 'Verification code expired. Request a new code.'}), 400
    except PhoneCodeEmptyError:
        return jsonify({'error': 'Verification code cannot be empty'}), 400
    except FloodWaitError as e:
        _reset_auth_state()
        return jsonify({'error': f'Flood wait: please wait {e.seconds} seconds'}), 429
    except Exception as e:
        return jsonify({'error': f'Sign-in error: {type(e).__name__}'}), 500

@auth_bp.route('/api/auth/2fa', methods=['POST'])
def submit_2fa():
    """Submits 2FA cloud password and immediately sanitizes memory."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    data = request.get_json(silent=True) or {}
    password = data.get('password', '')

    if not password:
        return jsonify({'error': 'Password cannot be empty'}), 400

    if not _AUTH_STATE['client'] or not _AUTH_STATE['loop'] or time.time() > _AUTH_STATE['expires_at']:
        _reset_auth_state()
        return jsonify({'error': 'Login session expired. Please request a new code.'}), 408

    if _AUTH_STATE['attempts'] >= 4:
        _reset_auth_state()
        return jsonify({'error': 'Too many failed attempts. Login cancelled.'}), 429

    _AUTH_STATE['attempts'] += 1
    client = _AUTH_STATE['client']
    loop = _AUTH_STATE['loop']
    target_session = _AUTH_STATE.get('target_session') or core.SESSION_FILE

    try:
        user = loop.run_until_complete(client.sign_in(password=password))
        me = user
        if not me:
            try:
                me = loop.run_until_complete(client.get_me())
            except Exception:
                pass

        del password
        user_data = {
            'id': getattr(me, 'id', 0),
            'first_name': getattr(me, 'first_name', '') or '',
            'last_name': getattr(me, 'last_name', '') or '',
            'username': getattr(me, 'username', '') or '',
            'phone': _mask_phone(_AUTH_STATE.get('phone', ''))
        }

        update_session_meta(target_session, user_data)
        save_persisted_user(user_data)
        _reset_auth_state()

        # Update config.ini to make new session active
        try:
            config_path = os.path.join(core.PROJECT_ROOT, 'config.ini')
            cfg = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
            cfg.read(config_path, encoding='utf-8')
            if not cfg.has_section('Telegram'):
                cfg.add_section('Telegram')
            cfg.set('Telegram', 'session_name', target_session)
            with open(config_path, 'w', encoding='utf-8') as f:
                cfg.write(f)
            core.SESSION_FILE = target_session
        except Exception:
            pass

        _STATUS_CACHE['authorized'] = True
        _STATUS_CACHE['last_check'] = time.time()
        _STATUS_CACHE['user'] = user_data

        trigger_server_restart()
        return jsonify({'ok': True, 'user': user_data, 'session_name': target_session, 'restarting': True})

    except PasswordHashInvalidError:
        del password
        return jsonify({'error': 'Invalid 2FA password'}), 400
    except FloodWaitError as e:
        del password
        _reset_auth_state()
        return jsonify({'error': f'Flood wait: please wait {e.seconds} seconds'}), 429
    except Exception as e:
        del password
        return jsonify({'error': f'2FA error: {type(e).__name__}'}), 500

@auth_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    """Logs out and clears local session cache."""
    if not verify_csrf():
        return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

    _reset_auth_state()
    _STATUS_CACHE['authorized'] = False
    _STATUS_CACHE['user'] = None

    session_file = os.path.join(core.PROJECT_ROOT, f"{core.SESSION_FILE}.session")
    if _is_valid_session_name(core.SESSION_FILE) and os.path.exists(session_file):
        try:
            os.remove(session_file)
        except Exception:
            pass

    return jsonify({'ok': True, 'message': 'Logged out successfully'})
