#!/usr/bin/env python3
"""
routes/update.py — Update checker and apply endpoints.
"""
import os
import re
import sys
import json
import time
import threading
import logging
from flask import Blueprint, jsonify, request
import core

VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'VERSION')
try:
    with open(VERSION_FILE, 'r', encoding='utf-8') as f:
        APP_VERSION = f.read().strip()
except Exception:
    APP_VERSION = '0.0.0'

UPDATE_CACHE = {'last_check': 0, 'data': None, 'etag': None}
_REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
_BRANCH_RE = re.compile(r'^[A-Za-z0-9_./-]+$')

def _is_valid_repo_and_branch(repo, branch):
    if not repo or not branch:
        return False
    if '..' in repo or not _REPO_RE.fullmatch(repo):
        return False
    if branch.startswith('-') or '..' in branch or '//' in branch or branch.endswith('.lock'):
        return False
    return bool(_BRANCH_RE.fullmatch(branch))

update_bp = Blueprint('update', __name__)

@update_bp.route('/api/version')
def get_version():
    return jsonify({'version': APP_VERSION})

@update_bp.route('/api/check_update')
def check_update():
    import configparser
    import urllib.request
    import urllib.error

    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    config.read(os.path.join(core.PROJECT_ROOT, 'config.ini'), encoding='utf-8')
    repo = config.get('Update', 'github_repo', fallback='no-n-ice/TeleBackup').strip()
    branch = config.get('Update', 'update_branch', fallback='main').strip()
    token = config.get('Update', 'github_token', fallback='').strip()

    force = request.args.get('force', '0').strip().lower() in ('1', 'true', 'yes')
    now = time.time()
    if not force and UPDATE_CACHE['data'] and (now - UPDATE_CACHE['last_check'] < 600):
        return jsonify(UPDATE_CACHE['data'])

    if not repo:
        return jsonify({'update_available': False, 'current': APP_VERSION, 'error': 'No repo configured'}), 200
    if not _is_valid_repo_and_branch(repo, branch):
        return jsonify({'update_available': False, 'current': APP_VERSION, 'error': 'Invalid github_repo or update_branch format'}), 200

    headers = {'User-Agent': 'TeleBackup-Viewer'}
    if token:
        headers['Authorization'] = f'Bearer {token}'

    url = f'https://api.github.com/repos/{repo}/commits/{branch}'
    req = urllib.request.Request(url, headers=headers)
    if not force and UPDATE_CACHE['etag']:
        req.add_header('If-None-Match', UPDATE_CACHE['etag'])

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            UPDATE_CACHE['etag'] = response.headers.get('ETag')
            commit_data = json.loads(response.read().decode('utf-8'))
            
            # Fetch remote VERSION file
            raw_url = f'https://raw.githubusercontent.com/{repo}/{branch}/VERSION'
            raw_req = urllib.request.Request(raw_url, headers=headers)
            with urllib.request.urlopen(raw_req, timeout=5) as raw_resp:
                remote_version = raw_resp.read().decode('utf-8').strip()

            commit_msg = commit_data.get('commit', {}).get('message', '').split('\n')[0]
            commit_date = commit_data.get('commit', {}).get('committer', {}).get('date', '')

            res_data = {
                'update_available': remote_version != APP_VERSION,
                'current': APP_VERSION,
                'latest': remote_version,
                'commit_msg': commit_msg,
                'commit_date': commit_date,
                'repo': repo,
                'branch': branch
            }
            UPDATE_CACHE['data'] = res_data
            UPDATE_CACHE['last_check'] = now
            return jsonify(res_data)
    except urllib.error.HTTPError as e:
        if e.code == 304 and UPDATE_CACHE['data']:
            UPDATE_CACHE['last_check'] = now
            return jsonify(UPDATE_CACHE['data'])
        if e.code == 404:
            err_msg = 'Repository not found or private (HTTP 404)'
        elif e.code == 403:
            err_msg = 'GitHub API rate limit exceeded (HTTP 403)'
        else:
            err_msg = f'GitHub HTTP {e.code}'
        return jsonify({'update_available': False, 'current': APP_VERSION, 'error': err_msg}), 200
    except Exception as e:
        return jsonify({'update_available': False, 'current': APP_VERSION, 'error': str(e)}), 200

@update_bp.route('/api/apply_update', methods=['POST'])
def apply_update():
    import configparser
    import subprocess
    import sys

    if getattr(core, 'IS_BULK_ARCHIVING', False):
        return jsonify({'ok': False, 'error': 'Cannot apply update while bulk archiving is active.'}), 400

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    config.read(os.path.join(root_dir, 'config.ini'), encoding='utf-8')
    repo = config.get('Update', 'github_repo', fallback='no-n-ice/TeleBackup').strip()
    branch = config.get('Update', 'update_branch', fallback='main').strip()
    if not _is_valid_repo_and_branch(repo, branch):
        return jsonify({'ok': False, 'error': 'Invalid github_repo or update_branch in config.ini'}), 400

    flag_file = os.path.join(root_dir, '_update_pending.flag')
    with open(flag_file, 'w', encoding='utf-8') as f:
        f.write('update_pending')

    # Launch updater script
    updater_script = os.path.join(root_dir, 'tools', 'update.py')
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) or subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, updater_script], creationflags=flags)
    else:
        subprocess.Popen([sys.executable, updater_script])

    def shutdown():
        time.sleep(1)
        os._exit(0)

    threading.Thread(target=shutdown, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Updater launched. Server shutting down.'})
