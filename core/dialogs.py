#!/usr/bin/env python3
"""
core/dialogs.py — Telegram dialog discovery, migration linking, and quick/full dialog cache builders.
"""
import os
import re
import time
import json
import logging
import sqlite3
import asyncio
from datetime import datetime
from telethon import TelegramClient, types, functions
from telethon.tl.types import User, PeerChannel, PeerChat
from .db import (
    get_db_conn, get_offset_id, flush_live_db_to_main, DB_FILE, CACHE_FILE,
    init_db, run_db_migration, load_all_users_from_db, link_migrated_chats
)
from . import db
from . import helpers
from .helpers import (
    dt_converter, sanitize_filename, get_entity_name, fetch_and_save_user,
    get_chat_id_variants, are_chat_ids_equal, get_media_target_dir, ensure_chat_media_subdirs,
    is_channel_peer_id, sanitize_dialog_counts
)
import core

class TelegramNotAuthorizedError(Exception):
    """Raised when Telegram session is not authorized."""
    pass


async def _fetch_dialog_total_messages(client, dialog, saved_count=0, context_label=""):
    """Fetches true history message count via GetHistory(limit=0); never falls back to global message IDs for non-channels."""
    from telethon.errors import FloodWaitError
    saved = max(0, int(saved_count or 0))
    for attempt in range(2):
        try:
            msgs_total = await asyncio.wait_for(client.get_messages(dialog.entity, limit=0), timeout=3.5)
            total = getattr(msgs_total, 'total', None)
            if total is None:
                total = len(msgs_total)
            return max(0, int(total or 0)), 2
        except FloodWaitError as fw:
            wait_sec = getattr(fw, 'seconds', 0) or 0
            if attempt == 0 and 0 < wait_sec <= 10:
                await asyncio.sleep(wait_sec + 0.5)
                continue
            logging.debug(f"FloodWait ({wait_sec}s) fetching msgs_total ({context_label}) for {dialog.name}")
            break
        except Exception as e:
            logging.debug(f"Error fetching msgs_total ({context_label}) on {dialog.name}: {e}")
            break

    # Fallback: only channels/megagroups have per-chat sequential IDs; DMs/bots/basic groups have account-global IDs (1.4M+)
    if dialog.is_channel:
        return max(0, int(getattr(dialog.message, 'id', 0) or saved)), 1
    return saved, 1


def _build_migration_part(src, label_suffix):
    """Creates a complex-dialog part dict while preserving peer metadata and count provenance."""
    part = {
        "id": src['id'],
        "name": src['name'] + label_suffix,
        "total_messages": src.get('total_messages', 0),
        "counts_version": src.get('counts_version', 1),
        "chat_type": src.get('chat_type'),
        "saved_count": src.get('saved_count', 0),
        "is_archived": src.get('is_archived', False),
    }
    if src.get('avatar_path'):
        part["avatar_path"] = src.get('avatar_path')
    return part


def _merge_migrated_dialogs(dialogs_list, migration_links):
    """Collapses migrated basic-group -> supergroup pairs into complex multi-part dialog entries."""
    final_dialogs, skip_ids = [], set()
    for d in dialogs_list:
        if d['id'] in skip_ids:
            continue

        old_ids = [old for old, new in migration_links.items() if new == d['id']]
        if old_ids:
            old_id = old_ids[0]
            old_d = next((x for x in dialogs_list if x['id'] == old_id), None)
            parts = []
            if old_d:
                parts.append(_build_migration_part(old_d, " (Часть 1)"))
                skip_ids.add(old_id)
                final_dialogs = [fd for fd in final_dialogs if fd['id'] != old_id]
            parts.append(_build_migration_part(d, " (Часть 2)"))
            d['is_complex'], d['parts'] = True, parts
            if not d.get('avatar_path') and old_d and old_d.get('avatar_path'):
                d['avatar_path'] = old_d.get('avatar_path')
            final_dialogs.append(d)
            continue

        if d['id'] in migration_links:
            new_id = migration_links[d['id']]
            new_d = next((x for x in dialogs_list if x['id'] == new_id), None)
            parts = [_build_migration_part(d, " (Часть 1)")]
            if new_d:
                parts.append(_build_migration_part(new_d, " (Часть 2)"))
                skip_ids.add(new_id)
                final_dialogs = [fd for fd in final_dialogs if fd['id'] != new_id]
            d['is_complex'], d['parts'] = True, parts
            if new_d:
                d['name'] = new_d['name']
                if not d.get('avatar_path') and new_d.get('avatar_path'):
                    d['avatar_path'] = new_d.get('avatar_path')
            final_dialogs.append(d)
            continue

        final_dialogs.append(d)
    return final_dialogs

async def async_fetch_and_cache_dialogs(db_conn=None, client=None):
    """Стягивает список диалогов и кэширует, возвращает список для веб-интерфейса."""
    should_close = False
    if db_conn is None:
        db_conn = get_db_conn()
        should_close = True
    try:
        return await _async_fetch_and_cache_dialogs_impl(db_conn, client=client)
    finally:
        if should_close and db_conn:
            try:
                db_conn.close()
            except Exception:
                pass


async def _async_fetch_and_cache_dialogs_impl(db_conn, client=None):
    archived_chats_ids = set()
    custom_settings_ids = set()
    saved_counts = {}
    chat_avatar_paths = {}
    try:
        cursor = db_conn.cursor()
        try:
            cursor.execute("SELECT chat_id, archive_settings_json FROM tbl_chats")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE tbl_chats ADD COLUMN archive_settings_json TEXT DEFAULT NULL")
                if hasattr(db_conn, 'commit'):
                    db_conn.commit()
            except Exception:
                pass
            cursor.execute("SELECT chat_id, archive_settings_json FROM tbl_chats")
        rows = cursor.fetchall()
        custom_settings_ids = {row['chat_id'] for row in rows if row['archive_settings_json'] and str(row['archive_settings_json']).strip() not in ('', 'null', 'None')}
        
        cursor.execute("SELECT chat_id FROM tbl_chats WHERE (last_backup_offset_id IS NOT NULL AND last_backup_offset_id > 0) OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = tbl_chats.chat_id)")
        archived_chats_ids = {row['chat_id'] for row in cursor.fetchall()}
        cursor.execute("SELECT chat_id, COUNT(message_id) as count FROM tbl_messages GROUP BY chat_id")
        for row in cursor.fetchall(): saved_counts[row['chat_id']] = row['count']
        cursor.execute("SELECT chat_id, chat_avatar_path FROM tbl_chats WHERE chat_avatar_path IS NOT NULL")
        for row in cursor.fetchall():
            if row['chat_avatar_path']:
                chat_avatar_paths[row['chat_id']] = row['chat_avatar_path']
    except Exception as e:
        logging.error('Exception caught', exc_info=True)

    client_was_created = False
    curr_loop = asyncio.get_running_loop()
    
    if client and client.is_connected():
        pass
    elif getattr(core, 'LIVE_CLIENT', None) and getattr(core.LIVE_CLIENT, 'loop', None) == curr_loop:
        client = core.LIVE_CLIENT
        if not client.is_connected():
            try:
                await client.connect()
            except Exception as e_conn:
                logging.warning(f"Reconnecting LIVE_CLIENT failed: {e_conn}")
    else:
        client = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
        client_was_created = True

    dialogs_list = []
    migration_links = {}
    
    try:
        if client_was_created:
            await client.connect()
            
        if not await client.is_user_authorized():
            raise TelegramNotAuthorizedError("Telegram session not authorized. Run cli.py or log in via WebUI.")
        
        async for dialog in client.iter_dialogs():
            if not dialog.entity: continue
            
            if hasattr(dialog.entity, 'migrated_to') and dialog.entity.migrated_to:
                migration_links[dialog.id] = int(f"-100{dialog.entity.migrated_to.channel_id}")
            
            # Accurate message count: query get_messages(limit=0)
            total_messages, counts_version = await _fetch_dialog_total_messages(
                client, dialog, saved_counts.get(dialog.id, 0), context_label="full"
            )
            total_messages = max(total_messages, saved_counts.get(dialog.id, 0))
                
            chat_type = 'unknown'
            if getattr(dialog.entity, 'bot', False):
                chat_type = 'bot'
            elif dialog.is_user:
                chat_type = 'dm'
            elif dialog.is_group or getattr(dialog.entity, 'megagroup', False):
                chat_type = 'group'
            elif dialog.is_channel:
                chat_type = 'channel'
                
            last_active = 0
            if dialog.date:
                last_active = dialog.date.timestamp()

            chat_wallpaper_path_rel = None
            if hasattr(dialog.entity, 'wallpaper') and dialog.entity.wallpaper:
                try:
                    wp_dir = os.path.join(core.MEDIA_BACKUP_DIR, "chat_wallpapers")
                    wp_path_abs = os.path.join(wp_dir, f"{dialog.id}_wallpaper.jpg")
                    if os.path.exists(wp_path_abs):
                        chat_wallpaper_path_rel = os.path.relpath(wp_path_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                except Exception as e_wp:
                    logging.debug(f"Error checking wallpaper for dialog {dialog.id}: {e_wp}")

            chat_avatar_rel = chat_avatar_paths.get(dialog.id)
            try:
                avatars_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "avatars_global")
                os.makedirs(avatars_dir, exist_ok=True)
                thumb_name = f"{dialog.id}_thumb.jpg"
                thumb_abs = os.path.join(avatars_dir, thumb_name)
                
                if not chat_avatar_rel:
                    clean_id = str(dialog.id).removeprefix('-100').removeprefix('-')
                    for test_name in (thumb_name, f"{dialog.id}.jpg", f"{clean_id}_thumb.jpg", f"{clean_id}.jpg"):
                        test_abs = os.path.join(avatars_dir, test_name)
                        if os.path.exists(test_abs):
                            chat_avatar_rel = f"avatars_global/{test_name}"
                            break

                if not chat_avatar_rel and getattr(dialog.entity, 'photo', None):
                    photo = dialog.entity.photo
                    if not isinstance(photo, (types.UserProfilePhotoEmpty, types.ChatPhotoEmpty)):
                        stripped_thumb = getattr(photo, 'stripped_thumb', None)
                        if stripped_thumb:
                            try:
                                from telethon import utils as tl_utils
                                jpg_bytes = tl_utils.stripped_photo_to_jpg(stripped_thumb)
                                if jpg_bytes:
                                    with open(thumb_abs, 'wb') as f_av:
                                        f_av.write(jpg_bytes)
                                    chat_avatar_rel = f"avatars_global/{thumb_name}"
                            except Exception as e_st:
                                logging.debug(f"Stripped thumb decode skipped for {dialog.id}: {e_st}")
                        if not chat_avatar_rel:
                            home_dc = getattr(getattr(client, 'session', None), 'dc_id', None)
                            photo_dc = getattr(photo, 'dc_id', None)
                            if home_dc and photo_dc == home_dc:
                                try:
                                    res = await client.download_profile_photo(dialog.entity, file=thumb_abs, download_big=False)
                                    if res and os.path.exists(thumb_abs):
                                        chat_avatar_rel = f"avatars_global/{thumb_name}"
                                except Exception as e_av:
                                    logging.debug(f"Thumbnail download skipped for {dialog.id}: {e_av}")
            except Exception as e_av_all:
                logging.debug(f"Avatar handling error for {dialog.id}: {e_av_all}")

            p_cnt = getattr(dialog.entity, 'participants_count', 0) or 0
            try:
                cursor.execute("UPDATE tbl_chats SET participants_count = ? WHERE chat_id = ?", (p_cnt, dialog.id))
            except Exception:
                pass

            dialogs_list.append({
                "id": dialog.id, "name": dialog.name,
                "is_archived": (dialog.id in archived_chats_ids), 
                "total_messages": total_messages,
                "counts_version": counts_version,
                "saved_count": saved_counts.get(dialog.id, 0),
                "has_custom_settings": (dialog.id in custom_settings_ids),
                "chat_type": chat_type,
                "last_active": last_active,
                "chat_wallpaper_path": chat_wallpaper_path_rel,
                "avatar_path": chat_avatar_rel,
                "participants_count": p_cnt
            })
    except TelegramNotAuthorizedError:
        raise
    except Exception as e:
        logging.error(f"[Core] Error during full dialog fetch: {type(e).__name__}: {e}", exc_info=True)
        if dialogs_list:
            logging.info(f"[Core] Preserving {len(dialogs_list)} fetched dialogs despite scan error.")
        else:
            raise
    finally:
        try:
            await client._clean_exported_senders()
        except Exception:
            pass
        if client_was_created:
            await client.disconnect()
            await asyncio.sleep(0.05)

    if migration_links:
        try:
            cursor = db_conn.cursor()
            for old_id, new_id in migration_links.items():
                cursor.execute("UPDATE tbl_chats SET migrated_to_chat_id = ? WHERE chat_id = ?", (new_id, old_id))
            db_conn.commit()
            
            dialogs_list = _merge_migrated_dialogs(dialogs_list, migration_links)
        except Exception as e:
            print(f"[Core] Ошибка сохранения миграций в БД: {e}")
            try:
                db_conn.rollback()
            except Exception:
                pass

    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(dialogs_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error('Exception caught', exc_info=True)
    
    return dialogs_list


async def async_quick_fetch_and_cache_dialogs(db_conn=None, client=None, recent_limit=60):
    """
    Быстрое обновление диалогов: запрашивает у Telegram только недавние диалоги (recent_limit),
    сверяя их с существующим кэшем.
    Чаты, которые уже есть в кэше, освобождаются от тяжелых get_messages(limit=0) запросов.
    Новые чаты (новосозданные, новые каналы/группы) полностью догружаются и добавляются в кэш.
    """
    if not os.path.exists(CACHE_FILE):
        return None

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cached_list = json.load(f)
    except Exception as e:
        logging.warning("Failed to read CACHE_FILE for quick refresh, baseline scan required.")
        return None

    if not isinstance(cached_list, list):
        return None

    should_close = False
    if db_conn is None:
        db_conn = get_db_conn()
        should_close = True
    try:
        return await _async_quick_fetch_and_cache_dialogs_impl(cached_list, db_conn, client=client, recent_limit=recent_limit)
    finally:
        if should_close and db_conn:
            try:
                db_conn.close()
            except Exception:
                pass


async def _async_quick_fetch_and_cache_dialogs_impl(cached_list, db_conn, client=None, recent_limit=60):
    known_chat_ids = set()
    cached_map = {}
    cached_parts_map = {}
    for d in cached_list:
        chat_id = d.get('id')
        if chat_id is not None:
            known_chat_ids.add(chat_id)
            cached_map[chat_id] = d
        if d.get('parts'):
            for p in d['parts']:
                if p.get('id') is not None:
                    known_chat_ids.add(p['id'])
                    cached_parts_map[p['id']] = p

    archived_chats_ids = set()
    custom_settings_ids = set()
    saved_counts = {}
    chat_avatar_paths = {}
    try:
        cursor = db_conn.cursor()
        try:
            cursor.execute("SELECT chat_id, archive_settings_json FROM tbl_chats")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE tbl_chats ADD COLUMN archive_settings_json TEXT DEFAULT NULL")
                if hasattr(db_conn, 'commit'):
                    db_conn.commit()
            except Exception:
                pass
            cursor.execute("SELECT chat_id, archive_settings_json FROM tbl_chats")
        rows = cursor.fetchall()
        custom_settings_ids = {row['chat_id'] for row in rows if row['archive_settings_json'] and str(row['archive_settings_json']).strip() not in ('', 'null', 'None')}

        cursor.execute("SELECT chat_id FROM tbl_chats WHERE (last_backup_offset_id IS NOT NULL AND last_backup_offset_id > 0) OR EXISTS (SELECT 1 FROM tbl_messages m WHERE m.chat_id = tbl_chats.chat_id)")
        archived_chats_ids = {row['chat_id'] for row in cursor.fetchall()}

        cursor.execute("SELECT chat_id, COUNT(message_id) as count FROM tbl_messages GROUP BY chat_id")
        for row in cursor.fetchall():
            saved_counts[row['chat_id']] = row['count']

        cursor.execute("SELECT chat_id, chat_avatar_path FROM tbl_chats WHERE chat_avatar_path IS NOT NULL")
        for row in cursor.fetchall():
            if row['chat_avatar_path']:
                chat_avatar_paths[row['chat_id']] = row['chat_avatar_path']
    except Exception as e:
        logging.error('Exception caught in quick dialog fetch DB query', exc_info=True)

    client_was_created = False
    curr_loop = asyncio.get_running_loop()
    if client and client.is_connected():
        pass
    elif getattr(core, 'LIVE_CLIENT', None) and getattr(core.LIVE_CLIENT, 'loop', None) == curr_loop:
        client = core.LIVE_CLIENT
        if not client.is_connected():
            try:
                await client.connect()
            except Exception as e_conn:
                logging.warning(f"Reconnecting LIVE_CLIENT failed in quick refresh: {e_conn}")
    else:
        client = TelegramClient(core.SESSION_FILE, core.API_ID, core.API_HASH)
        client_was_created = True

    newly_discovered = []
    migration_links = {}
    try:
        if client_was_created:
            await client.connect()

        if not await client.is_user_authorized():
            raise TelegramNotAuthorizedError("Telegram session not authorized. Run cli.py or log in via WebUI.")

        async for dialog in client.iter_dialogs(limit=recent_limit):
            if not dialog.entity:
                continue

            if hasattr(dialog.entity, 'migrated_to') and dialog.entity.migrated_to:
                migration_links[dialog.id] = int(f"-100{dialog.entity.migrated_to.channel_id}")

            # Если чат уже известен в кэше — обновляем имя, дату, аватар и лечим устаревшие счетчики
            if dialog.id in known_chat_ids:
                cached_d = cached_map.get(dialog.id)
                cached_part = cached_parts_map.get(dialog.id)
                target_entry = cached_d or cached_part
                if target_entry:
                    if cached_d:
                        if dialog.name and cached_d.get('name') != dialog.name:
                            cached_d['name'] = dialog.name
                        if dialog.date:
                            cached_d['last_active'] = dialog.date.timestamp()
                        if not cached_d.get('avatar_path'):
                            av_candidate = chat_avatar_paths.get(dialog.id)
                            if not av_candidate:
                                clean_id = str(dialog.id).removeprefix('-100').removeprefix('-')
                                for test_name in (f"{dialog.id}_thumb.jpg", f"{dialog.id}.jpg", f"{clean_id}_thumb.jpg", f"{clean_id}.jpg"):
                                    test_abs = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "avatars_global", test_name)
                                    if os.path.exists(test_abs):
                                        av_candidate = f"avatars_global/{test_name}"
                                        break
                            if av_candidate:
                                cached_d['avatar_path'] = av_candidate
                    # Self-heal legacy cached counts (< v2) on DMs, bots, and basic groups
                    if not is_channel_peer_id(dialog.id) and int(target_entry.get('counts_version') or 0) < 2:
                        healed_total, healed_ver = await _fetch_dialog_total_messages(
                            client, dialog, saved_counts.get(dialog.id, 0), context_label="quick_heal"
                        )
                        healed_total = max(healed_total, saved_counts.get(dialog.id, 0))
                        target_entry['total_messages'] = healed_total
                        target_entry['counts_version'] = healed_ver
                        if cached_part and cached_part is not target_entry:
                            cached_part['total_messages'] = healed_total
                            cached_part['counts_version'] = healed_ver
                continue

            # НОВОЕ: обнаружен новый чат
            total_messages, counts_version = await _fetch_dialog_total_messages(
                client, dialog, saved_counts.get(dialog.id, 0), context_label="quick_new"
            )
            total_messages = max(total_messages, saved_counts.get(dialog.id, 0))

            chat_type = 'unknown'
            if getattr(dialog.entity, 'bot', False):
                chat_type = 'bot'
            elif dialog.is_user:
                chat_type = 'dm'
            elif dialog.is_group or getattr(dialog.entity, 'megagroup', False):
                chat_type = 'group'
            elif dialog.is_channel:
                chat_type = 'channel'

            last_active = dialog.date.timestamp() if dialog.date else 0

            chat_wallpaper_path_rel = None
            if hasattr(dialog.entity, 'wallpaper') and dialog.entity.wallpaper:
                try:
                    wp_dir = os.path.join(core.MEDIA_BACKUP_DIR, "chat_wallpapers")
                    wp_path_abs = os.path.join(wp_dir, f"{dialog.id}_wallpaper.jpg")
                    if os.path.exists(wp_path_abs):
                        chat_wallpaper_path_rel = os.path.relpath(wp_path_abs, core.MEDIA_BACKUP_DIR).replace('\\', '/')
                except Exception as e_wp:
                    logging.debug(f"Error checking wallpaper for dialog {dialog.id}: {e_wp}")

            chat_avatar_rel = chat_avatar_paths.get(dialog.id)
            try:
                avatars_dir = os.path.join(core.ABS_MEDIA_BACKUP_DIR, "avatars_global")
                os.makedirs(avatars_dir, exist_ok=True)
                thumb_name = f"{dialog.id}_thumb.jpg"
                thumb_abs = os.path.join(avatars_dir, thumb_name)
                if not chat_avatar_rel:
                    clean_id = str(dialog.id).removeprefix('-100').removeprefix('-')
                    for test_name in (thumb_name, f"{dialog.id}.jpg", f"{clean_id}_thumb.jpg", f"{clean_id}.jpg"):
                        test_abs = os.path.join(avatars_dir, test_name)
                        if os.path.exists(test_abs):
                            chat_avatar_rel = f"avatars_global/{test_name}"
                            break

                if not chat_avatar_rel and getattr(dialog.entity, 'photo', None):
                    photo = dialog.entity.photo
                    if not isinstance(photo, (types.UserProfilePhotoEmpty, types.ChatPhotoEmpty)):
                        stripped_thumb = getattr(photo, 'stripped_thumb', None)
                        if stripped_thumb:
                            try:
                                from telethon import utils as tl_utils
                                jpg_bytes = tl_utils.stripped_photo_to_jpg(stripped_thumb)
                                if jpg_bytes:
                                    with open(thumb_abs, 'wb') as f_av:
                                        f_av.write(jpg_bytes)
                                    chat_avatar_rel = f"avatars_global/{thumb_name}"
                            except Exception as e_st:
                                logging.debug(f"Stripped thumb decode skipped for {dialog.id}: {e_st}")
                        if not chat_avatar_rel:
                            home_dc = getattr(getattr(client, 'session', None), 'dc_id', None)
                            photo_dc = getattr(photo, 'dc_id', None)
                            if home_dc and photo_dc == home_dc:
                                try:
                                    res = await client.download_profile_photo(dialog.entity, file=thumb_abs, download_big=False)
                                    if res and os.path.exists(thumb_abs):
                                        chat_avatar_rel = f"avatars_global/{thumb_name}"
                                except Exception as e_av:
                                    logging.debug(f"Thumbnail download skipped for {dialog.id}: {e_av}")
            except Exception as e_av_all:
                logging.debug(f"Avatar handling error for {dialog.id}: {e_av_all}")

            p_cnt = getattr(dialog.entity, 'participants_count', 0) or 0
            new_item = {
                "id": dialog.id,
                "name": dialog.name,
                "is_archived": (dialog.id in archived_chats_ids),
                "total_messages": total_messages,
                "counts_version": counts_version,
                "saved_count": saved_counts.get(dialog.id, 0),
                "has_custom_settings": (dialog.id in custom_settings_ids),
                "chat_type": chat_type,
                "last_active": last_active,
                "chat_wallpaper_path": chat_wallpaper_path_rel,
                "avatar_path": chat_avatar_rel,
                "participants_count": p_cnt
            }
            newly_discovered.append(new_item)
            known_chat_ids.add(dialog.id)
            cached_map[dialog.id] = new_item
    except TelegramNotAuthorizedError:
        raise
    except Exception as e:
        logging.error(f"[Core] Error during quick dialog fetch: {type(e).__name__}: {e}", exc_info=True)
        if newly_discovered or cached_list:
            logging.info(f"[Core] Returning existing/partial cached dialogs despite quick refresh error.")
        else:
            raise
    finally:
        try:
            await client._clean_exported_senders()
        except Exception:
            pass
        if client_was_created:
            await client.disconnect()
            await asyncio.sleep(0.05)

    if newly_discovered:
        cached_list = newly_discovered + cached_list

    if migration_links:
        try:
            cursor = db_conn.cursor()
            for old_id, new_id in migration_links.items():
                cursor.execute("UPDATE tbl_chats SET migrated_to_chat_id = ? WHERE chat_id = ?", (new_id, old_id))
                cursor.execute("""
                    INSERT OR IGNORE INTO tbl_chats (chat_id, chat_name, safe_folder_name, archive_settings_json, last_backup_offset_id)
                    SELECT ?, chat_name, safe_folder_name, archive_settings_json, 0 FROM tbl_chats WHERE chat_id = ?
                """, (new_id, old_id))
            db_conn.commit()

            cached_list = _merge_migrated_dialogs(cached_list, migration_links)
        except Exception as e:
            logging.error(f"[QuickDialogs] Error updating migration links: {e}")
            try:
                db_conn.rollback()
            except Exception:
                pass

    # Обновляем статусы и счетчики для всех чатов на основе актуальной БД
    for d in cached_list:
        d['is_archived'] = (d['id'] in archived_chats_ids)
        d['saved_count'] = saved_counts.get(d['id'], 0)
        sanitize_dialog_counts(d, d['saved_count'])
        d['has_custom_settings'] = (d['id'] in custom_settings_ids)
        if d.get('is_complex') and d.get('parts'):
            for p in d['parts']:
                p['is_archived'] = (p['id'] in archived_chats_ids)
                p['saved_count'] = saved_counts.get(p['id'], 0)
                sanitize_dialog_counts(p, p['saved_count'])

    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cached_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error('Exception caught writing updated quick cache', exc_info=True)

    return cached_list

