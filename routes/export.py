"""
routes/export.py — WebUI Export Engine and Archive Maintenance Tools API.

Provides endpoints for:
- Exporting Telegram chat history to Markdown/JSON with custom date ranges,
  partitioning intervals (Single, Day, Week, Month), and detail styles.
- Streaming generated export bundles (.md or .zip) directly to browser or saving to disk.
- Querying export preview counts and previous export history.
- Running background maintenance tools (dedup_hardlinks, import_tg_export).
"""

import os
import sys
import uuid
import json
import zipfile
import tempfile
import threading
import io
import logging
from pathlib import PurePath
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, send_file, Response, current_app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import core
from core import db
from core.db import get_db_conn, get_chat_pipeline
import tools.db_export as db_export

export_bp = Blueprint('export_bp', __name__)

EXPORT_DIR = os.path.join(PROJECT_ROOT, "db_exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# In-memory buffer for async tool execution tasks
TOOL_TASKS = {}
TOOL_TASKS_LOCK = threading.Lock()


@export_bp.route('/api/export/preview_count', methods=['GET'])
def export_preview_count():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id required'}), 400

    range_mode = request.args.get('range_mode', 'all')
    latest_days = request.args.get('latest_days', type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    conn = get_db_conn()
    cursor = conn.cursor()
    pipeline = get_chat_pipeline(cursor, chat_id)
    placeholders = ','.join('?' for _ in pipeline)

    query = f"SELECT COUNT(*), MIN(date), MAX(date) FROM tbl_messages WHERE chat_id IN ({placeholders})"
    params = list(pipeline)

    if range_mode == 'latest_days' and latest_days:
        cursor.execute(f"SELECT MAX(date) FROM tbl_messages WHERE chat_id IN ({placeholders})", list(pipeline))
        row_max = cursor.fetchone()
        latest_str = row_max[0] if (row_max and row_max[0]) else None
        if latest_str:
            dt_latest = datetime.fromisoformat(latest_str.replace('Z', '+00:00'))
        else:
            dt_latest = datetime.now(timezone.utc)
        computed_start = (dt_latest - timedelta(days=latest_days)).strftime('%Y-%m-%d')
        query += " AND date >= ?"
        params.append(computed_start)
    elif range_mode == 'custom_range':
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date + "T23:59:59")

    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()

    count = row[0] if row else 0
    min_date = row[1] if row else None
    max_date = row[2] if row else None

    return jsonify({
        'chat_id': chat_id,
        'count': count,
        'min_date': min_date,
        'max_date': max_date
    })


@export_bp.route('/api/export/run', methods=['POST'])
def export_run():
    data = request.get_json(force=True, silent=True) or {}
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({'error': 'chat_id required'}), 400

    try:
        chat_id = int(chat_id)
    except ValueError:
        return jsonify({'error': 'invalid chat_id'}), 400

    options = {
        'format': data.get('format', 'md'),
        'style': data.get('style', 'verbose'),
        'interval': data.get('interval', 'single'),
        'range_mode': data.get('range_mode', 'all'),
        'latest_days': data.get('latest_days'),
        'start_date': data.get('start_date'),
        'end_date': data.get('end_date'),
        'show_reactions': bool(data.get('show_reactions', True)),
        'show_replies': bool(data.get('show_replies', True)),
        'show_forwards': bool(data.get('show_forwards', True)),
        'show_media': bool(data.get('show_media', True)),
        'show_polls': bool(data.get('show_polls', True)),
        'show_service': bool(data.get('show_service', True)),
    }
    lang = data.get('lang', 'en')
    delivery = data.get('delivery', 'download')

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT chat_name FROM tbl_chats WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    chat_name = row['chat_name'] if row else f"Chat_{chat_id}"
    safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '_')).rstrip().replace(" ", "_")
    if not safe_name:
        safe_name = f"chat_{chat_id}"
    users_cache = db_export.load_users_cache(cursor, lang=lang)
    conn.close()

    if delivery == 'download':
        # Create in temporary directory to stream
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = db_export.export_chat_to_md(
                chat_id=chat_id,
                chat_name=chat_name,
                users_cache=users_cache,
                options=options,
                lang=lang,
                custom_output_dir=tmp_dir
            )
            created_files = res.get('files', [])
            if not created_files:
                return jsonify({'error': 'No messages to export for given criteria'}), 404

            if len(created_files) == 1:
                single_path = created_files[0]
                filename = os.path.basename(single_path)
                with open(single_path, 'rb') as f:
                    file_bytes = f.read()
                mimetype = "application/json" if filename.endswith('.json') else "text/markdown"
                return Response(
                    file_bytes,
                    mimetype=mimetype,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                )
            else:
                # Multiple files created by interval partitioning -> bundle into ZIP
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for fpath in created_files:
                        zip_file.write(fpath, arcname=os.path.basename(fpath))
                zip_buffer.seek(0)
                zip_filename = f"{safe_name}_export_{options.get('interval', 'archive')}.zip"
                return Response(
                    zip_buffer.getvalue(),
                    mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'}
                )

    # delivery == 'disk' or 'both'
    res = db_export.export_chat_to_md(
        chat_id=chat_id,
        chat_name=chat_name,
        users_cache=users_cache,
        options=options,
        lang=lang,
        custom_output_dir=EXPORT_DIR
    )
    return jsonify({
        'status': res.get('status', 'ok'),
        'exported_count': res.get('exported_count', 0),
        'files': [os.path.basename(f) for f in res.get('files', [])],
        'output_dir': res.get('output_dir', EXPORT_DIR)
    })


@export_bp.route('/api/export/history', methods=['GET'])
def export_history():
    chat_id = request.args.get('chat_id', type=int)
    results = []

    if not os.path.exists(EXPORT_DIR):
        return jsonify({'files': []})

    for entry in os.scandir(EXPORT_DIR):
        if entry.is_file() and (entry.name.endswith('.md') or entry.name.endswith('.json') or entry.name.endswith('.zip')):
            if chat_id:
                # Filter if matching chat_id or safe folder
                # Optional check: match ID in filename
                pass
            st = entry.stat()
            size = st.st_size
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"

            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            results.append({
                'filename': entry.name,
                'size_bytes': size,
                'size_str': size_str,
                'mtime': mtime,
                'download_url': f"/api/export/download_file?file={entry.name}"
            })

    results.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify({'files': results})


@export_bp.route('/api/export/download_file', methods=['GET'])
def export_download_file():
    filename = request.args.get('file', '')
    if not filename or '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Invalid filename'}), 400

    target = os.path.join(EXPORT_DIR, filename)
    try:
        real_export = os.path.realpath(EXPORT_DIR)
        real_target = os.path.realpath(target)
        if os.path.islink(target) or os.path.commonpath([real_export, real_target]) != real_export:
            return jsonify({'error': 'Invalid filename'}), 403
    except Exception:
        return jsonify({'error': 'Invalid filename'}), 403
    if not os.path.isfile(real_target):
        return jsonify({'error': 'File not found'}), 404

    mimetype = "text/markdown"
    if filename.endswith('.json'):
        mimetype = "application/json"
    elif filename.endswith('.zip'):
        mimetype = "application/zip"

    return send_file(target, mimetype=mimetype, as_attachment=True, download_name=filename)


# --- Maintenance Tools API ---

def _run_tool_thread(task_id, tool_name, execute, args):
    task = TOOL_TASKS[task_id]
    log_lines = task['logs']

    def log(msg):
        log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    try:
        log(f"Starting {tool_name} (execute={execute})...")

        if tool_name == 'dedup_hardlinks':
            import tools.dedup_hardlinks as d_tool
            # Redirect stdout to capture prints
            old_stdout = sys.stdout
            sys.stdout = buffer = io.StringIO()
            try:
                d_tool.dedup_hardlinks(execute=execute)
            finally:
                sys.stdout = old_stdout
            for line in buffer.getvalue().splitlines():
                if line.strip(): log(line)

        elif tool_name == 'import_tg_export':
            import tools.import_tg_export as imp_tool
            json_path = (args.get('json_path') or '').strip() if isinstance(args, dict) else ''
            chat_id = args.get('chat_id') if isinstance(args, dict) else None
            if not json_path or '..' in PurePath(json_path).parts or os.path.islink(json_path):
                log("Error: Invalid or disallowed export path")
                task['status'] = 'error'
                return
            if not os.path.exists(json_path):
                log(f"Error: Export path not found at '{json_path}'")
                task['status'] = 'error'
                return
            if os.path.isfile(json_path) and not json_path.lower().endswith('.json'):
                log("Error: File path must point to a .json export file or directory")
                task['status'] = 'error'
                return
            old_stdout = sys.stdout
            sys.stdout = buffer = io.StringIO()
            try:
                ok = imp_tool.import_tg_export(
                    export_dir=json_path,
                    custom_chat_id=chat_id,
                    execute=execute,
                )
            finally:
                sys.stdout = old_stdout
            for line in buffer.getvalue().splitlines():
                if line.strip(): log(line)
            if not ok:
                task['status'] = 'error'
                return

        else:
            log(f"Error: Unknown tool '{tool_name}'")
            task['status'] = 'error'
            return

        log("Finished successfully.")
        task['status'] = 'completed'

    except Exception as e:
        log(f"Fatal error: {e}")
        logging.error(f"Error in {tool_name} task {task_id}", exc_info=True)
        task['status'] = 'error'


@export_bp.route('/api/tools/run', methods=['POST'])
def tools_run():
    data = request.get_json(force=True, silent=True) or {}
    tool_name = data.get('tool')
    execute = bool(data.get('execute', False))
    args = data.get('args', {})

    valid_tools = {'dedup_hardlinks', 'import_tg_export'}
    if tool_name not in valid_tools:
        return jsonify({'error': f"Unknown tool. Supported: {list(valid_tools)}"}), 400

    task_id = str(uuid.uuid4())[:8]
    task = {
        'id': task_id,
        'tool': tool_name,
        'execute': execute,
        'status': 'running',
        'logs': [],
        'started_at': datetime.now().isoformat()
    }

    with TOOL_TASKS_LOCK:
        while len(TOOL_TASKS) >= 50:
            done_key = next((k for k, v in TOOL_TASKS.items() if v.get('status') in ('completed', 'error')), None)
            evict_key = done_key if done_key is not None else next(iter(TOOL_TASKS))
            TOOL_TASKS.pop(evict_key, None)
        TOOL_TASKS[task_id] = task

    t = threading.Thread(target=_run_tool_thread, args=(task_id, tool_name, execute, args), daemon=True)
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id, 'tool': tool_name, 'execute': execute})


@export_bp.route('/api/tools/status', methods=['GET'])
def tools_status():
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'error': 'task_id required'}), 400

    with TOOL_TASKS_LOCK:
        task = TOOL_TASKS.get(task_id)

    if not task:
        return jsonify({'error': 'Task not found'}), 404

    return jsonify(task)

