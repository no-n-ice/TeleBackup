#!/usr/bin/env python3
"""
core/db_participants.py — Subscriber & Participant Churn Tracker DB CRUD helpers.
"""
import sqlite3
import logging
from datetime import datetime, timezone

def _get_db_conn(timeout=60.0):
    from . import db as _db
    return _db.get_db_conn(timeout=timeout)

# ══════════════════════════════════════════════════════════════════
# ── SUBSCRIBER & PARTICIPANT CHURN TRACKER HELPERS ───────────────
# ══════════════════════════════════════════════════════════════════

def update_chat_participants_count(chat_id, count, cursor=None):
    """Updates tbl_chats.participants_count and logs a snapshot event if changed."""
    should_close = False
    if cursor is None:
        conn = _get_db_conn()
        cursor = conn.cursor()
        should_close = True

    try:
        from .helpers import get_chat_id_variants
        variants = list(get_chat_id_variants(chat_id))
        placeholders = ",".join("?" for _ in variants)

        cursor.execute(f"SELECT participants_count FROM tbl_chats WHERE chat_id IN ({placeholders})", variants)
        row = cursor.fetchone()
        prev_count = row['participants_count'] if row else 0

        cursor.execute(f"""
            UPDATE tbl_chats 
            SET participants_count = ?, participants_synced_at = ?
            WHERE chat_id IN ({placeholders})
        """, [count, datetime.now(timezone.utc).isoformat()] + variants)

        if prev_count != count:
            cursor.execute("""
                INSERT INTO tbl_participant_events (chat_id, event_type, date, participants_count, source)
                VALUES (?, 'count_snapshot', ?, ?, 'sync')
            """, (chat_id, datetime.now(timezone.utc).isoformat(), count))

        if should_close:
            cursor.connection.commit()
    except Exception as e:
        logging.error(f"[DB] Error updating participants count: {e}")
    finally:
        if should_close:
            cursor.connection.close()


def save_chat_participant(chat_id, user_id, status='active', entered_at=None, left_at=None, 
                          exit_reason=None, kicked_by_user_id=None, invited_by_user_id=None, 
                          admin_rank=None, cursor=None):
    """Inserts or updates a subscriber/participant record."""
    should_close = False
    if cursor is None:
        conn = _get_db_conn()
        cursor = conn.cursor()
        should_close = True

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        cursor.execute("""
            INSERT INTO tbl_chat_participants (
                chat_id, user_id, status, entered_at, first_seen_at, left_at,
                exit_reason, kicked_by_user_id, invited_by_user_id, admin_rank, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                status = excluded.status,
                entered_at = COALESCE(excluded.entered_at, tbl_chat_participants.entered_at),
                left_at = COALESCE(excluded.left_at, tbl_chat_participants.left_at),
                exit_reason = COALESCE(excluded.exit_reason, tbl_chat_participants.exit_reason),
                kicked_by_user_id = COALESCE(excluded.kicked_by_user_id, tbl_chat_participants.kicked_by_user_id),
                invited_by_user_id = COALESCE(excluded.invited_by_user_id, tbl_chat_participants.invited_by_user_id),
                admin_rank = COALESCE(excluded.admin_rank, tbl_chat_participants.admin_rank),
                updated_at = excluded.updated_at
        """, (
            chat_id, user_id, status, entered_at, now_iso, left_at,
            exit_reason, kicked_by_user_id, invited_by_user_id, admin_rank, now_iso
        ))
        if should_close:
            cursor.connection.commit()
    except Exception as e:
        logging.error(f"[DB] Error saving chat participant {user_id} in {chat_id}: {e}")
    finally:
        if should_close:
            cursor.connection.close()


def save_participant_event(chat_id, user_id, event_type, date=None, actor_user_id=None,
                           exit_reason=None, participants_count=None, source='admin_log',
                           external_event_id=None, raw_json=None, cursor=None):
    """Logs an enter/exit/kick/ban or snapshot event in tbl_participant_events."""
    should_close = False
    if cursor is None:
        conn = _get_db_conn()
        cursor = conn.cursor()
        should_close = True

    date_iso = date or datetime.now(timezone.utc).isoformat()
    try:
        cursor.execute("""
            INSERT INTO tbl_participant_events (
                chat_id, user_id, event_type, date, actor_user_id, exit_reason,
                participants_count, source, external_event_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chat_id, user_id, event_type, date_iso, actor_user_id, exit_reason,
            participants_count, source, external_event_id, raw_json
        ))
        if should_close:
            cursor.connection.commit()
    except Exception as e:
        logging.error(f"[DB] Error saving participant event: {e}")
    finally:
        if should_close:
            cursor.connection.close()


def get_chat_participants_summary(chat_id):
    """Returns participant statistics and tracker status for a chat."""
    from .helpers import get_chat_id_variants
    variants = list(get_chat_id_variants(chat_id))
    placeholders = ",".join("?" for _ in variants)

    conn = _get_db_conn()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT participants_count, track_participants, participants_synced_at, chat_type, backup_status
        FROM tbl_chats WHERE chat_id IN ({placeholders})
    """, variants)
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    # Check if participants have been tracked yet; if not, do a fast local DB reconciliation
    cursor.execute(f"SELECT COUNT(*) as cnt FROM tbl_chat_participants WHERE chat_id IN ({placeholders})", variants)
    existing_cnt = cursor.fetchone()['cnt']
    if existing_cnt == 0:
        try:
            from .participants import reconcile_participants_from_db
            reconcile_participants_from_db(chat_id, cursor=cursor)
            conn.commit()
            cursor.execute(f"SELECT participants_count, participants_synced_at FROM tbl_chats WHERE chat_id IN ({placeholders})", variants)
            updated_row = cursor.fetchone()
            row_p_cnt = updated_row['participants_count'] if updated_row else row['participants_count']
            row_synced_at = updated_row['participants_synced_at'] if updated_row else row['participants_synced_at']
        except Exception as e_recon:
            logging.debug(f"[Participants] Auto-reconcile on summary skipped: {e_recon}")
            row_p_cnt = row['participants_count']
            row_synced_at = row['participants_synced_at']
    else:
        row_p_cnt = row['participants_count']
        row_synced_at = row['participants_synced_at']

    res = {
        'chat_id': chat_id,
        'participants_count': row_p_cnt or 0,
        'track_participants': bool(row['track_participants']),
        'participants_synced_at': row_synced_at,
        'chat_type': row['chat_type'],
        'backup_status': row['backup_status'],
        'total_tracked': 0,
        'active_count': 0,
        'left_count': 0,
        'kicked_count': 0,
        'banned_count': 0
    }

    cursor.execute(f"""
        SELECT status, COUNT(*) as cnt
        FROM tbl_chat_participants
        WHERE chat_id IN ({placeholders})
        GROUP BY status
    """, variants)
    for s_row in cursor.fetchall():
        status = s_row['status']
        cnt = s_row['cnt']
        res['total_tracked'] += cnt
        if status in ('active', 'admin', 'creator'):
            res['active_count'] += cnt
        elif status == 'left':
            res['left_count'] += cnt
        elif status == 'kicked':
            res['kicked_count'] += cnt
        elif status == 'banned':
            res['banned_count'] += cnt

    # If chat is inaccessible or deleted on Telegram, prioritize local active_count
    if res['active_count'] > 0 and (res['participants_count'] == 0 or row['backup_status'] in ('inaccessible', 'deleted')):
        res['participants_count'] = res['active_count']

    # Fetch recent count snapshots for growth sparkline
    cursor.execute(f"""
        SELECT date, participants_count
        FROM tbl_participant_events
        WHERE chat_id IN ({placeholders}) AND participants_count IS NOT NULL
        ORDER BY date ASC LIMIT 100
    """, variants)
    res['snapshots'] = [{'date': r['date'], 'count': r['participants_count']} for r in cursor.fetchall()]

    conn.close()
    return res


def get_chat_participants_list(chat_id, status=None, search=None, limit=100, offset=0):
    """Returns paginated list of subscribers joined with user profile info."""
    from .helpers import get_chat_id_variants
    variants = list(get_chat_id_variants(chat_id))
    placeholders = ",".join("?" for _ in variants)

    conn = _get_db_conn()
    cursor = conn.cursor()

    query = f"""
        SELECT 
            cp.user_id, cp.status, cp.entered_at, cp.first_seen_at, cp.left_at,
            cp.exit_reason, cp.kicked_by_user_id, cp.invited_by_user_id, cp.admin_rank, cp.updated_at,
            u.first_name, u.last_name, u.username, u.avatar_path, u.is_bot, u.is_premium,
            k.first_name as kicker_first_name, k.last_name as kicker_last_name, k.username as kicker_username
        FROM tbl_chat_participants cp
        LEFT JOIN tbl_users u ON cp.user_id = u.user_id
        LEFT JOIN tbl_users k ON cp.kicked_by_user_id = k.user_id
        WHERE cp.chat_id IN ({placeholders})
    """
    params = list(variants)

    if status and status != 'all':
        if status == 'active':
            query += " AND cp.status IN ('active', 'admin', 'creator')"
        elif status == 'exited':
            query += " AND cp.status IN ('left', 'kicked', 'banned')"
        else:
            query += " AND cp.status = ?"
            params.append(status)

    if search:
        search_pattern = f"%{search}%"
        query += " AND (u.first_name LIKE ? OR u.last_name LIKE ? OR u.username LIKE ? OR CAST(cp.user_id AS TEXT) LIKE ?)"
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

    query += " ORDER BY COALESCE(cp.left_at, cp.entered_at, cp.first_seen_at) DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    results = [dict(r) for r in rows]
    conn.close()
    return results


def get_chat_participant_events(chat_id, limit=100, offset=0):
    """Returns chronological audit feed of subscriber joins, leaves, kicks, and bans."""
    from .helpers import get_chat_id_variants
    variants = list(get_chat_id_variants(chat_id))
    placeholders = ",".join("?" for _ in variants)

    conn = _get_db_conn()
    cursor = conn.cursor()

    query = f"""
        SELECT 
            pe.id, pe.chat_id, pe.user_id, pe.event_type, pe.date, pe.actor_user_id,
            pe.exit_reason, pe.participants_count, pe.source,
            u.first_name, u.last_name, u.username, u.avatar_path,
            act.first_name as actor_first_name, act.last_name as actor_last_name, act.username as actor_username
        FROM tbl_participant_events pe
        LEFT JOIN tbl_users u ON pe.user_id = u.user_id
        LEFT JOIN tbl_users act ON pe.actor_user_id = act.user_id
        WHERE pe.chat_id IN ({placeholders}) AND pe.event_type != 'count_snapshot'
        ORDER BY pe.date DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, variants + [limit, offset])
    rows = cursor.fetchall()
    results = [dict(r) for r in rows]
    conn.close()
    return results


def set_track_participants(chat_id, enabled):
    """Toggles subscriber tracking for a chat."""
    from .helpers import get_chat_id_variants
    variants = list(get_chat_id_variants(chat_id))
    placeholders = ",".join("?" for _ in variants)
    conn = _get_db_conn()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE tbl_chats SET track_participants = ? WHERE chat_id IN ({placeholders})", [1 if enabled else 0] + variants)
    conn.commit()
    conn.close()


