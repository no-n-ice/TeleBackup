#!/usr/bin/env python3
"""
routes/transcribe.py — Speech-to-Text (faster-whisper) model management, settings, and single/batch transcription routes.
"""
import os
import re
import logging
from pathlib import PurePath
from flask import Blueprint, request, jsonify
import core
from core import db

transcribe_bp = Blueprint('transcribe', __name__)
_VALID_COMPUTE_TYPES = frozenset({'auto', 'int8', 'int8_float16', 'int8_bfloat16', 'float16', 'bfloat16', 'float32'})
_LANG_CODE_RE = re.compile(r'^[A-Za-z]{2,8}$')

def _validate_whisper_download_root(raw_root):
    if not raw_root:
        return '', None
    if '..' in PurePath(raw_root).parts:
        return None, "Relative parent traversal ('..') in download_root is not allowed"
    abs_root = os.path.abspath(raw_root)
    real_root = os.path.realpath(abs_root)
    is_drive_root = os.path.splitdrive(real_root)[1] in ('\\', '/', '')
    blocked_prefixes = (
        'c:\\windows', 'c:\\program files', 'c:\\program files (x86)', 'c:\\programdata',
        '/usr', '/etc', '/bin', '/sbin', '/var', '/dev', '/proc', '/sys', '/boot', '/root',
    )
    low_root = real_root.lower()
    if is_drive_root or any(low_root == b or low_root.startswith(b + ('\\' if '\\' in b else '/')) for b in blocked_prefixes):
        return None, "Cannot set Whisper download_root to a system directory or drive root"
    real_project = os.path.realpath(core.PROJECT_ROOT)
    try:
        if os.path.commonpath([real_project, real_root]) == real_project:
            allowed_sub = (
                os.path.realpath(os.path.join(real_project, 'models')),
                os.path.realpath(os.path.join(real_project, 'whisper_models')),
            )
            if not any(os.path.commonpath([a, real_root]) == a for a in allowed_sub):
                return None, "Whisper download_root inside the project directory must use a dedicated models/ subfolder"
    except ValueError:
        pass
    return real_root, None

# --- Speech-to-Text Transcription Routes (faster-whisper) ---
@transcribe_bp.route('/api/transcribe/status', methods=['GET'])
def get_transcribe_status():
    from core import transcriber
    caps = transcriber.detect_whisper_capabilities()
    saved_config = db.get_whisper_settings()
    configured = bool(saved_config and saved_config.get('configured'))
    default_cfg = {
        'model': 'base',
        'device': 'auto',
        'compute_type': 'auto',
        'download_root': '',
        'configured': configured
    }
    if saved_config:
        default_cfg.update(saved_config)

    dl_root = default_cfg.get('download_root') or None
    models_info = transcriber.get_installed_models_info(download_root=dl_root)
    dl_progress = transcriber.get_download_progress()

    return jsonify({
        'available': caps['available'],
        'has_cuda': caps['has_cuda'],
        'device_name': caps['device_name'],
        'models': ['tiny', 'base', 'small', 'medium', 'large-v3-turbo'],
        'models_info': models_info,
        'download_progress': dl_progress,
        'default_model': 'base',
        'config': default_cfg
    })

@transcribe_bp.route('/api/transcribe/download_start', methods=['POST'])
def start_model_download():
    from core import transcriber
    import threading
    payload = request.get_json(silent=True) or {}
    model_name = payload.get('model', 'base')
    if model_name not in transcriber.VALID_WHISPER_MODELS:
        return jsonify({'error': f'Invalid model name: {model_name}'}), 400

    saved_cfg = db.get_whisper_settings() or {}
    dl_root = saved_cfg.get('download_root') or None

    t = threading.Thread(target=transcriber.download_whisper_model, args=(model_name, dl_root), daemon=True)
    t.start()
    return jsonify({'status': 'started', 'model': model_name})

@transcribe_bp.route('/api/transcribe/delete', methods=['POST'])
def delete_whisper_model():
    from core import transcriber
    payload = request.get_json(silent=True) or {}
    model_name = (payload.get('model') or '').strip()
    if not model_name:
        return jsonify({'error': 'model is required'}), 400
    if model_name not in transcriber.VALID_WHISPER_MODELS:
        return jsonify({'error': f'Invalid model name: {model_name}'}), 400

    saved_cfg = db.get_whisper_settings() or {}
    dl_root = saved_cfg.get('download_root') or None

    res = transcriber.evict_and_delete_model(model_name, download_root=dl_root)
    return jsonify(res)

@transcribe_bp.route('/api/transcribe/batch_start', methods=['POST'])
def start_batch_transcribe():
    from core import worker
    payload = request.get_json(silent=True) or {}
    chat_id = payload.get('chat_id')
    if not chat_id:
        return jsonify({'error': 'chat_id is required'}), 400
    try:
        chat_id = int(chat_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid chat_id'}), 400

    sender_ids = payload.get('sender_ids')
    worker.start_batch_transcribe_task(chat_id, sender_ids=sender_ids)
    return jsonify({'status': 'queued', 'chat_id': chat_id})

@transcribe_bp.route('/api/transcribe/batch_status', methods=['GET'])
def get_batch_transcribe_status():
    from core import worker
    with worker.status_lock:
        return jsonify(dict(worker.ARCHIVE_STATUS))

@transcribe_bp.route('/api/transcribe/settings', methods=['GET', 'POST'])
def handle_transcribe_settings():
    if request.method == 'GET':
        saved_config = db.get_whisper_settings() or {
            'model': 'base',
            'device': 'auto',
            'compute_type': 'auto',
            'download_root': '',
            'configured': False
        }
        return jsonify({'status': 'ok', 'config': saved_config})

    current = db.get_whisper_settings() or {
        'model': 'base',
        'device': 'auto',
        'compute_type': 'auto',
        'download_root': '',
        'configured': False
    }

    from core import transcriber
    payload = request.get_json(silent=True) or {}
    model = payload.get('model', current.get('model', 'base'))
    if model not in transcriber.VALID_WHISPER_MODELS:
        return jsonify({'error': f'Invalid model name: {model}'}), 400
    device = payload.get('device', current.get('device', 'auto'))
    if device not in {'auto', 'cuda', 'cpu'}:
        device = current.get('device', 'auto')
    compute_type = payload.get('compute_type', current.get('compute_type', 'auto'))
    if compute_type not in _VALID_COMPUTE_TYPES:
        compute_type = 'auto'
    if 'download_root' in payload:
        raw_dl_root = (payload.get('download_root') or '').strip()
        download_root, dl_err = _validate_whisper_download_root(raw_dl_root)
        if dl_err:
            return jsonify({'error': dl_err}), 400
    else:
        download_root = current.get('download_root', '')

    new_config = {
        'model': model,
        'device': device,
        'compute_type': compute_type,
        'download_root': download_root,
        'configured': True
    }
    conn = db.get_db_conn()
    cur = conn.cursor()
    db.save_whisper_settings(cur, new_config)
    conn.commit()
    conn.close()

    return jsonify({'status': 'ok', 'config': new_config})

@transcribe_bp.route('/api/messages/<int:chat_id>/<int:msg_id>/transcribe', methods=['POST'])
def transcribe_message_audio(chat_id, msg_id):
    from core import transcriber
    if not transcriber.is_whisper_available():
        return jsonify({
            'status': 'error',
            'message': 'faster-whisper is not installed. Run "pip install faster-whisper" or "python tools/setup_whisper.py"'
        }), 503

    conn = db.get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT message_id, chat_id, text, media_type, media_local_path, transcription_text
        FROM tbl_messages
        WHERE chat_id = ? AND message_id = ?
    """, (chat_id, msg_id))
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Message not found'}), 404

    # If already transcribed, return existing
    if row['transcription_text']:
        conn.close()
        return jsonify({
            'status': 'ok',
            'cached': True,
            'text': row['transcription_text']
        })

    rel_path = row['media_local_path']
    if not rel_path:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Message has no local audio/video file'}), 400

    abs_path = os.path.join(core.ABS_MEDIA_BACKUP_DIR, rel_path.replace('/', os.sep))
    try:
        real_media_root = os.path.realpath(core.ABS_MEDIA_BACKUP_DIR)
        real_abs_path = os.path.realpath(abs_path)
        if os.path.islink(abs_path) or os.path.commonpath([real_media_root, real_abs_path]) != real_media_root:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Invalid media file path'}), 403
    except Exception:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Invalid media file path'}), 403

    if not os.path.isfile(real_abs_path):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Media file missing on disk'}), 404

    payload = request.get_json(silent=True) or {}
    saved_cfg = db.get_whisper_settings() or {}
    model_size = payload.get('model') or saved_cfg.get('model', 'base')
    if model_size not in transcriber.VALID_WHISPER_MODELS:
        conn.close()
        return jsonify({'status': 'error', 'message': f'Invalid model name: {model_size}'}), 400
    device = payload.get('device') or saved_cfg.get('device', 'auto')
    if device not in {'auto', 'cuda', 'cpu'}:
        device = 'auto'
    compute_type = payload.get('compute_type') or saved_cfg.get('compute_type', 'auto')
    if compute_type not in _VALID_COMPUTE_TYPES:
        compute_type = 'auto'
    download_root = saved_cfg.get('download_root') or None
    raw_lang = (payload.get('language') or '').strip()
    language = raw_lang.lower() if (raw_lang and _LANG_CODE_RE.match(raw_lang)) else None

    try:
        result = transcriber.transcribe_file(
            real_abs_path,
            language=language,
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            download_root=download_root
        )
        transcribed_text = result['text']

        # Update tbl_messages
        cur.execute("""
            UPDATE tbl_messages
            SET transcription_text = ?
            WHERE chat_id = ? AND message_id = ?
        """, (transcribed_text, chat_id, msg_id))

        # Update FTS index (remove duplicate first to prevent accumulation)
        existing_text = row['text'] or ''
        combined_fts = f"{existing_text} {transcribed_text}".strip()
        db.delete_fts_messages(cur, chat_id, [msg_id])
        cur.execute("""
            INSERT INTO tbl_messages_fts (message_id, chat_id, text)
            VALUES (?, ?, ?)
        """, (msg_id, chat_id, combined_fts))

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'ok',
            'cached': False,
            'text': transcribed_text,
            'language': result.get('language'),
            'duration': result.get('duration')
        })
    except Exception as e:
        conn.close()
        logging.error(f"[Whisper] Transcription failed for msg {msg_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500



