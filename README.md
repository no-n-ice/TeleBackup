# TeleBackup

Ever had been kicked off a group chat that had lots of important photos? Were your DMs deleted, and you scramble to find shared memories of an important person? Or perchance, you remember someone saying something, and when you come back you see the "edit" tag, but cant remember the original message?

There is an all-in-one solution for all those problems: A local Telegram backup tool and offline web viewer built with Python (Telethon + Flask), SQLite, and vanilla JavaScript.

TeleBackup downloads your chats, channels, groups, and media to your own drive and lets you browse them through a local web interface (`127.0.0.1:8000`) that behaves like Telegram Desktop. Because the viewer reads from your local SQLite database and `media_backup/` folder, your archive stays intact and searchable even if messages are later edited, deleted, or the chat itself becomes inaccessible on Telegram, as long as the script is running during those events.

<!-- ![TeleBackup Web UI](docs/screenshots/chat_viewer.png) -->

## What It Does

* **Incremental & Resumable Backups:** Back up full chat histories or specific date ranges via the Web UI or headless CLI. If a large backup is interrupted by network drops or Telegram rate limits, you can resume it in either direction (`latest_first` or `older_first`) or run gap reconciliation to verify deleted message ranges without scanning the whole chat.
* **Granular Media Filtering:** Set size limits per media category (photos, videos, voice, round video notes, documents) globally or per chat. You can also exclude specific users, inline bots, or forwarded bot sources (such as media-downloader bots) from cluttering your disk while keeping their text messages, with an ability to download those items later.
* **24/7 Live Archiver:** Runs passively in the background to capture incoming messages, reactions, edits, and deletions in real time without sending read receipts. Previous versions of edited messages are saved in an expandable diff history, and deleted messages are visually flagged (`is_deleted`) rather than removed from your database.
* **Fast Search & Cross-Chat Investigator:** Instant full-text search powered by SQLite FTS5, date-picker navigation, and scoped in-chat search (`Ctrl+F`). For deeper digging, the built-in Investigator lets you filter across all chats by sender, forwarded origin, media category, file size, and date range, with streaming CSV export.
* **Offline Voice & Video Note Transcription:** Optional local speech-to-text using `faster-whisper` and `PyAV`. Transcribe individual voice/round notes on demand or run a background batch queue for a chat. Transcripts are indexed directly into SQLite, so spoken audio shows up in normal text searches.
* **Media Asset Inspector:** Audit a chat's media health to see what is downloaded, skipped by download rules, or missing, estimate catch-up sizes, download individual missing files, or batch-export media to a local folder.
* **Multi-Account & Multi-Machine Support:** Switch between multiple Telegram sessions directly from the UI (supports QR code login and phone/2FA). If you run backups on multiple computers, the non-destructive Database Merger (`Settings → Advanced`) can combine external SQLite databases and media folders into your main archive.
* **Local Access Protection:** Bound to localhost and gated by a 6-character optionally customizable Access PIN

---

## Quick Start

### 1. Get Telegram API Credentials

1. Log in to [my.telegram.org](https://my.telegram.org) and open **API development tools**.
2. Create an application and copy your `api_id` and `api_hash`.

### 2. Configure

If unsure how to edit files, open them via any .txt file viewer and edit that way.

Rename `config.ini.example` to `config.ini` and fill in your credentials:

```markdown
[Telegram]
api_id = your_api_id_here
api_hash = your_api_hash_here
session_name = your_imagination_here 

[Backup]
media_dir = media_backup
max_file_size_mb = 20     # this is used only for CLI mode, webUI mode has its own settings
download_workers = 3      # abstract number, but works fine. if met with TG rate restrictions, lower it
```

*(Optional)* Rename `.env.example` to `.env` file if you want to set a custom permanent Web UI PIN.

### 3. Install Dependencies

Requires Python 3.10+. Edit the `requirements.txt` to allow whisper dependencies to be downloaded, if you wish to use speech to text functionality. If unsure what's already downloaded, let it run to catch missing.

```bash
pip install -r requirements.txt
```

### 4. Run

Use `run.bat` or `run.sh`

OR

Open the cmd terminal inside the project folder, or navigate there yourself. Start the web server:

```bash
python app.py
```

1. Open `http://localhost:8000` in your browser.
2. Enter the **6-character PIN** printed in your terminal (or your custom `TELEBACKUP_PIN`).
3. Log in to your Telegram account via the Web UI using **QR Code scan** (recommended) or **Phone + Code**, or authenticate in the terminal via `python cli.py`. If webUI login fails, the terminal login should do the trick. After logging in, simply refresh the opened page and you should be all good.

For headless backups without the web server, configure `chat_ids` in `config.ini` and run:

```bash
python cli.py
```

---

## Optional: Local Speech-to-Text (Whisper)

To enable offline transcription for voice messages and circular video notes:

```bash
pip install faster-whisper av
python tools/setup_whisper.py
```

Once installed, open **Settings → Advanced → Speech-to-Text (Whisper)** in the Web UI to download, switch, or delete models (`tiny`, `base`, `small`, `medium`, `large-v3-turbo`) and set a custom model download directory. Hardware acceleration (`CUDA / float16` on NVIDIA GPUs, `CPU / int8` fallback) is selected automatically.

---

## CLI & Maintenance Tools

Auxiliary scripts live in `tools/` and can be run directly from the project root:

| Command                                     | Description                                                                                                                                                                                                               |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python tools/db_export.py`               | Export chat history to Markdown (`.md`) partitioned by day, week, month, custom date range, or single file (interactive or CLI flags; EN/RU support).                                                                   |
| `python tools/import_tg_export.py <path>` | Import an official Telegram Desktop JSON/HTML export dump (`result.json` + media) into the SQLite database.                                                                                                             |
| `python tools/dedup_hardlinks.py`         | Deduplicate identical media files (>64 KB) across chat folders using filesystem hardlinks without breaking relative paths. Files will still be accessible inside corresponding folders, but overall will take less space. |
| `python tools/setup_whisper.py`           | Install and verify `faster-whisper` and `PyAV` for local transcription.                                                                                                                                               |
| `python tools/update.py`                  | Pull the latest version from GitHub and run database migrations. Identical functionality to the webUI settings modal update.                                                                                              |

---

## Keyboard Shortcuts

* `Ctrl + K` or `/` — Global search across all chats
* `Ctrl + F` — In-chat search
* `Esc` — Close active modal, lightbox, or search panel
* `Left / Right` — Previous / next media item in lightbox
* `?` — Open shortcuts cheatsheet

---

## License

See [LICENSE](LICENSE).
