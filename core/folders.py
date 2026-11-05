#!/usr/bin/env python3
"""
core/folders.py — Telegram Dialog Filters & Custom User Folders DB & API operations.
"""
import os
import sys
import json
import uuid
import logging
from datetime import datetime, timezone

def _get_db_conn(timeout=60.0):
    from . import db as _db
    return _db.get_db_conn(timeout=timeout)


def get_folders_list(conn=None):
    """
    Returns all stored folders with assigned chat_ids and counts.
    """
    close_conn = False
    if conn is None:
        conn = _get_db_conn()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT folder_id, title, emoticon, color, source, 
                   pinned_peers_json, include_peers_json, exclude_peers_json, 
                   filter_flags_json, order_index, updated_at
            FROM tbl_folders
            ORDER BY order_index ASC, title ASC
        """)
        rows = cursor.fetchall()
        folders = []
        for r in rows:
            f_id = str(r['folder_id'])
            # Fetch explicitly assigned chats
            cursor.execute("SELECT chat_id, is_pinned FROM tbl_folder_chats WHERE folder_id = ?", (f_id,))
            chat_rows = cursor.fetchall()
            chat_ids = [cr['chat_id'] for cr in chat_rows]
            pinned_ids = [cr['chat_id'] for cr in chat_rows if cr['is_pinned']]

            flags = {}
            if r['filter_flags_json']:
                try:
                    flags = json.loads(r['filter_flags_json'])
                except Exception:
                    flags = {}

            folders.append({
                'folder_id': f_id,
                'title': r['title'],
                'emoticon': r['emoticon'] or '📁',
                'color': r['color'] or 0,
                'source': r['source'] or 'custom',
                'chat_ids': chat_ids,
                'pinned_ids': pinned_ids,
                'chat_count': len(chat_ids),
                'flags': flags,
                'order_index': r['order_index'] or 0,
                'updated_at': r['updated_at']
            })
        return folders
    finally:
        if close_conn:
            conn.close()


def create_folder(title, emoticon='📁', source='custom', chat_ids=None, flags=None, conn=None):
    """
    Creates a new custom or local folder.
    """
    close_conn = False
    if conn is None:
        conn = _get_db_conn()
        close_conn = True

    try:
        folder_id = f"c_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tbl_folders (folder_id, title, emoticon, source, filter_flags_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (folder_id, title.strip(), emoticon.strip() if emoticon else '📁', source, json.dumps(flags or {}), now))

        if chat_ids:
            for cid in chat_ids:
                try:
                    cursor.execute("INSERT OR IGNORE INTO tbl_folder_chats (folder_id, chat_id) VALUES (?, ?)", (folder_id, int(cid)))
                except Exception:
                    pass
        conn.commit()
        return {'folder_id': folder_id, 'title': title, 'emoticon': emoticon, 'source': source, 'chat_ids': chat_ids or []}
    finally:
        if close_conn:
            conn.close()


def delete_folder(folder_id, conn=None):
    """
    Deletes a folder and all its chat associations.
    """
    close_conn = False
    if conn is None:
        conn = _get_db_conn()
        close_conn = True

    try:
        cursor = conn.cursor()
        f_id = str(folder_id)
        cursor.execute("DELETE FROM tbl_folders WHERE folder_id = ?", (f_id,))
        cursor.execute("DELETE FROM tbl_folder_chats WHERE folder_id = ?", (f_id,))
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


def add_chat_to_folder(folder_id, chat_id, is_pinned=0, conn=None):
    """
    Adds a chat to a folder.
    """
    close_conn = False
    if conn is None:
        conn = _get_db_conn()
        close_conn = True

    try:
        cursor = conn.cursor()
        f_id = str(folder_id)
        c_id = int(chat_id)
        cursor.execute("INSERT OR REPLACE INTO tbl_folder_chats (folder_id, chat_id, is_pinned) VALUES (?, ?, ?)", (f_id, c_id, 1 if is_pinned else 0))
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


def remove_chat_from_folder(folder_id, chat_id, conn=None):
    """
    Removes a chat from a folder.
    """
    close_conn = False
    if conn is None:
        conn = _get_db_conn()
        close_conn = True

    try:
        cursor = conn.cursor()
        f_id = str(folder_id)
        c_id = int(chat_id)
        cursor.execute("DELETE FROM tbl_folder_chats WHERE folder_id = ? AND chat_id = ?", (f_id, c_id))
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


async def sync_telegram_dialog_filters(client, conn=None):
    """
    Harvests official Telegram folders (DialogFilters) and persists them in SQLite.
    """
    if not client:
        return {'ok': False, 'error': 'Telegram client not connected'}

    from telethon.tl.functions.messages import GetDialogFiltersRequest
    from telethon.tl.types import DialogFilter, DialogFilterChatlist
    from telethon.utils import get_peer_id

    try:
        filters_res = await client(GetDialogFiltersRequest())
    except Exception as exc:
        logging.warning(f"[Folders] Failed to fetch dialog filters from Telegram: {exc}")
        return {'ok': False, 'error': str(exc)}

    close_conn = False
    if conn is None:
        conn = _get_db_conn()
        close_conn = True

    synced_count = 0
    now = datetime.now(timezone.utc).isoformat()

    try:
        cursor = conn.cursor()
        filter_items = getattr(filters_res, 'filters', None)
        if filter_items is None:
            if hasattr(filters_res, '__iter__') and not isinstance(filters_res, (str, bytes, dict)):
                filter_items = list(filters_res)
            else:
                filter_items = [filters_res]

        for idx, flt in enumerate(filter_items):
            if hasattr(flt, 'id') and flt.id:
                f_id = str(flt.id)
                # Title might be TextWithEntities or string
                raw_title = getattr(flt, 'title', None)
                if hasattr(raw_title, 'text'):
                    title = raw_title.text
                elif isinstance(raw_title, str):
                    title = raw_title
                else:
                    title = str(raw_title or f"Folder {flt.id}")

                emoticon = getattr(flt, 'emoticon', None) or '📁'
                color = getattr(flt, 'color', 0) or 0

                # Extract peers
                pinned_peers = getattr(flt, 'pinned_peers', []) or []
                include_peers = getattr(flt, 'include_peers', []) or []
                exclude_peers = getattr(flt, 'exclude_peers', []) or []

                pinned_cids = []
                for p in pinned_peers:
                    try:
                        pinned_cids.append(get_peer_id(p))
                    except Exception:
                        pass

                include_cids = []
                for p in include_peers:
                    try:
                        include_cids.append(get_peer_id(p))
                    except Exception:
                        pass

                exclude_cids = []
                for p in exclude_peers:
                    try:
                        exclude_cids.append(get_peer_id(p))
                    except Exception:
                        pass

                flags = {
                    'contacts': bool(getattr(flt, 'contacts', False)),
                    'non_contacts': bool(getattr(flt, 'non_contacts', False)),
                    'groups': bool(getattr(flt, 'groups', False)),
                    'broadcasts': bool(getattr(flt, 'broadcasts', False)),
                    'bots': bool(getattr(flt, 'bots', False)),
                    'exclude_muted': bool(getattr(flt, 'exclude_muted', False)),
                    'exclude_read': bool(getattr(flt, 'exclude_read', False)),
                    'exclude_archived': bool(getattr(flt, 'exclude_archived', False)),
                }

                cursor.execute("""
                    INSERT OR REPLACE INTO tbl_folders (
                        folder_id, title, emoticon, color, source,
                        pinned_peers_json, include_peers_json, exclude_peers_json,
                        filter_flags_json, order_index, updated_at
                    ) VALUES (?, ?, ?, ?, 'telegram', ?, ?, ?, ?, ?, ?)
                """, (
                    f_id, title, emoticon, color,
                    json.dumps(pinned_cids), json.dumps(include_cids), json.dumps(exclude_cids),
                    json.dumps(flags), idx, now
                ))

                # Populate tbl_folder_chats for pinned and explicitly included chats
                all_cids = set(pinned_cids + include_cids)
                for cid in all_cids:
                    is_pinned = 1 if cid in pinned_cids else 0
                    cursor.execute("INSERT OR REPLACE INTO tbl_folder_chats (folder_id, chat_id, is_pinned) VALUES (?, ?, ?)", (f_id, cid, is_pinned))

                # If flag-based inclusion is on (e.g. broadcasts=True, groups=True), also populate any matching chats currently in tbl_chats
                matched_types = []
                if flags.get('broadcasts'):
                    matched_types.append('channel')
                if flags.get('groups'):
                    matched_types.extend(['group', 'supergroup'])
                if flags.get('bots'):
                    matched_types.append('bot')
                if flags.get('contacts') or flags.get('non_contacts'):
                    matched_types.extend(['user', 'private'])

                if matched_types:
                    placeholders = ','.join('?' for _ in matched_types)
                    cursor.execute(f"SELECT chat_id FROM tbl_chats WHERE chat_type IN ({placeholders})", matched_types)
                    for crow in cursor.fetchall():
                        cid = crow['chat_id']
                        if cid not in exclude_cids:
                            cursor.execute("INSERT OR IGNORE INTO tbl_folder_chats (folder_id, chat_id, is_pinned) VALUES (?, ?, 0)", (f_id, cid))

                synced_count += 1

        conn.commit()
        return {'ok': True, 'synced_count': synced_count, 'folders': get_folders_list(conn=conn)}
    finally:
        if close_conn:
            conn.close()

