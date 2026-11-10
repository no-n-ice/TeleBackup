# Security Policy & Architecture

TeleBackup is a **100% local, self-hosted, offline-first backup and viewer tool** for Telegram. It connects directly to Telegram servers using MTProto client protocols via [Telethon](https://github.com/LonamiWebs/Telethon).

There are **zero cloud dependencies, no multi-tenant servers, and no telemetry or tracking**. Every user creates their own `api_id` and `api_hash` on [my.telegram.org](https://my.telegram.org) and runs the application locally on their personal machine (`127.0.0.1:8000`).

---

## Supported Versions

| Version        | Supported |
| -------------- | --------- |
| Latest release | ✅         |
| Older versions | ❌         |

---

## 1. Threat Model & Operational Assumptions

TeleBackup assumes a **single-user, local-first workstation execution model**. The primary threat vectors addressed in this security architecture include:

1. **Malicious Websites & Drive-By Web Attacks:**
   - A user visits a malicious website in their web browser while TeleBackup is running locally.
   - The malicious site attempts to interact with `http://127.0.0.1:8000` via **DNS Rebinding**, cross-origin `fetch`/`XMLHttpRequest`, or iframe embedding to read messages, steal credentials, or trigger actions.
2. **Untrusted / Hostile Telegram Payloads:**
   - Public channels, group chats, or direct messages may contain adversarial content:
     - Malicious files uploaded to Telegram (e.g. SVG or HTML files with embedded scripts, Windows executables, batch scripts).
     - Crafted message formatting entities (e.g. `code` tags with malformed language strings, custom emoji attributes, reaction emojis).
     - Polyglot image files or decompression bombs designed to exhaust memory during thumbnail generation.
     - Formula injection payloads in exported CSV files.
3. **Local Multi-User Environments:**
   - In shared OS environments, other unprivileged local users or processes might attempt to read unencrypted Telegram MTProto session files, access PINs, or SQLite databases.
4. **Path Traversal & Host File System Abuse:**
   - Malicious paths passed into file-oriented features (multi-machine DB merging, Telegram Desktop import, batch media export, or Whisper model downloads) attempting to escape target directories (`..`) or overwrite sensitive system folders (e.g., Windows Startup, AppData, System32).
5. **Supply Chain & Update Integrity:**
   - Over-The-Air (OTA) self-update mechanisms manipulated via untrusted repository overrides, branch hijacking, or archive extraction attacks (ZipSlip, zip bombs).

---

## 2. Defense-in-Depth: 11 Security Layers

To provide robust protection against these threats, TeleBackup implements 11 defense-in-depth security layers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   BROWSER / CLIENT REQUEST                             │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: DNS-Rebinding Guard (Host Allowlist: localhost / 127.0.0.1)    │
│ LAYER 1: Cross-Origin Mutation Guard (Origin / Referer Local Validation│
│ LAYER 2: Sliding-Window PIN Rate Limiter (Max 5 attempts / 60s)        │
│ LAYER 2: WebUI Access PIN & HttpOnly SameSite=Strict Session Cookie    │
│ LAYER 3: Global Anti-CSRF Token Validation (X-TeleBackup-Auth-Token)   │
│ HTTP Headers: X-Content-Type-Options: nosniff, SAMEORIGIN, no-referrer │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 4: Media Serving Sandbox CSP & Forced Attachment Download Policy │
│ LAYER 5: Pillow Image Verification & 64 MP Decompression Bomb Cap      │
│ LAYER 6: Read-Only Schema-Verified DB Merger (?mode=ro & planted DB)   │
│ LAYER 7: Path Containment, Executable Renaming (.safe_download)        │
│ LAYER 8: Telegram Credential Protection & Zero Credential Logging      │
│ LAYER 9: CSV Spreadsheet Formula Sanitization & Capped SSE Queues      │
│ LAYER 10: OTA Updater Integrity (Repo regex, --ff-only, ZipSlip guard) │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 11: Frontend DOM XSS Protection, sanitizeUrl & Pinned CDN SRI    │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Layer 1: Localhost Network & DNS Rebinding Protection

- **DNS Rebinding Guard (`Host` Header Allowlist):**
  - An attacker can register a domain with a low TTL that resolves first to their server, and subsequently rebinds to `127.0.0.1`. If a user visits that domain, the browser may send requests to the local TeleBackup port carrying the attacker's `Host` header.
  - In `app.py`, `security_middleware` inspects the HTTP `Host` header against an explicit allowlist:
    ```python
    ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
    ```
  - Any request presenting a foreign or unparseable `Host` header is immediately rejected with HTTP `400 Bad Request`.
- **Cross-Origin Mutation Guard:**
  - For state-mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`), the middleware inspects the `Origin` and `Referer` headers.
  - Requests carrying an `Origin` of `null` (e.g. from sandboxed iframes) or a foreign domain are rejected with HTTP `403 Forbidden`.
  - Only requests originating from the trusted local origin (`http://127.0.0.1:8000` or `http://localhost:8000`) are accepted.
- **Strict HTTP Security Headers:**
  - Every HTTP response is stamped with protective security headers:
    - `X-Content-Type-Options: nosniff` — prevents MIME-sniffing attacks.
    - `X-Frame-Options: SAMEORIGIN` — blocks clickjacking from external iframes.
    - `Referrer-Policy: no-referrer` — prevents URL leakage in outgoing browser requests.

---

### Layer 2: WebUI Authentication, Rate Limiting & Session Lifecycle

- **Cryptographically Secure Access PIN:**
  - On startup, TeleBackup generates a random 8-character hex PIN (`secrets.token_hex(4)`) unless explicitly specified via `TELEBACKUP_PIN` in `.env`.
  - The PIN is persisted in `.pin` with restricted permissions (`0o600`) and printed once to the local console on startup.
- **Sliding-Window IP Rate Limiter:**
  - Brute-force guessing attacks against `/api/app/verify_pin` are blocked by an in-memory sliding-window rate limiter (`_check_pin_rate_limit`).
  - Limits failed attempts to **5 tries per 60-second window**. On the 6th failure, the client receives HTTP `429 Too Many Requests` with a dynamic lockout countdown.
  - Verification of a correct PIN immediately resets the failed counter.
- **Session Cookie & Rotation:**
  - Successful PIN verification issues a `telebackup_session` cookie signed with a per-installation secret, flagged `HttpOnly`, `SameSite=Strict`, and scoped for 30 days.
  - Calling `POST /api/app/logout` rotates `SESSION_TOKEN` in memory and rewrites `.session_token` on disk, immediately invalidating all active browser sessions.

---

### Layer 3: Global Anti-CSRF Defense

- **Per-Process CSRF Secret Token:**
  - A cryptographically random 256-bit token (`AUTH_CSRF_TOKEN = secrets.token_hex(32)`) is generated at process startup and persisted in `.csrf_token`.
  - Injected into the WebUI shell via `<meta name="auth-csrf" content="{{ auth_csrf_token }}">`.
- **Constant-Time Verification:**
  - All mutating endpoints (`POST`, `PUT`, `DELETE`, `PATCH`) require the token via the `X-TeleBackup-Auth-Token` HTTP header or `auth_csrf_token` body parameter.
  - Validated using `secrets.compare_digest` to prevent timing attacks. Missing or invalid tokens return HTTP `403 CSRF_FORBIDDEN`.

---

### Layer 4: Sandboxed Media Serving & Attachment Policy

Downloaded media from Telegram channels contains untrusted binary and text content from unknown third parties. TeleBackup enforces strict sandbox controls on `/data/<path:filename>`:

1. **Sandboxed Content Security Policy (CSP):**
   - Responses for `/data/` endpoints are served with a restrictive sandbox header:
     ```http
     Content-Security-Policy: sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'
     ```
   - The absence of `allow-scripts` guarantees that even if a user browses a downloaded `.html`, `.svg`, or `.xml` file, the browser execution engine **strictly disables JavaScript execution and DOM manipulation**.
2. **Forced Attachment Download Policy:**
   - Only known safe, passive media types are rendered inline:
     - Images: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`
     - Video: `.mp4`, `.webm`
     - Audio: `.ogg`, `.oga`, `.mp3`, `.flac`, `.wav`
   - Any file outside this safe passive allowlist (e.g., `.html`, `.svg`, `.xml`, `.js`, `.pdf`, `.exe`, `.zip`) is unconditionally served with:
     ```http
     Content-Disposition: attachment; filename="<sanitized_name>"
     ```
   - This ensures the browser downloads the file rather than interpreting it as an active web document.
3. **Strict Path Containment:**
   - Every file request is resolved against `ABS_MEDIA_BACKUP_DIR` using `os.path.commonpath` to ensure directory traversal (`..`) cannot escape the media directory.

---

### Layer 5: Image Processing & Polyglot Bomb Mitigation

- **Decompression Bomb Limit:**
  - High-resolution or malicious polyglot images uploaded to Telegram or supplied as custom wallpapers could consume gigabytes of RAM during thumbnail generation or PIL decoding.
  - TeleBackup restricts Pillow image dimensions globally:
    ```python
    Image.MAX_IMAGE_PIXELS = 64_000_000  # 64 Megapixels cap
    ```
- **Re-Encoding & Format Normalization (`/api/upload_bg`):**
  - Background wallpapers uploaded via the WebUI are verified with `img.verify()`, normalized to RGB, and re-encoded to JPEG before saving to disk. This strips any embedded scripts, metadata, or polyglot payloads.

---

### Layer 6: Database Isolation & Multi-Machine DB Merger

The database merger (`merge_database_file` in `core/db_merger.py`) allows combining databases and media directories from different backup runs:

1. **Read-Only SQLite URI Mode:**
   - The source database is attached strictly in read-only mode using SQLite URIs (`file:///<path>?mode=ro`, `uri=True`). The merge engine cannot modify or corrupt the source database.
2. **SQLite Magic Header Verification:**
   - Before opening, the engine reads the first 16 bytes of the source file to verify the official SQLite 3 file header (`SQLite format 3\000`), rejecting invalid or corrupted files immediately.
3. **Self-Merge & Planted DB Defense:**
   - The merger explicitly refuses to merge the active `telegram_backup.db` into itself.
   - It also rejects merging any `.db` file located inside `media_backup/` or `.session` files, preventing attackers from planting a malicious database via Telegram file downloads.
4. **Safe Media Directory Synchronization:**
   - Media copying verifies all destination file paths remain strictly inside `ABS_MEDIA_BACKUP_DIR`.
   - Symlinks and dot-directories are rejected.
   - Files matching dangerous extensions (`.db`, `.session`, `.env`, `.py`, `.exe`, `.bat`, `.cmd`, `.sh`) are skipped during synchronization.
5. **File Permission Hardening (`restrict_file_permissions`):**
   - TeleBackup enforces `0o600` (read/write by owner only) on all sensitive files on disk:
     - `.pin`, `.csrf_token`, `.flask_secret`, `.session_token`
     - `.env`, `config.ini`
     - `*.session`, `telegram_backup.db`

---

### Layer 7: File System Traversal & Dangerous File Neutralization

- **Batch Media Export Guard (`routes/viewer.py`):**
  - When batch-exporting media via `/api/export_media_files`, the target directory is validated against sensitive host locations. The system rejects exports targeting:
    - Windows Startup directories
    - User `AppData` or `Local AppData` folders
    - System root directories (`C:\Windows`, `C:\Windows\System32`)
    - The active TeleBackup project directory
  - **Executable Neutralization (`.safe_download`):**
    - Any media file carrying an executable or scriptable extension (`.exe`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.sh`, `.scr`, `.msi`, `.jar`, `.com`) is automatically renamed with a `.safe_download` suffix upon export to prevent accidental execution by the user.
- **Telegram Desktop Import Containment (`tools/import_tg_export.py`):**
  - Verifies that the source directory is an authentic export dump (`result.json`).
  - Rejects attempts to import the active project root or system directories.
  - Ensures all imported media files are copied strictly into `media_backup/` without following symlinks.
- **Session Name Traversal Guard (`routes/auth.py`):**
  - Telegram session names are validated against a strict alphanumeric regex:
    ```python
    re.match(r"^[A-Za-z0-9_-]{1,64}$", session_name)
    ```
  - Reserved names (`config`, `telegram_backup`, `media_backup`) and path separators are prohibited, preventing unauthorized creation or deletion of arbitrary `.session` files.
- **Whisper Speech-to-Text Model Safety (`core/transcriber.py`, `routes/transcribe.py`):**
  - Download and deletion requests are validated against a strict model allowlist (`VALID_WHISPER_MODELS`).
  - Model deletion (`evict_and_delete_model`) verifies that the target folder is a direct child of the cache directory, matches Hugging Face naming conventions, and contains authentic model marker files (`model.bin`, `model.safetensors`, or `config.json`) before deletion.
  - Custom download root paths cannot point to system drives, Windows directories, or the project root.

---

### Layer 8: Telegram API & Credential Protection

- **Zero Credential Logging:**
  - Telegram SMS/app verification codes, 2FA cloud passwords, and MTProto authorization keys are never logged to console, disk, or error reports.
- **Masked Phone Numbers:**
  - Phone numbers returned via `/api/auth/status` or session inspection endpoints are masked (e.g. `+380***06`).
- **Ephemeral Authentication State:**
  - In-memory login client handshakes are stored with a 300-second TTL. The client loop is closed and credentials wiped from memory immediately upon login completion, logout, or error.
- **Session File SQLite WAL Mode:**
  - On startup, TeleBackup applies `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=15000;` directly to session databases, mitigating concurrent process locking issues.

---

### Layer 9: Anti-Abuse, Rate Limits & Injection Safeguards

- **CSV Spreadsheet Formula Injection Protection:**
  - In the Lego Block Investigator (`routes/investigator.py`), streaming CSV exports sanitize all cell contents with `_sanitize_csv_cell`.
  - Any field beginning with spreadsheet formula triggers (`=`, `+`, `-`, `@`, `\t`, `\r`) is prefixed with a single quote (`'`), neutralizing Remote Code Execution (RCE) via DDE or formula injection in Excel/Calc.
- **SSE Listener Queue Capping:**
  - To prevent memory exhaustion from orphaned EventSource connections, `worker.ARCHIVE_LISTENERS` enforces a maximum cap of **32 concurrent SSE queues**. Stale queues are automatically evicted under a thread lock.
- **HTTP Request Body Size Limits:**
  - Flask request bodies are capped globally at **32 MB** (`MAX_CONTENT_LENGTH = 32 * 1024 * 1024`), preventing unauthenticated memory exhaustion via large payload streaming.

---

### Layer 10: Supply Chain & OTA Updater Security

The self-updater (`tools/update.py` and `routes/update.py`) automates updates from GitHub with strict verification:

1. **Repository & Branch Regex Validation:**
   - Configured repository must match `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`.
   - Branch name must match `^[A-Za-z0-9_./-]+$`.
2. **Fast-Forward Git Pulls Only:**
   - Git-based updates execute `git pull --ff-only origin <branch>`, rejecting upstream history rewrites, diverged branches, and merge commits.
3. **ZIP Update Safety (ZipSlip & Bomb Prevention):**
   - ZIP updates download exclusively over HTTPS from `https://github.com/`.
   - Extraction checks every entry against `os.path.commonpath` to eliminate **ZipSlip** directory traversal.
   - Symlinks and hardlinks inside ZIP archives are rejected.
   - Total uncompressed size is capped at **250 MB**, preventing zip bomb exhaustion.

---

### Layer 11: Frontend DOM XSS Protection & Pinned Subresource Integrity (SRI)

Telegram messages can contain arbitrary untrusted text, code snippets, custom emojis, reactions, and URLs. The frontend implements defense-in-depth sanitization:

1. **Subresource Integrity (SRI) Hashes:**
   - All third-party CDN scripts in `templates/index.html` are pinned with exact SHA-384 hashes, `crossorigin="anonymous"`, and `referrerpolicy="no-referrer"`:
     - **Chart.js 4.4.8:** `sha384-ad/mnlcdvT/lwWwZQ4twScbCSXwbHqsmBhZXm1QakbchvwZeTi/qeeBbqBhLMIG/`
     - **Lottie-Web 5.12.2:** `sha384-WaqNvh6jLsmbhXbNalqup2y7bap57Bi1ymS5vviX3gu5A4lhvv4/v0W8SStzOW14`
     - **Pako 2.1.0:** `sha384-5aclu1SllAoScv5kkflZyGCEiEPssLDeoU1sMmknvUhZyuiVbVpdDr1kJb8s8Wus`
2. **Strict URL Sanitization (`window.sanitizeUrl`):**
   - Centralized sanitizer in `static/js/formatting.js` inspects all URLs before rendering into `href` attributes or `onclick` handlers:
     - Rejects dangerous schemes: `javascript:`, `data:`, `vbscript:`, and protocol-relative URLs (`//`).
     - Allows safe schemes: `http://`, `https://`, `tg://`, `mailto:`.
3. **Comprehensive HTML Entity Encoding:**
   - Formatting engine (`formatting.js`) escapes `MessageEntityPre.language`, entity IDs, custom emoji attributes, reaction emojis, and reactor names.
   - Message renderer (`messages.js`) replaces inline `onclick="window.open(...)"` with clean data attributes and sanitized click handlers. Initials in avatars, contact cards, and service actions are strictly escaped.
   - User profiles (`users.js`), participant lists (`participants.js`), admin event logs (`admin_log.js`), and folders (`folders.js`) escape all user-provided strings (usernames, ranks, kick reasons, folder titles).
   - Error messages rendered in toast notifications, modals, and status badges escape `error.message` before inserting into the DOM.

---

## 3. Security Checklist for Local Deployments

When running TeleBackup on your machine, observe these best practices:

- [x] **Keep Default Binding:** Run on `127.0.0.1:8000` (localhost). Do not bind to `0.0.0.0` or expose the port to untrusted local networks (e.g. public Wi-Fi).
- [x] **Protect `.session` Files:** Do not share or commit `*.session`, `.env`, or `config.ini` files. They contain active MTProto session credentials.
- [x] **Use the Access PIN:** Keep PIN authentication active. Do not hardcode trivial PINs in `.env`.
- [x] **Review Reverse Proxy Setup (If Used):** If accessing TeleBackup remotely over a private VPN (e.g. Tailscale, WireGuard), terminate TLS with a reverse proxy (e.g. Nginx, Caddy) and require client authentication. Pass the original `Host` header through your proxy.
- [x] **Keep FLASK_DEBUG=0:** Ensure `FLASK_DEBUG=0` in `.env` for production use. Werkzeug's interactive debugger must never be enabled on untrusted networks.

---

## 4. Reporting a Vulnerability

If you discover a security vulnerability in TeleBackup, please report it responsibly:

1. **Do NOT open a public GitHub issue.**
2. Send an advisory via GitHub Private Vulnerability Reporting (navigate to the repository's **Security** tab → click **"Report a vulnerability"**).
3. Please provide:
   - A clear description of the vulnerability and its potential impact.
   - Minimal proof of concept (PoC) or reproduction steps.
   - Recommended remediation steps, if known.

Reports will be acknowledged promptly, and security fixes will be published in a timely manner.
