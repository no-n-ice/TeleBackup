#!/usr/bin/env python3
"""
emoji.py — Custom emoji extraction and download engine.
"""
import os
import logging
from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
from telethon.tl.types import DocumentAttributeCustomEmoji
from . import db
import core

def extract_custom_emoji_ids_from_message(message):
    ids = set()
    if getattr(message, 'entities', None):
        for e in message.entities:
            if type(e).__name__ == 'MessageEntityCustomEmoji' and hasattr(e, 'document_id'):
                ids.add(e.document_id)
    if getattr(message, 'reactions', None):
        reactions = message.reactions
        for res in (getattr(reactions, 'results', None) or []):
            rxn = getattr(res, 'reaction', None)
            if type(rxn).__name__ == 'ReactionCustomEmoji' and hasattr(rxn, 'document_id'):
                ids.add(rxn.document_id)
        for rec in (getattr(reactions, 'recent_reactions', None) or []):
            rxn = getattr(rec, 'reaction', None)
            if type(rxn).__name__ == 'ReactionCustomEmoji' and hasattr(rxn, 'document_id'):
                ids.add(rxn.document_id)
    return ids


def extract_custom_emoji_ids_from_user(user, user_full=None):
    """Extracts custom emoji document IDs from user's emoji status and full profile bio entities."""
    ids = set()
    emoji_status = getattr(user, 'emoji_status', None)
    if emoji_status and hasattr(emoji_status, 'document_id'):
        ids.add(emoji_status.document_id)
    if user_full:
        full_user = getattr(user_full, 'full_user', None) or user_full
        for ent in (getattr(full_user, 'about_entities', None) or []):
            if type(ent).__name__ == 'MessageEntityCustomEmoji' and hasattr(ent, 'document_id'):
                ids.add(ent.document_id)
    return ids


def extract_custom_emoji_ids_from_chat(chat, chat_full=None):
    """Extracts custom emoji document IDs from channel/supergroup emoji status."""
    ids = set()
    emoji_status = getattr(chat, 'emoji_status', None)
    if emoji_status and hasattr(emoji_status, 'document_id'):
        ids.add(emoji_status.document_id)
    if chat_full:
        full_chat = getattr(chat_full, 'full_chat', None) or chat_full
        c_status = getattr(full_chat, 'emoji_status', None)
        if c_status and hasattr(c_status, 'document_id'):
            ids.add(c_status.document_id)
    return ids


async def async_fetch_and_save_custom_emojis(client, doc_ids, conn=None):
    if not doc_ids:
        return
    custom_emoji_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "custom_emoji")
    os.makedirs(custom_emoji_dir, exist_ok=True)
    
    close_conn = False
    if conn is None:
        conn = db.get_db_conn()
        close_conn = True
    try:
        cur = conn.cursor()
        missing_ids = []
        for d in set(doc_ids):
            try:
                d_int = int(d)
            except (ValueError, TypeError):
                continue
            cur.execute("SELECT file_path FROM tbl_custom_emoji WHERE document_id = ?", (d_int,))
            row = cur.fetchone()
            if row and row['file_path']:
                abs_p = os.path.join(core.ABS_MEDIA_BACKUP_DIR, row['file_path'])
                if os.path.exists(abs_p):
                    continue
            missing_ids.append(d_int)
            
        if not missing_ids:
            return
            
        batch_size = 100
        for i in range(0, len(missing_ids), batch_size):
            batch = missing_ids[i:i + batch_size]
            try:
                res = await client(GetCustomEmojiDocumentsRequest(document_id=batch))
                for doc in res:
                    if not hasattr(doc, 'id') or not doc.id:
                        continue
                    doc_id = doc.id
                    mime = getattr(doc, 'mime_type', 'image/webp') or 'image/webp'
                    alt = '❓'
                    for attr in (getattr(doc, 'attributes', None) or []):
                        if isinstance(attr, DocumentAttributeCustomEmoji):
                            alt = getattr(attr, 'alt', '❓') or '❓'
                            break
                            
                    ext = '.webp'
                    if 'video' in mime or 'webm' in mime:
                        ext = '.webm'
                    elif 'tgsticker' in mime or 'json' in mime:
                        ext = '.tgs'
                    elif 'png' in mime:
                        ext = '.png'
                    elif 'gif' in mime:
                        ext = '.gif'
                        
                    filename = f"{doc_id}{ext}"
                    target_path = os.path.join(custom_emoji_dir, filename)
                    rel_path = os.path.relpath(target_path, core.ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
                    
                    if not os.path.exists(target_path):
                        await client.download_media(doc, file=target_path)
                        
                    db.upsert_custom_emoji(cur, doc_id, rel_path, mime, alt)
                conn.commit()
            except Exception as e:
                logging.error(f"Error downloading custom emoji batch: {e}")
    finally:
        if close_conn:
            try: conn.close()
            except Exception: pass
