import logging
import re
import os
import shutil
import json
import asyncio
import random
from datetime import datetime
from telethon.tl.types import DocumentAttributeFilename, User
import configparser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config_path = os.path.join(PROJECT_ROOT, 'config.ini')
if not os.path.exists(config_path):
    config_path = 'config.ini'
if os.path.exists(config_path):
    try:
        config.read(config_path, encoding='utf-8')
    except Exception:
        pass

MEDIA_BACKUP_DIR = config.get('Backup', 'media_dir', fallback='media_backup').strip() or 'media_backup'
try:
    raw_max_size = config.get('Backup', 'max_file_size_mb', fallback='10').strip()
    MAX_FILE_SIZE_MB = int(raw_max_size) if raw_max_size else 10
except Exception:
    MAX_FILE_SIZE_MB = 10

ABS_MEDIA_BACKUP_DIR = os.path.join(PROJECT_ROOT, MEDIA_BACKUP_DIR) if not os.path.isabs(MEDIA_BACKUP_DIR) else os.path.abspath(MEDIA_BACKUP_DIR)
MAX_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024 if MAX_FILE_SIZE_MB > 0 else 0

try:
    from .db import get_db_conn, fetch_message_from_db
except ImportError:
    from db import get_db_conn, fetch_message_from_db


def dt_converter(o):
    if isinstance(o, datetime): return o.isoformat()
    if hasattr(o, 'to_dict'): return o.to_dict()
    return str(o)

def sanitize_filename(filename):
    if not filename: return "unknown_file"
    return re.sub(r'[\\/*?:"<>|]', "_", filename).strip()

def get_message_forward_sender_id(message):
    """Safely extracts forward origin user_id or channel_id from a Telethon Message object."""
    fwd = getattr(message, 'fwd_from', None)
    if not fwd:
        return None
    f_peer = getattr(fwd, 'from_id', None) or getattr(fwd, 'from_', None) or getattr(fwd, 'saved_from_peer', None)
    if not f_peer:
        return None
    try:
        from telethon import utils
        return utils.get_peer_id(f_peer)
    except Exception:
        return getattr(f_peer, 'user_id', None) or getattr(f_peer, 'channel_id', None)

MEDIA_SUBFOLDERS = ('photos', 'videos', 'voice', 'round_video', 'audio', 'stickers', 'gifs', 'documents', 'other', 'avatars')

def ensure_chat_media_subdirs(chat_media_dir):
    """Ensures all standard media subdirectories exist for a chat."""
    for sub in MEDIA_SUBFOLDERS:
        os.makedirs(os.path.join(chat_media_dir, sub), exist_ok=True)

def get_media_subfolder_name(mime_type=None, is_photo=False, attributes=None, filename=None, is_voice=False, is_round_video=False):
    """
    Determines the appropriate media subfolder name based on media properties.
    Returns one of: 'photos', 'videos', 'voice', 'round_video', 'audio', 'stickers', 'gifs', 'documents', 'other'.
    """
    if is_photo:
        return 'photos'
    
    mime = (mime_type or '').lower().strip()
    attrs = attributes or []
    
    is_sticker_attr = False
    is_animated_attr = False
    is_voice_attr = bool(is_voice)
    is_round_attr = bool(is_round_video)
    is_audio_attr = False
    is_video_attr = False
    
    for attr in attrs:
        attr_cls = getattr(attr, '__class__', type(attr)).__name__
        if 'Sticker' in attr_cls:
            is_sticker_attr = True
        elif 'Animated' in attr_cls:
            is_animated_attr = True
        elif 'Audio' in attr_cls:
            is_audio_attr = True
            if getattr(attr, 'voice', False):
                is_voice_attr = True
        elif 'Video' in attr_cls:
            is_video_attr = True
            if getattr(attr, 'round_message', False):
                is_round_attr = True
                
    if is_sticker_attr or mime == 'application/x-tgsticker':
        return 'stickers'
    if is_round_attr:
        return 'round_video'
    if is_voice_attr:
        return 'voice'
    if is_animated_attr or mime == 'image/gif':
        return 'gifs'
        
    if mime in ('image/webp', 'video/webm') and is_sticker_attr:
        return 'stickers'
        
    if is_audio_attr or mime.startswith('audio/'):
        return 'voice' if is_voice_attr else 'audio'
        
    if is_video_attr or mime.startswith('video/'):
        return 'videos'
        
    if mime.startswith('image/'):
        return 'photos'
        
    doc_extensions = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.zip', '.rar', '.7z', '.tar', '.gz', '.json', '.xml', '.csv', '.apk', '.exe')
    if filename:
        fn_lower = filename.lower()
        if any(fn_lower.endswith(ext) for ext in doc_extensions):
            return 'documents'
            
    if mime in ('application/pdf', 'application/zip', 'application/x-rar-compressed', 'application/x-7z-compressed',
                'text/plain', 'text/csv', 'application/json', 'application/xml', 'application/msword') or \
       'document' in mime or 'spreadsheet' in mime or 'presentation' in mime:
        return 'documents'
        
    return 'documents' if (mime and mime != 'application/octet-stream') else 'other'

def get_media_target_dir(chat_media_dir, mime_type=None, is_photo=False, attributes=None, filename=None, is_voice=False, is_round_video=False):
    """
    Returns the absolute path to the target subfolder inside chat_media_dir.
    Creates the directory if it doesn't exist.
    """
    subfolder = get_media_subfolder_name(
        mime_type=mime_type,
        is_photo=is_photo,
        attributes=attributes,
        filename=filename,
        is_voice=is_voice,
        is_round_video=is_round_video
    )
    target_path = os.path.join(chat_media_dir, subfolder)
    os.makedirs(target_path, exist_ok=True)
    return target_path

def get_thumbs_target_dir(target_dir):
    """
    Returns the absolute path to the 'thumbs' subfolder inside target_dir.
    Creates the directory if it doesn't exist.
    """
    thumbs_path = os.path.join(target_dir, "thumbs")
    os.makedirs(thumbs_path, exist_ok=True)
    return thumbs_path

def get_shared_stickers_dir():
    """
    Returns the absolute path to the global shared stickers directory: media_backup/.shared/stickers/
    Creates directory structure on demand.
    """
    shared_path = os.path.join(ABS_MEDIA_BACKUP_DIR, ".shared", "stickers")
    os.makedirs(shared_path, exist_ok=True)
    return shared_path

def try_create_hardlink(src_abs, dst_abs):
    """
    Creates an NTFS hardlink from src_abs to dst_abs.
    If cross-volume or filesystem does not support links, falls back to shutil.copyfile.
    Returns True if hardlinked or copied, False on error.
    """
    if not src_abs or not os.path.isfile(src_abs):
        return False
    if os.path.exists(dst_abs):
        return True
    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
    try:
        os.link(src_abs, dst_abs)
        return True
    except OSError:
        try:
            shutil.copyfile(src_abs, dst_abs)
            return True
        except Exception:
            return False

def find_existing_local_media(cursor, filename, file_size=None):
    """
    Finds an existing downloaded media file with matching filename and size.
    Returns absolute path if file exists on disk, else None.
    """
    if not filename:
        return None
    try:
        if file_size and file_size > 0:
            cursor.execute("""
                SELECT media_local_path 
                FROM tbl_messages 
                WHERE media_original_name = ? AND file_size = ? AND media_local_path IS NOT NULL 
                LIMIT 5
            """, (filename, file_size))
        else:
            cursor.execute("""
                SELECT media_local_path 
                FROM tbl_messages 
                WHERE media_original_name = ? AND media_local_path IS NOT NULL 
                LIMIT 5
            """, (filename,))
        for row in cursor.fetchall():
            rel = row['media_local_path'] if hasattr(row, 'keys') else row[0]
            if rel:
                abs_path = os.path.join(ABS_MEDIA_BACKUP_DIR, rel.replace('/', os.sep))
                if os.path.isfile(abs_path):
                    return abs_path
    except Exception as e:
        logging.debug(f"[find_existing_local_media] error: {e}")
    return None

def get_chat_id_variants(chat_id):
    """Returns ordered list of chat ID variants (prioritizing exact ID and normalized -100 ID)."""
    if chat_id is None:
        return []
    s = str(chat_id).strip()
    if not s:
        return []

    ordered = []
    seen = set()

    def _add(val):
        if val not in seen:
            seen.add(val)
            ordered.append(val)

    try:
        orig_int = int(chat_id)
        _add(orig_int)
        if 0 < orig_int < 1000000000000:
            _add(-1000000000000 - orig_int)
    except Exception:
        pass

    try:
        bare_int = int(s.replace("-100", "").replace("-", ""))
        _add(int(f"-100{bare_int}"))
        _add(-bare_int)
        _add(bare_int)
    except Exception:
        pass

    return ordered


def build_canonical_media_filename(msg, db_orig_name=None):
    """Builds canonical media filename matching Phase 1 convention so hardlink dedup & sticker sharing work."""
    if db_orig_name and str(db_orig_name).strip():
        return str(db_orig_name).strip()

    if getattr(msg, 'photo', None):
        media_id = getattr(msg.photo, 'id', 'photo')
        return f"{msg.id} - {media_id}.jpg"

    doc = getattr(msg, 'document', None)
    if doc:
        media_id = getattr(doc, 'id', 'doc')
        mime = getattr(doc, 'mime_type', '') or ''
        attrs = getattr(doc, 'attributes', None) or []
        is_sticker = (mime == 'application/x-tgsticker')
        is_gif = (mime == 'image/gif')
        orig_fn = None

        for attr in attrs:
            cls_name = type(attr).__name__
            if 'Sticker' in cls_name:
                is_sticker = True
            elif 'Animated' in cls_name:
                is_gif = True

        # Audio title/performer check matching Phase 1
        for attr in attrs:
            if 'Audio' in type(attr).__name__ and getattr(attr, 'title', None):
                orig_fn = f"{attr.performer} - {attr.title}" if getattr(attr, 'performer', None) else f"{attr.title}"
                _, f_ext = os.path.splitext(getattr(attr, 'file_name', '') or '')
                if f_ext:
                    orig_fn += f_ext
                elif mime == 'audio/mpeg':
                    orig_fn += '.mp3'
                elif mime in ('audio/ogg', 'audio/x-m4a', 'audio/m4a'):
                    orig_fn += '.ogg' if 'ogg' in mime else '.m4a'
                break

        if not orig_fn:
            for attr in attrs:
                if isinstance(attr, DocumentAttributeFilename):
                    orig_fn = attr.file_name
                    break

        if not orig_fn and getattr(msg, 'file', None) and getattr(msg.file, 'name', None):
            orig_fn = msg.file.name

        ext = ".dat"
        if is_sticker or is_gif:
            if orig_fn and os.path.splitext(orig_fn)[1]:
                ext = os.path.splitext(orig_fn)[1]
            else:
                ext_map = {'application/x-tgsticker': '.tgs', 'image/webp': '.webp', 'video/webm': '.webm', 'image/gif': '.gif', 'video/mp4': '.mp4'}
                for k, v in ext_map.items():
                    if k in mime:
                        ext = v
                        break
            return f"{media_id}{ext}"
        else:
            if orig_fn:
                return f"{msg.id} - {sanitize_filename(orig_fn)}"
            ext_map = {'image/jpeg': '.jpg', 'image/png': '.png', 'video/mp4': '.mp4', 'audio/ogg': '.ogg', 'application/pdf': '.pdf', 'application/zip': '.zip'}
            for k, v in ext_map.items():
                if k in mime:
                    ext = v
                    break
            return f"{msg.id} - {media_id}{ext}"

    return f"{msg.id}.bin"


def are_chat_ids_equal(id1, id2):
    if id1 is None or id2 is None:
        return False
    s1 = str(id1).replace("-100", "").replace("-", "").strip()
    s2 = str(id2).replace("-100", "").replace("-", "").strip()
    return s1 == s2


def classify_telegram_entity_error(exc):
    """
    Classifies Telethon entity resolution exceptions into permanent ('deleted') or temporary ('inaccessible').
    Returns (category, reason) or (None, str(exc)).
    """
    if exc is None:
        return None, None
    try:
        from telethon.errors import (
            ChannelPrivateError, ChatAdminRequiredError,
            UserDeactivatedError, ChannelInvalidError,
            ChatIdInvalidError, UserBannedInChannelError
        )
        permanent_types = (UserDeactivatedError, ChannelInvalidError, ChatIdInvalidError)
        temporary_types = (ChannelPrivateError, ChatAdminRequiredError, UserBannedInChannelError)
        if isinstance(exc, permanent_types):
            return 'deleted', f"permanent: {type(exc).__name__}"
        if isinstance(exc, temporary_types):
            return 'inaccessible', f"temporary: {type(exc).__name__}"
    except ImportError:
        pass

    err_str = str(exc).lower()
    if any(k in err_str for k in ('deactivated', 'deleted', 'channel_invalid', 'peer_id_invalid', 'chat_id_invalid')):
        return 'deleted', f"permanent: {err_str[:40]}"
    if any(k in err_str for k in ('channel_private', 'admin_required', 'banned', 'forbidden', 'chat_admin_required')):
        return 'inaccessible', f"temporary: {err_str[:40]}"
    return None, str(exc)[:60]


def classify_media_download_error(exc):
    """
    Classifies media download exceptions or Telethon failures into:
    (code, display_reason, is_permanent)

    Distinguishes Telegram server internal timeouts, unhosted/purged files, expired references,
    rate limits, DC migration issues, and general network errors.
    """
    if exc is None:
        return 'unavailable', 'Unavailable on Telegram (purged or expired)', True

    if isinstance(exc, str):
        s_lower = exc.lower()
        if 'unavailable' in s_lower or 'purged' in s_lower or 'expired' in s_lower:
            return 'unavailable', 'Unavailable on Telegram (purged or expired)', True
        if 'timeout' in s_lower or 'unsuccessful' in s_lower:
            return 'telegram_timeout', 'Telegram Server Timeout (internal issue)', False
        return 'download_error', exc[:60], False

    # Check for specific Telethon / Python exception types
    try:
        from telethon import errors
        if isinstance(exc, (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError)):
            return 'file_ref_expired', 'File reference expired on Telegram', True
        if isinstance(exc, errors.FileMigrateError):
            new_dc = getattr(exc, 'new_dc', getattr(exc, 'dc', '?'))
            return 'dc_migrate_error', f'Telegram DC Migration Failed (DC {new_dc})', False
        if isinstance(exc, errors.FloodWaitError):
            secs = getattr(exc, 'seconds', 0)
            return 'rate_limited', f'Rate limited by Telegram ({secs}s)', False
        if isinstance(exc, (errors.ChannelPrivateError, errors.ChatAdminRequiredError, errors.UserBannedInChannelError)):
            return 'access_denied', 'Access denied to chat/channel on Telegram', True
        if isinstance(exc, errors.RPCError):
            return 'rpc_error', f'Telegram RPC Error ({type(exc).__name__})', False
    except ImportError:
        pass

    import asyncio
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return 'telegram_timeout', 'Telegram Server Timeout (internal issue)', False

    # Telethon raises ValueError("Request was unsuccessful X time(s)") when _call fails after retries
    # caused by TimeoutError or internal RPC issues on GetFileRequest
    if isinstance(exc, ValueError) and 'request was unsuccessful' in str(exc).lower():
        ctx = getattr(exc, '__context__', None) or getattr(exc, '__cause__', None)
        if ctx:
            ctx_str = str(ctx).lower()
            if 'timeout' in ctx_str:
                return 'telegram_timeout', 'Telegram Server Timeout (internal issue)', False
            if 'getfilerequest' in ctx_str:
                return 'telegram_timeout', 'Telegram Server Timeout (GetFileRequest)', False
        return 'telegram_timeout', 'Telegram Server Timeout (internal issue)', False

    err_str = str(exc).lower()
    if 'timeout while fetching data' in err_str or 'getfilerequest' in err_str:
        return 'telegram_timeout', 'Telegram Server Timeout (GetFileRequest)', False
    if 'request was unsuccessful' in err_str:
        return 'telegram_timeout', 'Telegram Server Timeout (internal issue)', False
    if any(k in err_str for k in ('reference expired', 'fileref')):
        return 'file_ref_expired', 'File reference expired on Telegram', True
    if 'flood' in err_str:
        return 'rate_limited', 'Rate limited by Telegram', False
    if 'connection' in err_str or 'reset' in err_str:
        return 'network_error', 'Connection lost during download', False

    clean_msg = str(exc).strip()
    if not clean_msg:
        clean_msg = type(exc).__name__
    return 'download_error', f'Download error: {clean_msg[:50]}', False


def is_media_skip_permanent(media_skipped):
    """
    Checks if a media_skipped string represents an unrecoverable state that should
    never be retried automatically in catchup sweeps.
    """
    if not media_skipped:
        return False
    s = str(media_skipped).lower()
    return any(k in s for k in ('unavailable', 'purged', 'expired', 'user_rule_excluded', 'access denied'))


def is_media_skip_download_failed(media_skipped):
    """
    Checks if a media_skipped string represents a download failure or server timeout.
    """
    if not media_skipped:
        return False
    s = str(media_skipped).lower()
    return (s.startswith('failed:') or
            s.startswith('download error') or
            'server timeout' in s or
            'migration error' in s or
            'rate limited' in s or
            'request was unsuccessful' in s)



async def get_entity_name(client, entity_peer, msg_id=None):
    if not entity_peer: return None, None
    if hasattr(entity_peer, 'title') and entity_peer.title:
        return entity_peer.title, getattr(entity_peer, 'id', None)
    if hasattr(entity_peer, 'first_name') and entity_peer.first_name:
        n = entity_peer.first_name
        if getattr(entity_peer, 'last_name', None): n += f" {entity_peer.last_name}"
        return n, getattr(entity_peer, 'id', None)
    try:
        entity = await client.get_entity(entity_peer)
        name = getattr(entity, 'title', '')
        if not name and hasattr(entity, 'first_name'):
            name = entity.first_name
            if entity.last_name: name += f" {entity.last_name}"
        return name or None, getattr(entity, 'id', None)
    except Exception:
        # 1. Advanced trick for channels/chats: try to fetch a specific message
        if (hasattr(entity_peer, 'channel_id') or hasattr(entity_peer, 'chat_id')) and msg_id:
            try:
                from telethon.tl.functions.messages import GetMessagesRequest
                from telethon.tl.types import InputMessageID
                res = await client(GetMessagesRequest(
                    peer=entity_peer,
                    id=[InputMessageID(id=msg_id)]
                ))
                if res and hasattr(res, 'chats') and res.chats:
                    entity = res.chats[0]
                    name = getattr(entity, 'title', getattr(entity, 'first_name', ''))
                    if name: return name, entity.id
            except Exception as e:
                logging.debug(f"[get_entity_name] GetMessages error: {e}")

        # 2. Try GetFullChannel (Forceful refresh)
        if hasattr(entity_peer, 'channel_id'):
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                res = await client(GetFullChannelRequest(channel=entity_peer))
                if res and res.chats:
                    entity = res.chats[0]
                    name = getattr(entity, 'title', '')
                    if name: return name, entity.id
            except Exception as e:
                logging.debug(f"[get_entity_name] GetFullChannel error: {e}")

        # 3. Try GetChannelsRequest (Direct ID lookup)
        if hasattr(entity_peer, 'channel_id'):
            try:
                from telethon.tl.functions.channels import GetChannelsRequest
                res = await client(GetChannelsRequest(id=[entity_peer]))
                if res and res.chats:
                    entity = res.chats[0]
                    name = getattr(entity, 'title', '')
                    if name: return name, entity.id
            except Exception as e:
                logging.debug(f"[get_entity_name] GetChannels error: {e}")

        # 4. Fallback for legacy chats
        if hasattr(entity_peer, 'chat_id'):
            try:
                from telethon.tl.functions.messages import GetFullChatRequest
                res = await client(GetFullChatRequest(chat_id=entity_peer.chat_id))
                if res and res.chats:
                    entity = res.chats[0]
                    name = getattr(entity, 'title', '')
                    if name: return name, entity.id
            except Exception as e:
                logging.debug(f"[get_entity_name] GetFullChat error: {e}")

        channel_id = getattr(entity_peer, 'channel_id', None)
        if channel_id: return f"Channel (ID: {channel_id})", channel_id
        chat_id = getattr(entity_peer, 'chat_id', None)
        if chat_id: return f"Group (ID: {chat_id})", chat_id
        user_id = getattr(entity_peer, 'user_id', None)
        if user_id: return f"User (ID: {user_id})", user_id
        return None, None

async def fetch_and_save_user(peer, client, cursor, users_data_cache, avatars_dir, fallback_name=None):
    """Загружает юзера/канал из Telegram, сохраняет его в БД и кэш."""
    if peer is None: return users_data_cache
    
    # Global avatars directory to prevent broken paths on chat renames
    global_avatars_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, "avatars_global")
    os.makedirs(global_avatars_dir, exist_ok=True)
    
    """Загружает юзера/канал из Telegram, сохраняет его в БД и кэш."""
    if peer is None: return users_data_cache
    
    # Global avatars directory to prevent broken paths on chat renames
    global_avatars_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, "avatars_global")
    os.makedirs(global_avatars_dir, exist_ok=True)
    
    user_id = None
    if isinstance(peer, int): user_id = peer
    elif hasattr(peer, 'user_id'): user_id = peer.user_id
    elif hasattr(peer, 'channel_id'): user_id = peer.channel_id
    elif hasattr(peer, 'chat_id'): user_id = peer.chat_id
    else: user_id = getattr(peer, 'id', None)
    
    if user_id is None: return users_data_cache
    
    if user_id in users_data_cache:
        existing_info = users_data_cache[user_id]
        fn_val = existing_info.get('first_name') or ''
        has_real_name = "[ID:" not in fn_val
        has_avatar = existing_info.get('avatar_path') is not None
        
        # If we have both a real name and an avatar, we can skip
        if has_real_name and has_avatar:
            return users_data_cache
        
        # If we have a real name but no avatar, and the file actually exists on disk, update cache and skip
        if has_real_name and not has_avatar:
            avatar_path_abs = os.path.join(global_avatars_dir, f"{user_id}.jpg")
            if os.path.exists(avatar_path_abs):
                existing_info['avatar_path'] = os.path.relpath(avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
                return users_data_cache

    try:
        # Resolve entity: if peer already has title or first_name, reuse it directly
        if hasattr(peer, 'title') or hasattr(peer, 'first_name'):
            user = peer
        else:
            user = await client.get_entity(peer)
        user_id = user.id
        avatar_path_rel = None
        avatar_path_abs = os.path.join(global_avatars_dir, f"{user_id}.jpg")
        
        try:
            if getattr(user, 'photo', None):
                photo_id = getattr(user.photo, 'photo_id', None)
                if photo_id:
                    p_abs = os.path.join(global_avatars_dir, f"{user_id}_{photo_id}.jpg")
                    if not os.path.exists(p_abs):
                        await client.download_profile_photo(user, file=p_abs)
                    if os.path.exists(p_abs):
                        avatar_path_rel = os.path.relpath(p_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
                        try:
                            import shutil
                            shutil.copy2(p_abs, avatar_path_abs)
                        except Exception:
                            pass
                if not avatar_path_rel:
                    await client.download_profile_photo(user, file=avatar_path_abs)
                    if os.path.exists(avatar_path_abs):
                        avatar_path_rel = os.path.relpath(avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
            elif os.path.exists(avatar_path_abs):
                avatar_path_rel = os.path.relpath(avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
        except Exception: 
            if os.path.exists(avatar_path_abs):
                 avatar_path_rel = os.path.relpath(avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')

        first_name = getattr(user, 'first_name', None) or getattr(user, 'title', None) or fallback_name or f"ID: {user_id}"
        last_name = getattr(user, 'last_name', None)
        username = getattr(user, 'username', None)
        is_bot = 1 if getattr(user, 'bot', False) else 0
        is_premium = 1 if getattr(user, 'premium', False) else 0
        is_restricted = 1 if getattr(user, 'restricted', False) else 0
        verified = 1 if getattr(user, 'verified', False) else 0
        is_scam = 1 if getattr(user, 'scam', False) else 0
        is_fake = 1 if getattr(user, 'fake', False) else 0
        raw_usernames = getattr(user, 'usernames', None)
        usernames_json_val = json.dumps([getattr(u, 'username', str(u)) for u in raw_usernames]) if raw_usernames else None
        lang_code = getattr(user, 'lang_code', None)

        # Custom emoji status extraction
        emoji_status = getattr(user, 'emoji_status', None)
        emoji_status_doc_id = None
        emoji_status_json_val = None
        if emoji_status:
            emoji_status_doc_id = getattr(emoji_status, 'document_id', None)
            try:
                emoji_status_json_val = json.dumps(dt_converter(emoji_status))
            except Exception:
                emoji_status_json_val = json.dumps({"_": type(emoji_status).__name__, "document_id": emoji_status_doc_id})

        user_info = {"first_name": first_name, "last_name": last_name, "username": username, "avatar_path": avatar_path_rel,
                     "is_bot": is_bot, "is_premium": is_premium, "is_restricted": is_restricted,
                     "verified": verified, "usernames_json": usernames_json_val, "lang_code": lang_code,
                     "is_scam": is_scam, "is_fake": is_fake,
                     "emoji_status_document_id": emoji_status_doc_id, "emoji_status_json": emoji_status_json_val}
        
        cursor.execute(
            """INSERT INTO tbl_users (
                user_id, first_name, last_name, username, avatar_path, is_bot,
                is_premium, is_restricted, verified, usernames_json, lang_code,
                is_scam, is_fake, emoji_status_document_id, emoji_status_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                username=excluded.username,
                avatar_path=COALESCE(excluded.avatar_path, tbl_users.avatar_path),
                is_bot=excluded.is_bot,
                is_premium=excluded.is_premium,
                is_restricted=excluded.is_restricted,
                verified=excluded.verified,
                usernames_json=COALESCE(excluded.usernames_json, tbl_users.usernames_json),
                lang_code=COALESCE(excluded.lang_code, tbl_users.lang_code),
                is_scam=excluded.is_scam,
                is_fake=excluded.is_fake,
                emoji_status_document_id=COALESCE(excluded.emoji_status_document_id, tbl_users.emoji_status_document_id),
                emoji_status_json=COALESCE(excluded.emoji_status_json, tbl_users.emoji_status_json)
            """,
            (user_id, first_name, last_name, username, avatar_path_rel, is_bot, is_premium, is_restricted, verified, usernames_json_val, lang_code, is_scam, is_fake, emoji_status_doc_id, emoji_status_json_val)
        )
        users_data_cache[user_id] = user_info
    except Exception:
        fname = fallback_name or f"[ID: {user_id}]"
        user_info = { "first_name": fname, "last_name": "[Unavailable]", "username": None, "avatar_path": None,
                     "is_bot": 0, "is_premium": 0, "is_restricted": 0,
                     "verified": 0, "usernames_json": None, "lang_code": None,
                     "is_scam": 0, "is_fake": 0,
                     "emoji_status_document_id": None, "emoji_status_json": None }
        cursor.execute(
            """INSERT INTO tbl_users (
                user_id, first_name, last_name, username, avatar_path, is_bot,
                is_premium, is_restricted, verified, usernames_json, lang_code,
                is_scam, is_fake
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name=COALESCE(tbl_users.first_name, excluded.first_name),
                last_name=COALESCE(tbl_users.last_name, excluded.last_name)
            """,
            (user_id, fname, "[Unavailable]", None, None, 0, 0, 0, 0, None, None, 0, 0)
        )
        users_data_cache[user_id] = user_info
    return users_data_cache


def normalize_id(peer_id):
    """Transforms short ID (e.g. from linked_chat_id) into full -100... format if needed."""
    try:
        n = int(peer_id)
        if 0 < n < 1000000000000:
            return -1000000000000 - n
        return n
    except (ValueError, TypeError):
        return peer_id


async def safe_fetch_full_channel(client, channel_entity, cursor, delay_seconds=0.0):
    """Safely fetches full profile for a channel or chat."""
    from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantsRequest
    from telethon.tl.functions.messages import GetFullChatRequest
    from telethon.tl.types import ChannelParticipantsAdmins, Channel
    from telethon.utils import get_peer_id

    chat_id = get_peer_id(channel_entity)
    chat_name = getattr(channel_entity, 'title', getattr(channel_entity, 'first_name', f"Channel_{chat_id}"))
    username = getattr(channel_entity, 'username', None)
    safe_folder = "".join(c for c in chat_name if c.isalnum() or c in (' ', '_')).rstrip()
    chat_folder_name = f"{safe_folder}_{chat_id}"

    global_avatars_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, "avatars_global")
    os.makedirs(global_avatars_dir, exist_ok=True)
    avatar_path_rel = None
    avatar_path_abs = os.path.join(global_avatars_dir, f"{chat_id}.jpg")
    try:
        if getattr(channel_entity, 'photo', None):
            photo_id = getattr(channel_entity.photo, 'photo_id', None)
            if photo_id:
                chan_photo_abs = os.path.join(global_avatars_dir, f"{chat_id}_{photo_id}.jpg")
                if not os.path.exists(chan_photo_abs):
                    await client.download_profile_photo(channel_entity, file=chan_photo_abs)
                if os.path.exists(chan_photo_abs):
                    avatar_path_rel = os.path.relpath(chan_photo_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
                    try:
                        import shutil
                        shutil.copy2(chan_photo_abs, avatar_path_abs)
                    except Exception:
                        pass
            if not avatar_path_rel:
                await client.download_profile_photo(channel_entity, file=avatar_path_abs)
                if os.path.exists(avatar_path_abs):
                    avatar_path_rel = os.path.relpath(avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
            chat_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, chat_folder_name)
            if os.path.exists(chat_dir) and avatar_path_rel:
                import shutil
                try:
                    shutil.copy2(os.path.join(ABS_MEDIA_BACKUP_DIR, avatar_path_rel), os.path.join(chat_dir, "chat_photo.jpg"))
                except Exception:
                    pass
        elif os.path.exists(avatar_path_abs):
            avatar_path_rel = os.path.relpath(avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
    except Exception:
        pass

    chat_about = None
    linked_chat_id = None
    admin_list_data = []
    chat_type = 'channel' if getattr(channel_entity, 'broadcast', False) else 'group'
    creation_date = dt_converter(getattr(channel_entity, 'date', None))

    if isinstance(channel_entity, Channel):
        try:
            full_res = await client(GetFullChannelRequest(channel=channel_entity))
            chat_about = getattr(full_res.full_chat, 'about', None)
            linked_chat_id = getattr(full_res.full_chat, 'linked_chat_id', None)
        except Exception:
            pass
        try:
            admin_res = await client(GetParticipantsRequest(
                channel=channel_entity, filter=ChannelParticipantsAdmins(), offset=0, limit=200, hash=0
            ))
            for p in admin_res.participants:
                admin_list_data.append({
                    "user_id": p.user_id,
                    "rank": getattr(p, 'rank', None),
                    "is_creator": 'Creator' in type(p).__name__
                })
        except Exception:
            pass
    else:
        try:
            full_res = await client(GetFullChatRequest(chat_id=getattr(channel_entity, 'id', chat_id)))
            chat_about = getattr(full_res.full_chat, 'about', None)
        except Exception:
            pass

    admin_list_json = json.dumps(admin_list_data) if admin_list_data else None

    # Upsert into tbl_chats
    cursor.execute("""
        INSERT INTO tbl_chats (chat_id, chat_name, safe_folder_name, chat_avatar_path, chat_about, chat_creation_date, linked_chat_id, admin_list_json, chat_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            chat_name=COALESCE(excluded.chat_name, tbl_chats.chat_name),
            chat_avatar_path=COALESCE(excluded.chat_avatar_path, tbl_chats.chat_avatar_path),
            chat_about=COALESCE(excluded.chat_about, tbl_chats.chat_about),
            linked_chat_id=COALESCE(excluded.linked_chat_id, tbl_chats.linked_chat_id),
            admin_list_json=COALESCE(excluded.admin_list_json, tbl_chats.admin_list_json),
            chat_type=COALESCE(excluded.chat_type, tbl_chats.chat_type)
    """, (chat_id, chat_name, chat_folder_name, avatar_path_rel, chat_about, creation_date, linked_chat_id, admin_list_json, chat_type))

    # Also upsert into tbl_users
    cursor.execute("""
        INSERT INTO tbl_users (user_id, first_name, last_name, username, avatar_path, rank, about)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name=excluded.first_name,
            username=COALESCE(excluded.username, tbl_users.username),
            avatar_path=COALESCE(excluded.avatar_path, tbl_users.avatar_path),
            about=COALESCE(excluded.about, tbl_users.about)
    """, (chat_id, chat_name, '', username, avatar_path_rel, chat_type, chat_about))

    return {
        "ok": True,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "chat_about": chat_about,
        "chat_avatar_path": avatar_path_rel,
        "chat_type": chat_type
    }


import time

_PROFILE_FETCH_LOCK = asyncio.Lock()
_LAST_PROFILE_FETCH_TIMESTAMP = 0.0

async def safe_fetch_full_user(client, user_id, cursor, delay_seconds=10.0):
    """
    Safely fetches full profile for a single user with strict global rate limiting,
    FIFO serialization, jitter, and flood protection.
    UNDER NO CIRCUMSTANCE will Telegram profile calls execute within < 10 seconds of each other.
    """
    global _LAST_PROFILE_FETCH_TIMESTAMP
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.tl.types import Channel, Chat
    from telethon.errors import FloodWaitError
    
    async with _PROFILE_FETCH_LOCK:
        now = time.time()
        elapsed = now - _LAST_PROFILE_FETCH_TIMESTAMP
        min_required_gap = max(10.0, delay_seconds) + random.uniform(0.5, 2.0)
        if elapsed < min_required_gap:
            sleep_needed = min_required_gap - elapsed
            logging.info(f"[ProfileScheduler] Enforcing cooldown for user {user_id}: sleeping {sleep_needed:.2f}s")
            await asyncio.sleep(sleep_needed)
            
        _LAST_PROFILE_FETCH_TIMESTAMP = time.time()
        
        try:
            user_entity = await client.get_entity(user_id)
            if isinstance(user_entity, (Channel, Chat)):
                return await safe_fetch_full_channel(client, user_entity, cursor, delay_seconds=0.0)

            full_res = await client(GetFullUserRequest(id=user_entity))
            full_user = getattr(full_res, 'full_user', None)
            if not full_user:
                return {"error": "No full_user data returned", "user_id": user_id}

            global_avatars_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, "avatars_global")
            os.makedirs(global_avatars_dir, exist_ok=True)
            primary_avatar_path_abs = os.path.join(global_avatars_dir, f"{user_id}.jpg")
            primary_avatar_rel = None
            try:
                if getattr(user_entity, 'photo', None):
                    await client.download_profile_photo(user_entity, file=primary_avatar_path_abs)
                if os.path.exists(primary_avatar_path_abs):
                    primary_avatar_rel = os.path.relpath(primary_avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
            except Exception as e_av:
                logging.debug(f"[safe_fetch_full_user] Primary avatar error: {e_av}")
                if os.path.exists(primary_avatar_path_abs):
                    primary_avatar_rel = os.path.relpath(primary_avatar_path_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')

            about = getattr(full_user, 'about', None)
            
            # Birthday
            birthday = getattr(full_user, 'birthday', None)
            birthday_json = None
            if birthday:
                b_dict = {
                    'day': getattr(birthday, 'day', None),
                    'month': getattr(birthday, 'month', None),
                    'year': getattr(birthday, 'year', None)
                }
                birthday_json = json.dumps(b_dict)
                
            # Business info
            business_json = None
            b_work_hours = getattr(full_user, 'business_work_hours', None)
            b_location = getattr(full_user, 'business_location', None)
            b_intro = getattr(full_user, 'business_intro', None)
            if b_work_hours or b_location or b_intro:
                b_dict = {}
                if b_work_hours:
                    b_dict['timezone_id'] = getattr(b_work_hours, 'timezone_id', None)
                    weekly_intervals = []
                    for interval in (getattr(b_work_hours, 'weekly_open', None) or []):
                        weekly_intervals.append({
                            'start_minute': getattr(interval, 'start_minute', None),
                            'end_minute': getattr(interval, 'end_minute', None)
                        })
                    b_dict['weekly_open'] = weekly_intervals
                if b_location:
                    b_dict['address'] = getattr(b_location, 'address', None)
                    geo = getattr(b_location, 'geo_point', None)
                    if geo:
                        b_dict['geo'] = {
                            'lat': getattr(geo, 'lat', None),
                            'long': getattr(geo, 'long', None)
                        }
                if b_intro:
                    b_dict['intro_title'] = getattr(b_intro, 'title', None)
                    b_dict['intro_description'] = getattr(b_intro, 'description', None)
                    # Download intro sticker if present
                    sticker = getattr(b_intro, 'sticker', None)
                    if sticker:
                        business_sticker_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, "business_stickers")
                        os.makedirs(business_sticker_dir, exist_ok=True)
                        target_sticker_path = os.path.join(business_sticker_dir, f"intro_{user_id}.webp")
                        if not os.path.exists(target_sticker_path):
                            try:
                                await client.download_media(sticker, file=target_sticker_path)
                            except Exception:
                                pass
                        if os.path.exists(target_sticker_path):
                            b_dict['intro_sticker_path'] = os.path.relpath(target_sticker_path, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
                business_json = json.dumps(b_dict)
                
            # Phone number
            # Phone number: deliberately not storing other users' phone numbers (privacy)
            phone = None

            # Profile photos history
            profile_photos_paths = []
            try:
                async for photo in client.iter_profile_photos(user_entity, limit=10):
                    photo_id = getattr(photo, 'id', None)
                    if photo_id:
                        p_filename = f"{user_id}_{photo_id}.jpg"
                        p_abs = os.path.join(global_avatars_dir, p_filename)
                        if not os.path.exists(p_abs):
                            try:
                                await client.download_media(photo, file=p_abs)
                            except Exception:
                                pass
                        if os.path.exists(p_abs):
                            profile_photos_paths.append(os.path.relpath(p_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/'))
            except Exception as e_p:
                logging.debug(f"[safe_fetch_full_user] Profile photos error: {e_p}")
            profile_photos_json = json.dumps(profile_photos_paths) if profile_photos_paths else None
            if profile_photos_paths:
                primary_avatar_rel = profile_photos_paths[0]
                try:
                    import shutil
                    latest_abs = os.path.join(ABS_MEDIA_BACKUP_DIR, primary_avatar_rel)
                    if os.path.exists(latest_abs):
                        shutil.copy2(latest_abs, primary_avatar_path_abs)
                except Exception as e_cp:
                    logging.debug(f"[safe_fetch_full_user] copy avatar error: {e_cp}")
            elif not getattr(user_entity, 'photo', None):
                primary_avatar_rel = None

            # User stories
            stories_list = []
            try:
                from telethon.tl.functions.stories import GetPeerStoriesRequest
                stories_res = await client(GetPeerStoriesRequest(peer=user_entity))
                stories_dir = os.path.join(ABS_MEDIA_BACKUP_DIR, "stories")
                os.makedirs(stories_dir, exist_ok=True)
                peer_stories = getattr(stories_res, 'stories', None)
                st_items = getattr(peer_stories, 'stories', []) if peer_stories else []
                for st in st_items:
                    st_id = getattr(st, 'id', None)
                    st_date = getattr(st, 'date', None)
                    st_media_rel = None
                    if getattr(st, 'media', None):
                        ext = ".mp4" if type(st.media).__name__ in ('MessageMediaDocument',) else ".jpg"
                        st_filename = f"story_{user_id}_{st_id}{ext}"
                        st_abs = os.path.join(stories_dir, st_filename)
                        if not os.path.exists(st_abs):
                            try:
                                await client.download_media(st.media, file=st_abs)
                            except Exception:
                                pass
                        if os.path.exists(st_abs):
                            st_media_rel = os.path.relpath(st_abs, ABS_MEDIA_BACKUP_DIR).replace('\\', '/')
                    stories_list.append({
                        'id': st_id,
                        'date': st_date.isoformat() if st_date else None,
                        'caption': getattr(st, 'caption', None),
                        'media_path': st_media_rel,
                        'is_expired': getattr(st, 'expired', False)
                    })
            except Exception as e_st:
                logging.debug(f"[safe_fetch_full_user] Stories fetch error: {e_st}")
            stories_json = json.dumps(stories_list) if stories_list else None

            # Saved Star Gifts
            gifts_json = None
            try:
                from telethon.tl.functions.payments import GetUserStarGiftsRequest
                gifts_res = await client(GetUserStarGiftsRequest(user_id=user_entity, offset='', limit=50))
                gifts_list = []
                for g in (getattr(gifts_res, 'gifts', None) or []):
                    gift_obj = getattr(g, 'gift', None)
                    g_dict = {
                        'date': getattr(g, 'date', None).isoformat() if getattr(g, 'date', None) else None,
                        'stars': getattr(gift_obj, 'stars', None) if gift_obj else None,
                        'title': getattr(gift_obj, 'title', None) or getattr(g, 'name', None),
                        'message': getattr(g, 'message', None),
                        'name': getattr(g, 'name', None),
                        'unsaved': getattr(g, 'unsaved', False)
                    }
                    gifts_list.append(g_dict)
                if gifts_list:
                    gifts_json = json.dumps(gifts_list)
            except Exception as e:
                logging.debug(f"[safe_fetch_full_user] Gifts fetch error: {e}")

            # Personal Channel
            personal_channel_id = getattr(full_user, 'personal_channel_id', None)
            personal_channel_title = None
            if personal_channel_id:
                try:
                    ch_ent = await client.get_entity(personal_channel_id)
                    personal_channel_title = getattr(ch_ent, 'title', None)
                except Exception:
                    personal_channel_title = f"Channel {personal_channel_id}"

            now_iso = datetime.now().isoformat()
            full_profile_dict = {
                'about': about,
                'birthday': json.loads(birthday_json) if birthday_json else None,
                'business': json.loads(business_json) if business_json else None,
                'common_chats_count': getattr(full_user, 'common_chats_count', 0),
                'pinned_msg_id': getattr(full_user, 'pinned_msg_id', None),
                'personal_channel_id': personal_channel_id,
                'personal_channel_title': personal_channel_title
            }
            full_profile_json = json.dumps(full_profile_dict)
            
            # Download any custom emojis in user's bio (about_entities)
            about_entities = getattr(full_user, 'about_entities', None) or []
            bio_custom_emoji_ids = []
            for ent in about_entities:
                if type(ent).__name__ == 'MessageEntityCustomEmoji' and hasattr(ent, 'document_id'):
                    bio_custom_emoji_ids.append(ent.document_id)
            if bio_custom_emoji_ids:
                try:
                    from core.emoji import async_fetch_and_save_custom_emojis
                    conn = cursor.connection
                    await async_fetch_and_save_custom_emojis(client, bio_custom_emoji_ids, conn)
                except Exception as e:
                    logging.debug(f"[safe_fetch_full_user] bio emoji fetch error: {e}")
                    
            u_is_premium = 1 if getattr(user_entity, 'premium', False) else 0
            u_verified = 1 if getattr(user_entity, 'verified', False) else 0
            u_status = getattr(user_entity, 'emoji_status', None)
            u_status_id = getattr(u_status, 'document_id', None) if u_status else None
            u_status_json = json.dumps(u_status.to_dict()) if hasattr(u_status, 'to_dict') else None
            u_usernames = getattr(user_entity, 'usernames', None)
            u_usernames_json = json.dumps([{'username': u.username, 'active': getattr(u, 'active', True)} for u in u_usernames]) if u_usernames else None

            has_no_photo = 1 if (not profile_photos_paths and not getattr(user_entity, 'photo', None)) else 0
            cursor.execute("""
                UPDATE tbl_users SET
                    avatar_path = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, avatar_path) END,
                    about = ?,
                    birthday_json = ?,
                    business_json = ?,
                    full_profile_json = ?,
                    full_profile_updated_at = ?,
                    gifts_json = COALESCE(?, gifts_json),
                    phone = COALESCE(?, phone),
                    profile_photos_json = COALESCE(?, profile_photos_json),
                    stories_json = COALESCE(?, stories_json),
                    personal_channel_id = COALESCE(?, personal_channel_id),
                    personal_channel_title = COALESCE(?, personal_channel_title),
                    is_premium = CASE WHEN ? = 1 THEN 1 ELSE is_premium END,
                    verified = CASE WHEN ? = 1 THEN 1 ELSE verified END,
                    emoji_status_document_id = COALESCE(?, emoji_status_document_id),
                    emoji_status_json = COALESCE(?, emoji_status_json),
                    usernames_json = COALESCE(?, usernames_json)
                WHERE user_id = ?
            """, (
                has_no_photo, primary_avatar_rel,
                about, birthday_json, business_json, full_profile_json, now_iso,
                gifts_json, phone, profile_photos_json, stories_json,
                personal_channel_id, personal_channel_title,
                u_is_premium, u_verified, u_status_id, u_status_json, u_usernames_json,
                user_id
            ))
            
            return {
                "ok": True,
                "user_id": user_id,
                "avatar_path": primary_avatar_rel,
                "about": about,
                "has_birthday": bool(birthday_json),
                "has_business": bool(business_json),
                "has_stories": bool(stories_json),
                "photos_count": len(profile_photos_paths),
                "updated_at": now_iso
            }
        except FloodWaitError as e:
            logging.warning(f"[safe_fetch_full_user] FloodWaitError: must wait {e.seconds}s for user {user_id}")
            return {"error": f"FloodWaitError: wait {e.seconds} seconds", "flood_wait": e.seconds, "user_id": user_id}
        except Exception as e:
            logging.error(f"[safe_fetch_full_user] Error fetching user {user_id}: {e}")
            return {"error": str(e), "user_id": user_id}


def normalize_media_settings(settings):
    """
    Ensures settings contains properly structured sub-dicts for media categories:
    photos, videos, voice, video_notes, documents.
    Accepts both flat flags (download_photos) and nested dicts (photos: {enabled: ...}).
    """
    if not settings:
        settings = {}
    normalized = dict(settings)
    
    mapping = {
        'photos': ['download_photos', 'photo'],
        'videos': ['download_videos', 'video'],
        'voice': ['download_voices', 'voices', 'audio'],
        'video_notes': ['download_video_notes', 'video_note'],
        'documents': ['download_files', 'download_documents', 'files', 'document']
    }
    for cat, aliases in mapping.items():
        curr = normalized.get(cat)
        if isinstance(curr, dict):
            if 'enabled' not in curr:
                curr['enabled'] = True
            if 'max_mb' not in curr:
                curr['max_mb'] = 0
        else:
            enabled = True
            for alias in aliases:
                if alias in normalized:
                    enabled = bool(normalized[alias])
                    break
            normalized[cat] = {'enabled': enabled, 'max_mb': 0}
            
    return normalized


async def download_media_robust(client, msg, target_file, chat_entity=None, progress_callback=None):
    """
    Robustly downloads a message media attachment with multi-DC migration recovery.
    Handles:
    - FileMigrateError & ValueError('Request was unsuccessful') by bypassing home DC and targeting media dc_id directly.
    - Stale/hung borrowed MTProto senders in client._borrowed_senders.
    - Expired/invalid file references by refetching message via chat_entity.
    """
    import sys
    core_mod = sys.modules.get('core')
    if core_mod and getattr(core_mod, 'GLOBAL_ABORT', False):
        raise asyncio.CancelledError("Aborted by user")

    if not msg or not getattr(msg, 'media', None):
        return None

    # Attempt 1: Standard high-level download_media
    try:
        dl_path = await msg.download_media(file=target_file, progress_callback=progress_callback)
        if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path) > 0:
            return dl_path
    except asyncio.CancelledError:
        raise
    except Exception as first_err:
        if core_mod and getattr(core_mod, 'GLOBAL_ABORT', False):
            raise asyncio.CancelledError("Aborted by user")
        logging.info(f"[download_media_robust] Primary download_media failed for msg {getattr(msg, 'id', '?')}: {first_err}")

    if core_mod and getattr(core_mod, 'GLOBAL_ABORT', False):
        raise asyncio.CancelledError("Aborted by user")

    # Attempt 2: Direct location-based download targeting media DC explicitly
    dc_id = None
    try:
        from telethon.tl import types
        from telethon import errors

        photo = getattr(msg, 'photo', None)
        doc = getattr(msg, 'document', None)
        loc = None
        file_size = 0

        if photo and isinstance(photo, types.Photo):
            dc_id = getattr(photo, 'dc_id', None)
            sizes = (getattr(photo, 'sizes', []) or []) + (getattr(photo, 'video_sizes', []) or [])
            size = client._get_thumb(sizes, None) if hasattr(client, '_get_thumb') else (sizes[-1] if sizes else None)
            if size and not isinstance(size, types.PhotoSizeEmpty):
                thumb_type = getattr(size, 'type', '')
                loc = types.InputPhotoFileLocation(
                    id=photo.id,
                    access_hash=photo.access_hash,
                    file_reference=photo.file_reference,
                    thumb_size=thumb_type
                )
                if isinstance(size, types.PhotoSizeProgressive):
                    file_size = max(size.sizes)
                else:
                    file_size = getattr(size, 'size', getattr(size, 'file_size', 0))

        elif doc and isinstance(doc, types.Document):
            dc_id = getattr(doc, 'dc_id', None)
            loc = types.InputDocumentFileLocation(
                id=doc.id,
                access_hash=doc.access_hash,
                file_reference=doc.file_reference,
                thumb_size=''
            )
            file_size = getattr(doc, 'size', 0)

        if loc and dc_id:
            # Only purge borrowed sender if it is actually disconnected (safe for parallel workers)
            if hasattr(client, '_borrowed_senders') and dc_id in client._borrowed_senders:
                try:
                    _, sender = client._borrowed_senders[dc_id]
                    if not sender or not sender.is_connected():
                        client._borrowed_senders.pop(dc_id, None)
                except Exception:
                    pass

            dl_res = await client.download_file(
                loc,
                file=target_file,
                file_size=file_size,
                dc_id=dc_id,
                progress_callback=progress_callback
            )
            if target_file and os.path.exists(target_file) and os.path.getsize(target_file) > 0:
                return target_file
            elif dl_res and os.path.exists(str(dl_res)):
                return str(dl_res)

    except asyncio.CancelledError:
        raise
    except Exception as dc_err:
        if core_mod and getattr(core_mod, 'GLOBAL_ABORT', False):
            raise asyncio.CancelledError("Aborted by user")
        from telethon import errors
        if isinstance(dc_err, (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError)) and chat_entity:
            try:
                fresh_msg = await client.get_messages(chat_entity, ids=msg.id)
                if fresh_msg and getattr(fresh_msg, 'media', None):
                    dl_path = await fresh_msg.download_media(file=target_file, progress_callback=progress_callback)
                    if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path) > 0:
                        return dl_path
            except Exception:
                pass
        logging.info(f"[download_media_robust] Direct DC {dc_id} download failed for msg {getattr(msg, 'id', '?')}: {dc_err}")
        is_timeout_val_err = isinstance(dc_err, ValueError) and 'request was unsuccessful' in str(dc_err).lower()
        if chat_entity and not is_timeout_val_err and not isinstance(dc_err, (TimeoutError, asyncio.TimeoutError)):
            try:
                fresh_msg = await client.get_messages(chat_entity, ids=msg.id)
                if fresh_msg and getattr(fresh_msg, 'media', None):
                    dl_path = await fresh_msg.download_media(file=target_file, progress_callback=progress_callback)
                    if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path) > 0:
                        return dl_path
            except Exception:
                pass
        raise dc_err

    return None




def unpack_telegram_waveform(raw_bytes: bytes) -> list[int]:
    """Unpacks 5-bit encoded Telegram voice waveform into an integer list [0..31]."""
    if not raw_bytes or not isinstance(raw_bytes, (bytes, bytearray)):
        return []
    bit_count = len(raw_bytes) * 8
    values_count = bit_count // 5
    values = []
    for i in range(values_count):
        byte_idx = (i * 5) // 8
        bit_offset = (i * 5) % 8
        val = (raw_bytes[byte_idx] >> bit_offset) & 0x1F
        if bit_offset > 3 and byte_idx + 1 < len(raw_bytes):
            val |= (raw_bytes[byte_idx + 1] << (8 - bit_offset)) & 0x1F
        values.append(int(val))
    return values


def generate_audio_waveform(file_path: str, num_samples: int = 100) -> list[int]:
    """Extracts or computes audio waveform amplitudes [0..31] from audio file using PyAV if available."""
    if not file_path or not os.path.exists(file_path):
        return []
    try:
        import av
        import struct
        import math

        container = av.open(file_path)
        audio_stream = next((s for s in container.streams if s.type == 'audio'), None)
        if not audio_stream:
            return []

        resampler = av.AudioResampler(format='s16', layout='mono', rate=8000)
        samples = []
        for frame in container.decode(audio_stream):
            resampled = resampler.resample(frame)
            if resampled:
                frames_list = resampled if isinstance(resampled, list) else [resampled]
                for f in frames_list:
                    raw = f.planes[0].to_bytes()
                    count = len(raw) // 2
                    if count > 0:
                        pcm = struct.unpack(f"<{count}h", raw)
                        samples.extend(pcm)

        container.close()
        if not samples:
            return []

        chunk_size = max(1, len(samples) // num_samples)
        waveform = []
        for i in range(0, len(samples), chunk_size):
            chunk = samples[i:i + chunk_size]
            if not chunk:
                continue
            rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
            val = min(31, int((rms / 32767.0) * 31 * 2))
            waveform.append(val)
        return waveform[:num_samples]
    except Exception:
        return []
