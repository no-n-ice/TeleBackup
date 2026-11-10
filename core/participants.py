"""
core/participants.py — Subscriber Enter/Exit & Churn Tracking Engine

Synchronizes channel/group participants, detects members entering or leaving,
classifies exit reasons (voluntary leave vs admin kick/ban with actor attribution)
using Telegram Admin Log (GetAdminLogRequest), participant snapshot diffs,
and service messages. Prioritizes admin actions log as the primary ground truth,
reconciling with historical service actions and message senders (excluding forward origins).
"""

import json
import logging
from datetime import datetime, timezone
from telethon.tl.functions.channels import GetParticipantsRequest, GetAdminLogRequest, GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import (
    ChannelParticipantsAdmins, ChannelParticipantsRecent,
    ChannelAdminLogEventsFilter,
    ChannelAdminLogEventActionParticipantJoin,
    ChannelAdminLogEventActionParticipantJoinByInvite,
    ChannelAdminLogEventActionParticipantJoinByRequest,
    ChannelAdminLogEventActionParticipantInvite,
    ChannelAdminLogEventActionParticipantLeave,
    ChannelAdminLogEventActionParticipantToggleBan,
    ChannelParticipantBanned, ChannelParticipantLeft,
    Channel, Chat, User
)
from telethon.errors import (
    ChatAdminRequiredError, ChannelPrivateError, ChannelInvalidError,
    ChatNotModifiedError, UserNotParticipantError, FloodWaitError
)
import core
from core import db


def reconcile_participants_from_db(chat_id, cursor=None):
    """
    Reconciles participants from local database history with strict priority:
    1. Base Priority: tbl_admin_log (official Telegram audit trail for join/leave/kick/ban).
    2. Secondary Priority: tbl_messages.service_action_json (reconstructs actions not present in admin log).
    3. Tertiary Priority: tbl_messages.sender_id (actual authors who spoke in chat; re-activates if spoken after left date).
    Explicitly excludes forward origins (forward_info_json).
    Returns (participants_dict, active_count).
    """
    should_close = False
    if cursor is None:
        conn = db.get_db_conn()
        cursor = conn.cursor()
        should_close = True

    try:
        pipeline = db.get_chat_pipeline(cursor, chat_id)
    except Exception:
        pipeline = [chat_id]

    placeholders = ','.join('?' for _ in pipeline)
    participants = {}  # user_id -> dict

    # 1. Base Priority: tbl_admin_log
    try:
        cursor.execute(f"""
            SELECT event_id, date, user_id, action_type, action_json, new_value_json, prev_value_json
            FROM tbl_admin_log
            WHERE chat_id IN ({placeholders})
            ORDER BY date ASC
        """, pipeline)
        for row in cursor.fetchall():
            ev_date = row['date']
            actor_id = row['user_id']
            a_type = row['action_type'] or ''
            a_json_str = row['action_json'] or '{}'
            new_val_str = row['new_value_json'] or '{}'

            try:
                a_data = json.loads(a_json_str) if isinstance(a_json_str, str) else (a_json_str or {})
            except Exception:
                a_data = {}
            try:
                new_val = json.loads(new_val_str) if isinstance(new_val_str, str) else (new_val_str or {})
            except Exception:
                new_val = {}

            if 'Join' in a_type:
                target_uid = a_data.get('user_id') or actor_id
                p = participants.setdefault(target_uid, {'user_id': target_uid, 'first_seen_at': ev_date})
                p.update({
                    'status': 'active',
                    'entered_at': ev_date,
                    'left_at': None,
                    'exit_reason': None
                })
            elif 'Invite' in a_type:
                target_uid = a_data.get('participant', {}).get('user_id') or a_data.get('user_id')
                if target_uid:
                    p = participants.setdefault(target_uid, {'user_id': target_uid, 'first_seen_at': ev_date})
                    p.update({
                        'status': 'active',
                        'entered_at': ev_date,
                        'invited_by_user_id': actor_id,
                        'left_at': None,
                        'exit_reason': None
                    })
            elif 'Leave' in a_type:
                target_uid = a_data.get('user_id') or actor_id
                p = participants.setdefault(target_uid, {'user_id': target_uid, 'first_seen_at': ev_date})
                p.update({
                    'status': 'left',
                    'left_at': ev_date,
                    'exit_reason': 'own_will'
                })
            elif 'ToggleBan' in a_type or 'Ban' in a_type:
                target_uid = (new_val.get('user_id') or a_data.get('user_id')
                              or a_data.get('participant', {}).get('user_id'))
                if target_uid:
                    is_banned = new_val.get('banned') or a_data.get('banned') or ('Banned' in str(new_val))
                    is_kicked = new_val.get('left') or a_data.get('left') or ('Left' in str(new_val))
                    if is_banned:
                        reason = 'banned'
                    elif is_kicked:
                        reason = 'kicked'
                    else:
                        reason = 'kicked'

                    p = participants.setdefault(target_uid, {'user_id': target_uid, 'first_seen_at': ev_date})
                    if is_banned or is_kicked:
                        p.update({
                            'status': reason,
                            'left_at': ev_date,
                            'exit_reason': reason,
                            'kicked_by_user_id': actor_id
                        })
                    else:
                        # Unbanned / reinstated
                        p.update({
                            'status': 'active',
                            'left_at': None,
                            'exit_reason': None
                        })
    except Exception as e_al:
        logging.debug(f"[Participants] Error reading admin log base: {e_al}")

    # 2. Secondary Priority: tbl_messages.service_action_json
    try:
        cursor.execute(f"""
            SELECT message_id, sender_id, date, service_action_json
            FROM tbl_messages
            WHERE chat_id IN ({placeholders}) AND service_action_json IS NOT NULL
            ORDER BY date ASC
        """, pipeline)
        for s_row in cursor.fetchall():
            s_date = s_row['date']
            sender_id = s_row['sender_id']
            try:
                act = json.loads(s_row['service_action_json'])
                act_type = act.get('_')

                if act_type == 'MessageActionChatAddUser':
                    for au_id in act.get('users', []):
                        p = participants.setdefault(au_id, {'user_id': au_id, 'first_seen_at': s_date})
                        if s_date >= (p.get('left_at') or ''):
                            p.update({
                                'status': 'active',
                                'entered_at': s_date,
                                'invited_by_user_id': sender_id,
                                'left_at': None,
                                'exit_reason': None
                            })
                elif act_type == 'MessageActionChatJoinedByLink':
                    p = participants.setdefault(sender_id, {'user_id': sender_id, 'first_seen_at': s_date})
                    if s_date >= (p.get('left_at') or ''):
                        p.update({
                            'status': 'active',
                            'entered_at': s_date,
                            'invited_by_user_id': act.get('inviter_id') or sender_id,
                            'left_at': None,
                            'exit_reason': None
                        })
                elif act_type == 'MessageActionChatDeleteUser':
                    deleted_uid = act.get('user_id')
                    if deleted_uid:
                        reason = 'own_will' if deleted_uid == sender_id else 'kicked'
                        kicker = sender_id if reason == 'kicked' else None
                        p = participants.setdefault(deleted_uid, {'user_id': deleted_uid, 'first_seen_at': s_date})
                        if s_date >= (p.get('entered_at') or ''):
                            p.update({
                                'status': reason,
                                'left_at': s_date,
                                'exit_reason': reason,
                                'kicked_by_user_id': kicker
                            })
            except Exception:
                pass
    except Exception as e_srv:
        logging.debug(f"[Participants] Error reading service messages: {e_srv}")

    # 3. Tertiary Priority: Actual message authors (tbl_messages.sender_id)
    try:
        cursor.execute(f"""
            SELECT m.sender_id, min(m.date) as first_d, max(m.date) as last_d
            FROM tbl_messages m
            WHERE m.chat_id IN ({placeholders}) AND m.sender_id IS NOT NULL AND m.sender_id > 0
            GROUP BY m.sender_id
        """, pipeline)
        for m_row in cursor.fetchall():
            u_id = m_row['sender_id']
            first_d = m_row['first_d']
            last_d = m_row['last_d']

            if u_id not in participants:
                participants[u_id] = {
                    'user_id': u_id,
                    'status': 'active',
                    'entered_at': first_d,
                    'first_seen_at': first_d,
                    'left_at': None,
                    'exit_reason': None
                }
            else:
                p = participants[u_id]
                # If they spoke in the chat after recorded left_at date, they are active
                if p.get('left_at') and last_d and last_d > p['left_at']:
                    p['status'] = 'active'
                    p['left_at'] = None
                    p['exit_reason'] = None
    except Exception as e_senders:
        logging.debug(f"[Participants] Error checking message senders: {e_senders}")

    # Save reconciled participants into tbl_chat_participants
    now_iso = datetime.now(timezone.utc).isoformat()
    for p in participants.values():
        db.save_chat_participant(
            chat_id=chat_id,
            user_id=p['user_id'],
            status=p.get('status', 'active'),
            entered_at=p.get('entered_at'),
            left_at=p.get('left_at'),
            exit_reason=p.get('exit_reason'),
            kicked_by_user_id=p.get('kicked_by_user_id'),
            invited_by_user_id=p.get('invited_by_user_id'),
            cursor=cursor
        )

    active_count = sum(1 for p in participants.values() if p.get('status') in ('active', 'admin', 'creator'))

    # Update participants_count in tbl_chats
    cursor.execute("""
        UPDATE tbl_chats
        SET participants_count = ?, participants_synced_at = ?
        WHERE chat_id = ?
    """, (active_count, now_iso, chat_id))

    if should_close:
        cursor.connection.commit()
        cursor.connection.close()

    return participants, active_count


async def sync_chat_participants(client, chat_id, db_conn=None):
    """
    Main participant sync orchestrator.
    - Always reconciles local DB history (admin log base + service messages + senders).
    - If client connected and chat accessible on Telegram:
        * Checks whether chat is a 1-on-1 DM (User entity).
        * Fetches live admin log if admin permissions allow.
        * Fetches iter_participants if permitted or available.
        * Differentiates permission denials (ChatAdminRequiredError) from inaccessible chats.
    - Updates tbl_chats.participants_count and participants_synced_at.
    - Returns descriptive message (up to date / access denied / inaccessible / synced).
    """
    cursor = db_conn.cursor() if db_conn else None
    close_db = False
    if not cursor:
        db_conn = db.get_db_conn()
        cursor = db_conn.cursor()
        close_db = True

    now_str = datetime.now(timezone.utc).isoformat()
    status_message = None
    warning_message = None
    info_message = None
    is_inaccessible = False
    admin_restricted = False

    try:
        # Step 1: Base local DB reconciliation
        prev_participants, prev_active_count = reconcile_participants_from_db(chat_id, cursor=cursor)
        participants = dict(prev_participants)
        p_count = prev_active_count

        chat_entity = None
        new_admin_events = 0
        new_active_members = 0
        new_departed_members = 0

        # Step 2: Live online probe if client is provided and connected
        if client and client.is_connected():
            try:
                chat_entity = await client.get_entity(chat_id)
            except ValueError as e_val:
                logging.debug(f"[Participants] Entity not found on Telegram for {chat_id}: {e_val}")
                is_inaccessible = True
                warning_message = "Chat inaccessible on Telegram (entity not found or chat cleared). Reconciled from local history."
            except (ChannelPrivateError, ChannelInvalidError) as e_priv:
                logging.debug(f"[Participants] Channel private / inaccessible for {chat_id}: {e_priv}")
                is_inaccessible = True
                warning_message = "Chat inaccessible on Telegram (private or user removed). Reconciled from local history."
            except UserNotParticipantError as e_not_part:
                logging.debug(f"[Participants] User not participant in {chat_id}: {e_not_part}")
                is_inaccessible = True
                warning_message = "Access denied: You are not a member of this chat. Reconciled from local history."
            except FloodWaitError as e_flood:
                logging.warning(f"[Participants] FloodWait for {chat_id}: {e_flood.seconds}s")
                warning_message = f"Telegram rate limit: FloodWait for {e_flood.seconds}s. Reconciled from local history."
            except (ConnectionError, TimeoutError, OSError) as e_net:
                logging.warning(f"[Participants] Network error probing {chat_id}: {e_net}")
                warning_message = f"Telegram network error ({type(e_net).__name__}). Reconciled from local history."
            except RuntimeError as e_rt:
                logging.error(f"[Participants] Event loop or runtime error probing {chat_id}: {e_rt}", exc_info=True)
                warning_message = f"Runtime synchronization notice: {e_rt}. Reconciled from local history."
            except Exception as e_other:
                logging.warning(f"[Participants] Error fetching entity for {chat_id}: {e_other}", exc_info=True)
                warning_message = f"Telegram probe warning ({type(e_other).__name__}: {e_other}). Reconciled from local history."

        # Step 3: If chat_entity is accessible, perform live inspection
        if chat_entity:
            # 3a. If 1-on-1 private Direct Message
            if isinstance(chat_entity, User) or getattr(chat_entity, 'is_user', False):
                status_message = "1-on-1 Direct Message: Up to date."
                p_count = min(len(participants), 2) or 2
            else:
                admin_log_accessible = True
                members_accessible = True

                # 3b. Admin Log fetch (supergroups/channels)
                if isinstance(chat_entity, Channel):
                    try:
                        filter_events = ChannelAdminLogEventsFilter(
                            join=True, leave=True, invite=True, ban=True, unkick=True
                        )
                        admin_log = await client(GetAdminLogRequest(
                            channel=chat_entity, q='', max_id=0, min_id=0, limit=100, events_filter=filter_events
                        ))
                        for ev in getattr(admin_log, 'events', []):
                            action = ev.action
                            ev_date = ev.date.isoformat() if hasattr(ev.date, 'isoformat') else str(ev.date)
                            actor_id = ev.user_id

                            if isinstance(action, (ChannelAdminLogEventActionParticipantJoin,
                                                  ChannelAdminLogEventActionParticipantJoinByInvite,
                                                  ChannelAdminLogEventActionParticipantJoinByRequest)):
                                target_user_id = getattr(ev, 'user_id', None) or actor_id
                                if target_user_id not in participants or participants[target_user_id].get('status') != 'active':
                                    new_active_members += 1
                                db.save_chat_participant(chat_id, target_user_id, status='active', entered_at=ev_date, cursor=cursor)
                                db.save_participant_event(
                                    chat_id=chat_id, user_id=target_user_id, event_type='join',
                                    date=ev_date, actor_user_id=actor_id, source='admin_log',
                                    external_event_id=ev.id, cursor=cursor
                                )
                                participants[target_user_id] = {'user_id': target_user_id, 'status': 'active'}
                                new_admin_events += 1

                            elif isinstance(action, ChannelAdminLogEventActionParticipantInvite):
                                target_p = action.participant
                                target_user_id = getattr(target_p, 'user_id', None)
                                if target_user_id:
                                    if target_user_id not in participants or participants[target_user_id].get('status') != 'active':
                                        new_active_members += 1
                                    db.save_chat_participant(chat_id, target_user_id, status='active',
                                                             entered_at=ev_date, invited_by_user_id=actor_id, cursor=cursor)
                                    db.save_participant_event(
                                        chat_id=chat_id, user_id=target_user_id, event_type='invite',
                                        date=ev_date, actor_user_id=actor_id, source='admin_log',
                                        external_event_id=ev.id, cursor=cursor
                                    )
                                    participants[target_user_id] = {'user_id': target_user_id, 'status': 'active'}
                                    new_admin_events += 1

                            elif isinstance(action, ChannelAdminLogEventActionParticipantLeave):
                                target_user_id = ev.user_id
                                if target_user_id in participants and participants[target_user_id].get('status') == 'active':
                                    new_departed_members += 1
                                db.save_chat_participant(chat_id, target_user_id, status='left',
                                                         left_at=ev_date, exit_reason='own_will', cursor=cursor)
                                db.save_participant_event(
                                    chat_id=chat_id, user_id=target_user_id, event_type='leave',
                                    date=ev_date, actor_user_id=target_user_id, exit_reason='own_will',
                                    source='admin_log', external_event_id=ev.id, cursor=cursor
                                )
                                participants[target_user_id] = {'user_id': target_user_id, 'status': 'left'}
                                new_admin_events += 1

                            elif isinstance(action, ChannelAdminLogEventActionParticipantToggleBan):
                                new_val = action.new_value
                                target_user_id = getattr(new_val, 'user_id', None) or getattr(action.prev_value, 'user_id', None)
                                if target_user_id:
                                    is_kick_or_ban = isinstance(new_val, (ChannelParticipantBanned, ChannelParticipantLeft))
                                    if is_kick_or_ban and actor_id != target_user_id:
                                        reason = 'banned' if isinstance(new_val, ChannelParticipantBanned) else 'kicked'
                                        if target_user_id in participants and participants[target_user_id].get('status') == 'active':
                                            new_departed_members += 1
                                        db.save_chat_participant(chat_id, target_user_id, status=reason,
                                                                 left_at=ev_date, exit_reason=reason,
                                                                 kicked_by_user_id=actor_id, cursor=cursor)
                                        db.save_participant_event(
                                            chat_id=chat_id, user_id=target_user_id, event_type=reason,
                                            date=ev_date, actor_user_id=actor_id, exit_reason=reason,
                                            source='admin_log', external_event_id=ev.id, cursor=cursor
                                        )
                                        participants[target_user_id] = {'user_id': target_user_id, 'status': reason}
                                        new_admin_events += 1
                    except ChatAdminRequiredError:
                        admin_log_accessible = False
                    except Exception as e_al:
                        admin_log_accessible = False
                        logging.debug(f"[Participants] Admin log fetch skipped: {e_al}")

                # 3c. Snapshot sync via iter_participants
                snapshot_users = set()
                try:
                    async for user_obj in client.iter_participants(chat_entity, limit=5000):
                        if not user_obj: continue
                        u_id = user_obj.id
                        snapshot_users.add(u_id)

                        cursor.execute("""
                            INSERT INTO tbl_users (user_id, first_name, last_name, username, is_bot, is_premium)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET
                                first_name = COALESCE(excluded.first_name, tbl_users.first_name),
                                last_name = COALESCE(excluded.last_name, tbl_users.last_name),
                                username = COALESCE(excluded.username, tbl_users.username)
                        """, (
                            u_id, getattr(user_obj, 'first_name', None), getattr(user_obj, 'last_name', None),
                            getattr(user_obj, 'username', None), 1 if getattr(user_obj, 'bot', False) else 0,
                            1 if getattr(user_obj, 'premium', False) else 0
                        ))
                        if u_id not in participants or participants[u_id].get('status') != 'active':
                            new_active_members += 1
                        db.save_chat_participant(chat_id, u_id, status='active', cursor=cursor)
                        participants[u_id] = {'user_id': u_id, 'status': 'active'}

                    if snapshot_users:
                        cursor.execute("""
                            SELECT user_id FROM tbl_chat_participants
                            WHERE chat_id = ? AND status IN ('active', 'admin', 'creator')
                        """, (chat_id,))
                        previous_active = {r['user_id'] for r in cursor.fetchall()}
                        missing_users = previous_active - snapshot_users
                        for m_uid in missing_users:
                            new_departed_members += 1
                            cursor.execute("""
                                UPDATE tbl_chat_participants
                                SET status = 'left', left_at = COALESCE(left_at, ?),
                                    exit_reason = COALESCE(exit_reason, 'own_will'), updated_at = ?
                                WHERE chat_id = ? AND user_id = ?
                            """, (now_str, now_str, chat_id, m_uid))
                            db.save_participant_event(
                                chat_id=chat_id, user_id=m_uid, event_type='leave',
                                date=now_str, exit_reason='own_will', source='snapshot_diff', cursor=cursor
                            )
                            if m_uid in participants:
                                participants[m_uid]['status'] = 'left'
                except ChatAdminRequiredError:
                    members_accessible = False
                except Exception as e_ip:
                    members_accessible = False
                    logging.debug(f"[Participants] iter_participants skipped: {e_ip}")

                # 3d. Participant count from live entity
                live_p_cnt = getattr(chat_entity, 'participants_count', None)
                try:
                    if isinstance(chat_entity, Channel):
                        full = await client(GetFullChannelRequest(chat_entity))
                        if getattr(full, 'full_chat', None):
                            live_p_cnt = getattr(full.full_chat, 'participants_count', live_p_cnt)
                    elif isinstance(chat_entity, Chat):
                        full = await client(GetFullChatRequest(chat_id=getattr(chat_entity, 'id', chat_id)))
                        if getattr(full, 'full_chat', None):
                            live_p_cnt = getattr(full.full_chat, 'participants_count', live_p_cnt)
                except Exception:
                    pass

                current_active_count = sum(1 for p in participants.values() if p.get('status') in ('active', 'admin', 'creator'))
                if live_p_cnt is not None and live_p_cnt > 0:
                    p_count = live_p_cnt
                else:
                    p_count = current_active_count

                # Formulate intelligent status message
                if not admin_log_accessible and not members_accessible:
                    admin_restricted = True
                    info_message = f"Chat member roster & admin log restricted by Telegram (Admin rights required). Reconciled {current_active_count} active members from chat history."
                    status_message = info_message
                elif new_admin_events > 0 or new_active_members > 0 or new_departed_members > 0:
                    status_message = f"Synced: {new_active_members} active, {new_departed_members} departed, {new_admin_events} admin events."
                else:
                    status_message = f"Up to date: No differences from local database ({current_active_count} active members verified)."

        elif not is_inaccessible and not warning_message:
            current_active_count = sum(1 for p in participants.values() if p.get('status') in ('active', 'admin', 'creator'))
            status_message = f"Offline sync: {current_active_count} active participants reconciled from local history."

        # Final write of participants_count to tbl_chats
        current_active_count = sum(1 for p in participants.values() if p.get('status') in ('active', 'admin', 'creator'))
        if p_count is None or p_count == 0 or is_inaccessible:
            p_count = current_active_count

        cursor.execute("UPDATE tbl_chats SET participants_count = ?, participants_synced_at = ? WHERE chat_id = ?",
                       (p_count, now_str, chat_id))

        if close_db:
            db_conn.commit()
            db_conn.close()

        res_payload = {
            'status': 'ok',
            'chat_id': chat_id,
            'participants_count': p_count,
            'active_count': current_active_count,
            'total_tracked': len(participants),
            'synced_at': now_str,
            'message': status_message or warning_message or "Participants synced successfully."
        }
        if warning_message:
            res_payload['warning'] = warning_message
        elif info_message:
            res_payload['info'] = info_message
        return res_payload

    except Exception as e:
        logging.error(f"[Participants] Sync fatal error for chat {chat_id}: {e}", exc_info=True)
        if close_db and db_conn:
            db_conn.close()
        return {'status': 'error', 'message': f"Internal sync error: {e}"}
