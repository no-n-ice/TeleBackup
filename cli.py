#!/usr/bin/env python3
import asyncio
import configparser
import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import core
from telethon import TelegramClient

def cli_progress_updater(chat_id, text, processed=None, total=None, status="running", last_item=None):
    """Simple callback for displaying progress in terminal."""
    if processed is not None and total is not None and total > 0:
        percent = int((processed / total) * 100)
        suffix = f" | {last_item}" if last_item else ""
        print(f"[{chat_id}] {text} ({percent}%){suffix}", end='\r' if status == "running" else '\n')
    else:
        print(f"[{chat_id}] {text}")

import os

def get_app_version():
    try:
        vfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'VERSION')
        with open(vfile, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return '0.0.0'

async def main():
    print(f"--- Starting Telegram Backup CLI (v{get_app_version()}) ---")
    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    if os.path.exists('config.ini'):
        config.read('config.ini', encoding='utf-8')
    
    # --- 1. AUTHENTICATION ---
    try:
        api_id = config.getint('Telegram', 'api_id')
        api_hash = config.get('Telegram', 'api_hash')
        session_name = config.get('Telegram', 'session_name', fallback='backup_session')
    except Exception as e:
        print("❌ Error reading config.ini: Ensure api_id (number) and api_hash are filled.")
        return

    print("Checking Telegram session...")
    client = TelegramClient(session_name, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("\n⚠️ Session not found or unauthorized.")
        print("Please enter your phone number and verification code:")
        # Telethon interactive prompt in terminal
        await client.start()  # type: ignore
        print("✅ Authorized successfully! Session saved.\n")
    await client.disconnect()
    # ----------------------

    # --- 2. START BACKUP ---
    chat_ids_str = config.get('Backup', 'chat_ids', fallback='')
    if not chat_ids_str or chat_ids_str == '-':
        print("!! No target chats in config.ini (chat_ids parameter).")
        return
        
    chat_ids = [int(cid.strip()) for cid in chat_ids_str.split(',') if cid.strip()]
    
    dl_media = config.getboolean('Backup', 'download_media', fallback=True)
    settings = {
        'photos': {'enabled': dl_media, 'max_mb': 0},
        'videos': {'enabled': dl_media, 'max_mb': 0},
        'voices': {'enabled': dl_media, 'max_mb': 0},
        'audio': {'enabled': dl_media, 'max_mb': 0},
        'files': {'enabled': dl_media, 'max_mb': 0},
    }

    for chat_id in chat_ids:
        print(f"\n--- Starting backup for chat: {chat_id} ---")
        try:
            final_text, migrated_to = await core.async_run_backup(
                chat_id=chat_id,
                settings=settings,
                total_messages=0, 
                progress_callback=cli_progress_updater
            )
            print(f"\n[{chat_id}] Result: {final_text}")
            
            if migrated_to:
                print(f"[{chat_id}] ⚠️ Notice: Chat migrated to supergroup (ID: {migrated_to}).")
                print(f"Add {migrated_to} to config.ini to continue fetching new history.")
                
        except Exception as e:
            print(f"\n[{chat_id}] ❌ Error: {e}")

if __name__ == '__main__':
    conn = core.get_db_conn()
    core.init_db(conn.cursor())
    core.link_migrated_chats(conn)
    conn.close()
    
    asyncio.run(main())