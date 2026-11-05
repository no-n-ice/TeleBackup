#!/usr/bin/env python3
import os
import re
import sys
import time
import shutil
import zipfile
import subprocess
import configparser
import urllib.request
from pathlib import PurePath

_REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
_BRANCH_RE = re.compile(r'^[A-Za-z0-9_./-]+$')
_MAX_ZIP_EXTRACT_BYTES = 250 * 1024 * 1024
_MAX_ZIP_ENTRIES = 10000

def is_valid_update_source(repo, branch):
    if not repo or not branch:
        return False
    if '..' in repo or not _REPO_RE.fullmatch(repo):
        return False
    if branch.startswith('-') or '..' in branch or '//' in branch or branch.endswith('.lock'):
        return False
    if not _BRANCH_RE.fullmatch(branch):
        return False
    return True

def safe_extract_zip(zip_ref, extract_dir):
    """Extracts a ZIP archive while blocking ZipSlip path traversal, symlinks, and decompression bombs."""
    shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)
    real_extract = os.path.realpath(extract_dir)

    infos = zip_ref.infolist()
    if len(infos) > _MAX_ZIP_ENTRIES:
        raise ValueError(f"ZIP archive contains too many entries ({len(infos)} > {_MAX_ZIP_ENTRIES})")

    total_bytes = 0
    for info in infos:
        member_name = info.filename
        if not member_name:
            continue
        norm_parts = PurePath(member_name.replace('\\', '/')).parts
        if not norm_parts or norm_parts[0] in ('/', '\\') or '..' in norm_parts:
            raise ValueError(f"Unsafe ZIP member path rejected: {member_name}")

        # Reject POSIX symlinks stored in ZIP external attributes
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise ValueError(f"Symlink ZIP entry rejected: {member_name}")

        total_bytes += max(0, int(info.file_size or 0))
        if total_bytes > _MAX_ZIP_EXTRACT_BYTES:
            raise ValueError("ZIP archive exceeds maximum allowed uncompressed size (250 MB)")

        dest_path = os.path.join(real_extract, *norm_parts)
        real_dest = os.path.realpath(dest_path)
        if real_dest != real_extract and os.path.commonpath([real_extract, real_dest]) != real_extract:
            raise ValueError(f"ZipSlip path traversal blocked: {member_name}")

        if info.is_dir() or member_name.endswith('/'):
            os.makedirs(real_dest, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(real_dest), exist_ok=True)
            with zip_ref.open(info, 'r') as src_stream, open(real_dest, 'wb') as dst_stream:
                copied = 0
                while True:
                    chunk = src_stream.read(65536)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > _MAX_ZIP_EXTRACT_BYTES:
                        raise ValueError("ZIP member exceeded maximum allowed uncompressed size")
                    dst_stream.write(chunk)

EXCLUDE_PATHS = {
    'config.ini',
    'backup_session.session',
    'telegram_backup.db',
    'live_stream_backup.db',
    'media_backup',
    'dialog_cache.json',
    'sessions_meta.json',
    '.stuff',
    '.agents',
    'PLANS',
    'scratch',
    '_update_pending.flag',
    '.env',
    '.pin',
    '.csrf_token',
    '.flask_secret',
    '.session_token',
    '.whisper_verified',
    'db_exports',
    'exports',
}

def is_path_excluded(rel_file_path):
    if not rel_file_path:
        return False
    parts = rel_file_path.split(os.sep)
    base = parts[-1].lower()
    top = parts[0]
    if top in EXCLUDE_PATHS or rel_file_path in EXCLUDE_PATHS or base in EXCLUDE_PATHS:
        return True
    if any(rel_file_path.startswith(ex + os.sep) for ex in EXCLUDE_PATHS):
        return True
    if '.session' in base:
        return True
    if base.endswith('.db') or '.db-' in base or base.endswith('.sqlite') or base.endswith('.sqlite3'):
        return True
    if base.startswith('dialog_cache') and base.endswith('.json'):
        return True
    if base.startswith('.env') and base != '.env.example':
        return True
    return False

def log(msg):
    print(f"[Updater] {msg}")

def main():
    log("Waiting for main Flask server to exit...")
    time.sleep(2)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(current_dir).lower() == 'tools':
        project_dir = os.path.dirname(current_dir)
    else:
        project_dir = current_dir
    config_path = os.path.join(project_dir, 'config.ini')

    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')

    repo = config.get('Update', 'github_repo', fallback='no-n-ice/TeleBackup').strip()
    branch = config.get('Update', 'update_branch', fallback='main').strip()

    if not is_valid_update_source(repo, branch):
        log(f"Invalid github_repo ('{repo}') or update_branch ('{branch}') in config.ini. Aborting update.")
        flag_file = os.path.join(project_dir, '_update_pending.flag')
        if os.path.exists(flag_file):
            try: os.remove(flag_file)
            except Exception: pass
        return

    git_updated = False
    if os.path.exists(os.path.join(project_dir, '.git')):
        try:
            log("Attempting git pull...")
            res = subprocess.run(['git', 'pull', '--ff-only', 'origin', branch], cwd=project_dir, capture_output=True, text=True)
            if res.returncode == 0:
                log("Git pull successful!")
                git_updated = True
            else:
                log(f"Git pull failed: {res.stderr}. Falling back to ZIP download...")
        except Exception as e:
            log(f"Git execution failed: {e}. Falling back to ZIP download...")

    if not git_updated:
        zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
        if not zip_url.startswith('https://github.com/'):
            log("Refusing non-HTTPS GitHub update URL.")
            return
        zip_path = os.path.join(project_dir, '_update_temp.zip')
        extract_dir = os.path.join(project_dir, '_update_temp_extracted')
        real_project = os.path.realpath(project_dir)

        try:
            log(f"Downloading update ZIP from {zip_url}...")
            req = urllib.request.Request(zip_url, headers={'User-Agent': 'TeleBackup-Updater'})
            with urllib.request.urlopen(req, timeout=30) as resp, open(zip_path, 'wb') as out_file:
                shutil.copyfileobj(resp, out_file)

            log("Extracting update package...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract_zip(zip_ref, extract_dir)

            # Locate inner directory inside ZIP
            extracted_subdirs = [os.path.join(extract_dir, d) for d in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, d))]
            source_root = extracted_subdirs[0] if extracted_subdirs else extract_dir

            log("Overwriting updated files...")
            for root, dirs, files in os.walk(source_root, followlinks=False):
                rel_path = os.path.relpath(root, source_root)
                if '..' in PurePath(rel_path).parts:
                    continue
                target_dir = os.path.normpath(os.path.join(project_dir, rel_path))
                if os.path.commonpath([real_project, os.path.realpath(target_dir)]) != real_project:
                    continue

                if rel_path == '.':
                    rel_path = ''

                top_part = rel_path.split(os.sep)[0] if rel_path else ''
                if is_path_excluded(top_part):
                    continue

                if not os.path.exists(target_dir):
                    os.makedirs(target_dir, exist_ok=True)

                for file in files:
                    rel_file_path = os.path.normpath(os.path.join(rel_path, file)) if rel_path else file
                    if '..' in PurePath(rel_file_path).parts or is_path_excluded(rel_file_path):
                        continue

                    src_file = os.path.join(root, file)
                    dst_file = os.path.join(project_dir, rel_file_path)
                    if os.path.islink(src_file) or os.path.islink(dst_file):
                        continue
                    if os.path.commonpath([real_project, os.path.realpath(os.path.dirname(dst_file))]) != real_project:
                        continue

                    try:
                        shutil.copy2(src_file, dst_file)
                    except Exception as err:
                        log(f"Failed to overwrite {rel_file_path}: {err}")

            log("Clean up temporary download files...")
            shutil.rmtree(extract_dir, ignore_errors=True)
            if os.path.exists(zip_path):
                os.remove(zip_path)

        except Exception as e:
            log(f"ZIP update failed: {e}")

    # Run pip install if requirements.txt exists
    req_file = os.path.join(project_dir, 'requirements.txt')
    if os.path.exists(req_file):
        try:
            log("Updating dependencies (pip install)...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req_file, '--quiet'], check=False)
        except Exception as e:
            log(f"Pip install warning: {e}")

    # Remove flag
    flag_file = os.path.join(project_dir, '_update_pending.flag')
    if os.path.exists(flag_file):
        try:
            os.remove(flag_file)
        except Exception:
            pass

    log("Restarting main application app.py...")
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) or subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, 'app.py'], cwd=project_dir, creationflags=flags)
    else:
        subprocess.Popen([sys.executable, 'app.py'], cwd=project_dir)

    log("Update complete!")

if __name__ == '__main__':
    main()
