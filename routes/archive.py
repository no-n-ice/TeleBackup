#!/usr/bin/env python3
"""
routes/archive.py — Archive management routes and SSE progress stream.
"""
import os
import json
import queue
import threading
import logging
import asyncio
from flask import Blueprint, request, jsonify, Response
import core
from core import db
from core import helpers
from core.helpers import normalize_id
from core import worker

archive_bp = Blueprint('archive', __name__)


def _enrich_dialogs_with_db(dialog_list):
    if not dialog_list:
        return dialog_list

    conn = core.get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT chat_id FROM tbl_chats WHERE (last_backup_offset_id IS NOT NULL AND last_backup_offset_id > 0) OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = tbl_chats.chat_id)")
        archived_ids_raw = [row['chat_id'] for row in cursor.fetchall()]
        archived_ids = set()
        for cid in archived_ids_raw:
            archived_ids.add(cid)
            archived_ids.add(str(cid))
            try: archived_ids.add(int(cid))
            except Exception: pass

        cursor.execute("SELECT chat_id, COUNT(message_id) as count FROM tbl_messages GROUP BY chat_id")
        saved_counts_raw = cursor.fetchall()
        saved_counts = {}
        for row in saved_counts_raw:
            cid = row['chat_id']
            cnt = row['count']
            saved_counts[cid] = cnt
            saved_counts[str(cid)] = cnt
            try: saved_counts[int(cid)] = cnt
            except Exception: pass

        cursor.execute("SELECT chat_id, chat_avatar_path FROM tbl_chats WHERE chat_avatar_path IS NOT NULL")
        avatar_paths_raw = cursor.fetchall()
        avatar_paths = {}
        for row in avatar_paths_raw:
            cid = row['chat_id']
            ap = row['chat_avatar_path']
            avatar_paths[cid] = ap
            avatar_paths[str(cid)] = ap
            try: avatar_paths[int(cid)] = ap
            except Exception: pass

        cursor.execute("""
            SELECT chat_id, chat_name, archive_settings_json, linked_chat_id, 
                   archive_range_mode, archive_range_days, archive_range_start, archive_range_end,
                   backup_status, min_downloaded_id, max_downloaded_id, server_top_id,
                   has_older_gap, has_newer_gap, last_halt_reason, last_halt_date, resume_strategy
            FROM tbl_chats
        """)
        rows = cursor.fetchall()
        custom_settings_ids = set()
        linked_chat_map = {}
        chat_name_map = {}
        range_settings_map = {}
        chat_bounds_map = {}
        for row in rows:
            cid = row['chat_id']
            r_mode = row['archive_range_mode']
            r_days = row['archive_range_days']
            r_start = row['archive_range_start']
            r_end = row['archive_range_end']
            if row['archive_settings_json'] and str(row['archive_settings_json']).strip() not in ('', 'null', 'None'):
                custom_settings_ids.add(cid)
                custom_settings_ids.add(str(cid))
                try:
                    s_obj = json.loads(row['archive_settings_json'])
                    if isinstance(s_obj, dict):
                        if not r_mode or r_mode == 'all':
                            r_mode = s_obj.get('archive_range_mode') or r_mode
                        if r_days is None:
                            r_days = s_obj.get('archive_range_days')
                        if not r_start:
                            r_start = s_obj.get('archive_range_start')
                        if not r_end:
                            r_end = s_obj.get('archive_range_end')
                except Exception:
                    pass
            range_settings_map[cid] = (r_mode or 'all', r_days, r_start, r_end)
            range_settings_map[str(cid)] = range_settings_map[cid]
            if row['linked_chat_id']:
                linked_chat_map[cid] = row['linked_chat_id']
                linked_chat_map[str(cid)] = row['linked_chat_id']
            if row['chat_name']:
                chat_name_map[cid] = row['chat_name']
                chat_name_map[str(cid)] = row['chat_name']

            b_info = {
                'backup_status': row['backup_status'],
                'min_downloaded_id': row['min_downloaded_id'],
                'max_downloaded_id': row['max_downloaded_id'],
                'server_top_id': row['server_top_id'],
                'has_older_gap': bool(row['has_older_gap']),
                'has_newer_gap': bool(row['has_newer_gap']),
                'last_halt_reason': row['last_halt_reason'],
                'last_halt_date': row['last_halt_date'],
                'resume_strategy': row['resume_strategy']
            }
            chat_bounds_map[cid] = b_info
            chat_bounds_map[str(cid)] = b_info
    except Exception as e:
        logging.error(f"Error enriching dialogs with DB: {e}")
        archived_ids, saved_counts, avatar_paths, custom_settings_ids, linked_chat_map, chat_name_map, range_settings_map, chat_bounds_map = set(), {}, {}, set(), {}, {}, {}, {}
    finally:
        conn.close()

    dialogs_by_id = {d['id']: d for d in dialog_list}
    for d in dialog_list:
        cid = d['id']
        d['is_archived'] = (cid in archived_ids or str(cid) in archived_ids)
        d['saved_count'] = saved_counts.get(cid) or saved_counts.get(str(cid), 0)
        d['total_messages'] = max(d.get('total_messages', 0), d['saved_count'])
        d['has_custom_settings'] = (cid in custom_settings_ids or str(cid) in custom_settings_ids)
        r_info = range_settings_map.get(cid) or range_settings_map.get(str(cid)) or ('all', None, None, None)
        d['archive_range_mode'] = r_info[0]
        d['archive_range_days'] = r_info[1]
        d['archive_range_start'] = r_info[2]
        d['archive_range_end'] = r_info[3]

        b_info = chat_bounds_map.get(cid) or chat_bounds_map.get(str(cid)) or {}
        if b_info.get('backup_status') == 'inaccessible':
            # Dialog actively returned by Telegram - auto-recover status
            try:
                rec_conn = core.get_db_conn()
                rec_cur = rec_conn.cursor()
                rec_cur.execute("UPDATE tbl_chats SET backup_status = 'complete', last_halt_reason = NULL WHERE chat_id = ?", (cid,))
                rec_conn.commit()
                rec_conn.close()
                b_info['backup_status'] = 'complete'
                b_info['last_halt_reason'] = None
            except Exception:
                pass
        d['backup_status'] = b_info.get('backup_status') or ('completed' if d['is_archived'] else 'not_started')
        d['has_older_gap'] = bool(b_info.get('has_older_gap', 0))
        d['has_newer_gap'] = bool(b_info.get('has_newer_gap', 0))
        d['min_downloaded_id'] = b_info.get('min_downloaded_id')
        d['max_downloaded_id'] = b_info.get('max_downloaded_id')
        d['server_top_id'] = b_info.get('server_top_id')
        d['last_halt_reason'] = b_info.get('last_halt_reason')
        d['last_halt_date'] = b_info.get('last_halt_date')
        d['resume_strategy'] = b_info.get('resume_strategy')

        if cid in linked_chat_map or str(cid) in linked_chat_map:
            raw_linked = linked_chat_map.get(cid) or linked_chat_map.get(str(cid))
            linked_id = normalize_id(raw_linked)
            d['linked_chat_id'] = linked_id
            linked_dialog = dialogs_by_id.get(linked_id) or dialogs_by_id.get(str(linked_id))
            linked_name = (linked_dialog.get('name') if linked_dialog else None) or chat_name_map.get(linked_id) or chat_name_map.get(raw_linked)
            d['linked_chat_name'] = linked_name
            linked_count = saved_counts.get(linked_id) or saved_counts.get(str(linked_id), 0)
            if not linked_count and linked_dialog:
                linked_count = linked_dialog.get('total_messages', 0) or linked_dialog.get('saved_count', 0)
            d['linked_chat_count'] = linked_count

        # Avatar URL resolution: from tbl_chats, existing avatar_path, or avatars_global folder
        av_path = avatar_paths.get(cid) or avatar_paths.get(str(cid)) or d.get('avatar_path')
        if not av_path:
            clean_cid = str(cid).removeprefix('-100').removeprefix('-')
            for test_name in (f"{cid}_thumb.jpg", f"{cid}.jpg", f"{clean_cid}_thumb.jpg", f"{clean_cid}.jpg"):
                candidate = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "avatars_global", test_name)
                if os.path.exists(candidate):
                    av_path = f"avatars_global/{test_name}"
                    break

        if av_path and str(av_path) != "None":
            clean_path = str(av_path).replace('\\', '/').lstrip('/')
            if clean_path.startswith('media_backup/'):
                clean_path = clean_path[len('media_backup/'):]
            d['avatar_url'] = "/data/" + clean_path
            d['avatar_path'] = clean_path
        else:
            d.pop('avatar_url', None)

        if d.get('is_complex') and d.get('parts'):
            for p in d['parts']:
                pid = p['id']
                p['is_archived'] = (pid in archived_ids or str(pid) in archived_ids)
                p['saved_count'] = saved_counts.get(pid) or saved_counts.get(str(pid), 0)
                p['total_messages'] = max(p.get('total_messages', 0), p['saved_count'])

                p_b_info = chat_bounds_map.get(pid) or chat_bounds_map.get(str(pid)) or {}
                p['backup_status'] = p_b_info.get('backup_status') or ('completed' if p['is_archived'] else 'not_started')
                p['has_older_gap'] = bool(p_b_info.get('has_older_gap', 0))
                p['has_newer_gap'] = bool(p_b_info.get('has_newer_gap', 0))
                p['min_downloaded_id'] = p_b_info.get('min_downloaded_id')
                p['max_downloaded_id'] = p_b_info.get('max_downloaded_id')
                p['server_top_id'] = p_b_info.get('server_top_id')
                p['last_halt_reason'] = p_b_info.get('last_halt_reason')
                p['last_halt_date'] = p_b_info.get('last_halt_date')
                p['resume_strategy'] = p_b_info.get('resume_strategy')

                p_av = avatar_paths.get(pid) or avatar_paths.get(str(pid)) or p.get('avatar_path')
                if not p_av:
                    clean_pid = str(pid).removeprefix('-100').removeprefix('-')
                    for test_name in (f"{pid}_thumb.jpg", f"{pid}.jpg", f"{clean_pid}_thumb.jpg", f"{clean_pid}.jpg"):
                        candidate = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "avatars_global", test_name)
                        if os.path.exists(candidate):
                            p_av = f"avatars_global/{test_name}"
                            break
                if p_av and str(p_av) != "None":
                    cp = str(p_av).replace('\\', '/').lstrip('/')
                    if cp.startswith('media_backup/'): cp = cp[len('media_backup/'):]
                    p['avatar_url'] = "/data/" + cp
                    p['avatar_path'] = cp
                    if not d.get('avatar_url'):
                        d['avatar_url'] = p['avatar_url']
                        d['avatar_path'] = cp

    return dialog_list


_DIALOGS_FETCH_LOCK = threading.Lock()
_IN_FLIGHT_FETCH_FUTURE = None


def _is_backup_active():
    """Returns True if an archiving or download task is currently active."""
    if getattr(core, 'IS_BULK_ARCHIVING', False):
        return True
    with worker.status_lock:
        return any(
            v.get('status') in ('running', 'queued')
            for v in worker.ARCHIVE_STATUS.values()
            if v.get('phase') != 'reconcile'
        )


@archive_bp.route('/api/dialogs')
@archive_bp.route('/api/get_all_dialogs')
def get_all_dialogs():
    cache_only = str(request.args.get('cache_only', '')).lower() in ('true', '1', 'yes')
    cached_dialogs = None
    if os.path.exists(core.CACHE_FILE):
        try:
            with open(core.CACHE_FILE, 'r', encoding='utf-8') as f:
                cached_dialogs = json.load(f)
        except Exception as e:
            logging.error('Exception caught', exc_info=True)
            cached_dialogs = None

    if cached_dialogs is not None:
        enriched = _enrich_dialogs_with_db(cached_dialogs)
        return jsonify(enriched)

    # Если кэша нет, а просили только его - отдаем пустоту
    if cache_only:
        return jsonify([])

    # Защита: если реально выполняется бэкап
    if _is_backup_active():
        return jsonify({"error": "Backup in progress, Telegram client is busy. Please wait a few minutes."}), 503

    # Если соседний запрос уже выполняет полный скан, присоединяемся к существующему future
    global _IN_FLIGHT_FETCH_FUTURE
    with _DIALOGS_FETCH_LOCK:
        existing_future = _IN_FLIGHT_FETCH_FUTURE if (_IN_FLIGHT_FETCH_FUTURE is not None and not _IN_FLIGHT_FETCH_FUTURE.done()) else None

    if existing_future is not None:
        try:
            dialogs = existing_future.result(timeout=60.0)
        except Exception as e:
            logging.error(f"Error awaiting in-flight dialog fetch: {e}")
            dialogs = None
        if dialogs is None:
            return jsonify({"error": "Telegram scan failed or session not authorized.", "code": "UNAUTHORIZED"}), 500
        enriched = _enrich_dialogs_with_db(dialogs)
        return jsonify(enriched)

    # Иначе запускаем Telethon (сканирование Telegram)
    if worker.TELETHON_LOCK.acquire(blocking=True, timeout=25.0):
        try:
            with _DIALOGS_FETCH_LOCK:
                if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
                    future = asyncio.run_coroutine_threadsafe(core.async_fetch_and_cache_dialogs(), worker.LIVE_LOOP)
                    _IN_FLIGHT_FETCH_FUTURE = future
                else:
                    future = None
            if future:
                dialogs = future.result(timeout=60.0)
            else:
                dialogs = asyncio.run(core.async_fetch_and_cache_dialogs())
        except Exception as e:
            logging.error(f"Error during full dialog fetch: {e}")
            dialogs = None
        finally:
            with _DIALOGS_FETCH_LOCK:
                _IN_FLIGHT_FETCH_FUTURE = None
            worker.TELETHON_LOCK.release()
    else:
        return jsonify({"error": "Telegram client is busy with an existing operation. Please wait a moment."}), 503

    if dialogs is None:
        return jsonify({"error": "Telegram session not authorized. Run cli.py or log in via WebUI.", "code": "UNAUTHORIZED"}), 401

    enriched = _enrich_dialogs_with_db(dialogs)
    return jsonify(enriched)

@archive_bp.route('/api/refresh_dialogs')
def refresh_dialogs():
    if os.path.exists(core.CACHE_FILE):
        try:
            os.remove(core.CACHE_FILE)
        except Exception:
            pass
    return get_all_dialogs()

@archive_bp.route('/api/quick_refresh_dialogs')
def quick_refresh_dialogs():
    """
    Быстрое обновление диалогов через Telegram API:
    Опрашивает только последние диалоги (limit=60), пропуская уже известные в кэше.
    Новосозданные чаты и изменения названий подтягиваются за ~1 секунду.
    """
    if not os.path.exists(core.CACHE_FILE):
        return jsonify({"error": "Initial scan requires Full refresh first.", "code": "REQUIRES_FULL_REFRESH"}), 400

    if _is_backup_active():
        return jsonify({"error": "Backup in progress, Telegram client is busy. Please wait a few minutes."}), 503

    global _IN_FLIGHT_FETCH_FUTURE
    with _DIALOGS_FETCH_LOCK:
        existing_future = _IN_FLIGHT_FETCH_FUTURE if (_IN_FLIGHT_FETCH_FUTURE is not None and not _IN_FLIGHT_FETCH_FUTURE.done()) else None

    dialogs = None

    if existing_future is not None:
        try:
            dialogs = existing_future.result(timeout=60.0)
        except Exception:
            dialogs = None
    elif worker.TELETHON_LOCK.acquire(blocking=True, timeout=15.0):
        try:
            if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    core.async_quick_fetch_and_cache_dialogs(), worker.LIVE_LOOP
                )
                dialogs = future.result(timeout=30.0)
            else:
                dialogs = asyncio.run(core.async_quick_fetch_and_cache_dialogs())
        except Exception as e:
            logging.error(f"Error during quick dialog fetch: {e}")
            dialogs = None
        finally:
            worker.TELETHON_LOCK.release()
    else:
        return jsonify({"error": "Telegram client is busy with an existing operation. Please wait a moment."}), 503
    if dialogs is None:
        return jsonify({"error": "Telegram session not authorized. Run cli.py or log in via WebUI.", "code": "UNAUTHORIZED"}), 401

    enriched = _enrich_dialogs_with_db(dialogs)
    return jsonify(enriched)


@archive_bp.route('/api/delete_chat', methods=['POST'])
def delete_chat():
    """Полностью удаляет архив чата: записи в БД + медиа-папку на диске."""
    data = request.json or {}
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({'error': 'chat_id required'}), 400
    chat_id = int(chat_id)

    conn = core.get_db_conn()
    cursor = conn.cursor()
    pipeline = core.get_chat_pipeline(cursor, chat_id)

    # Защита: нельзя удалять чат, который сейчас архивируется
    with worker.status_lock:
        for p_id in pipeline:
            st = worker.ARCHIVE_STATUS.get(p_id, {})
            if st.get('status') in ('running', 'queued'):
                conn.close()
                return jsonify({'error': 'Chat (or its part) is currently archiving. Stop it first.'}), 409

    deleted_messages = 0
    deleted_folders = []
    placeholders = ','.join('?' for _ in pipeline)

    try:
        # Подготовка: узнаём safe_folder_name до удаления для ВСЕХ частей
        cursor.execute(f"SELECT safe_folder_name FROM tbl_chats WHERE chat_id IN ({placeholders})", pipeline)
        rows = cursor.fetchall()
        safe_folders = [r['safe_folder_name'] for r in rows if r['safe_folder_name']]

        # 1. Удаляем сообщения из FTS
        cursor.execute(f"DELETE FROM tbl_messages_fts WHERE chat_id IN ({placeholders})", pipeline)
        # 2. Удаляем сообщения
        cursor.execute(f"DELETE FROM tbl_messages WHERE chat_id IN ({placeholders})", pipeline)
        deleted_messages = cursor.rowcount
        # 3. Удаляем запись чата
        cursor.execute(f"DELETE FROM tbl_chats WHERE chat_id IN ({placeholders})", pipeline)
        conn.commit()

        # 4. Удаляем медиа-папки
        for safe_folder in safe_folders:
            media_path = os.path.join(core.ABS_MEDIA_BACKUP_DIR, safe_folder)
            if os.path.isdir(media_path):
                import shutil
                shutil.rmtree(media_path, ignore_errors=True)
                deleted_folders.append(safe_folder)

        # 5. Кэш инвалидировать не нужно, так как get_all_dialogs подтягивает актуальный статус из БД
        with worker.status_lock:
            for p_id in pipeline:
                if p_id in worker.ARCHIVE_STATUS:
                    del worker.ARCHIVE_STATUS[p_id]
                
        # Notify listeners to clear the UI for this chat
        init_msg = json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)})
        for q in list(worker.ARCHIVE_LISTENERS):
            try: q.put_nowait(init_msg)
            except queue.Full: pass

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'error': str(e)}), 500

    conn.close()
    return jsonify({
        'ok': True,
        'deleted_messages': deleted_messages,
        'deleted_folders': deleted_folders
    })

@archive_bp.route('/api/reload_chat', methods=['POST'])
def reload_chat():
    """Ставит существующий архивированный чат в очередь на повторную архивацию."""
    data = request.json or {}
    chat_id = data.get('chat_id')
    settings = data.get('settings', {})
    total = data.get('total', 0)
    reconcile_gaps = bool(data.get('reconcile_gaps', False))
    if not chat_id:
        return jsonify({'error': 'chat_id required'}), 400
    chat_id = int(chat_id)

    phase = data.get('phase')
    with worker.status_lock:
        st = worker.ARCHIVE_STATUS.get(chat_id, {})
        if st.get('status') in ('running', 'queued'):
            return jsonify({'error': 'Chat is already queued or running.'}), 409
        status_text = 'Queued (update + O(1) scan)...' if reconcile_gaps else 'Queued (reloading)...'
        worker.ARCHIVE_STATUS[chat_id] = {'status': 'queued', 'processed': 0, 'total': total, 'status_text': status_text}
        if phase is not None: worker.ARCHIVE_STATUS[chat_id]["phase"] = phase

    worker.ARCHIVE_QUEUE.put({'id': chat_id, 'total': total, 'settings': settings, 'repair': False, 'reconcile_gaps': reconcile_gaps})
    
    # Сразу оповещаем слушателей об изменении статуса
    init_msg = json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)})
    for q in list(worker.ARCHIVE_LISTENERS):
        try: q.put_nowait(init_msg)
        except queue.Empty: pass
        except Exception: pass
        
    return jsonify({'ok': True})



@archive_bp.route('/api/start_archive', methods=['POST'])
def start_archive():
    data = request.json
    tasks, settings = data.get('tasks', []), data.get('settings', {})
    if not tasks: return jsonify({"error": "No chats selected for archiving."}), 400
    
    session_file_path = f"{core.SESSION_FILE}.session"
    if not os.path.exists(session_file_path):
        return jsonify({"status": "error", "error": "Client not authorized. Run cli.py to log in."}), 401

    core.GLOBAL_ABORT = False
    
    added_chat_ids = set()
    for task in tasks:
        chat_id = task.get('id')
        phase = task.get('phase')
        added_chat_ids.add(str(chat_id))
        with worker.status_lock:
            if chat_id in worker.ARCHIVE_STATUS and worker.ARCHIVE_STATUS[chat_id].get('status') in ('running', 'queued'):
                continue
            worker.ARCHIVE_STATUS[chat_id] = {"status": "queued", "processed": 0, "total": task.get('total', 0), "status_text": "Queued..."}
            if phase is not None: worker.ARCHIVE_STATUS[chat_id]["phase"] = phase
        worker.ARCHIVE_QUEUE.put({"id": chat_id, "total": task.get('total', 0), "settings": settings})

        # Auto-download linked chat if present, not skipped, and not already queued
        try:
            skip_linked = settings.get('skip_linked', False)
            conn = core.get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT linked_chat_id, archive_settings_json FROM tbl_chats WHERE chat_id = ?", (int(chat_id),))
            row = cur.fetchone()
            conn.close()
            
            if row:
                if row['archive_settings_json']:
                    try:
                        c_settings = json.loads(row['archive_settings_json'])
                        if c_settings.get('skip_linked'):
                            skip_linked = True
                    except Exception: pass

            if not skip_linked and row and row['linked_chat_id']:
                linked_id = normalize_id(row['linked_chat_id'])
                if str(linked_id) not in added_chat_ids:
                    added_chat_ids.add(str(linked_id))
                    with worker.status_lock:
                        if linked_id not in worker.ARCHIVE_STATUS or worker.ARCHIVE_STATUS[linked_id].get('status') not in ('running', 'queued'):
                            worker.ARCHIVE_STATUS[linked_id] = {"status": "queued", "processed": 0, "total": 0, "status_text": "Queued (Linked Chat)..."}
                            worker.ARCHIVE_QUEUE.put({"id": linked_id, "total": 0, "settings": settings})
        except Exception as e:
            logging.debug(f"[start_archive] Linked chat check error: {e}")
            
    return jsonify({"status": "ok", "message": f"Added tasks."})



@archive_bp.route('/api/stop_archive', methods=['POST'])
def stop_archive():
    data = request.get_json(silent=True) or {}
    target_chat_id = data.get('chat_id')

    if target_chat_id is not None:
        try:
            target_chat_id = int(target_chat_id)
        except (ValueError, TypeError):
            pass
        if hasattr(core, 'request_abort'):
            core.request_abort(target_chat_id)

        # Selectively remove matching queued tasks for this chat only
        retained = []
        while not worker.ARCHIVE_QUEUE.empty():
            try:
                t = worker.ARCHIVE_QUEUE.get_nowait()
                if t.get('id') != target_chat_id and str(t.get('id')) != str(target_chat_id):
                    retained.append(t)
                worker.ARCHIVE_QUEUE.task_done()
            except queue.Empty:
                break
        for t in retained:
            worker.ARCHIVE_QUEUE.put(t)

        with worker.status_lock:
            for k in list(worker.ARCHIVE_STATUS.keys()):
                str_k = str(k)
                str_cid = str(target_chat_id)
                v = worker.ARCHIVE_STATUS[k]
                cid_match = (str_k == str_cid or str_k == f"transcribe_{str_cid}" or 
                             (isinstance(v, dict) and str(v.get('chat_id')) == str_cid))
                if cid_match:
                    worker.ARCHIVE_STATUS[k]['status'] = 'error'
                    worker.ARCHIVE_STATUS[k]['status_text'] = 'Stopped by user'
                    worker.ARCHIVE_STATUS[k]['error'] = 'Stopped by user'
    else:
        if hasattr(core, 'request_abort'):
            core.request_abort(None)
        else:
            core.GLOBAL_ABORT = True
        while not worker.ARCHIVE_QUEUE.empty():
            try:
                worker.ARCHIVE_QUEUE.get_nowait()
                worker.ARCHIVE_QUEUE.task_done()
            except queue.Empty:
                break

        with worker.status_lock:
            for k, v in worker.ARCHIVE_STATUS.items():
                if v['status'] in ('running', 'queued'):
                    v['status'] = 'error'
                    v['status_text'] = 'Stopped by user'

    # Stream event sync
    for q in list(worker.ARCHIVE_LISTENERS):
        try: q.put_nowait(json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)}))
        except queue.Full: pass

    return jsonify({"status": "ok"})

@archive_bp.route('/api/archive_status')
def get_archive_status():
    with worker.status_lock: return jsonify(dict(worker.ARCHIVE_STATUS))



@archive_bp.route('/api/live_status')
def get_live_status():
    return jsonify({"enabled": worker.LIVE_ENABLED})

@archive_bp.route('/api/toggle_live', methods=['POST'])
def toggle_live():
    data = request.json or {}
    enabled = data.get('enabled', True)
    worker.LIVE_ENABLED = enabled
    try:
        if enabled:
            if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
                asyncio.run_coroutine_threadsafe(core.start_live_archiver(worker.live_event_callback), worker.LIVE_LOOP)
            else:
                worker.start_live_background_thread()
        else:
            if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
                asyncio.run_coroutine_threadsafe(core.stop_live_archiver(), worker.LIVE_LOOP)
    except Exception as e:
        print(f"[App] Error toggling Live Archiver: {e}")
    return jsonify({"status": "ok", "enabled": worker.LIVE_ENABLED})



@archive_bp.route('/api/archive_stream')
def archive_stream():
    def event_stream():
        q = queue.Queue(maxsize=100)
        while len(worker.ARCHIVE_LISTENERS) >= 32: worker.ARCHIVE_LISTENERS.pop(0)
        worker.ARCHIVE_LISTENERS.append(q)
        try:
            # Сразу шлем полный снапшот текущего статуса
            with worker.status_lock:
                yield f"data: {json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)})}\n\n"
            while True:
                # Ждем обновлений или шлем пинг каждые 15 сек (keep-alive)
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except Exception as e:
            logging.error('Exception caught', exc_info=True) # Клиент отключился
        finally:
            if q in worker.ARCHIVE_LISTENERS:
                worker.ARCHIVE_LISTENERS.remove(q)
                
    return Response(event_stream(), mimetype="text/event-stream")

async def _execute_telethon_task(coro_builder):
    """
    Executes an async task with Telethon lock.
    Uses LIVE_CLIENT if connected, otherwise connects a temporary TelegramClient.
    """
    if not worker.TELETHON_LOCK.acquire(blocking=True, timeout=8.0):
        return {"error": "Telegram client is busy with another task or backup.", "busy": True}, 503

    conn = None
    client = None
    created = False
    try:
        conn = core.get_db_conn()
        cursor = conn.cursor()

        if getattr(core, 'LIVE_CLIENT', None) and core.LIVE_CLIENT.is_connected():
            client = core.LIVE_CLIENT
        else:
            from telethon import TelegramClient
            client = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
            await client.connect()
            created = True
            if not await client.is_user_authorized():
                return {"error": "Telegram session not authorized.", "code": "UNAUTHORIZED"}, 401

        result = await coro_builder(client, conn, cursor)
        conn.commit()
        return result, 200
    except Exception as e:
        logging.error("Error in telethon task", exc_info=True)
        return {"error": str(e)}, 500
    finally:
        if conn:
            conn.close()
        if created and client:
            try:
                await client.disconnect()
            except Exception:
                pass
        worker.TELETHON_LOCK.release()

def _run_telethon_sync(coro_builder):
    if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
        future = asyncio.run_coroutine_threadsafe(
            _execute_telethon_task(coro_builder), worker.LIVE_LOOP
        )
        return future.result()
    else:
        return asyncio.run(_execute_telethon_task(coro_builder))

@archive_bp.route('/api/fetch_user_profile', methods=['POST'])
def fetch_user_profile():
    data = request.get_json(silent=True) or request.args
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    user_id_int = int(user_id)

    async def _task(client, conn, cursor):
        res = await helpers.safe_fetch_full_user(client, user_id_int, cursor, delay_seconds=10.0)
        if not res:
            return {"error": "Failed to fetch user profile or user not found"}
        profile = db.get_user_full_profile(cursor, user_id_int)
        return {"ok": True, "profile": profile}

    res, status_code = _run_telethon_sync(_task)
    return jsonify(res), status_code

@archive_bp.route('/api/fetch_chat_profiles', methods=['POST'])
def fetch_chat_profiles():
    data = request.get_json(silent=True) or request.args
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400
    max_users = min(int(data.get('max_users', 50)), 200)
    chat_id_int = int(chat_id)

    async def _task(client, conn, cursor):
        entity = await client.get_entity(chat_id_int)
        participants = await client.get_participants(entity, limit=max_users)
        fetched_count = 0
        for p in participants:
            if getattr(p, 'bot', False):
                continue
            await helpers.safe_fetch_full_user(client, p.id, cursor, delay_seconds=10.0)
            fetched_count += 1
        return {"ok": True, "fetched_count": fetched_count}

    res, status_code = _run_telethon_sync(_task)
    return jsonify(res), status_code

@archive_bp.route('/api/archive_admin_log', methods=['POST'])
def archive_admin_log():
    data = request.get_json(silent=True) or request.args
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400
    limit = min(int(data.get('limit', 100)), 500)
    chat_id_int = int(chat_id)

    # Admin log only applies to channels and supergroups
    if chat_id_int > 0:
        return jsonify({"ok": True, "saved_events_count": 0, "message": "Admin event log is only available for channels and supergroups."}), 200

    from core.backup import backup_admin_log

    async def _task(client, conn, cursor):
        count = await backup_admin_log(client, chat_id_int, conn, cursor, limit=limit)
        return {"ok": True, "saved_events_count": count}

    res, status_code = _run_telethon_sync(_task)
    return jsonify(res), status_code

@archive_bp.route('/api/refresh_views', methods=['POST'])
def refresh_views():
    data = request.get_json(silent=True) or request.args
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400
    limit = min(int(data.get('limit', 100)), 300)
    chat_id_int = int(chat_id)

    # Views and reactions refresh is only applicable to channels and supergroups
    if chat_id_int > 0:
        return jsonify({"ok": True, "updated_messages_count": 0, "message": "Views and reactions are not tracked for private chats (DMs)."}), 200

    async def _task(client, conn, cursor):
        entity = await client.get_entity(chat_id_int)
        messages = await client.get_messages(entity, limit=limit)
        updated_count = 0
        for msg in messages:
            reactions_json = None
            if getattr(msg, 'reactions', None):
                try:
                    rx_dict = msg.reactions.to_dict()
                    if getattr(msg.reactions, 'can_see_list', False) and not getattr(msg.reactions, 'recent_reactions', None):
                        try:
                            from telethon.tl.functions.messages import GetMessageReactionsListRequest
                            rx_list = await client(GetMessageReactionsListRequest(peer=entity, id=msg.id, limit=20))
                            if rx_list and rx_list.reactions:
                                rx_dict['recent_reactions'] = [r.to_dict() for r in rx_list.reactions]
                                if rx_list.users:
                                    av_dir = os.path.join(core.MEDIA_BACKUP_DIR, "avatars_global")
                                    for u in rx_list.users:
                                        await helpers.fetch_and_save_user(u, client, cursor, {}, av_dir)
                        except Exception:
                            pass
                    reactions_json = json.dumps(rx_dict, default=helpers.dt_converter)
                except Exception:
                    pass
            
            views = getattr(msg, 'views', None)
            forwards = getattr(msg, 'forwards', None)
            
            cursor.execute("""
                UPDATE tbl_messages 
                SET views_count = COALESCE(?, views_count),
                    forwards = COALESCE(?, forwards),
                    reactions_json = COALESCE(?, reactions_json)
                WHERE message_id = ? AND chat_id = ?
            """, (views, forwards, reactions_json, msg.id, chat_id_int))
            if cursor.rowcount > 0:
                updated_count += 1
        return {"ok": True, "updated_messages_count": updated_count}

    res, status_code = _run_telethon_sync(_task)
    return jsonify(res), status_code

@archive_bp.route('/api/trigger_media_catchup', methods=['POST'])
def trigger_media_catchup():
    core.GLOBAL_ABORT = False
    data = request.json or {}
    chat_id = data.get('chat_id')
    settings = data.get('settings', {})
    bypass_limits = bool(data.get('bypass_limits', False))
    include_msg_ids = data.get('include_msg_ids')
    priority_msg_ids = data.get('priority_msg_ids')

    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    chat_id = int(chat_id)
    if not settings:
        conn = None
        try:
            conn = core.get_db_conn()
            cur = conn.cursor()
            chat_variants = list(helpers.get_chat_id_variants(chat_id))
            ph = ','.join('?' for _ in chat_variants)
            cur.execute(f"SELECT archive_settings_json FROM tbl_chats WHERE chat_id IN ({ph})", chat_variants)
            row = cur.fetchone()
            if row and row['archive_settings_json']:
                try: settings = json.loads(row['archive_settings_json'])
                except Exception: settings = {}
            if not settings:
                settings = db.get_global_settings(cur) or {}
        except Exception as e:
            logging.debug(f"[trigger_media_catchup] settings fallback error: {e}")
            settings = {}
        finally:
            if conn:
                try: conn.close()
                except Exception: pass
    settings = helpers.normalize_media_settings(settings or {})

    with worker.status_lock:
        existing = worker.ARCHIVE_STATUS.get(chat_id)
        if existing and existing.get('status') in ('running', 'queued') and existing.get('phase') == 'catchup':
            return jsonify({'ok': True, 'already_running': True, 'message': 'Media catch-up is already running for this chat.'})
        worker.ARCHIVE_STATUS[chat_id] = {
            "status": "queued",
            "processed": 0,
            "total": 0,
            "status_text": "Queued (Media Catch-up)...",
            "phase": "catchup"
        }
    worker.ARCHIVE_QUEUE.put({
        'action': 'media_catchup',
        'id': chat_id,
        'settings': settings,
        'bypass_limits': bypass_limits,
        'include_msg_ids': include_msg_ids,
        'priority_msg_ids': priority_msg_ids
    })
    init_msg = json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)})
    for q in list(worker.ARCHIVE_LISTENERS):
        try: q.put_nowait(init_msg)
        except Exception: pass
    return jsonify({"ok": True, "message": "Media catch-up task enqueued!"})


@archive_bp.route('/api/resume_chat_download', methods=['POST'])
def resume_chat_download():
    """
    Resume an incomplete or halted chat archive using dual-strategy engine.
    Strategy: 'latest_first' (catch up newer then backfill older) or 'older_first' (backfill older then catch up newer).
    """
    data = request.json or {}
    chat_id = data.get('chat_id')
    strategy = data.get('strategy', 'latest_first')
    settings = data.get('settings')

    if not chat_id:
        return jsonify({'error': 'chat_id required'}), 400
    chat_id = int(chat_id)

    session_file_path = f"{core.SESSION_FILE}.session"
    if not os.path.exists(session_file_path):
        return jsonify({"status": "error", "error": "Client not authorized. Run cli.py to log in."}), 401

    core.GLOBAL_ABORT = False

    if not settings:
        try:
            conn = core.get_db_conn()
            cur = conn.cursor()
            c_set = db.get_chat_settings(cur, chat_id)
            conn.close()
            settings = c_set if c_set else db.get_global_settings()
        except Exception as e:
            logging.debug(f"[resume_chat_download] settings fallback error: {e}")
            settings = {}

    strat_label = "Latest First" if strategy == "latest_first" else "Older First"
    with worker.status_lock:
        st = worker.ARCHIVE_STATUS.get(chat_id, {})
        if st.get('status') in ('running', 'queued'):
            return jsonify({'error': 'Chat is already queued or running.'}), 409
        worker.ARCHIVE_STATUS[chat_id] = {
            "status": "queued",
            "processed": 0,
            "total": 0,
            "status_text": f"Queued (Resume {strat_label})...",
            "resume_strategy": strategy
        }

    worker.ARCHIVE_QUEUE.put({
        'action': 'resume',
        'is_resume': True,
        'id': chat_id,
        'strategy': strategy,
        'settings': settings,
        'total': 0
    })

    init_msg = json.dumps({'type': 'init', 'data': dict(worker.ARCHIVE_STATUS)})
    for q in list(worker.ARCHIVE_LISTENERS):
        try: q.put_nowait(init_msg)
        except Exception: pass

    return jsonify({"ok": True, "message": f"Resume ({strat_label}) queued successfully."})


@archive_bp.route('/api/download_single_media', methods=['POST'])
def download_single_media():
    data = request.json or {}
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    bypass_limits = bool(data.get('bypass_limits', True))

    if not chat_id or not message_id:
        return jsonify({'error': 'chat_id and message_id are required'}), 400

    chat_id = int(chat_id)
    message_id = int(message_id)

    async def _task(client, conn, cursor):
        chat_variants = list(helpers.get_chat_id_variants(chat_id))
        chat_entity = None
        for variant in chat_variants:
            try:
                chat_entity = await client.get_entity(variant)
                if chat_entity:
                    break
            except Exception:
                pass

        if not chat_entity:
            from telethon.tl.types import PeerChannel, PeerChat, PeerUser
            for variant in chat_variants:
                try:
                    str_id = str(variant).replace('-100', '').replace('-', '')
                    num_id = int(str_id)
                    for peer_cls in (PeerChannel, PeerChat, PeerUser):
                        try:
                            chat_entity = await client.get_entity(peer_cls(num_id))
                            if chat_entity:
                                break
                        except Exception:
                            pass
                    if chat_entity:
                        break
                except Exception:
                    pass

        if not chat_entity:
            return {"error": f"Could not resolve Telegram entity for chat {chat_id}"}

        msg = await client.get_messages(chat_entity, ids=message_id)
        if not msg or not (getattr(msg, 'media', None) or getattr(msg, 'photo', None) or getattr(msg, 'document', None)):
            return {"error": "Message or media not found in Telegram"}

        ph = ','.join('?' for _ in chat_variants)
        cursor.execute(f"SELECT safe_folder_name FROM tbl_chats WHERE chat_id IN ({ph})", chat_variants)
        row = cursor.fetchone()
        safe_folder = row['safe_folder_name'] if row and row['safe_folder_name'] else f"Chat_{chat_id}"
        chat_media_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, safe_folder)
        helpers.ensure_chat_media_subdirs(chat_media_dir)

        cursor.execute(f"""
            SELECT media_original_name, file_size 
            FROM tbl_messages 
            WHERE chat_id IN ({ph}) AND message_id = ?
            LIMIT 1
        """, (*chat_variants, message_id))
        m_row = cursor.fetchone()
        db_orig_name = m_row['media_original_name'] if m_row else None
        db_file_size = (m_row['file_size'] or 0) if m_row else 0

        is_photo = bool(getattr(msg, 'photo', None))
        mime_type = getattr(getattr(msg, 'file', None), 'mime_type', None)
        attrs = getattr(getattr(msg, 'document', None), 'attributes', None)

        unique_fn = helpers.build_canonical_media_filename(msg, db_orig_name)
        is_voice = bool(getattr(msg, 'voice', None))
        is_round = bool(getattr(msg, 'video_note', None))
        is_stk = bool(getattr(msg, 'document', None) and any('Sticker' in getattr(a, '__class__', type(a)).__name__ for a in (attrs or [])))

        target_dir = helpers.get_media_target_dir(
            chat_media_dir,
            mime_type=mime_type,
            is_photo=is_photo,
            attributes=attrs,
            filename=unique_fn,
            is_voice=is_voice,
            is_round_video=is_round
        )
        target_file = os.path.join(helpers.get_shared_stickers_dir(), unique_fn) if is_stk else os.path.join(target_dir, unique_fn)
        legacy_file = os.path.join(chat_media_dir, unique_fn)
        os.makedirs(os.path.dirname(target_file), exist_ok=True)

        file_size = getattr(getattr(msg, 'document', None), 'size', 0) or db_file_size

        if os.path.exists(target_file) and os.path.getsize(target_file) > 0:
            dl_path = target_file
        elif os.path.exists(legacy_file) and os.path.getsize(legacy_file) > 0:
            dl_path = legacy_file
        else:
            existing_src = helpers.find_existing_local_media(cursor, unique_fn, file_size)
            if existing_src and helpers.try_create_hardlink(existing_src, target_file):
                dl_path = target_file
            else:
                try:
                    dl_path = await helpers.download_media_robust(client, msg, target_file, chat_entity=chat_entity)
                except Exception as dl_err:
                    code, clean_label, is_perm = helpers.classify_media_download_error(dl_err)
                    err_msg = f"Unavailable on Telegram ({clean_label})" if is_perm else f"Failed: {clean_label}"
                    cursor.execute(f"""
                        UPDATE tbl_messages
                        SET media_skipped = ?
                        WHERE chat_id IN ({ph}) AND message_id = ?
                    """, (err_msg, *chat_variants, message_id))
                    conn.commit()
                    return {"ok": False, "error": clean_label, "media_skipped": err_msg, "message_id": message_id}

        if not dl_path or not os.path.exists(dl_path):
            clean_reason = "Unavailable on Telegram (purged or expired)"
            cursor.execute(f"""
                UPDATE tbl_messages
                SET media_skipped = ?
                WHERE chat_id IN ({ph}) AND message_id = ?
            """, (clean_reason, *chat_variants, message_id))
            conn.commit()
            return {"ok": False, "error": clean_reason, "media_skipped": clean_reason, "message_id": message_id}

        rel_path = os.path.relpath(dl_path, core.ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
        try:
            file_size = os.path.getsize(dl_path)
        except Exception:
            pass

        cursor.execute(f"""
            UPDATE tbl_messages
            SET media_local_path = ?, media_original_name = COALESCE(media_original_name, ?), media_skipped = NULL, file_size = ?
            WHERE chat_id IN ({ph}) AND message_id = ?
        """, (rel_path, unique_fn, file_size, *chat_variants, message_id))
        conn.commit()


        return {
            "ok": True,
            "message_id": message_id,
            "media_local_path": rel_path,
            "file_size": file_size
        }

    res, status_code = _run_telethon_sync(_task)
    return jsonify(res), status_code


@archive_bp.route('/api/recheck_chat_access', methods=['POST'])
def recheck_chat_access():
    """
    Probes Telegram entity access for a chat. If accessible, auto-resets status to 'complete'.
    If blocked or deleted, updates status to 'inaccessible' or 'deleted'.
    """
    data = request.get_json(silent=True) or request.args
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400
    chat_id_int = int(chat_id)

    async def _task(client, conn, cursor):
        try:
            entity = await client.get_entity(chat_id_int)
            cursor.execute("""
                UPDATE tbl_chats 
                SET backup_status = 'complete', last_halt_reason = NULL 
                WHERE chat_id = ?
            """, (chat_id_int,))
            conn.commit()
            chat_title = getattr(entity, 'title', getattr(entity, 'first_name', ''))
            return {"ok": True, "accessible": True, "status": "complete", "title": chat_title}
        except Exception as e:
            cat, reason = helpers.classify_telegram_entity_error(e)
            status_to_set = cat or 'inaccessible'
            cursor.execute("""
                UPDATE tbl_chats 
                SET backup_status = ?, last_halt_reason = ? 
                WHERE chat_id = ?
            """, (status_to_set, reason, chat_id_int))
            conn.commit()
            return {"ok": True, "accessible": False, "status": status_to_set, "reason": reason}

    res, status_code = _run_telethon_sync(_task)
    return jsonify(res), status_code



@archive_bp.route('/api/reconcile_gaps/<chat_id>', methods=['POST'])
def trigger_reconcile_gaps(chat_id):
    """Triggers True O(1) HOR gap reconciliation task."""
    try:
        cid = int(chat_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid chat_id'}), 400
    from core import worker
    with worker.status_lock:
        st = worker.ARCHIVE_STATUS.get(cid, {})
        if st.get('status') in ('running', 'queued'):
            return jsonify({'error': 'Chat is already queued or running.'}), 409
    worker.start_reconcile_gaps_task(cid)
    return jsonify({'status': 'queued', 'chat_id': cid})
