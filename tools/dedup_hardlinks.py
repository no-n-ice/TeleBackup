#!/usr/bin/env python3
"""
tools/dedup_hardlinks.py — Scans media files across chat backup folders, detects identical files,
and replaces duplicates with NTFS hardlinks (os.link).

Usage:
    python tools/dedup_hardlinks.py             # Dry-run (computes duplicates & space savings)
    python tools/dedup_hardlinks.py --execute   # Commits hardlinks
"""

import os
import sys
import argparse
import hashlib
from collections import defaultdict

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import core

def get_file_hash(filepath, block_size=65536):
    """Computes SHA-256 for accurate content verification."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while True:
                buf = f.read(block_size)
                if not buf:
                    break
                hasher.update(buf)
        return hasher.hexdigest()
    except Exception:
        return None

def dedup_hardlinks(execute=False, min_size_bytes=1024):
    media_dir = core.ABS_MEDIA_BACKUP_DIR
    if not os.path.exists(media_dir):
        print(f"❌ Media directory not found: {media_dir}")
        return

    print(f"[*] Scanning media files in {media_dir}...")
    size_map = defaultdict(list)
    total_scanned = 0

    for root, dirs, files in os.walk(media_dir):
        # Skip thumbs folder from hardlinking
        if os.path.basename(root) == 'thumbs':
            continue
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
                if st.st_size >= min_size_bytes:
                    size_map[st.st_size].append((p, st.st_ino, st.st_dev))
                    total_scanned += 1
            except Exception:
                pass

    print(f"[*] Scanned {total_scanned} files. Identifying potential size collisions...")

    # Filter sizes that have at least 2 files
    candidate_sizes = {s: files for s, files in size_map.items() if len(files) > 1}
    print(f"[*] Found {len(candidate_sizes)} size buckets with duplicate candidates.")

    duplicate_pairs = []
    total_reclaimable_bytes = 0

    for sz, file_list in candidate_sizes.items():
        # Sub-group by SHA-256 hash
        hash_map = defaultdict(list)
        for filepath, ino, dev in file_list:
            h = get_file_hash(filepath)
            if h:
                hash_map[h].append((filepath, ino, dev))

        for h, matches in hash_map.items():
            if len(matches) > 1:
                master = matches[0]
                master_path, master_ino, master_dev = master

                for dup_path, dup_ino, dup_dev in matches[1:]:
                    # Check if already hardlinked on same volume
                    if master_ino == dup_ino and master_dev == dup_dev:
                        continue  # Already shares same inode/clusters
                    
                    duplicate_pairs.append((master_path, dup_path, sz))
                    total_reclaimable_bytes += sz

    reclaim_mb = total_reclaimable_bytes / (1024 * 1024)
    reclaim_gb = reclaim_mb / 1024
    print(f"[*] Found {len(duplicate_pairs)} duplicate file pairs across folders.")
    print(f"[*] Potential storage to reclaim: {reclaim_mb:.2f} MB ({reclaim_gb:.3f} GB).")

    if not execute:
        print("\n[DRY RUN] No files were changed.")
        print("Run with --execute to commit NTFS hardlinks.")
        return

    print("\n[EXECUTE] Creating NTFS hardlinks...")
    success_count = 0
    fail_count = 0

    for master_path, dup_path, sz in duplicate_pairs:
        tmp_link = dup_path + ".tmp_hlink"
        try:
            if os.path.exists(tmp_link):
                os.remove(tmp_link)
            os.link(master_path, tmp_link)
            os.replace(tmp_link, dup_path)
            success_count += 1
        except Exception as e:
            if os.path.exists(tmp_link):
                try: os.remove(tmp_link)
                except Exception: pass
            fail_count += 1

    print(f"✅ Deduplication complete! Replaced {success_count} duplicate files with NTFS hardlinks.")
    if fail_count > 0:
        print(f"⚠️ {fail_count} files could not be linked (e.g. cross-volume or permission error).")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Deduplicate identical media files using NTFS hardlinks.")
    parser.add_argument('--execute', action='store_true', help="Execute hardlinks (default is dry-run)")
    parser.add_argument('--min-size', type=int, default=10240, help="Minimum file size in bytes (default: 10KB)")
    args = parser.parse_args()
    dedup_hardlinks(execute=args.execute, min_size_bytes=args.min_size)
