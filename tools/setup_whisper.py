#!/usr/bin/env python3
"""
tools/setup_whisper.py — Verifies Whisper environment, audio demuxing capabilities,
and creates .whisper_verified status file.
"""

import os
import sys

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

def check_whisper_setup():
    print("[*] Checking speech-to-text requirements...")

    # 1. Check faster-whisper
    try:
        import faster_whisper
        print(f"  [OK] faster-whisper is installed (v{faster_whisper.__version__})")
    except ImportError:
        print("  [FAIL] faster-whisper is not installed.")
        print("         Run: pip install faster-whisper")
        return False

    # 2. Check audio/video demuxing via PyAV or ffmpeg
    has_av = False
    try:
        import av
        print(f"  [OK] PyAV is installed (v{av.__version__}) — native demuxing available")
        has_av = True
    except ImportError:
        print("  [WARN] PyAV is not installed.")

    import shutil
    has_ffmpeg = shutil.which('ffmpeg') is not None
    if has_ffmpeg:
        print("  [OK] ffmpeg binary found on system PATH")
    else:
        print("  [INFO] ffmpeg binary not on PATH (PyAV will handle demuxing)")

    if not has_av and not has_ffmpeg:
        print("  [FAIL] Neither PyAV nor ffmpeg was found. Run: pip install av")
        return False

    # 3. Test torch / GPU
    try:
        import torch
        if torch.cuda.is_available():
            print(f"  [OK] CUDA GPU acceleration available: {torch.cuda.get_device_name(0)}")
        else:
            print("  [INFO] Running on CPU (int8 quantization will be used for high speed)")
    except Exception:
        print("  [INFO] torch not loaded directly; CPU mode default")

    verified_file = os.path.join(PROJECT_ROOT, '.whisper_verified')
    with open(verified_file, 'w', encoding='utf-8') as f:
        f.write('status=verified\n')

    print(f"✅ Whisper setup verified successfully! Wrote {verified_file}")
    return True

if __name__ == '__main__':
    ok = check_whisper_setup()
    sys.exit(0 if ok else 1)
