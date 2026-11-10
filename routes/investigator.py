#!/usr/bin/env python3
"""
routes/investigator.py — Lego Block Investigator backend API.
Provides dynamic cross-chat analytics, aggregation, and investigator presets.
"""

import sqlite3
import csv
import io
from flask import Blueprint, request, jsonify, Response
import core
from core import helpers

investigator_bp = Blueprint('investigator', __name__)

def _get_db():
    return core.db.get_db_conn()

@investigator_bp.route('/api/investigate/meta', methods=['GET'])
def get_investigate_meta():
    """
    Returns available filter dimensions: active chats, top senders, media types, date bounds.
    """
    try:
        conn = _get_db()
        cursor = conn.cursor()

        chat_id_arg = request.args.get('chat_id')
        chat_filter_sql = ""
        chat_filter_params = []
        if chat_id_arg and chat_id_arg != 'all':
            try:
                cid = int(chat_id_arg)
                chat_filter_sql = "AND m.chat_id = ?"
                chat_filter_params = [cid]
            except ValueError:
                pass

        # Active chats with message counts
        cursor.execute("""
            SELECT c.chat_id, c.chat_name, c.chat_type, COUNT(m.message_id) AS msg_count
            FROM tbl_chats c
            LEFT JOIN tbl_messages m ON c.chat_id = m.chat_id
            GROUP BY c.chat_id
            HAVING msg_count > 0
            ORDER BY msg_count DESC
        """)
        chats = [
            {
                'chat_id': r['chat_id'],
                'chat_name': r['chat_name'] or f"Chat {r['chat_id']}",
                'chat_type': r['chat_type'],
                'msg_count': r['msg_count']
            }
            for r in cursor.fetchall()
        ]

        # Top senders/participants in this chat (or global) with resolved names (chatters + forwards + bots)
        if chat_filter_params:
            cid = chat_filter_params[0]
            cursor.execute("""
                WITH chat_participants AS (
                    -- Direct message senders
                    SELECT m.sender_id AS user_id, COUNT(*) AS cnt
                    FROM tbl_messages m
                    WHERE m.chat_id = ? AND m.sender_id IS NOT NULL AND m.sender_id != 0
                    GROUP BY m.sender_id

                    UNION ALL

                    -- Forwarded sources/bots/users
                    SELECT CAST(json_extract(m.forward_info_json, '$.from_id') AS INTEGER) AS user_id, COUNT(*) AS cnt
                    FROM tbl_messages m
                    WHERE m.chat_id = ? 
                      AND m.forward_info_json IS NOT NULL 
                      AND json_extract(m.forward_info_json, '$.from_id') IS NOT NULL
                    GROUP BY json_extract(m.forward_info_json, '$.from_id')

                    UNION ALL

                    -- Via bot inline senders
                    SELECT m.via_bot_id AS user_id, COUNT(*) AS cnt
                    FROM tbl_messages m
                    WHERE m.chat_id = ? AND m.via_bot_id IS NOT NULL AND m.via_bot_id != 0
                    GROUP BY m.via_bot_id
                )
                SELECT cp.user_id AS sender_id,
                       SUM(cp.cnt) AS msg_count,
                       COALESCE(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), u.username, 'ID ' || cp.user_id) AS sender_name,
                       COALESCE(u.is_bot, 0) AS is_bot,
                       u.avatar_path
                FROM chat_participants cp
                LEFT JOIN tbl_users u ON cp.user_id = u.user_id
                WHERE cp.user_id IS NOT NULL AND cp.user_id != 0
                GROUP BY cp.user_id
                ORDER BY msg_count DESC
                LIMIT 150
            """, [cid, cid, cid])
        else:
            cursor.execute("""
                WITH global_participants AS (
                    SELECT m.sender_id AS user_id, COUNT(*) AS cnt
                    FROM tbl_messages m
                    WHERE m.sender_id IS NOT NULL AND m.sender_id != 0
                    GROUP BY m.sender_id

                    UNION ALL

                    SELECT CAST(json_extract(m.forward_info_json, '$.from_id') AS INTEGER) AS user_id, COUNT(*) AS cnt
                    FROM tbl_messages m
                    WHERE m.forward_info_json IS NOT NULL AND json_extract(m.forward_info_json, '$.from_id') IS NOT NULL
                    GROUP BY json_extract(m.forward_info_json, '$.from_id')

                    UNION ALL

                    SELECT m.via_bot_id AS user_id, COUNT(*) AS cnt
                    FROM tbl_messages m
                    WHERE m.via_bot_id IS NOT NULL AND m.via_bot_id != 0
                    GROUP BY m.via_bot_id
                )
                SELECT gp.user_id AS sender_id,
                       SUM(gp.cnt) AS msg_count,
                       COALESCE(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), u.username, 'ID ' || gp.user_id) AS sender_name,
                       COALESCE(u.is_bot, 0) AS is_bot,
                       u.avatar_path
                FROM global_participants gp
                LEFT JOIN tbl_users u ON gp.user_id = u.user_id
                WHERE gp.user_id IS NOT NULL AND gp.user_id != 0
                GROUP BY gp.user_id
                ORDER BY msg_count DESC
                LIMIT 150
            """)

        senders = [
            {
                'sender_id': r['sender_id'],
                'name': r['sender_name'] if r['sender_name'].strip() else f"ID {r['sender_id']}",
                'is_bot': bool(r['is_bot']),
                'avatar_path': r['avatar_path'],
                'msg_count': r['msg_count']
            }
            for r in cursor.fetchall()
        ]

        # Distinct media types
        cursor.execute(f"""
            SELECT DISTINCT media_type
            FROM tbl_messages m
            WHERE media_type IS NOT NULL AND media_type != '' {chat_filter_sql}
            ORDER BY media_type ASC
        """, chat_filter_params)
        media_types = [r['media_type'] for r in cursor.fetchall()]

        selected_chat = None
        if chat_filter_params:
            cursor.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM tbl_messages WHERE chat_id = ?", chat_filter_params)
            min_date, max_date, total_messages = cursor.fetchone()
            cursor.execute("SELECT chat_id, chat_name, chat_avatar_path, chat_type FROM tbl_chats WHERE chat_id = ?", chat_filter_params)
            cr = cursor.fetchone()
            if cr:
                selected_chat = {
                    'chat_id': cr['chat_id'],
                    'chat_name': cr['chat_name'] or f"Chat {cr['chat_id']}",
                    'avatar_path': cr['chat_avatar_path'],
                    'chat_type': cr['chat_type']
                }
        else:
            cursor.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM tbl_messages")
            min_date, max_date, total_messages = cursor.fetchone()

        conn.close()

        return jsonify({
            'status': 'ok',
            'chats': chats,
            'selected_chat': selected_chat,
            'senders': senders,
            'media_types': media_types,
            'min_date': (min_date[:10] if min_date else None),
            'max_date': (max_date[:10] if max_date else None),
            'total_messages': total_messages
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _build_investigation_query(payload):
    """
    Builds parameterized SQL for investigator analytics based on payload filters.
    """
    preset = payload.get('preset')
    chats = payload.get('chats')
    senders = payload.get('senders')
    media_types = payload.get('media_types')
    date_start = payload.get('date_start')
    date_end = payload.get('date_end')
    group_by = payload.get('group_by', 'sender')
    sort_by = payload.get('sort_by', 'total_size')
    sort_order = 'ASC' if str(payload.get('sort_order', 'desc')).lower() == 'asc' else 'DESC'
    limit = min(int(payload.get('limit', 25)), 100)

    where_clauses = ["1=1"]
    params = []

    # Handle Presets
    if preset == 'heavyweight':
        where_clauses.append("m.file_size > 0")
        group_by = 'sender'
        sort_by = 'total_size'
        sort_order = 'DESC'
    elif preset == 'bot_footprint':
        group_by = 'bot'
        sort_by = 'count'
        sort_order = 'DESC'
    elif preset == 'cross_chat_memes':
        group_by = 'cross_chat'
        sort_by = 'chat_count'
        sort_order = 'DESC'
    elif preset == 'activity_timeline':
        group_by = 'month'
        sort_by = 'label'
        sort_order = 'ASC'
    elif preset == 'peak_hours':
        group_by = 'hour'
        sort_by = 'label'
        sort_order = 'ASC'

    # Filter constraints
    if chats and chats != 'all':
        if isinstance(chats, list) and len(chats) > 0:
            placeholders = ','.join('?' for _ in chats)
            where_clauses.append(f"m.chat_id IN ({placeholders})")
            params.extend(chats)

    if senders and senders != 'all':
        if isinstance(senders, list) and len(senders) > 0:
            placeholders = ','.join('?' for _ in senders)
            sender_cond = f"""(
                m.sender_id IN ({placeholders})
                OR m.via_bot_id IN ({placeholders})
                OR CAST(json_extract(m.forward_info_json, '$.from_id') AS INTEGER) IN ({placeholders})
            )"""
            where_clauses.append(sender_cond)
            params.extend(senders)
            params.extend(senders)
            params.extend(senders)

    if media_types and media_types != 'all':
        if isinstance(media_types, list) and len(media_types) > 0:
            placeholders = ','.join('?' for _ in media_types)
            where_clauses.append(f"m.media_type IN ({placeholders})")
            params.extend(media_types)

    if date_start:
        where_clauses.append("m.date >= ?")
        params.append(f"{date_start} 00:00:00")

    if date_end:
        where_clauses.append("m.date <= ?")
        params.append(f"{date_end} 23:59:59")

    where_sql = " AND ".join(where_clauses)

    # Dimensional Groupings
    if group_by == 'sender':
        select_group = """
            COALESCE(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), u.username, 'ID ' || m.sender_id) AS label,
            m.sender_id AS ref_id,
            u.avatar_path AS avatar_path,
            u.username AS username,
            COALESCE(u.is_bot, 0) AS is_bot
        """
        group_sql = "GROUP BY m.sender_id"
    elif group_by == 'chat':
        select_group = """
            COALESCE(c.chat_name, 'Chat ' || m.chat_id) AS label,
            m.chat_id AS ref_id,
            c.chat_avatar_path AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY m.chat_id"
    elif group_by == 'media_type':
        select_group = """
            COALESCE(m.media_type, 'text_only') AS label,
            NULL AS ref_id,
            NULL AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY m.media_type"
    elif group_by == 'month':
        select_group = """
            substr(m.date, 1, 7) AS label,
            NULL AS ref_id,
            NULL AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY substr(m.date, 1, 7)"
    elif group_by == 'day':
        select_group = """
            substr(m.date, 1, 10) AS label,
            NULL AS ref_id,
            NULL AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY substr(m.date, 1, 10)"
    elif group_by == 'hour':
        select_group = """
            strftime('%H:00', m.date) AS label,
            NULL AS ref_id,
            NULL AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY strftime('%H:00', m.date)"
    elif group_by == 'day_of_week':
        select_group = """
            CASE CAST(strftime('%w', m.date) AS INTEGER)
                WHEN 0 THEN '0. Sunday'
                WHEN 1 THEN '1. Monday'
                WHEN 2 THEN '2. Tuesday'
                WHEN 3 THEN '3. Wednesday'
                WHEN 4 THEN '4. Thursday'
                WHEN 5 THEN '5. Friday'
                WHEN 6 THEN '6. Saturday'
            END AS label,
            NULL AS ref_id,
            NULL AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY strftime('%w', m.date)"
    elif group_by == 'bot':
        select_group = """
            CASE WHEN u.is_bot = 1 THEN 'Bots' ELSE 'Humans' END AS label,
            NULL AS ref_id,
            NULL AS avatar_path,
            NULL AS username,
            0 AS is_bot
        """
        group_sql = "GROUP BY (u.is_bot = 1)"
    elif group_by == 'cross_chat':
        # Special cross-chat media query
        if chats and chats != 'all' and isinstance(chats, list) and len(chats) > 0:
            chat_ids_str = ','.join(str(int(c)) for c in chats)
            subquery = f"""
                m.media_original_name IN (
                    SELECT sub.media_original_name
                    FROM tbl_messages sub
                    WHERE sub.media_original_name IS NOT NULL AND sub.media_original_name != ''
                    GROUP BY sub.media_original_name
                    HAVING COUNT(DISTINCT sub.chat_id) > 1
                ) AND m.chat_id IN ({chat_ids_str})
            """
            having_clause = "HAVING count > 0"
        else:
            subquery = "1=1"
            having_clause = "HAVING COUNT(DISTINCT m.chat_id) > 1"

        sql = f"""
            SELECT m.media_original_name AS label,
                   COUNT(DISTINCT m.chat_id) AS chat_count,
                   COUNT(m.message_id) AS count,
                   COUNT(m.media_local_path) AS media_count,
                   COALESCE(SUM(m.file_size), 0) AS total_size,
                   COALESCE(AVG(CASE WHEN m.file_size > 0 THEN m.file_size ELSE NULL END), 0) AS avg_size,
                   MAX(CASE WHEN m.file_size > 0 AND m.media_local_path IS NOT NULL THEN m.file_size ELSE 0 END) AS top_file_size,
                   m.media_local_path AS sample_media_path,
                   m.message_id AS sample_msg_id,
                   m.chat_id AS sample_chat_id,
                   m.media_type AS sample_media_type,
                   m.media_original_name AS sample_media_name,
                   'cross_chat' AS ref_type,
                   NULL AS ref_id,
                   NULL AS avatar_path,
                   NULL AS username,
                   0 AS is_bot
            FROM tbl_messages m
            WHERE {where_sql}
              AND m.media_original_name IS NOT NULL
              AND m.media_original_name != ''
              AND {subquery}
            GROUP BY m.media_original_name
            {having_clause}
            ORDER BY chat_count {sort_order}, count DESC
            LIMIT ?
        """
        params.append(limit)
        return sql, params

    # Standard Aggregation Query
    order_col = 'total_size'
    if sort_by == 'count':
        order_col = 'count'
    elif sort_by == 'avg_size':
        order_col = 'avg_size'
    elif sort_by == 'label':
        order_col = 'label'

    sql = f"""
        SELECT {select_group},
               COUNT(m.message_id) AS count,
               COUNT(m.media_local_path) AS media_count,
               COALESCE(SUM(m.file_size), 0) AS total_size,
               COALESCE(AVG(CASE WHEN m.file_size > 0 THEN m.file_size ELSE NULL END), 0) AS avg_size,
               MAX(CASE WHEN m.file_size > 0 AND m.media_local_path IS NOT NULL THEN m.file_size ELSE 0 END) AS top_file_size,
               m.media_local_path AS sample_media_path,
               m.message_id AS sample_msg_id,
               m.chat_id AS sample_chat_id,
               m.media_type AS sample_media_type,
               m.media_original_name AS sample_media_name,
               '{group_by}' AS ref_type
        FROM tbl_messages m
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id
        LEFT JOIN tbl_chats c ON m.chat_id = c.chat_id
        WHERE {where_sql}
        {group_sql}
        ORDER BY {order_col} {sort_order}
        LIMIT ?
    """
    params.append(limit)
    return sql, params


@investigator_bp.route('/api/investigate', methods=['POST'])
def run_investigation():
    """
    Executes an investigation query and returns Chart.js datasets, tabular rows, and summary.
    """
    try:
        payload = request.get_json(silent=True) or {}
        sql, params = _build_investigation_query(payload)

        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows_raw = cursor.fetchall()

        labels = []
        counts = []
        sizes_mb = []
        table_rows = []

        total_msgs = 0
        total_bytes = 0

        for r in rows_raw:
            lbl = str(r['label']) if r['label'] is not None else 'Unknown'
            cnt = r['count']
            sz_bytes = r['total_size']
            sz_mb = round(sz_bytes / (1024 * 1024), 2)
            avg_kb = round(r['avg_size'] / 1024, 1)

            labels.append(lbl)
            counts.append(cnt)
            sizes_mb.append(sz_mb)

            total_msgs += cnt
            total_bytes += sz_bytes

            row_data = {
                'label': lbl,
                'count': cnt,
                'media_count': r['media_count'] if 'media_count' in r.keys() else 0,
                'total_bytes': sz_bytes,
                'total_mb': sz_mb,
                'avg_kb': avg_kb,
                'ref_id': r['ref_id'] if 'ref_id' in r.keys() else None,
                'ref_type': r['ref_type'] if 'ref_type' in r.keys() else None,
                'avatar_path': r['avatar_path'] if 'avatar_path' in r.keys() else None,
                'username': r['username'] if 'username' in r.keys() else None,
                'is_bot': bool(r['is_bot']) if 'is_bot' in r.keys() and r['is_bot'] else False,
                'top_file_size': r['top_file_size'] if 'top_file_size' in r.keys() else 0,
                'sample_media_path': r['sample_media_path'] if 'sample_media_path' in r.keys() else None,
                'sample_msg_id': r['sample_msg_id'] if 'sample_msg_id' in r.keys() else None,
                'sample_chat_id': r['sample_chat_id'] if 'sample_chat_id' in r.keys() else None,
                'sample_media_type': r['sample_media_type'] if 'sample_media_type' in r.keys() else None,
                'sample_media_name': r['sample_media_name'] if 'sample_media_name' in r.keys() else None
            }
            if 'chat_count' in r.keys():
                row_data['chat_count'] = r['chat_count']

            table_rows.append(row_data)

        # Ensure all 24 continuous hours are represented for chronological hour queries
        req_group = payload.get('group_by')
        req_preset = payload.get('preset')
        req_sort = payload.get('sort_by')
        if (req_group == 'hour' or req_preset == 'peak_hours') and (req_sort == 'label' or req_preset == 'peak_hours' or not req_sort):
            row_map = {r['label']: r for r in table_rows}
            padded_rows = []
            padded_labels = []
            padded_counts = []
            padded_sizes_mb = []
            for h in range(24):
                h_str = f"{h:02d}:00"
                if h_str in row_map:
                    row_data = row_map[h_str]
                else:
                    row_data = {
                        'label': h_str,
                        'count': 0,
                        'media_count': 0,
                        'total_bytes': 0,
                        'total_mb': 0.0,
                        'avg_kb': 0.0,
                        'ref_id': None,
                        'ref_type': 'hour',
                        'avatar_path': None,
                        'username': None,
                        'is_bot': False,
                        'top_file_size': 0,
                        'sample_media_path': None,
                        'sample_msg_id': None,
                        'sample_chat_id': None,
                        'sample_media_type': None,
                        'sample_media_name': None
                    }
                padded_rows.append(row_data)
                padded_labels.append(h_str)
                padded_counts.append(row_data['count'])
                padded_sizes_mb.append(row_data['total_mb'])
            table_rows = padded_rows
            labels = padded_labels
            counts = padded_counts
            sizes_mb = padded_sizes_mb

        conn.close()

        return jsonify({
            'status': 'ok',
            'labels': labels,
            'datasets': [
                {
                    'label': 'Message Count',
                    'data': counts,
                    'type': 'bar'
                },
                {
                    'label': 'Storage (MB)',
                    'data': sizes_mb,
                    'type': 'line'
                }
            ],
            'rows': table_rows,
            'summary': {
                'rows_returned': len(table_rows),
                'total_messages': total_msgs,
                'total_mb': round(total_bytes / (1024 * 1024), 2),
                'total_gb': round(total_bytes / (1024 * 1024 * 1024), 3)
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _sanitize_csv_cell(val):
    if not isinstance(val, str):
        return val
    stripped = val.lstrip()
    if stripped and stripped[0] in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{val}"
    return val

@investigator_bp.route('/api/investigate/export_csv', methods=['POST'])
def export_investigation_csv():
    """
    Exports current investigator query results directly as a downloadable CSV.
    """
    try:
        payload = request.get_json(silent=True) or {}
        # Export up to 500 rows for CSV
        payload['limit'] = 500
        sql, params = _build_investigation_query(payload)

        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows_raw = cursor.fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)

        headers = ['Dimension Label', 'Messages', 'Storage (MB)', 'Avg Size (KB)']
        has_chat_count = len(rows_raw) > 0 and 'chat_count' in rows_raw[0].keys()
        if has_chat_count:
            headers.append('Chat Count')

        writer.writerow(headers)

        for r in rows_raw:
            lbl = _sanitize_csv_cell(str(r['label']) if r['label'] is not None else 'Unknown')
            cnt = r['count']
            sz_mb = round(r['total_size'] / (1024 * 1024), 2)
            avg_kb = round(r['avg_size'] / 1024, 1)

            row = [lbl, cnt, sz_mb, avg_kb]
            if has_chat_count:
                row.append(r['chat_count'])
            writer.writerow(row)

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=investigation_export.csv'}
        )
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
