#!/usr/bin/env python3
"""
routes/stats.py — Global & per-chat statistics, Admin Log, Stars, User Profiles, Activity, Heatmap & Subscriber Churn routes.
"""
import os
import json
import time
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
import core
from core import db
from core import helpers

stats_bp = Blueprint('stats', __name__)
_GLOBAL_DIR_STATS_CACHE = {'size': 0, 'files': 0, 'timestamp': 0}

def get_chat_pipeline_cached(chat_id):
    conn = db.get_db_conn()
    cur = conn.cursor()
    pipeline = core.get_chat_pipeline(cur, int(chat_id))
    conn.close()
    return pipeline

# --- Статистика ---
@stats_bp.route('/api/stats/global')
def global_stats():
    conn = core.get_db_conn()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT 
                COUNT(*) AS total,
                COUNT(media_local_path) AS media,
                COUNT(service_action_json) AS service,
                SUM(CASE WHEN media_local_path IS NULL AND service_action_json IS NULL THEN 1 ELSE 0 END) AS text_only,
                COALESCE(SUM(CASE WHEN media_local_path IS NOT NULL THEN file_size ELSE 0 END), 0) AS media_bytes
            FROM tbl_messages
        """)
        row = cursor.fetchone()
        total_messages = row[0] or 0
        total_media_count = row[1] or 0
        service_messages = row[2] or 0
        text_messages = row[3] or 0
        db_media_bytes = row[4] or 0

        cursor.execute("SELECT media_type, COUNT(*) FROM tbl_messages WHERE media_local_path IS NOT NULL GROUP BY media_type")
        media_breakdown = {row[0] or 'other': row[1] for row in cursor.fetchall()}

        try:
            cursor.execute("SELECT COUNT(*) FROM tbl_messages_fts")
            fts_count = cursor.fetchone()[0]
            fts_enabled = True
        except Exception:
            fts_count = 0
            fts_enabled = False
        
        # --- ФИКС: Умный подсчет (исключаем старые части сшитых чатов) ---
        cursor.execute("""
            SELECT COUNT(c1.chat_id) 
            FROM tbl_chats c1
            LEFT JOIN tbl_chats c2 ON c1.migrated_to_chat_id = c2.chat_id
            WHERE c1.migrated_to_chat_id IS NULL OR c2.chat_id IS NULL
        """)
        total_chats = cursor.fetchone()[0]
    except Exception as e:
        print(f"[Stats Error] Error calculating statistics: {e}")
        total_messages, total_media_count, total_chats, service_messages, text_messages, media_breakdown, fts_count = 0, 0, 0, 0, 0, {}, 0
        fts_enabled = False
    finally:
        conn.close()

    # --- Подсчет физического размера на диске (с кэшированием на 10 мин) ---
    def get_dir_stats(path):
        total_size = 0
        total_files = 0
        if os.path.exists(path):
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        try:
                            total_size += os.path.getsize(fp)
                            total_files += 1
                        except Exception:
                            pass
        return total_size, total_files

    db_size = os.path.getsize(core.DB_FILE) if os.path.exists(core.DB_FILE) else 0

    force_recalc = request.args.get('force') in ('1', 'true')
    now = time.time()
    if force_recalc:
        media_size, media_files = get_dir_stats(core.ABS_MEDIA_BACKUP_DIR)
        _GLOBAL_DIR_STATS_CACHE['size'] = media_size
        _GLOBAL_DIR_STATS_CACHE['files'] = media_files
        _GLOBAL_DIR_STATS_CACHE['timestamp'] = now
    elif db_media_bytes > 0:
        media_size = db_media_bytes
        media_files = total_media_count
    elif (now - _GLOBAL_DIR_STATS_CACHE['timestamp'] > 600) or (_GLOBAL_DIR_STATS_CACHE['timestamp'] == 0):
        media_size, media_files = get_dir_stats(core.ABS_MEDIA_BACKUP_DIR)
        _GLOBAL_DIR_STATS_CACHE['size'] = media_size
        _GLOBAL_DIR_STATS_CACHE['files'] = media_files
        _GLOBAL_DIR_STATS_CACHE['timestamp'] = now
    else:
        media_size = _GLOBAL_DIR_STATS_CACHE['size']
        media_files = _GLOBAL_DIR_STATS_CACHE['files']
    
    return jsonify({
        'total_messages': total_messages,
        'total_chats': total_chats,
        'total_media_count': total_media_count,
        'service_messages': service_messages,
        'text_messages': text_messages,
        'media_breakdown': media_breakdown,
        'fts_count': fts_count,
        'fts_enabled': fts_enabled,
        'db_size_mb': round(db_size / (1024 * 1024), 2),
        'db_size_bytes': db_size,
        'media_size_mb': round(media_size / (1024 * 1024), 2),
        'media_size_bytes': media_size,
        'media_files_on_disk': media_files,
        'total_size_mb': round((db_size + media_size) / (1024 * 1024), 2),
        'total_size_bytes': db_size + media_size
    })

@stats_bp.route('/api/stats/chat')
def chat_stats():
    chat_id = request.args.get('chat_id')
    if not chat_id: return jsonify({"error": "chat_id is required"}), 400
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    
    try:
        chat_id_int = int(chat_id)
        pipeline = core.get_chat_pipeline(cursor, chat_id_int)
        placeholders = ','.join('?' for _ in pipeline)
        
        # 1. Основные счетчики
        cursor.execute(f"SELECT COUNT(*) FROM tbl_messages WHERE chat_id IN ({placeholders})", pipeline)
        total_messages = cursor.fetchone()[0]
        
        # 2. Медиа-счетчики
        cursor.execute(f"""
            SELECT media_type, COUNT(*) as c 
            FROM tbl_messages 
            WHERE chat_id IN ({placeholders}) AND media_type IS NOT NULL 
            GROUP BY media_type
        """, pipeline)
        media_counts = {row['media_type']: row['c'] for row in cursor.fetchall()}
        
        # 3. Размер на диске
        cursor.execute("SELECT safe_folder_name, stats_json, stats_updated_at, chat_type, migrated_to_chat_id FROM tbl_chats WHERE chat_id = ?", (chat_id_int,))
        chat_row = cursor.fetchone()
        media_size_mb = 0
        channel_stats = None
        stats_updated_at = None

        cursor.execute(f"SELECT COALESCE(SUM(file_size), 0) FROM tbl_messages WHERE chat_id IN ({placeholders}) AND media_local_path IS NOT NULL", pipeline)
        db_media_bytes = cursor.fetchone()[0] or 0
        if db_media_bytes > 0:
            media_size_mb = round(db_media_bytes / (1024 * 1024), 2)
        elif chat_row and chat_row['safe_folder_name']:
            chat_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, chat_row['safe_folder_name'])
            media_size_mb = round(_get_dir_size_local(chat_dir) / (1024 * 1024), 2)
            stats_updated_at = chat_row['stats_updated_at']
            if chat_row['stats_json']:
                try:
                    channel_stats = json.loads(chat_row['stats_json'])
                except Exception:
                    channel_stats = None

        # Проверка: является ли чат группой/супергруппой (в т.ч. мигрировавшей из группы)
        cursor.execute(f"SELECT chat_id, chat_type, migrated_to_chat_id FROM tbl_chats WHERE chat_id IN ({placeholders})", pipeline)
        pipeline_rows = cursor.fetchall()
        
        is_group_or_migrated = False
        if len(pipeline) > 1:
            is_group_or_migrated = True
        else:
            for p_row in pipeline_rows:
                if p_row['migrated_to_chat_id']:
                    is_group_or_migrated = True
                    break
                c_type = (p_row['chat_type'] or '').lower()
                if c_type in ('group', 'supergroup', 'dm', 'bot'):
                    is_group_or_migrated = True
                    break
                if c_type and c_type != 'channel':
                    is_group_or_migrated = True
                    break
                if p_row['chat_id'] > 0:
                    is_group_or_migrated = True
                    break

        # 4. Просмотры и репосты (только для каналов; для групп и мигрировавших супергрупп метрики не применимы)
        if is_group_or_migrated:
            total_views = 0
            total_forwards = 0
        else:
            cursor.execute(f"SELECT SUM(views_count), SUM(forwards) FROM tbl_messages WHERE chat_id IN ({placeholders})", pipeline)
            vf_row = cursor.fetchone()
            total_views = vf_row[0] or 0
            total_forwards = vf_row[1] or 0

        # 5. Реакции (топ-10 с выборкой последних 3000)
        cursor.execute(f"SELECT reactions_json FROM tbl_messages WHERE chat_id IN ({placeholders}) AND reactions_json IS NOT NULL ORDER BY message_id DESC LIMIT 3000", pipeline)
        reaction_counts = {}
        for r_row in cursor.fetchall():
            try:
                r_data = json.loads(r_row['reactions_json'])
                items = r_data if isinstance(r_data, list) else (r_data.get('results', []) if isinstance(r_data, dict) else [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    rx = item.get('reaction')
                    em = None
                    if isinstance(rx, dict):
                        em = rx.get('emoticon') or str(rx.get('document_id') or '')
                    if not em:
                        em = item.get('emoticon') or str(item.get('document_id') or '')
                    if not em:
                        continue
                    cnt = item.get('count', 0)
                    reaction_counts[em] = reaction_counts.get(em, 0) + cnt
            except Exception:
                pass
        top_reactions = sorted(reaction_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            
        conn.close()
        
        return jsonify({
            'total_messages': total_messages,
            'media_counts': media_counts,
            'media_size_mb': media_size_mb,
            'total_views': total_views,
            'total_forwards': total_forwards,
            'top_reactions': top_reactions,
            'channel_stats': channel_stats,
            'stats_updated_at': stats_updated_at,
            'is_channel': not is_group_or_migrated,
            'is_group': is_group_or_migrated
        })
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

@stats_bp.route('/api/admin_log')
def get_admin_log():
    chat_id = request.args.get('chat_id')
    if not chat_id: return jsonify({"error": "chat_id is required"}), 400
    limit = request.args.get('limit', default=100, type=int)
    offset_id = request.args.get('offset_id', default=0, type=int)
    offset = request.args.get('offset', default=0, type=int)
    action_type = request.args.get('action_type', default=None, type=str)
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    try:
        chat_id_int = int(chat_id)
        events = db.get_admin_log_events(cursor, chat_id_int, limit=limit, offset=offset, action_type=action_type, min_id=offset_id)
        conn.close()
        return jsonify(events)
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

@stats_bp.route('/api/stars_transactions')
def get_stars_transactions():
    chat_id = request.args.get('chat_id')
    if not chat_id: return jsonify({"error": "chat_id is required"}), 400
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    try:
        chat_id_int = int(chat_id)
        txs = db.get_stars_transactions(cursor, chat_id_int, limit=limit, offset=offset)
        conn.close()
        return jsonify(txs)
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500

@stats_bp.route('/api/user_full_profile')
def get_user_full_profile():
    user_id = request.args.get('user_id')
    if not user_id: return jsonify({"error": "user_id is required"}), 400
    
    conn = core.get_db_conn()
    cursor = conn.cursor()
    try:
        user_id_int = int(user_id)
        profile = db.get_user_full_profile(cursor, user_id_int)
        conn.close()
        if not profile:
            return jsonify({"error": "User not found"}), 404
        
        live_status = getattr(core, 'ACTIVE_USER_STATUS', {}).get(user_id_int)
        if live_status:
            profile['live_status'] = live_status
            
        return jsonify(profile)
    except Exception as e:
        if conn: conn.close()
        return jsonify({"error": str(e)}), 500


def _get_dir_size_local(path):
    total = 0
    if os.path.exists(path):
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
    return total

@stats_bp.route('/api/stats/top_users')
def get_top_users():
    chat_id = request.args.get('chat_id')
    limit = request.args.get('limit', type=int)
    conn = core.get_db_conn()
    c = conn.cursor()
    pipe = core.get_chat_pipeline(c, int(chat_id))
    
    query = f"""
        SELECT m.sender_id, u.first_name, u.last_name, COUNT(m.message_id) as mc
        FROM tbl_messages m JOIN tbl_users u ON m.sender_id = u.user_id
        WHERE m.chat_id IN ({','.join('?'*len(pipe))}) AND m.sender_id IS NOT NULL
        GROUP BY m.sender_id ORDER BY mc DESC
    """
    params = pipe.copy()
    if limit:
        query += " LIMIT ?"
        params.append(limit)
        
    c.execute(query, params)
    active_status_map = getattr(core, 'ACTIVE_USER_STATUS', {})
    stats = [
        {
            "sender_id": r["sender_id"],
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "message_count": r["mc"],
            "live_status": active_status_map.get(r["sender_id"])
        }
        for r in c.fetchall()
    ]
    conn.close()
    return jsonify(stats)

@stats_bp.route('/api/stats/activity')
def get_activity():
    chat_id = request.args.get('chat_id')
    conn = core.get_db_conn()
    c = conn.cursor()
    pipe = core.get_chat_pipeline(c, int(chat_id))
    c.execute(f"""
        SELECT substr(date, 1, 10) as ad, COUNT(message_id) as mc
        FROM tbl_messages WHERE chat_id IN ({','.join('?'*len(pipe))})
        GROUP BY ad ORDER BY ad ASC
    """, pipe)
    stats = [{"activity_date": r["ad"], "message_count": r["mc"]} for r in c.fetchall()]
    conn.close()
    return jsonify(stats)


@stats_bp.route('/api/stats/daily_heatmap')
def daily_heatmap():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400

    pipeline = get_chat_pipeline_cached(chat_id)
    placeholders = ','.join('?' for _ in pipeline)

    conn = db.get_db_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT substr(date, 1, 10) as day, count(*) as count
        FROM tbl_messages
        WHERE chat_id IN ({placeholders})
        GROUP BY day
        ORDER BY day ASC
    """, pipeline)
    
    rows = cur.fetchall()
    conn.close()
    result = {row['day']: row['count'] for row in rows if row['day']}
    return jsonify(result)


# --- Subscribers & Participant Churn Routes ---
@stats_bp.route('/api/participants/summary', methods=['GET'])
def get_participants_summary_route():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    res = db.get_chat_participants_summary(chat_id)
    if not res:
        return jsonify({
            'chat_id': chat_id,
            'participants_count': 0,
            'track_participants': False,
            'participants_synced_at': None,
            'chat_type': 'channel' if chat_id < -1000000000000 else ('group' if chat_id < 0 else 'user'),
            'backup_status': 'not_started',
            'total_tracked': 0,
            'active_count': 0,
            'left_count': 0,
            'kicked_count': 0,
            'banned_count': 0,
            'snapshots': []
        })
    return jsonify(res)

@stats_bp.route('/api/participants/list', methods=['GET'])
def get_participants_list_route():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    status = request.args.get('status', 'all')
    search = request.args.get('search')
    limit = min(request.args.get('limit', 100, type=int), 500)
    offset = request.args.get('offset', 0, type=int)
    rows = db.get_chat_participants_list(chat_id, status=status, search=search, limit=limit, offset=offset)
    return jsonify({'chat_id': chat_id, 'participants': rows, 'limit': limit, 'offset': offset})

@stats_bp.route('/api/participants/events', methods=['GET'])
def get_participant_events_route():
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    limit = min(request.args.get('limit', 100, type=int), 500)
    offset = request.args.get('offset', 0, type=int)
    events = db.get_chat_participant_events(chat_id, limit=limit, offset=offset)
    return jsonify({'chat_id': chat_id, 'events': events})

@stats_bp.route('/api/participants/toggle', methods=['POST'])
def toggle_participants_tracking_route():
    data = request.get_json(force=True, silent=True) or {}
    chat_id = data.get('chat_id')
    enabled = bool(data.get('enabled', False))
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400
    db.set_track_participants(int(chat_id), enabled)
    return jsonify({'status': 'ok', 'chat_id': int(chat_id), 'track_participants': enabled})

@stats_bp.route('/api/participants/sync', methods=['POST'])
def sync_participants_route():
    data = request.get_json(force=True, silent=True) or {}
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({'error': 'chat_id missing'}), 400

    from core import worker, participants as part_module
    import asyncio
    import threading

    chat_id_int = int(chat_id)
    live_client = getattr(core, 'LIVE_CLIENT', None)
    live_loop = getattr(worker, 'LIVE_LOOP', None)

    # 1. Threadsafe dispatch to LIVE_LOOP when LIVE_CLIENT is active
    if live_client and live_client.is_connected() and live_loop and live_loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(
                part_module.sync_chat_participants(live_client, chat_id_int),
                live_loop
            )
            res = future.result(timeout=30.0)
            return jsonify(res)
        except Exception as e_live:
            logging.warning(f"[Stats] LIVE_CLIENT threadsafe sync failed: {e_live}", exc_info=True)

    # 2. Standalone client / offline DB fallback in background thread
    result_container = {}

    def _run_sync():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        standalone_client = None
        try:
            try:
                standalone_client = core.TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
                loop.run_until_complete(standalone_client.connect())
                if not loop.run_until_complete(standalone_client.is_user_authorized()):
                    standalone_client = None
            except Exception as e_conn:
                logging.debug(f"[Stats] Standalone client connection skipped: {e_conn}")
                standalone_client = None

            res = loop.run_until_complete(part_module.sync_chat_participants(standalone_client, chat_id_int))
            if standalone_client:
                try:
                    loop.run_until_complete(standalone_client.disconnect())
                    loop.run_until_complete(asyncio.sleep(0.05))
                except Exception:
                    pass
            result_container['result'] = res
        except Exception as e:
            try:
                res = loop.run_until_complete(part_module.sync_chat_participants(None, chat_id_int))
                if isinstance(res, dict):
                    res['warning'] = f"Sync fell back to local history ({e})."
                result_container['result'] = res
            except Exception as e_inner:
                result_container['error'] = str(e_inner)
        finally:
            loop.close()

    t = threading.Thread(target=_run_sync)
    t.start()
    t.join(timeout=30.0)

    if 'error' in result_container:
        return jsonify({'status': 'error', 'message': result_container['error']}), 500
    if 'result' in result_container:
        return jsonify(result_container['result'])
    return jsonify({'status': 'running_background', 'message': 'Sync running in background'})
