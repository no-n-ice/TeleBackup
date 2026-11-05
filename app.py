#!/usr/bin/env python3
"""
app.py — Main Flask application entry point.
"""
import os
import sys
import time
import socket
import secrets
import threading
import urllib.parse
import logging

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from flask import Flask, request, redirect, jsonify, make_response, render_template_string, send_from_directory
import core
from core import worker
from routes import register_blueprints

# --- Load Environment Variables (.env) ---
def _load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

_load_env_file()

# Load App Version
VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'VERSION')
try:
    with open(VERSION_FILE, 'r', encoding='utf-8') as f:
        APP_VERSION = f.read().strip()
except Exception:
    APP_VERSION = '0.0.0'

# Initialize Flask
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = core.load_or_generate_secret('.flask_secret', 32)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB upload limit

# --- Access PIN & Session Security ---
ACCESS_PIN = os.environ.get('TELEBACKUP_PIN', '').strip()
if not ACCESS_PIN:
    ACCESS_PIN = core.load_or_generate_secret('.pin', 4)  # 8 hex chars, stable fallback

SESSION_TOKEN = core.load_or_generate_secret('.session_token', 32)

_DEFAULT_ALLOWED_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})

def _extract_host_and_port(raw_host, default_scheme='http'):
    if not raw_host:
        return '', None
    raw = raw_host.strip().lower()
    if ':' in raw and not raw.startswith('[') and not raw.startswith('http://') and not raw.startswith('https://'):
        if raw.count(':') > 1:
            raw = f"[{raw}]"
    if not raw.startswith('http://') and not raw.startswith('https://'):
        url = f"{default_scheme}://{raw}"
    else:
        url = raw
    try:
        p = urllib.parse.urlsplit(url)
        return (p.hostname or '').lower(), p.port or (443 if p.scheme == 'https' else 80)
    except Exception:
        return '', None

def _is_allowed_host(raw_host):
    if not raw_host:
        return False
    h, _ = _extract_host_and_port(raw_host)
    if not h:
        return False
    if h in _DEFAULT_ALLOWED_HOSTS:
        return True
    extra_raw = os.environ.get('TELEBACKUP_ALLOWED_HOSTS', '')
    if extra_raw:
        for item in extra_raw.split(','):
            item = item.strip()
            if not item:
                continue
            item_h, _ = _extract_host_and_port(item)
            if h == item_h:
                return True
    return False

def _is_allowed_origin(origin, req_host):
    if not origin:
        return True
    if origin == 'null':
        return False
    origin_host, origin_port = _extract_host_and_port(origin)
    req_host_only, req_port = _extract_host_and_port(req_host)
    if not origin_host or not req_host_only:
        return False
    # Exact match on host and port
    if origin_host == req_host_only and origin_port == req_port:
        return True
    # Equivalent local loopback variants (127.0.0.1, localhost, ::1) or allowed hosts on matching port
    if _is_allowed_host(origin_host) and _is_allowed_host(req_host_only):
        if origin_port == req_port:
            return True
    return False

# Sliding-window IP rate limiter for PIN verification
_FAILED_PIN_ATTEMPTS = {}  # ip -> list of timestamps
_PIN_LIMIT_LOCK = threading.Lock()
_MAX_TRACKED_PIN_IPS = 1024

def _prune_pin_attempts_unlocked(now):
    expired = [ip for ip, ts_list in _FAILED_PIN_ATTEMPTS.items() if not ts_list or (now - ts_list[-1]) >= 60]
    for ip in expired:
        _FAILED_PIN_ATTEMPTS.pop(ip, None)
    while len(_FAILED_PIN_ATTEMPTS) >= _MAX_TRACKED_PIN_IPS:
        oldest_ip = next(iter(_FAILED_PIN_ATTEMPTS))
        _FAILED_PIN_ATTEMPTS.pop(oldest_ip, None)

def _check_pin_rate_limit(client_ip):
    now = time.time()
    with _PIN_LIMIT_LOCK:
        if len(_FAILED_PIN_ATTEMPTS) > 512:
            _prune_pin_attempts_unlocked(now)
        attempts = [t for t in _FAILED_PIN_ATTEMPTS.get(client_ip, []) if now - t < 60]
        if attempts:
            _FAILED_PIN_ATTEMPTS[client_ip] = attempts
        else:
            _FAILED_PIN_ATTEMPTS.pop(client_ip, None)
        if len(attempts) >= 5:
            return False, int(60 - (now - attempts[0]))
    return True, 0

def _record_pin_failure(client_ip):
    now = time.time()
    with _PIN_LIMIT_LOCK:
        if client_ip not in _FAILED_PIN_ATTEMPTS and len(_FAILED_PIN_ATTEMPTS) >= _MAX_TRACKED_PIN_IPS:
            _prune_pin_attempts_unlocked(now)
        attempts = [t for t in _FAILED_PIN_ATTEMPTS.get(client_ip, []) if now - t < 60]
        attempts.append(now)
        _FAILED_PIN_ATTEMPTS[client_ip] = attempts



LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TeleBackup — Security Access</title>
    <style>
        :root {
            --bg-color: #0f141c;
            --card-bg: #181f2a;
            --border-color: #2b3648;
            --accent-color: #2481cc;
            --accent-hover: #2997e0;
            --text-color: #e5eaf2;
            --text-dim: #8a99ad;
            --error-color: #ff5252;
        }
        body {
            margin: 0;
            padding: 0;
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .login-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 36px 32px;
            width: 100%;
            max-width: 380px;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5);
            text-align: center;
            box-sizing: border-box;
        }
        .icon {
            font-size: 44px;
            margin-bottom: 12px;
            display: inline-block;
        }
        h1 {
            font-size: 20px;
            margin: 0 0 8px 0;
            font-weight: 600;
        }
        p {
            font-size: 13px;
            color: var(--text-dim);
            margin: 0 0 24px 0;
            line-height: 1.5;
        }
        .pin-input {
            width: 100%;
            box-sizing: border-box;
            background: rgba(0, 0, 0, 0.25);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 12px;
            font-size: 20px;
            letter-spacing: 6px;
            text-align: center;
            color: #ffffff;
            outline: none;
            transition: border-color 0.2s;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            text-transform: uppercase;
        }
        .pin-input:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 2px rgba(36, 129, 204, 0.25);
        }
        .submit-btn {
            width: 100%;
            background: var(--accent-color);
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 12px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 16px;
            transition: background 0.2s;
        }
        .submit-btn:hover {
            background: var(--accent-hover);
        }
        .error-msg {
            background: rgba(255, 82, 82, 0.12);
            border: 1px solid rgba(255, 82, 82, 0.3);
            color: var(--error-color);
            padding: 10px;
            border-radius: 6px;
            font-size: 12px;
            margin-bottom: 18px;
        }
        .hint {
            margin-top: 20px;
            font-size: 11px;
            color: var(--text-dim);
            background: rgba(255, 255, 255, 0.03);
            padding: 8px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon">🔒</div>
        <h1>TeleBackup Access</h1>
        <p>Enter the access PIN shown in your console terminal to unlock.</p>
        {% if error == '2' %}
        <div class="error-msg">⚠️ Too many failed attempts. Please wait {{ retry }}s before trying again.</div>
        {% elif error %}
        <div class="error-msg">⚠️ Invalid PIN. Please check your console.</div>
        {% endif %}
        <form method="POST" action="/api/app/verify_pin">
            <input type="text" name="pin" class="pin-input" placeholder="••••••••" maxlength="32" autofocus autocomplete="off" spellcheck="false" required>
            <button type="submit" class="submit-btn">Unlock Session</button>
        </form>
        <div class="hint">
            💡 The access PIN is generated at server startup and printed to the terminal.
        </div>
    </div>
</body>
</html>
"""

@app.before_request
def security_middleware():
    # 0. DNS Rebinding Protection (applies to all incoming requests)
    if not _is_allowed_host(request.host):
        return jsonify({'error': 'Forbidden Host header', 'code': 'INVALID_HOST'}), 403

    exempt_paths = ('/static/', '/app/login', '/api/app/verify_pin', '/favicon.ico')
    if any(request.path.startswith(p) for p in exempt_paths):
        return

    # Cross-Origin Mutation Guard (applies to state-mutating requests)
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        origin = request.headers.get('Origin')
        if origin and not _is_allowed_origin(origin, request.host):
            return jsonify({'error': 'Cross-origin request blocked', 'code': 'INVALID_ORIGIN'}), 403

    # 1. Session Token Authentication Check (PIN Guard)
    token = request.cookies.get('telebackup_session')
    if not token or not secrets.compare_digest(token, SESSION_TOKEN):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized: Access PIN verification required', 'code': 'SESSION_REQUIRED'}), 401
        return redirect('/app/login')

    # 2. Global CSRF Protection for state-mutating requests
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        from routes.auth import AUTH_CSRF_TOKEN
        token = request.headers.get('X-TeleBackup-Auth-Token')
        if not token:
            body = request.get_json(silent=True) or {}
            token = body.get('auth_csrf_token')
        if not token or not secrets.compare_digest(token, AUTH_CSRF_TOKEN):
            return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_FORBIDDEN'}), 403

@app.after_request
def apply_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.png', mimetype='image/png')

@app.route('/app/login')
def app_login():
    err_code = request.args.get('error', '')
    retry_val = request.args.get('retry', '60')
    return render_template_string(LOGIN_HTML, error=err_code, retry=retry_val)

@app.route('/api/app/verify_pin', methods=['POST'])
def app_verify_pin():
    client_ip = request.remote_addr or '127.0.0.1'
    pin = request.form.get('pin')
    is_json = False
    if pin is None:
        data = request.get_json(silent=True) or {}
        pin = data.get('pin')
        is_json = True
    pin = (pin or '').strip()

    allowed, retry_after = _check_pin_rate_limit(client_ip)
    if not allowed:
        if is_json:
            return jsonify({'ok': False, 'error': f'Too many failed attempts. Try again in {retry_after} seconds.', 'code': 'RATE_LIMITED'}), 429
        return redirect(f'/app/login?error=2&retry={retry_after}')

    if pin and secrets.compare_digest(pin.lower(), ACCESS_PIN.lower()):
        with _PIN_LIMIT_LOCK:
            _FAILED_PIN_ATTEMPTS.pop(client_ip, None)
        if is_json:
            resp = make_response(jsonify({'ok': True}))
        else:
            resp = make_response(redirect('/'))
        resp.set_cookie(
            'telebackup_session',
            SESSION_TOKEN,
            httponly=True,
            secure=request.is_secure,
            samesite='Strict',
            max_age=86400 * 30,
        )
        return resp

    _record_pin_failure(client_ip)
    if is_json:
        return jsonify({'ok': False, 'error': 'Invalid PIN'}), 401
    return redirect('/app/login?error=1')

@app.route('/api/app/logout', methods=['POST'])
def app_logout():
    global SESSION_TOKEN
    SESSION_TOKEN = secrets.token_hex(32)
    try:
        tok_path = os.path.join(core.PROJECT_ROOT, '.session_token')
        with open(tok_path, 'w', encoding='utf-8') as f:
            f.write(SESSION_TOKEN)
        core.restrict_file_permissions(tok_path)
    except Exception:
        pass
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie('telebackup_session', httponly=True, samesite='Strict')
    return resp

@app.context_processor
def inject_version():
    from routes.auth import AUTH_CSRF_TOKEN
    return {'version': APP_VERSION, 'auth_csrf_token': AUTH_CSRF_TOKEN}

# Register Blueprint routes
register_blueprints(app)

# Automatic DB initialization and migration
try:
    _conn = core.get_db_conn()
    _cur = _conn.cursor()
    core.init_db(_cur)
    core.run_db_migration(_cur)
    _conn.commit()
    _conn.close()
except Exception as _e:
    print(f"[App Startup] Database initialization error: {_e}")

# Ensure WAL mode on session file to eliminate database locks and restrict sensitive file permissions
try:
    for _sensitive_rel in ('.env', 'config.ini', 'sessions_meta.json'):
        core.restrict_file_permissions(os.path.join(core.PROJECT_ROOT, _sensitive_rel))
    core.restrict_file_permissions(core.DB_FILE)
    import sqlite3
    _s_file = os.path.join(core.PROJECT_ROOT, f"{core.SESSION_FILE}.session")
    if os.path.exists(_s_file):
        core.restrict_file_permissions(_s_file)
        _s_conn = sqlite3.connect(_s_file, timeout=5.0)
        _s_conn.execute("PRAGMA journal_mode=WAL;")
        _s_conn.execute("PRAGMA busy_timeout=15000;")
        _s_conn.commit()
        _s_conn.close()
except Exception as _e:
    pass

if __name__ == '__main__':
    try:
        import cryptg
        print("[Perf] cryptg detected — fast MTProto decryption active")
    except ImportError:
        print("[Perf] WARNING: cryptg not installed. Install with: pip install cryptg (2-4x download speedup)")

    print(f"[Perf] Parallel download workers: {core.DOWNLOAD_WORKERS}")

    try:
        db_main_conn = core.get_db_conn()
        core.init_db(db_main_conn.cursor())
        core.run_db_migration(db_main_conn.cursor())
        core.link_migrated_chats(db_main_conn)
        db_main_conn.commit()
        db_main_conn.close()
    except Exception as e:
        print(f"Database initialization error: {e}")
    
    # Verify port 8000 is available before starting background worker & live capture threads
    for attempt in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', 8000))
                break
            except OSError as e:
                if attempt < 9 and ("10048" in str(e) or "already in use" in str(e).lower()):
                    print(f"[App Startup] Port 8000 in use (previous instance exiting or duplicate app), waiting 1s ({attempt+1}/10)...")
                    time.sleep(1.0)
                else:
                    print("\n[App Startup] ERROR: Port 8000 is already in use by another running instance of TeleBackup.")
                    print("Only one instance of TeleBackup can run at a time to prevent SQLite session database conflicts.")
                    print("Please close any existing TeleBackup console window before starting a new one.\n")
                    sys.exit(1)

    # Start background archive worker
    worker_thread = threading.Thread(target=worker.archive_worker_thread, daemon=True)
    worker_thread.start()

    # Start live archiver background thread
    worker.start_live_background_thread()
    
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    if not debug_mode:
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
    
    print(f"\n{'='*56}")
    print(f"  [KEY] TELEBACKUP ACCESS PIN: {ACCESS_PIN}")
    print(f"  Enter this PIN in your browser to access TeleBackup.")
    print(f"  (Set TELEBACKUP_PIN in .env to use a custom permanent PIN)")
    print(f"{'='*56}\n")
    print(f"--- Telegram Backup & Viewer v{APP_VERSION} ---")
    print("Open in browser: http://127.0.0.1:8000")
    print(f"Media directory: {core.ABS_MEDIA_BACKUP_DIR}")
    print(f"Debug mode: {'ON (Warning: active debugging)' if debug_mode else 'OFF (Safe)'}")
    
    try:
        app.run(host='127.0.0.1', port=8000, debug=debug_mode, use_reloader=False)
    except OSError as e:
        if "10048" in str(e) or "already in use" in str(e).lower():
            print("\n[App Startup] Port 8000 occupied. Another instance is running.\n")
        else:
            raise
