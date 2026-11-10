#!/usr/bin/env python3
"""
routes/folders.py — API endpoints for Telegram Dialog Filters & Custom Folders.
"""
import asyncio
import logging
from flask import Blueprint, request, jsonify
import core
from core import folders

folders_bp = Blueprint('folders', __name__)

@folders_bp.route('/api/folders', methods=['GET'])
def list_folders():
    try:
        data = folders.get_folders_list()
        return jsonify({'ok': True, 'folders': data})
    except Exception as exc:
        logging.error(f"[Folders API] Error listing folders: {exc}", exc_info=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@folders_bp.route('/api/folders/create', methods=['POST'])
def create_custom_folder():
    payload = request.get_json(silent=True) or {}
    title = payload.get('title')
    if not title:
        return jsonify({'ok': False, 'error': 'Folder title is required'}), 400
    emoticon = payload.get('emoticon', '📁')
    chat_ids = payload.get('chat_ids', [])
    try:
        created = folders.create_folder(title=title, emoticon=emoticon, chat_ids=chat_ids)
        return jsonify({'ok': True, 'folder': created})
    except Exception as exc:
        logging.error(f"[Folders API] Error creating folder: {exc}", exc_info=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@folders_bp.route('/api/folders/delete', methods=['POST'])
def delete_folder():
    payload = request.get_json(silent=True) or {}
    folder_id = payload.get('folder_id')
    if not folder_id:
        return jsonify({'ok': False, 'error': 'folder_id is required'}), 400
    try:
        folders.delete_folder(folder_id)
        return jsonify({'ok': True})
    except Exception as exc:
        logging.error(f"[Folders API] Error deleting folder: {exc}", exc_info=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@folders_bp.route('/api/folders/chat', methods=['POST'])
def update_folder_chat():
    payload = request.get_json(silent=True) or {}
    folder_id = payload.get('folder_id')
    chat_id = payload.get('chat_id')
    action = payload.get('action', 'add')
    if not folder_id or not chat_id:
        return jsonify({'ok': False, 'error': 'folder_id and chat_id are required'}), 400

    try:
        if action == 'remove':
            folders.remove_chat_from_folder(folder_id, chat_id)
        else:
            is_pinned = bool(payload.get('is_pinned', False))
            folders.add_chat_to_folder(folder_id, chat_id, is_pinned=is_pinned)
        return jsonify({'ok': True})
    except Exception as exc:
        logging.error(f"[Folders API] Error updating folder chat: {exc}", exc_info=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500


@folders_bp.route('/api/folders/sync', methods=['POST'])
def sync_folders():
    client = core.LIVE_CLIENT
    if not client or not client.is_connected():
        return jsonify({'ok': False, 'error': 'Telegram client is not connected'}), 400

    try:
        from core import worker
        if worker.LIVE_LOOP and worker.LIVE_LOOP.is_running():
            future = asyncio.run_coroutine_threadsafe(
                folders.sync_telegram_dialog_filters(client),
                worker.LIVE_LOOP
            )
            res = future.result(timeout=20.0)
            return jsonify(res)
        else:
            loop = asyncio.new_event_loop()
            res = loop.run_until_complete(folders.sync_telegram_dialog_filters(client))
            loop.close()
            return jsonify(res)
    except Exception as exc:
        logging.error(f"[Folders API] Error syncing folders: {exc}", exc_info=True)
        return jsonify({'ok': False, 'error': str(exc)}), 500

