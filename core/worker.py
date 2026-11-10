#!/usr/bin/env python3
"""
worker.py — Background archive queue worker, progress broadcaster, and live loop thread.
"""
import os
import json
import time
import asyncio
import threading
import queue
import logging
import core
from . import db
from . import helpers

ARCHIVE_STATUS = {}
ARCHIVE_QUEUE = queue.Queue()
ARCHIVE_LISTENERS = []
status_lock = threading.Lock()
TELETHON_LOCK = threading.Lock()

LIVE_ENABLED = True
LIVE_LOOP = None
LIVE_THREAD = None

def live_event_callback(event_type, payload):
    event_payload = {"type": event_type, "data": payload}
    for q in list(ARCHIVE_LISTENERS):
        try:
            q.put_nowait(json.dumps(event_payload))
        except queue.Full:
            pass

def _run_live_event_loop():
    global LIVE_LOOP
    LIVE_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(LIVE_LOOP)
    print("[LiveArchiver] Initializing background Live Update thread...")
    try:
        success = LIVE_LOOP.run_until_complete(core.start_live_archiver(live_event_callback))
        if success:
            print("[LiveArchiver] Background live message capture active 24/7!")
        else:
            print("[LiveArchiver] Background live message capture inactive (failed to start or unauthorized).")
        LIVE_LOOP.run_forever()
    except Exception as e:
        print(f"[LiveArchiver] Exception in background thread: {e}")

def start_live_background_thread():
    global LIVE_THREAD
    if LIVE_THREAD and LIVE_THREAD.is_alive():
        return
    LIVE_THREAD = threading.Thread(target=_run_live_event_loop, daemon=True)
    LIVE_THREAD.start()

def progress_updater(chat_id, text, processed=None, total=None, status="running", last_item=None, phase=None, **kwargs):
    """Callback for broadcasting progress from Core to web layer."""
    if hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id) and status in ('running', 'done'):
        return
    task_key = kwargs.get('task_key')
    effective_key = str(task_key) if task_key is not None else chat_id
    event_payload = {
        "id": effective_key,
        "chat_id": chat_id,
        "text": text,
        "status_text": text,
        "status": status
    }
    if status == "error":
        event_payload["error"] = text
    for k, v in kwargs.items():
        if v is not None:
            event_payload[k] = v
    
    with status_lock:
        if effective_key not in ARCHIVE_STATUS:
            ARCHIVE_STATUS[effective_key] = {"status": status, "chat_id": chat_id, "task_key": effective_key}
        target = ARCHIVE_STATUS[effective_key]
        target['status_text'] = text
        target['status'] = status
        target['chat_id'] = chat_id
        if status == "error":
            target['error'] = text
        if processed is not None: 
            target['processed'] = processed
            event_payload["processed"] = processed
        if total is not None: 
            target['total'] = total
            event_payload["total"] = total
        if last_item is not None: 
            target['last_item'] = last_item
            event_payload["last_item"] = last_item
        if phase is not None:
            target['phase'] = phase
            event_payload["phase"] = phase
        for k, v in kwargs.items():
            if v is not None:
                target[k] = v

    live_msg = json.dumps(event_payload)
    for q in list(ARCHIVE_LISTENERS):
        try:
            q.put_nowait(live_msg)
        except queue.Full:
            pass

def auto_enqueue_chat(chat_id, settings=None):
    """Automatically enqueue linked chat for archiving (with SSE broadcast)."""
    with status_lock:
        if chat_id not in ARCHIVE_STATUS or ARCHIVE_STATUS[chat_id].get('status') not in ('running', 'queued'):
            ARCHIVE_STATUS[chat_id] = {"status": "queued", "processed": 0, "total": 0, "status_text": "Queued (Linked Chat)..."}
            ARCHIVE_QUEUE.put({"id": chat_id, "total": 0, "settings": settings or {}})
            print(f"[Queue] Automatically added linked chat: {chat_id}")

            for q in list(ARCHIVE_LISTENERS):
                try:
                    q.put_nowait(json.dumps({'type': 'init', 'data': dict(ARCHIVE_STATUS)}))
                except queue.Full:
                    pass

def thread_wrapper_for_backup(chat_id, settings, total_messages, repair=False, reconcile_gaps=False):
    conn = db.get_db_conn()
    cur = conn.cursor()
    chat_settings = db.get_chat_settings(cur, chat_id)
    if chat_settings:
        settings = chat_settings
    elif settings and 'linked_settings' in settings and settings['linked_settings']:
        settings = settings['linked_settings']
    elif not settings:
        settings = db.get_global_settings(cur)
    conn.close()

    if hasattr(core, "clear_abort"): core.clear_abort(chat_id)
    progress_updater(chat_id, "Starting...", 0, total_messages)
    new_chat_id_to_queue = None
    
    try:
        with TELETHON_LOCK:
            if LIVE_LOOP and LIVE_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    core.async_run_backup(chat_id, settings, total_messages, progress_updater, repair=repair),
                    LIVE_LOOP
                )
                final_status_text, new_chat_id_to_queue = future.result()
            else:
                final_status_text, new_chat_id_to_queue = asyncio.run(
                    core.async_run_backup(chat_id, settings, total_messages, progress_updater, repair=repair)
                )
                    
        if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False) or (isinstance(final_status_text, str) and 'halted' in final_status_text.lower()):
            progress_updater(chat_id, "Stopped by user", status="error")
        else:
            if reconcile_gaps:
                progress_updater(chat_id, "Fast update complete. Starting O(1) HOR gap reconciliation...", status="running", phase="reconcile")
                thread_wrapper_for_reconcile_gaps(chat_id)
            else:
                progress_updater(chat_id, final_status_text, status="done")

    except Exception as e:
        error_msg = str(e)
        print(f"[Worker Error {chat_id}] {error_msg}")
        with status_lock:
            if chat_id in ARCHIVE_STATUS:
                ARCHIVE_STATUS[chat_id]["status"] = "error"
                ARCHIVE_STATUS[chat_id]["error"] = error_msg
                ARCHIVE_STATUS[chat_id]["status_text"] = f"Error: {error_msg}"
        progress_updater(chat_id, error_msg, status="error")

    if new_chat_id_to_queue and isinstance(new_chat_id_to_queue, int) and (new_chat_id_to_queue < 0 or new_chat_id_to_queue > 10000):
        with status_lock:
            if new_chat_id_to_queue not in ARCHIVE_STATUS or ARCHIVE_STATUS[new_chat_id_to_queue].get('status') not in ('running', 'queued'):
                ARCHIVE_STATUS[new_chat_id_to_queue] = {"status": "queued", "processed": 0, "total": 0, "status_text": "Queued (migration)..."}
                ARCHIVE_QUEUE.put({"id": new_chat_id_to_queue, "total": 0, "settings": settings, "repair": repair})
                print(f"[Queue] Auto-added migration relay: {new_chat_id_to_queue}")

def thread_wrapper_for_resume(chat_id, strategy="latest_first", settings=None, total_messages=0, reconcile_gaps=False):
    conn = db.get_db_conn()
    cur = conn.cursor()
    chat_settings = db.get_chat_settings(cur, chat_id)
    if chat_settings:
        merged = dict(chat_settings)
        if settings and isinstance(settings, dict):
            for k, v in settings.items():
                merged[k] = v
        settings = merged
    elif settings and 'linked_settings' in settings and settings['linked_settings']:
        settings = settings['linked_settings']
    elif not settings:
        settings = db.get_global_settings(cur)
    conn.close()

    strat_label = "Latest First" if strategy == "latest_first" else "Older First"
    if hasattr(core, "clear_abort"): core.clear_abort(chat_id)
    progress_updater(chat_id, f"Resuming ({strat_label})...", 0, total_messages)
    new_chat_id_to_queue = None
    
    try:
        with TELETHON_LOCK:
            if LIVE_LOOP and LIVE_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    core.async_resume_chat_download(chat_id, strategy=strategy, settings=settings, total_messages=total_messages, progress_callback=progress_updater),
                    LIVE_LOOP
                )
                final_status_text, new_chat_id_to_queue = future.result()
            else:
                final_status_text, new_chat_id_to_queue = asyncio.run(
                    core.async_resume_chat_download(chat_id, strategy=strategy, settings=settings, total_messages=total_messages, progress_callback=progress_updater)
                )
                    
        if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False) or (isinstance(final_status_text, str) and 'halted' in final_status_text.lower()):
            progress_updater(chat_id, "Stopped by user", status="error")
        else:
            if reconcile_gaps:
                progress_updater(chat_id, "Fast update complete. Starting O(1) HOR gap reconciliation...", status="running", phase="reconcile")
                thread_wrapper_for_reconcile_gaps(chat_id)
            else:
                progress_updater(chat_id, final_status_text, status="done")

    except Exception as e:
        error_msg = str(e)
        print(f"[Worker Resume Error {chat_id}] {error_msg}")
        with status_lock:
            if chat_id in ARCHIVE_STATUS:
                ARCHIVE_STATUS[chat_id]["status"] = "error"
                ARCHIVE_STATUS[chat_id]["error"] = error_msg
                ARCHIVE_STATUS[chat_id]["status_text"] = f"Error: {error_msg}"
        progress_updater(chat_id, error_msg, status="error")

    if new_chat_id_to_queue and isinstance(new_chat_id_to_queue, int) and (new_chat_id_to_queue < 0 or new_chat_id_to_queue > 10000):
        with status_lock:
            if new_chat_id_to_queue not in ARCHIVE_STATUS or ARCHIVE_STATUS[new_chat_id_to_queue].get('status') not in ('running', 'queued'):
                ARCHIVE_STATUS[new_chat_id_to_queue] = {"status": "queued", "processed": 0, "total": 0, "status_text": "Queued (migration)..."}
                ARCHIVE_QUEUE.put({"id": new_chat_id_to_queue, "total": 0, "settings": settings})
                print(f"[Queue] Auto-added migration relay: {new_chat_id_to_queue}")

def thread_wrapper_for_media_catchup(chat_id, settings, bypass_limits=False, include_msg_ids=None, priority_msg_ids=None):
    if hasattr(core, "clear_abort"): core.clear_abort(chat_id)
    progress_updater(chat_id, "Catching up media...", 0, 0, phase="catchup")
    try:
        with TELETHON_LOCK:
            if LIVE_LOOP and LIVE_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    core.async_download_missing_chat_media(
                        chat_id, settings, progress_updater,
                        bypass_limits=bypass_limits,
                        include_msg_ids=include_msg_ids,
                        priority_msg_ids=priority_msg_ids
                    ),
                    LIVE_LOOP
                )
                final_text = future.result()
            else:
                final_text = asyncio.run(
                    core.async_download_missing_chat_media(
                        chat_id, settings, progress_updater,
                        bypass_limits=bypass_limits,
                        include_msg_ids=include_msg_ids,
                        priority_msg_ids=priority_msg_ids
                    )
                )
        if (hasattr(core, "is_abort_requested") and core.is_abort_requested(chat_id)) or getattr(core, "GLOBAL_ABORT", False) or final_text == "Stopped by user.":
            progress_updater(chat_id, "Stopped by user", status="error", phase="catchup")
        else:
            progress_updater(chat_id, final_text, status="done", phase="catchup")
    except Exception as e:
        error_msg = str(e)
        progress_updater(chat_id, f"Error: {error_msg[:30]}...", status="error", phase="catchup")

def archive_worker_thread():
    """Background daemon processing archive task queue."""
    print("[Worker] Background archive worker started.")
    while True:
        try:
            task = ARCHIVE_QUEUE.get()
            if task.get('action') == 'media_catchup':
                thread_wrapper_for_media_catchup(
                    task['id'],
                    task.get('settings', {}),
                    bypass_limits=task.get('bypass_limits', False),
                    include_msg_ids=task.get('include_msg_ids'),
                    priority_msg_ids=task.get('priority_msg_ids')
                )
            elif task.get('action') == 'batch_transcribe':
                thread_wrapper_for_batch_transcribe(task['id'], task_key=task.get('task_key'), sender_ids=task.get('sender_ids'))
            elif task.get('action') == 'reconcile_gaps':
                thread_wrapper_for_reconcile_gaps(task['id'])
            elif task.get('action') == 'resume' or task.get('is_resume'):
                thread_wrapper_for_resume(
                    task['id'],
                    strategy=task.get('strategy') or task.get('resume_strategy', 'latest_first'),
                    settings=task.get('settings', {}),
                    total_messages=task.get('total', 0),
                    reconcile_gaps=task.get('reconcile_gaps', False)
                )
            else:
                thread_wrapper_for_backup(
                    task['id'], 
                    task['settings'], 
                    task['total'], 
                    repair=task.get('repair', False),
                    reconcile_gaps=task.get('reconcile_gaps', False)
                )
            ARCHIVE_QUEUE.task_done()
        except Exception as e:
            print(f"[Worker CRITICAL] {e}")
            time.sleep(5)

def thread_wrapper_for_batch_transcribe(chat_id, task_key=None, sender_ids=None):
    task_key = task_key or f"transcribe_{chat_id}" 
    """Worker function processing batch voice/video note transcription."""
    from core import transcriber
    if not transcriber.is_whisper_available():
        progress_updater(chat_id, "faster-whisper is not installed", status="error", phase="transcribe", task_key=task_key)
        return

    conn = db.get_db_conn()
    cur = conn.cursor()
    
    query = """
        SELECT message_id, media_local_path, is_round_video, sender_id, text
        FROM tbl_messages
        WHERE chat_id = ?
          AND (is_voice = 1 OR is_round_video = 1)
          AND media_local_path IS NOT NULL
          AND (transcription_text IS NULL OR transcription_text = '')
    """
    params = [chat_id]
    if sender_ids and isinstance(sender_ids, list) and len(sender_ids) > 0:
        placeholders = ','.join('?' for _ in sender_ids)
        query += f" AND sender_id IN ({placeholders})"
        params.extend(sender_ids)
    query += " ORDER BY message_id ASC"

    cur.execute(query, params)
    untranscribed = cur.fetchall()
    total = len(untranscribed)

    if total == 0:
        conn.close()
        progress_updater(chat_id, "No untranscribed voice notes found", processed=0, total=0, status="done", phase="transcribe", task_key=task_key)
        return

    if hasattr(core, "clear_abort"): core.clear_abort(chat_id)
    progress_updater(chat_id, f"Transcribing audio (0/{total})...", processed=0, total=total, status="running", phase="transcribe", task_key=task_key)

    saved_cfg = db.get_whisper_settings() or {}
    model_size = saved_cfg.get('model', 'base')
    device = saved_cfg.get('device', 'auto')
    compute_type = saved_cfg.get('compute_type', 'auto')
    download_root = saved_cfg.get('download_root', '')

    processed = 0
    try:
        for row in untranscribed:
            if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
                progress_updater(chat_id, "Stopped by user", processed=processed, total=total, status="error", phase="transcribe", task_key=task_key)
                break

            msg_id = row['message_id']
            rel_path = row['media_local_path']
            abs_path = os.path.join(core.ABS_MEDIA_BACKUP_DIR, rel_path.replace('/', os.sep))
            if not os.path.isfile(abs_path):
                processed += 1
                continue

            def _on_segment(current_sec, total_sec, seg_pct, snippet):
                sub_label = f"Msg #{msg_id}: {seg_pct}% ({round(current_sec, 1)}s / {round(total_sec, 1)}s)"
                if snippet:
                    clean_snip = snippet.replace('\n', ' ')
                    if len(clean_snip) > 40:
                        clean_snip = clean_snip[:37] + '...'
                    sub_label += f' — "{clean_snip}"'
                progress_updater(
                    chat_id,
                    f"Transcribing audio ({processed}/{total})...",
                    processed=processed,
                    total=total,
                    status="running",
                    phase="transcribe",
                    task_key=task_key,
                    sub_processed=round(current_sec, 1),
                    sub_total=round(total_sec, 1),
                    sub_pct=seg_pct,
                    sub_text=sub_label
                )

            # Signal starting this message
            progress_updater(
                chat_id,
                f"Transcribing audio ({processed}/{total})...",
                processed=processed,
                total=total,
                status="running",
                phase="transcribe",
                task_key=task_key,
                sub_pct=0,
                sub_text=f"Msg #{msg_id}: Decoding audio..."
            )

            try:
                res = transcriber.transcribe_file(
                    abs_path,
                    model_size=model_size,
                    device=device,
                    compute_type=compute_type,
                    download_root=download_root,
                    progress_callback=_on_segment
                )
                text = res['text']
                lang = res.get('language')
                dur = res.get('duration')

                cur.execute("""
                    UPDATE tbl_messages
                    SET transcription_text = ?,
                        transcription_lang = COALESCE(?, transcription_lang),
                        duration = CASE WHEN (duration IS NULL OR duration = 0) AND ? IS NOT NULL THEN ? ELSE duration END
                    WHERE chat_id = ? AND message_id = ?
                """, (text, lang, dur, int(dur or 0), chat_id, msg_id))

                existing_text = row['text'] or ''
                combined_fts = f"{existing_text} {text}".strip()
                db.delete_fts_messages(cur, chat_id, [msg_id])
                cur.execute("INSERT INTO tbl_messages_fts (message_id, chat_id, text) VALUES (?, ?, ?)", (msg_id, chat_id, combined_fts))
                conn.commit()

                live_event_callback("message_transcribed", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "transcription_text": text
                })
            except Exception as tr_err:
                logging.error(f"[BatchTranscribe] Failed msg {msg_id}: {tr_err}")

            processed += 1
            progress_updater(
                chat_id,
                f"Transcribing audio ({processed}/{total})...",
                processed=processed,
                total=total,
                status="running",
                phase="transcribe",
                task_key=task_key,
                sub_pct=100,
                sub_text=f"Msg #{msg_id}: Completed ({processed}/{total})"
            )

        if not (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) and not getattr(core, 'GLOBAL_ABORT', False):
            progress_updater(chat_id, f"Transcription completed ({processed}/{total})", processed=processed, total=total, status="done", phase="transcribe", task_key=task_key)
    finally:
        conn.close()

def start_batch_transcribe_task(chat_id, sender_ids=None):
    """Enqueues batch transcription task."""
    task_key = f"transcribe_{chat_id}"
    with status_lock:
        ARCHIVE_STATUS[task_key] = {
            "status": "queued",
            "processed": 0,
            "total": 0,
            "status_text": "Queued for transcription...",
            "phase": "transcribe",
            "chat_id": chat_id,
            "task_key": task_key
        }
        ARCHIVE_QUEUE.put({
            "action": "batch_transcribe",
            "id": chat_id,
            "task_key": task_key,
            "sender_ids": sender_ids
        })
        for q in list(ARCHIVE_LISTENERS):
            try:
                q.put_nowait(json.dumps({'type': 'init', 'data': dict(ARCHIVE_STATUS)}))
            except queue.Full:
                pass

def thread_wrapper_for_reconcile_gaps(chat_id):
    """Worker wrapper for True O(1) HOR gap reconciliation."""
    if hasattr(core, "clear_abort"): core.clear_abort(chat_id)
    progress_updater(chat_id, "Checking message gaps (O(1) HOR)...", status="running", phase="reconcile")
    try:
        with TELETHON_LOCK:
            if LIVE_LOOP and LIVE_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    core.async_reconcile_chat_gaps(chat_id, progress_callback=progress_updater),
                    LIVE_LOOP
                )
                res = future.result()
            else:
                res = asyncio.run(core.async_reconcile_chat_gaps(chat_id, progress_callback=progress_updater))
        if (hasattr(core, 'is_abort_requested') and core.is_abort_requested(chat_id)) or getattr(core, 'GLOBAL_ABORT', False):
            progress_updater(chat_id, "Stopped by user", status="error", phase="reconcile")
        else:
            progress_updater(chat_id, "Synchronization complete", status="done", phase="reconcile")
    except Exception as e:
        progress_updater(chat_id, f"Error: {str(e)[:35]}...", status="error", phase="reconcile")

def start_reconcile_gaps_task(chat_id):
    """Enqueues gap reconciliation task."""
    with status_lock:
        ARCHIVE_STATUS[chat_id] = {
            "status": "queued",
            "processed": 0,
            "total": 0,
            "status_text": "Queued for gap verification...",
            "phase": "reconcile"
        }
        ARCHIVE_QUEUE.put({
            "action": "reconcile_gaps",
            "id": chat_id
        })
        for q in list(ARCHIVE_LISTENERS):
            try:
                q.put_nowait(json.dumps({'type': 'init', 'data': dict(ARCHIVE_STATUS)}))
            except queue.Full:
                pass
