#!/usr/bin/env python3
"""
core/live_media.py — Live incoming media classifier, size/type filter, and downloader.
"""
import os
import json
import logging
import asyncio
from datetime import datetime
from telethon import types
from telethon.tl.types import (
    User, DocumentAttributeFilename, DocumentAttributeAudio, DocumentAttributeVideo,
    DocumentAttributeAnimated, DocumentAttributeSticker
)
from . import db
from . import helpers
from .db import get_db_conn
from .helpers import (
    sanitize_filename, dt_converter, get_media_target_dir, ensure_chat_media_subdirs
)
import core

async def process_live_media(msg, client, chat_id, safe_folder_name, settings):

    """

    Classifies media, inspects per-chat or global archive limits,

    downloads thumbnail/media if enabled, and returns complete dictionary of media fields.

    """

    settings = helpers.normalize_media_settings(settings or {})

    chat_media_dir = os.path.join(core.MEDIA_BACKUP_DIR, safe_folder_name)

    stickers_dir = os.path.join(chat_media_dir, "stickers")

    gifs_dir = os.path.join(chat_media_dir, "gifs")

    other_dir = os.path.join(chat_media_dir, "other")

    ensure_chat_media_subdirs(chat_media_dir)



    result = {

        'media_type': None,

        'media_mime_type': None,

        'media_original_name': None,

        'media_skipped': None,

        'media_local_path': None,

        'thumbnail_local_path': None,

        'poll_data': None,

        'media_other_data': None,

        'is_voice': 0,

        'is_round_video': 0,

        'video_cover_path': None,

        'video_timestamp': None,

        'duration': 0,

        'waveform_data': None,

    }



    if getattr(msg, 'poll', None):

        result['media_type'] = 'poll'

        try:

            poll = msg.poll.poll

            results = msg.poll.results

            poll_data = {

                "question": getattr(getattr(poll, 'question', None), 'text', 'Опрос'),

                "is_closed": getattr(poll, 'closed', False),

                "is_quiz": getattr(poll, 'quiz', False),

                "public_voters": getattr(poll, 'public_voters', False),

                "total_voters": getattr(results, 'total_voters', None),

                "options": []

            }

            results_map = {}

            if results and getattr(results, 'results', None):

                for res in results.results:

                    if res and getattr(res, 'option', None):

                        try:

                            opt_key = res.option.decode('utf-8') if isinstance(res.option, bytes) else str(res.option)

                            results_map[opt_key] = getattr(res, 'voters', 0)

                        except Exception:

                            pass

            if poll and getattr(poll, 'answers', None):

                for answer in poll.answers:

                    if answer:

                        try:

                            opt_key = answer.option.decode('utf-8') if isinstance(answer.option, bytes) else str(answer.option)

                            ans_text = getattr(answer.text, 'text', str(answer.text)) if getattr(answer, 'text', None) else ''

                            poll_data["options"].append({"text": ans_text, "voters": results_map.get(opt_key, 0)})

                        except Exception:

                            pass

            result['poll_data'] = poll_data

        except Exception as e_poll:

            logging.debug(f"[LiveMedia] Error parsing poll: {e_poll}")

        return result



    if getattr(msg, 'document', None):

        doc = msg.document

        media_id = doc.id

        mime_type = doc.mime_type or 'application/octet-stream'

        file_size = doc.size or 0

        file_size_mb = (file_size / (1024 * 1024)) if file_size else 0

        result['media_mime_type'] = mime_type

        attrs = doc.attributes or []

        is_sticker = (mime_type == 'application/x-tgsticker') or any('Sticker' in type(a).__name__ for a in attrs)

        is_gif = (mime_type == 'image/gif') or any('Animated' in type(a).__name__ for a in attrs)

        is_voice = 0

        is_round_video = 0

        original_filename = None



        for attr in attrs:

            if 'DocumentAttributeAnimated' in str(type(attr)):

                is_gif = True

            elif 'DocumentAttributeAudio' in str(type(attr)) or isinstance(attr, DocumentAttributeAudio):

                if getattr(attr, 'voice', False):

                    is_voice = 1

                if attr.title:

                    original_filename = f"{attr.title}"

                    if getattr(attr, 'performer', None):

                        original_filename = f"{attr.performer} - {original_filename}"

                    _, file_ext = os.path.splitext(getattr(attr, 'file_name', ''))

                    if file_ext: original_filename += file_ext

                    elif mime_type == 'audio/mpeg': original_filename += '.mp3'

                    elif mime_type in ('audio/ogg', 'audio/x-m4a', 'audio/m4a'):

                        original_filename += '.ogg' if 'ogg' in mime_type else '.m4a'

            elif 'DocumentAttributeVideo' in str(type(attr)) or isinstance(attr, DocumentAttributeVideo):

                if getattr(attr, 'round_message', False):

                    is_round_video = 1

                if getattr(attr, 'duration', None):

                    result['duration'] = int(attr.duration)

            elif isinstance(attr, DocumentAttributeFilename):

                original_filename = attr.file_name



        result['is_voice'] = is_voice

        result['is_round_video'] = is_round_video



        # Routing target directory

        target_dir = get_media_target_dir(

            chat_media_dir,

            mime_type=mime_type,

            attributes=doc.attributes,

            filename=original_filename,

            is_voice=is_voice,

            is_round_video=is_round_video

        )



        filename_ext = ".dat"

        if is_sticker or is_gif:

            if original_filename:

                _, file_ext = os.path.splitext(original_filename)

                if file_ext: filename_ext = file_ext

            else:

                ext_map = {'application/x-tgsticker': '.tgs', 'image/webp': '.webp', 'video/webm': '.webm', 'image/gif': '.gif', 'video/mp4': '.mp4'}

                for key, ext in ext_map.items():

                    if key in mime_type: filename_ext = ext; break

            unique_filename = f"{media_id}{filename_ext}"

        else:

            if original_filename:

                unique_filename = f"{msg.id} - {sanitize_filename(original_filename)}"

            else:

                ext_map = {'image/jpeg': '.jpg', 'image/png': '.png', 'video/mp4': '.mp4', 'audio/ogg': '.ogg', 'application/pdf': '.pdf', 'application/zip': '.zip'}

                for key, ext in ext_map.items():

                    if key in mime_type: filename_ext = ext; break

                unique_filename = f"{msg.id} - {media_id}{filename_ext}"



        result['media_original_name'] = unique_filename

        file_path = os.path.join(target_dir, unique_filename)



        # Media type discriminator

        if is_voice:

            result['media_type'] = 'voice'

            cfg, type_name = (settings.get('voice') or settings.get('voices', {})), "голосовых"

        elif is_round_video:

            result['media_type'] = 'video_note'

            cfg, type_name = (settings.get('video_notes') or settings.get('videos', {})), "видео-сообщений"

        elif is_sticker:

            result['media_type'] = 'sticker'

            cfg, type_name = (settings.get('photos') or settings.get('photo', {})), "стикеров"

        elif is_gif:

            result['media_type'] = 'gif'

            cfg, type_name = (settings.get('videos') or settings.get('video', {})), "GIF"

        elif mime_type.startswith('video/'):

            result['media_type'] = 'video'

            cfg, type_name = (settings.get('videos') or settings.get('video', {})), "видео"

        elif mime_type.startswith('audio/'):

            result['media_type'] = 'audio'

            cfg, type_name = (settings.get('voice') or settings.get('voices') or settings.get('audio', {})), "аудио"

        elif mime_type.startswith('image/'):

            result['media_type'] = 'photo'

            cfg, type_name = (settings.get('photos') or settings.get('photo', {})), "фото"

        else:

            result['media_type'] = 'document'

            cfg, type_name = (settings.get('documents') or settings.get('files', {})), "файлов"



        # Apply settings filter

        if cfg is not None:

            if not cfg.get('enabled', True):

                result['media_skipped'] = f"Загрузка {type_name} отключена"

            elif cfg.get('max_mb', 0) > 0 and file_size_mb > cfg.get('max_mb'):

                result['media_skipped'] = f"Больше {cfg.get('max_mb')} МБ"

        if not result['media_skipped'] and core.MAX_SIZE_BYTES > 0 and file_size > core.MAX_SIZE_BYTES:

            result['media_skipped'] = f"Файл превышает лимит {core.MAX_FILE_SIZE_MB} МБ"



        # Apply user/bot exclusion rules

        if not result['media_skipped'] and settings.get('user_rules'):

            u_rules = settings['user_rules']

            s_id = getattr(msg, 'sender_id', None)

            v_id = getattr(msg, 'via_bot_id', None)

            f_id = helpers.get_message_forward_sender_id(msg)

            r_key = (

                str(s_id) if s_id and str(s_id) in u_rules

                else (str(v_id) if v_id and str(v_id) in u_rules

                else (str(f_id) if f_id and str(f_id) in u_rules else None))

            )

            if r_key:

                u_rule = u_rules[r_key]

                r_name = u_rule.get('user_name') or u_rule.get('name') or f"User/Bot {r_key}"

                r_mode = u_rule.get('mode', 'exclude_all')

                if r_mode == 'exclude_all':

                    result['media_skipped'] = f"Исключено правилом для {r_name} [user_rule_excluded:{r_key}]"

                elif r_mode == 'custom':

                    u_cat_key = 'documents'

                    if result['media_type'] in ('photo', 'sticker'): u_cat_key = 'photos'

                    elif result['media_type'] in ('video', 'gif'): u_cat_key = 'videos'

                    elif result['media_type'] in ('voice', 'audio'): u_cat_key = 'voice'

                    elif result['media_type'] == 'video_note': u_cat_key = 'video_notes'

                    u_cfg = u_rule.get(u_cat_key) or {}

                    if not u_cfg.get('enabled', True):

                        result['media_skipped'] = f"Исключено ({u_cat_key}) для {r_name} [user_rule_excluded:{r_key}]"

                    elif u_cfg.get('max_mb', 0) > 0 and file_size_mb > u_cfg.get('max_mb'):

                        result['media_skipped'] = f"Больше {u_cfg.get('max_mb')} МБ ({r_name}) [user_rule_excluded:{r_key}]"



        # Download thumbnail / video cover (always try if available)

        if getattr(doc, 'thumbs', None):

            try:

                thumb = doc.thumbs[-1]

                thumb_filename = f"{media_id}.thumb.jpg" if is_gif else f"{os.path.splitext(unique_filename)[0]}.thumb.jpg"

                thumb_file_path = os.path.join(helpers.get_thumbs_target_dir(target_dir), thumb_filename)

                if not os.path.exists(thumb_file_path):

                    raw_thumb = await msg.download_media(thumb=thumb, file=thumb_file_path)

                    if raw_thumb and os.path.exists(raw_thumb):

                        result['thumbnail_local_path'] = os.path.relpath(raw_thumb, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                else:

                    result['thumbnail_local_path'] = os.path.relpath(thumb_file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            except Exception as e_th:

                logging.debug(f"[LiveMedia] Thumb download error: {e_th}")



        if hasattr(msg.media, 'video_cover') and msg.media.video_cover:

            try:

                vc_filename = f"{msg.id}_video_cover.jpg"

                vc_path = os.path.join(helpers.get_thumbs_target_dir(target_dir), vc_filename)

                if not os.path.exists(vc_path):

                    await client.download_media(msg.media.video_cover, file=vc_path)

                if os.path.exists(vc_path):

                    result['video_cover_path'] = os.path.relpath(vc_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            except Exception as e_vc:

                logging.debug(f"[LiveMedia] Video cover download error: {e_vc}")



        if hasattr(msg.media, 'video_timestamp'):

            result['video_timestamp'] = getattr(msg.media, 'video_timestamp', None)



        # Download media file if not skipped

        if not result['media_skipped']:

            legacy_file = os.path.join(chat_media_dir, unique_filename)

            if is_sticker:

                shared_stk = os.path.join(helpers.get_shared_stickers_dir(), unique_filename)

                if os.path.exists(shared_stk):

                    result['media_local_path'] = os.path.relpath(shared_stk, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                elif os.path.exists(file_path):

                    helpers.try_create_hardlink(file_path, shared_stk)

                    result['media_local_path'] = os.path.relpath(shared_stk, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                else:

                    try:

                        raw_path = await asyncio.wait_for(msg.download_media(file=shared_stk), timeout=120.0)

                        if raw_path and os.path.exists(raw_path):

                            result['media_local_path'] = os.path.relpath(raw_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                        else:

                            result['media_skipped'] = "Unavailable on Telegram (purged or expired)"

                    except Exception as e_dl:

                        logging.warning(f"[LiveMedia] Sticker download failed for msg {msg.id}: {e_dl}")

                        _c, _lbl, _p = helpers.classify_media_download_error(e_dl)

                        result['media_skipped'] = f"Unavailable on Telegram ({_lbl})" if _p else f"Failed: {_lbl}"

            elif os.path.exists(file_path):

                result['media_local_path'] = os.path.relpath(file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            elif os.path.exists(legacy_file):

                result['media_local_path'] = os.path.relpath(legacy_file, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            else:

                db_conn_lk = get_db_conn()

                cur_lk = db_conn_lk.cursor()

                existing_src = helpers.find_existing_local_media(cur_lk, unique_filename, file_size)

                db_conn_lk.close()

                if existing_src and helpers.try_create_hardlink(existing_src, file_path):

                    result['media_local_path'] = os.path.relpath(file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                else:

                    try:

                        raw_path = await asyncio.wait_for(msg.download_media(file=file_path), timeout=120.0)

                        if raw_path and os.path.exists(raw_path):

                            result['media_local_path'] = os.path.relpath(raw_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                        else:

                            result['media_skipped'] = "Unavailable on Telegram (purged or expired)"

                    except asyncio.TimeoutError:

                        result['media_skipped'] = "Failed: Telegram Server Timeout (120s)"

                    except Exception as e_dl:

                        logging.warning(f"[LiveMedia] File download failed for msg {msg.id}: {e_dl}")

                        _c, _lbl, _p = helpers.classify_media_download_error(e_dl)

                        result['media_skipped'] = f"Unavailable on Telegram ({_lbl})" if _p else f"Failed: {_lbl}"



        result['file_size'] = file_size or 0

        if file_size > 0:

            if not result.get('media_other_data'):

                result['media_other_data'] = {'size': file_size}

            elif isinstance(result.get('media_other_data'), dict) and 'size' not in result['media_other_data']:

                result['media_other_data']['size'] = file_size

        return result



    if getattr(msg, 'photo', None):

        result['media_type'] = 'photo'

        result['media_mime_type'] = 'image/jpeg'

        photo = msg.photo

        media_id = photo.id

        unique_filename = f"{msg.id} - {media_id}.jpg"

        result['media_original_name'] = unique_filename

        target_dir = get_media_target_dir(chat_media_dir, is_photo=True)

        file_path = os.path.join(target_dir, unique_filename)



        cfg = settings.get('photos', {})

        last_size_obj = photo.sizes[-1] if getattr(photo, 'sizes', None) else None

        file_size = getattr(last_size_obj, 'size', getattr(last_size_obj, 'file_size', 0)) if last_size_obj else 0

        file_size_mb = file_size / (1024 * 1024) if file_size else 0



        if not cfg.get('enabled', settings.get('download_photos', True)):

            result['media_skipped'] = "Загрузка фото отключена"

        elif cfg.get('max_mb', 0) > 0 and file_size_mb > cfg.get('max_mb'):

            result['media_skipped'] = f"Больше {cfg.get('max_mb')} МБ"

        elif core.MAX_SIZE_BYTES > 0 and file_size > core.MAX_SIZE_BYTES:

            result['media_skipped'] = f"Файл превышает лимит {core.MAX_FILE_SIZE_MB} МБ"



        # Apply user/bot exclusion rules for photos

        if not result['media_skipped'] and settings.get('user_rules'):

            u_rules = settings['user_rules']

            s_id = getattr(msg, 'sender_id', None)

            v_id = getattr(msg, 'via_bot_id', None)

            f_id = helpers.get_message_forward_sender_id(msg)

            r_key = (

                str(s_id) if s_id and str(s_id) in u_rules

                else (str(v_id) if v_id and str(v_id) in u_rules

                else (str(f_id) if f_id and str(f_id) in u_rules else None))

            )

            if r_key:

                u_rule = u_rules[r_key]

                r_name = u_rule.get('user_name') or u_rule.get('name') or f"User/Bot {r_key}"

                r_mode = u_rule.get('mode', 'exclude_all')

                if r_mode == 'exclude_all':

                    result['media_skipped'] = f"Исключено правилом для {r_name} [user_rule_excluded:{r_key}]"

                elif r_mode == 'custom':

                    u_cfg = u_rule.get('photos') or {}

                    if not u_cfg.get('enabled', True):

                        result['media_skipped'] = f"Исключено (photos) для {r_name} [user_rule_excluded:{r_key}]"

                    elif u_cfg.get('max_mb', 0) > 0 and file_size_mb > u_cfg.get('max_mb'):

                        result['media_skipped'] = f"Больше {u_cfg.get('max_mb')} МБ ({r_name}) [user_rule_excluded:{r_key}]"



        if not result['media_skipped']:

            legacy_path = os.path.join(chat_media_dir, unique_filename)

            if os.path.exists(file_path):

                result['media_local_path'] = os.path.relpath(file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            elif os.path.exists(legacy_path):

                result['media_local_path'] = os.path.relpath(legacy_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            else:

                db_conn_lk = get_db_conn()

                cur_lk = db_conn_lk.cursor()

                existing_src = helpers.find_existing_local_media(cur_lk, unique_filename, file_size)

                db_conn_lk.close()

                if existing_src and helpers.try_create_hardlink(existing_src, file_path):

                    result['media_local_path'] = os.path.relpath(file_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                else:

                    try:

                        raw_path = await asyncio.wait_for(msg.download_media(file=file_path), timeout=60.0)

                        if raw_path and os.path.exists(raw_path):

                            result['media_local_path'] = os.path.relpath(raw_path, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                        else:

                            result['media_skipped'] = "Unavailable on Telegram (purged or expired)"

                    except Exception as e_dl:

                        logging.warning(f"[LiveMedia] Photo download failed for msg {msg.id}: {e_dl}")

                        _c, _lbl, _p = helpers.classify_media_download_error(e_dl)

                        result['media_skipped'] = f"Unavailable on Telegram ({_lbl})" if _p else f"Failed: {_lbl}"



        result['file_size'] = file_size or 0

        if file_size > 0:

            if not result.get('media_other_data'):

                result['media_other_data'] = {'size': file_size}

            elif isinstance(result.get('media_other_data'), dict) and 'size' not in result['media_other_data']:

                result['media_other_data']['size'] = file_size

        return result



    if getattr(msg, 'media', None):

        media_cls = type(msg.media).__name__

        if media_cls == 'MessageMediaPaidMedia':

            result['media_type'] = 'paid_media'

            stars_amount = getattr(msg.media, 'stars_amount', 0)

            paid_items = []

            for idx_em, em in enumerate(getattr(msg.media, 'extended_media', None) or []):

                em_type = type(em).__name__

                if em_type == 'MessageExtendedMedia':

                    sub_media = getattr(em, 'media', None)

                    sub_path_rel = None

                    if sub_media:

                        paid_dir = os.path.join(chat_media_dir, "paid_media")

                        os.makedirs(paid_dir, exist_ok=True)

                        sub_fn = f"{msg.id}_paid_{idx_em}.jpg"

                        sub_abs = os.path.join(paid_dir, sub_fn)

                        if not os.path.exists(sub_abs):

                            try:

                                await client.download_media(sub_media, file=sub_abs)

                            except Exception: pass

                        if os.path.exists(sub_abs):

                            sub_path_rel = os.path.relpath(sub_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')

                    paid_items.append({"type": "media", "path": sub_path_rel})

                elif em_type == 'MessageExtendedMediaPreview':

                    paid_items.append({

                        "type": "preview",

                        "width": getattr(em, 'w', 0),

                        "height": getattr(em, 'h', 0),

                        "duration": getattr(em, 'video_duration', 0)

                    })

            result['media_other_data'] = {

                "stars_amount": stars_amount,

                "items": paid_items,

                "raw": msg.media.to_dict() if hasattr(msg.media, 'to_dict') else str(msg.media)

            }

        elif media_cls == 'MessageMediaInvoice':

            result['media_type'] = 'invoice'

            inv_photo = getattr(msg.media, 'photo', None)

            inv_photo_rel = None

            if inv_photo:

                inv_dir = os.path.join(chat_media_dir, "invoices")

                os.makedirs(inv_dir, exist_ok=True)

                inv_fn = f"{msg.id}_invoice.jpg"

                inv_abs = os.path.join(inv_dir, inv_fn)

                if not os.path.exists(inv_abs):

                    try: await client.download_media(inv_photo, file=inv_abs)

                    except Exception: pass

                if os.path.exists(inv_abs):

                    inv_photo_rel = os.path.relpath(inv_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')

            result['media_other_data'] = {

                "title": getattr(msg.media, 'title', None),

                "description": getattr(msg.media, 'description', None),

                "currency": getattr(msg.media, 'currency', None),

                "total_amount": getattr(msg.media, 'total_amount', None),

                "receipt_msg_id": getattr(msg.media, 'receipt_msg_id', None),

                "photo_path": inv_photo_rel,

                "raw": msg.media.to_dict() if hasattr(msg.media, 'to_dict') else str(msg.media)

            }

        elif media_cls in ('MessageMediaGiveaway', 'MessageMediaGiveawayResults'):

            result['media_type'] = 'giveaway'

            result['media_other_data'] = msg.media.to_dict() if hasattr(msg.media, 'to_dict') else str(msg.media)

        elif media_cls == 'MessageMediaStory':

            result['media_type'] = 'story'

            result['media_other_data'] = msg.media.to_dict() if hasattr(msg.media, 'to_dict') else str(msg.media)

        elif media_cls == 'MessageMediaWebPage':

            result['media_type'] = 'web_page'

            result['media_other_data'] = msg.media.to_dict() if hasattr(msg.media, 'to_dict') else str(msg.media)

        else:

            result['media_type'] = media_cls.replace('MessageMedia', '').lower()

            result['media_other_data'] = msg.media.to_dict() if hasattr(msg.media, 'to_dict') else str(msg.media)



    return result

