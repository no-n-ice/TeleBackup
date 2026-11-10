#!/usr/bin/env python3
"""
db-export.py — Telegram Database Export Tool

Exports Telegram chat history from SQLite (telegram_backup.db) to Markdown snapshots
with customizable time grouping (Day, Week, Month, Single file, or Custom range)
and detail depth controls (Compact/Verbose, Reactions, Replies, Forwards, Media, Polls).
Supports English (en) and Russian (ru).
"""

import logging
import sqlite3
import json
import os
import sys
import argparse
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DB_FILE = os.path.join(PROJECT_ROOT, "telegram_backup.db")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "db_exports")

from core.db import get_db_conn, get_chat_pipeline

# Translations catalog for CLI and Markdown snapshots
I18N_STRINGS = {
    'en': {
        'unknown_author': 'Unknown',
        'fwd_hidden_sender': '[Hidden sender]',
        'fwd_from': '> *[Forwarded from: {label}{post_suffix}{author_suffix}{date_str}]*\n',
        'fwd_from_compact': '*[Forwarded from: {label}{post_suffix}{author_suffix}{date_str}]* ',
        'fwd_msg': '> *[Forwarded message]*\n',
        'fwd_msg_compact': '*[Forwarded message]* ',
        'poll_no_question': 'No question',
        'poll_closed': 'Closed',
        'poll_active': 'Active',
        'poll_header': '\n{indent}📊 **Poll:** {question} *(Votes: {total_voters}, {status})*\n',
        'poll_option': '{indent}  ▫️ {text} — {voters} votes\n',
        'poll_data_fallback': '\n📊 *[Poll data]*\n',
        'rx_and_more': ' and {diff} more',
        'rx_from': '{emoji} (from: {users_str})',
        'rx_block': '\n> *(Reactions: {reactions})*',
        'action_add_user': 'added user to chat.',
        'action_del_user': 'removed user or left chat.',
        'action_edit_title': 'changed chat title to «{title}».',
        'action_pin_msg': 'pinned a message.',
        'action_generic': 'system action ({action_type}).',
        'action_fallback': 'system message.',
        'md_header': '# Chat Log: {chat_name} (Snapshot: {current_key})\n\n',
        'system_msg_verbose': '**[{display_date}] ⚙️ System message:**\n*{author} {action_text}*\n\n---\n\n',
        'in_reply_to': '> *In reply to:* {clean_reply}...\n',
        'reply_to_id': ' ↩️ *(reply to #{reply_id})*',
        'reply_to_snippet': ' ↩️ *(reply: {clean_rep}...)*',
        'topic_tag': '[Topic #{topic_id}] ',
        'attached_media': '*[Attached media: {media}]*\n',
        'no_messages': '❌ No messages found in selected range.',
        'exporting_chat': "\n📦 Exporting chat '{chat_name}' ({count} messages)...",
        'style_interval': "⚙️ Style: {style}, Interval: {interval}\n",
        'file_created': '📄 File created: {filename}',
        'export_success': "\n✅ Successfully exported {count} messages to folder '{output_dir}'.",
        'loading_users': '🔄 Loading users database...',
        'loaded_users': 'Loaded users: {count}',
        'no_chats': '❌ No saved chats in database.',
        'menu_title': '\n=== Telegram DB Export Tool ===',
        'select_chat': '\nSelect chat to export:',
        'chat_choice': '\nChat number: ',
        'invalid_choice': '❌ Invalid number.',
        'nan_choice': '❌ Input is not a number.',
        'grouping_header': '\n--- Partition / Snapshots Setup (Grouping Basis) ---',
        'grouping_options': (
            '  1. By months (Month snapshots: ChatName_YYYY-MM.md)\n'
            '  2. By weeks (Week snapshots: ChatName_YYYY-Www.md)\n'
            '  3. By days (Day snapshots: ChatName_YYYY-MM-DD.md)\n'
            '  4. Single file (Single snapshot file: ChatName_full.md)\n'
            '  5. Custom date range (Custom Date Range)'
        ),
        'grouping_prompt': 'Select option [1-5] (default 1): ',
        'custom_start': 'Start date (YYYY-MM-DD, or Enter = from beginning): ',
        'custom_end': 'End date (YYYY-MM-DD, or Enter = to current date): ',
        'custom_fmt_header': '\nFile format for custom period:',
        'custom_fmt_options': (
            '  1. Single file (Single)\n'
            '  2. By days (Day)\n'
            '  3. By weeks (Week)\n'
            '  4. By months (Month)'
        ),
        'custom_fmt_prompt': 'Select option [1-4] (default 1): ',
        'detail_header': '\n--- Detail Depth & Formatting ---',
        'detail_options': (
            '  1. Compact format (Compact: 1 line per message, fast reading)\n'
            '  2. Verbose format (Verbose: multi-line blocks, dividers)'
        ),
        'detail_prompt': 'Format [1-2] (default 2): ',
        'filters_header': '\n--- Element Filters (enable/disable) ---',
        'ask_reactions': 'Show reactions?',
        'ask_replies': 'Show quotes/replies?',
        'ask_forwards': 'Show forwarded message tag?',
        'ask_media': 'Show attached media names?',
        'ask_polls': 'Show poll options?',
        'ask_service': 'Show system messages (join/leave/pin)?',
    },
    'ru': {
        'unknown_author': 'Неизвестный',
        'fwd_hidden_sender': '[Скрытый отправитель]',
        'fwd_from': '> *[Переслано от: {label}{post_suffix}{author_suffix}{date_str}]*\n',
        'fwd_from_compact': '*[Переслано от: {label}{post_suffix}{author_suffix}{date_str}]* ',
        'fwd_msg': '> *[Пересланное сообщение]*\n',
        'fwd_msg_compact': '*[Пересланное сообщение]* ',
        'poll_no_question': 'Без вопроса',
        'poll_closed': 'Завершен',
        'poll_active': 'Активен',
        'poll_header': '\n{indent}📊 **Опрос:** {question} *(Голосов: {total_voters}, {status})*\n',
        'poll_option': '{indent}  ▫️ {text} — {voters} гол.\n',
        'poll_data_fallback': '\n📊 *[Данные опроса]*\n',
        'rx_and_more': ' и еще {diff}',
        'rx_from': '{emoji} (от: {users_str})',
        'rx_block': '\n> *(Реакции: {reactions})*',
        'action_add_user': 'добавил(а) пользователя в чат.',
        'action_del_user': 'удалил(а) пользователя или покинул(а) чат.',
        'action_edit_title': 'изменил(а) название чата на «{title}».',
        'action_pin_msg': 'закрепил(а) сообщение.',
        'action_generic': 'системное действие ({action_type}).',
        'action_fallback': 'системное сообщение.',
        'md_header': '# Лог чата: {chat_name} (Снимок: {current_key})\n\n',
        'system_msg_verbose': '**[{display_date}] ⚙️ Системное сообщение:**\n*{author} {action_text}*\n\n---\n\n',
        'in_reply_to': '> *В ответ на:* {clean_reply}...\n',
        'reply_to_id': ' ↩️ *(ответ на #{reply_id})*',
        'reply_to_snippet': ' ↩️ *(ответ: {clean_rep}...)*',
        'topic_tag': '[Тема #{topic_id}] ',
        'attached_media': '*[Прикрепленное медиа: {media}]*\n',
        'no_messages': '❌ В выбранном диапазоне нет сообщений.',
        'exporting_chat': "\n📦 Экспорт чата '{chat_name}' ({count} сообщений)...",
        'style_interval': "⚙️ Стиль: {style}, Разбивка: {interval}\n",
        'file_created': '📄 Файл создан: {filename}',
        'export_success': "\n✅ Успешно экспортировано {count} сообщений в папку '{output_dir}'.",
        'loading_users': '🔄 Загрузка базы пользователей...',
        'loaded_users': 'Загружено пользователей: {count}',
        'no_chats': '❌ В базе данных нет сохраненных чатов.',
        'menu_title': '\n=== Telegram DB Export Tool ===',
        'select_chat': '\nВыберите чат для экспорта:',
        'chat_choice': '\nНомер чата: ',
        'invalid_choice': '❌ Неверный номер.',
        'nan_choice': '❌ Введено не число.',
        'grouping_header': '\n--- Настройка разбивки / снимков (Grouping Basis) ---',
        'grouping_options': (
            '  1. По месяцам (Month snapshots: ChatName_YYYY-MM.md)\n'
            '  2. По неделям (Week snapshots: ChatName_YYYY-Www.md)\n'
            '  3. По дням (Day snapshots: ChatName_YYYY-MM-DD.md)\n'
            '  4. Единый файл (Single snapshot file: ChatName_full.md)\n'
            '  5. Настраиваемый период дат (Custom Date Range)'
        ),
        'grouping_prompt': 'Выберите вариант [1-5] (по умолчанию 1): ',
        'custom_start': 'Начальная дата (YYYY-MM-DD, или Enter = с начала): ',
        'custom_end': 'Конечная дата (YYYY-MM-DD, или Enter = по текущую): ',
        'custom_fmt_header': '\nФормат файлов для кастомного периода:',
        'custom_fmt_options': (
            '  1. Единый файл (Single)\n'
            '  2. По дням (Day)\n'
            '  3. По неделям (Week)\n'
            '  4. По месяцам (Month)'
        ),
        'custom_fmt_prompt': 'Выберите вариант [1-4] (по умолчанию 1): ',
        'detail_header': '\n--- Настройка детализации (Detail Depth & Formatting) ---',
        'detail_options': (
            '  1. Компактный формат (Compact: 1 строка на сообщение, быстрое чтение)\n'
            '  2. Подробный формат (Verbose: многострочные блоки, разделители)'
        ),
        'detail_prompt': 'Формат [1-2] (по умолчанию 2): ',
        'filters_header': '\n--- Фильтры элементов (включить/выключить) ---',
        'ask_reactions': 'Показывать реакции?',
        'ask_replies': 'Показывать цитаты/ответы?',
        'ask_forwards': 'Показывать метку пересланных сообщений?',
        'ask_media': 'Показывать названия прикрепленных медиафайлов?',
        'ask_polls': 'Показывать варианты опросов?',
        'ask_service': 'Показывать системные сообщения (вход/выход/закреп)?',
    }
}


def t(key, lang='en', **kwargs):
    """Retrieve translated string with formatting."""
    lang_dict = I18N_STRINGS.get(lang, I18N_STRINGS['en'])
    tmpl = lang_dict.get(key, I18N_STRINGS['en'].get(key, ''))
    if kwargs:
        return tmpl.format(**kwargs)
    return tmpl


def format_author(first_name, last_name, username, lang='en'):
    parts = []
    if first_name: parts.append(first_name)
    if last_name: parts.append(last_name)
    name = " ".join(parts) if parts else t('unknown_author', lang)
    if username:
        return f"{name} (@{username})"
    return name


def load_users_cache(cursor, lang='en'):
    """Loads all users into a dict for fast ID lookups."""
    cursor.execute("SELECT user_id, first_name, last_name, username FROM tbl_users")
    cache = {}
    for row in cursor.fetchall():
        cache[row['user_id']] = format_author(row['first_name'], row['last_name'], row['username'], lang=lang)
    return cache


def parse_forward_info(forward_json, verbose=True, lang='en'):
    if not forward_json:
        return ""
    try:
        data = json.loads(forward_json)
        from_name = data.get('from_name') or data.get('header_from_name')
        fwd_date = data.get('date', '')
        date_str = ''
        if fwd_date:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(fwd_date.replace('Z', '+00:00')) if isinstance(fwd_date, str) else fwd_date
                date_str = f" ({dt.strftime('%d.%m.%Y %H:%M')})" if hasattr(dt, 'strftime') else f" ({fwd_date})"
            except Exception:
                date_str = f" ({fwd_date})"
        channel_post = data.get('channel_post')
        post_suffix = f" #{channel_post}" if channel_post else ""
        post_author = data.get('post_author')
        author_suffix = f" ({post_author})" if post_author else ""
        label = from_name or t('fwd_hidden_sender', lang)
        if verbose:
            return t('fwd_from', lang, label=label, post_suffix=post_suffix, author_suffix=author_suffix, date_str=date_str)
        else:
            return t('fwd_from_compact', lang, label=label, post_suffix=post_suffix, author_suffix=author_suffix, date_str=date_str)
    except Exception:
        logging.error('Exception caught', exc_info=True)
    return t('fwd_msg', lang) if verbose else t('fwd_msg_compact', lang)


def parse_poll_data(poll_json, verbose=True, lang='en'):
    if not poll_json:
        return ""
    try:
        data = json.loads(poll_json)
        question = data.get('question') or t('poll_no_question', lang)
        total_voters = data.get('total_voters', 0)
        status = t('poll_closed', lang) if data.get('is_closed', False) else t('poll_active', lang)

        indent = "  " if not verbose else ""
        poll_text = t('poll_header', lang, indent=indent, question=question, total_voters=total_voters, status=status)
        for option in data.get('options', []):
            opt_text = option.get('text', '')
            voters = option.get('voters', 0)
            poll_text += t('poll_option', lang, indent=indent, text=opt_text, voters=voters)
        return poll_text
    except Exception:
        logging.error('Exception caught', exc_info=True)
    return t('poll_data_fallback', lang)


def parse_reactions(reactions_json, users_cache, custom_emoji_cache=None, verbose=True, lang='en'):
    if not reactions_json:
        return ""
    try:
        data = json.loads(reactions_json)

        def get_emoji(rx_dict):
            if not rx_dict: return '❓'
            if rx_dict.get('emoticon'): return rx_dict['emoticon']
            if rx_dict.get('_') == 'ReactionCustomEmoji':
                doc_id = str(rx_dict.get('document_id', ''))
                if custom_emoji_cache and doc_id in custom_emoji_cache:
                    return custom_emoji_cache[doc_id].get('alt_emoji') or '❤️‍🔥'
                return '❤️‍🔥'
            return '❓'

        total_counts = {}
        for r in data.get('results', []):
            emoji = get_emoji(r.get('reaction', {}))
            total_counts[emoji] = r.get('count', 1)

        if not verbose:
            reaction_parts = [f"{emoji} {total}" for emoji, total in total_counts.items()]
            if reaction_parts:
                return f" `[{', '.join(reaction_parts)}]`"
            return ""

        recent = data.get('recent_reactions', [])
        user_reactions = {}
        for r in recent:
            peer = r.get('peer_id', {})
            user_id = peer.get('user_id') or peer.get('channel_id')
            emoji = get_emoji(r.get('reaction', {}))

            if user_id:
                user_name = users_cache.get(user_id, f"ID:{user_id}")
                if emoji not in user_reactions:
                    user_reactions[emoji] = []
                user_reactions[emoji].append(user_name)

        reaction_parts = []
        for emoji, total in total_counts.items():
            known_users = user_reactions.get(emoji, [])
            if known_users:
                users_str = ", ".join(known_users)
                if total > len(known_users):
                    users_str += t('rx_and_more', lang, diff=(total - len(known_users)))
                reaction_parts.append(t('rx_from', lang, emoji=emoji, users_str=users_str))
            else:
                reaction_parts.append(f"{emoji} ({total})")

        if reaction_parts:
            return t('rx_block', lang, reactions="; ".join(reaction_parts))
    except Exception:
        logging.error('Exception caught', exc_info=True)
    return ""


def parse_service_action(action_json, lang='en'):
    if not action_json:
        return ""
    try:
        action = json.loads(action_json)
        action_type = action.get('_')
        if action_type == 'MessageActionChatAddUser':
            return t('action_add_user', lang)
        if action_type == 'MessageActionChatDeleteUser':
            return t('action_del_user', lang)
        if action_type == 'MessageActionChatEditTitle':
            return t('action_edit_title', lang, title=action.get('title', ''))
        if action_type == 'MessageActionPinMessage':
            return t('action_pin_msg', lang)
        return t('action_generic', lang, action_type=action_type)
    except Exception:
        logging.error('Exception caught', exc_info=True)
    return t('action_fallback', lang)


def get_partition_key(dt, interval):
    """Returns partition/snapshot key based on time interval."""
    if interval == 'single':
        return 'full'
    elif interval == 'day':
        return dt.strftime('%Y-%m-%d')
    elif interval == 'week':
        return dt.strftime('%Y-W%U')
    elif interval == 'month':
        return dt.strftime('%Y-%m')
    return 'export'


def export_chat_to_md(chat_id, chat_name, users_cache, options, lang='en', custom_output_dir=None):
    conn = get_db_conn()
    cursor = conn.cursor()

    chat_ids = get_chat_pipeline(cursor, chat_id)
    placeholders = ','.join('?' for _ in chat_ids)

    query = f"""
        SELECT 
            m.message_id, m.date, m.text, m.reply_snippet, m.reply_to_msg_id,
            m.media_original_name, m.service_action_json, m.reactions_json, 
            m.forward_info_json, m.poll_data_json, m.quote_text, m.is_forum_topic, m.reply_to_top_id,
            u.first_name, u.last_name, u.username
        FROM tbl_messages m
        LEFT JOIN tbl_users u ON m.sender_id = u.user_id
        WHERE m.chat_id IN ({placeholders})
    """
    params = list(chat_ids)

    # Handle range_mode
    range_mode = options.get('range_mode', 'all')
    if range_mode == 'latest_days' and options.get('latest_days'):
        try:
            days = int(options['latest_days'])
            cursor.execute(f"SELECT MAX(date) FROM tbl_messages WHERE chat_id IN ({placeholders})", list(chat_ids))
            row_max = cursor.fetchone()
            latest_str = row_max[0] if (row_max and row_max[0]) else None
            if latest_str:
                dt_latest = datetime.fromisoformat(latest_str.replace('Z', '+00:00'))
            else:
                dt_latest = datetime.now(timezone.utc)
            from datetime import timedelta
            start_dt = dt_latest - timedelta(days=days)
            options['start_date'] = start_dt.strftime('%Y-%m-%d')
        except Exception as e_range:
            logging.error(f"Error calculating latest_days range: {e_range}")

    # Custom date filtering
    if options.get('start_date'):
        query += " AND m.date >= ?"
        params.append(options['start_date'])
    if options.get('end_date'):
        query += " AND m.date <= ?"
        params.append(options['end_date'] + "T23:59:59")

    query += " ORDER BY m.date ASC"

    cursor.execute(query, params)
    messages = cursor.fetchall()

    target_dir = custom_output_dir or OUTPUT_DIR
    os.makedirs(target_dir, exist_ok=True)
    created_files = []

    if not messages:
        print(t('no_messages', lang))
        return {'status': 'empty', 'files': [], 'exported_count': 0, 'output_dir': target_dir}

    safe_chat_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '_')).rstrip().replace(" ", "_")
    if not safe_chat_name:
        safe_chat_name = f"chat_{chat_id}"

    # Handle JSON format export
    if options.get('format') == 'json':
        filename = os.path.join(target_dir, f"{safe_chat_name}_export.json")
        json_data = []
        for msg in messages:
            author = format_author(msg['first_name'], msg['last_name'], msg['username'], lang=lang)
            item = {
                'message_id': msg['message_id'],
                'date': msg['date'],
                'author': author,
                'text': msg['text'],
                'reply_to_msg_id': msg['reply_to_msg_id'],
                'media_name': msg['media_original_name'] if options.get('show_media', True) else None
            }
            if options.get('show_reactions', True) and msg['reactions_json']:
                item['reactions'] = parse_reactions(msg['reactions_json'], users_cache, verbose=False, lang=lang)
            if options.get('show_polls', True) and msg['poll_data_json']:
                try: item['poll'] = json.loads(msg['poll_data_json'])
                except Exception: pass
            if options.get('show_forwards', True) and msg['forward_info_json']:
                try: item['forward_info'] = json.loads(msg['forward_info_json'])
                except Exception: pass
            if options.get('show_service', True) and msg['service_action_json']:
                item['service_action'] = parse_service_action(msg['service_action_json'], lang=lang)
            json_data.append(item)
        with open(filename, 'w', encoding='utf-8') as jf:
            json.dump(json_data, jf, ensure_ascii=False, indent=2)
        created_files.append(filename)
        return {'status': 'ok', 'files': created_files, 'exported_count': len(messages), 'output_dir': target_dir}

    interval = options.get('interval', 'month')
    verbose = (options.get('style', 'verbose') == 'verbose')

    print(t('exporting_chat', lang, chat_name=chat_name, count=len(messages)))
    print(t('style_interval', lang, style=options.get('style', 'verbose').upper(), interval=interval.upper()))

    current_key = None
    current_day_header = None
    file_handle = None
    exported_count = 0

    for msg in messages:
        raw_date = msg['date']
        try:
            dt = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
            part_key = get_partition_key(dt, interval)
            display_date = dt.strftime("%Y-%m-%d %H:%M:%S")
            day_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M:%S")
        except Exception:
            part_key = "Unknown_Date"
            display_date = raw_date
            day_str = "Unknown"
            time_str = raw_date

        # Switch file on snapshot/period change
        if part_key != current_key:
            if file_handle:
                file_handle.close()
            current_key = part_key
            current_day_header = None

            filename = os.path.join(target_dir, f"{safe_chat_name}_{current_key}.md")
            created_files.append(filename)
            file_handle = open(filename, 'w', encoding='utf-8')
            file_handle.write(t('md_header', lang, chat_name=chat_name, current_key=current_key))
            print(t('file_created', lang, filename=filename))

        # Grouping day header in compact mode
        if not verbose and day_str != current_day_header:
            current_day_header = day_str
            file_handle.write(f"\n### 📅 {current_day_header}\n\n")

        author = format_author(msg['first_name'], msg['last_name'], msg['username'], lang=lang)
        text = msg['text'] or ""
        reply_snippet = msg['reply_snippet']
        reply_id = msg['reply_to_msg_id']
        media = msg['media_original_name']
        service = msg['service_action_json']

        # System message handling
        if service and options.get('show_service', True):
            action_text = parse_service_action(service, lang=lang)
            if verbose:
                file_handle.write(t('system_msg_verbose', lang, display_date=display_date, author=author, action_text=action_text))
            else:
                file_handle.write(f"⚙️ `[{time_str}]` **{author}** {action_text}\n\n")
            exported_count += 1
            continue
        elif service:
            continue

        # Optional blocks
        forward_text = parse_forward_info(msg['forward_info_json'], verbose=verbose, lang=lang) if options.get('show_forwards', True) else ""
        poll_text = parse_poll_data(msg['poll_data_json'], verbose=verbose, lang=lang) if options.get('show_polls', True) else ""
        reactions = parse_reactions(msg['reactions_json'], users_cache, verbose=verbose, lang=lang) if options.get('show_reactions', True) else ""
        media_str = t('attached_media', lang, media=media) if (media and verbose and options.get('show_media', True)) else (f" `[📸 {media}]`" if (media and not verbose and options.get('show_media', True)) else "")

        topic_tag = ""
        try:
            if msg['is_forum_topic'] and msg['reply_to_top_id']:
                topic_tag = t('topic_tag', lang, topic_id=msg['reply_to_top_id'])
        except (IndexError, KeyError):
            pass

        reply_content = None
        try:
            reply_content = msg['quote_text']
        except (IndexError, KeyError):
            pass
        reply_content = reply_content or reply_snippet

        if verbose:
            md_block = f"**[{display_date}] {topic_tag}{author}:**\n"
            if options.get('show_replies', True) and reply_content:
                clean_reply = reply_content.replace('\n', ' ')
                md_block += t('in_reply_to', lang, clean_reply=clean_reply)
            if forward_text:
                md_block += forward_text
            if text:
                md_block += f"{text}\n"
            if poll_text:
                md_block += poll_text
            if media_str:
                md_block += media_str
            if reactions:
                md_block += f"{reactions}\n"
            md_block += "\n---\n\n"
        else:
            # Compact mode
            msg_id_tag = f"`[#{msg['message_id']}]`"
            reply_tag = ""
            if options.get('show_replies', True):
                if reply_id:
                    reply_tag = t('reply_to_id', lang, reply_id=reply_id)
                elif reply_content:
                    clean_rep = reply_content.replace('\n', ' ')[:40]
                    reply_tag = t('reply_to_snippet', lang, clean_rep=clean_rep)

            md_block = f"`[{time_str}]` {topic_tag}**{author}** {msg_id_tag}{reply_tag}: "
            if forward_text: md_block += forward_text
            if text: md_block += text
            if media_str: md_block += media_str
            if reactions: md_block += reactions
            if poll_text: md_block += poll_text
            md_block += "\n\n"

        file_handle.write(md_block)
        exported_count += 1

    if file_handle:
        file_handle.close()

    print(t('export_success', lang, count=exported_count, output_dir=target_dir))
    return {'status': 'ok', 'files': created_files, 'exported_count': exported_count, 'output_dir': target_dir}


def run_export_programmatic(chat_id, options, lang='en', custom_output_dir=None):
    """Programmatic API interface for exporting chats."""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT chat_name FROM tbl_chats WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    chat_name = row['chat_name'] if row else f"Chat_{chat_id}"
    users_cache = load_users_cache(cursor, lang=lang)
    conn.close()
    return export_chat_to_md(chat_id, chat_name, users_cache, options, lang=lang, custom_output_dir=custom_output_dir)


def select_language():
    """Prompt user to select interactive language."""
    print("========================================")
    print("Select language / Выберите язык:")
    print("  1. English")
    print("  2. Русский")
    print("========================================")
    choice = input("Choice / Выбор [1-2] (default 1): ").strip()
    if choice == '2' or choice.lower() in ('ru', 'russian', 'русский'):
        return 'ru'
    return 'en'


def interactive_menu(conn, lang=None):
    if not lang:
        lang = select_language()

    cursor = conn.cursor()
    print(t('loading_users', lang))
    users_cache = load_users_cache(cursor, lang=lang)
    print(t('loaded_users', lang, count=len(users_cache)))

    cursor.execute("SELECT chat_id, chat_name, migrated_to_chat_id FROM tbl_chats")
    all_chats = cursor.fetchall()
    display_chats = [c for c in all_chats if not c['migrated_to_chat_id']]

    if not display_chats:
        print(t('no_chats', lang))
        return

    print(t('menu_title', lang))
    print(t('select_chat', lang))
    for i, chat in enumerate(display_chats):
        print(f"  {i + 1}. {chat['chat_name']} (ID: {chat['chat_id']})")

    try:
        choice = int(input(t('chat_choice', lang))) - 1
        if not (0 <= choice < len(display_chats)):
            print(t('invalid_choice', lang))
            return
        selected_chat = display_chats[choice]
    except ValueError:
        print(t('nan_choice', lang))
        return

    print(t('grouping_header', lang))
    print(t('grouping_options', lang))

    g_choice = input(t('grouping_prompt', lang)).strip() or "1"
    interval_map = {"1": "month", "2": "week", "3": "day", "4": "single", "5": "custom"}
    interval = interval_map.get(g_choice, "month")

    start_date = None
    end_date = None
    if interval == "custom":
        start_date = input(t('custom_start', lang)).strip() or None
        end_date = input(t('custom_end', lang)).strip() or None

        print(t('custom_fmt_header', lang))
        print(t('custom_fmt_options', lang))
        sub_g = input(t('custom_fmt_prompt', lang)).strip() or "1"
        sub_map = {"1": "single", "2": "day", "3": "week", "4": "month"}
        interval = sub_map.get(sub_g, "single")

    print(t('detail_header', lang))
    print(t('detail_options', lang))

    fmt_choice = input(t('detail_prompt', lang)).strip() or "2"
    style = "compact" if fmt_choice == "1" else "verbose"

    print(t('filters_header', lang))

    def ask_bool(prompt, default=True):
        def_str = "Y/n" if default else "y/N"
        ans = input(f"{prompt} [{def_str}]: ").strip().lower()
        if not ans: return default
        return ans.startswith('y') or ans.startswith('д') or ans == '1'

    show_reactions = ask_bool(t('ask_reactions', lang), default=True)
    show_replies = ask_bool(t('ask_replies', lang), default=True)
    show_forwards = ask_bool(t('ask_forwards', lang), default=True)
    show_media = ask_bool(t('ask_media', lang), default=True)
    show_polls = ask_bool(t('ask_polls', lang), default=True)
    show_service = ask_bool(t('ask_service', lang), default=True)

    options = {
        'interval': interval,
        'style': style,
        'start_date': start_date,
        'end_date': end_date,
        'show_reactions': show_reactions,
        'show_replies': show_replies,
        'show_forwards': show_forwards,
        'show_media': show_media,
        'show_polls': show_polls,
        'show_service': show_service,
    }

    export_chat_to_md(selected_chat['chat_id'], selected_chat['chat_name'], users_cache, options, lang=lang)


def main():
    parser = argparse.ArgumentParser(description="Telegram DB Export Tool (db-export.py)")
    parser.add_argument("--chat-id", type=int, help="Chat ID to export")
    parser.add_argument("--interval", choices=['single', 'day', 'week', 'month'], default='month', help="File grouping partition")
    parser.add_argument("--style", choices=['compact', 'verbose'], default='verbose', help="Display style")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--lang", choices=['en', 'ru'], default=None, help="Language for export & interface (en or ru)")
    parser.add_argument("--no-reactions", action="store_true", help="Disable reactions")
    parser.add_argument("--no-replies", action="store_true", help="Disable replies")
    parser.add_argument("--no-media", action="store_true", help="Disable media")

    args = parser.parse_args()
    conn = get_db_conn()

    if args.chat_id:
        lang = args.lang or 'en'
        cursor = conn.cursor()
        users_cache = load_users_cache(cursor, lang=lang)
        cursor.execute("SELECT chat_name FROM tbl_chats WHERE chat_id = ?", (args.chat_id,))
        row = cursor.fetchone()
        chat_name = row['chat_name'] if row else f"Chat_{args.chat_id}"

        options = {
            'interval': args.interval,
            'style': args.style,
            'start_date': args.start_date,
            'end_date': args.end_date,
            'show_reactions': not args.no_reactions,
            'show_replies': not args.no_replies,
            'show_forwards': True,
            'show_media': not args.no_media,
            'show_polls': True,
            'show_service': True,
        }
        export_chat_to_md(args.chat_id, chat_name, users_cache, options, lang=lang)
    else:
        interactive_menu(conn, lang=args.lang)


if __name__ == "__main__":
    main()
