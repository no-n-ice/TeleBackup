#!/usr/bin/env python3
"""
core/transcriber.py — High-performance local speech-to-text engine using faster-whisper.
Supports voice notes (.ogg) and video notes (.mp4) with PyAV audio demuxing.
"""

import os
import sys
import logging
import tempfile
import shutil
import threading
import time

_WHISPER_MODELS = ['tiny', 'base', 'small', 'medium', 'large-v3-turbo']
VALID_WHISPER_MODELS = frozenset(_WHISPER_MODELS)
MODEL_ESTIMATED_BYTES = {
    'tiny': 75 * 1024 * 1024,
    'base': 142 * 1024 * 1024,
    'small': 480 * 1024 * 1024,
    'medium': 1500 * 1024 * 1024,
    'large-v3-turbo': 1600 * 1024 * 1024,
}

_DOWNLOAD_PROGRESS = {
    'model': None,
    'downloading': False,
    'downloaded_bytes': 0,
    'total_bytes': 0,
    'percent': 0,
    'speed_mbps': 0.0,
    'error': None
}
_DOWNLOAD_LOCK = threading.Lock()

_WHISPER_AVAILABLE = None
_LOADED_MODELS = {}
_MODEL_LOCK = threading.RLock()

def is_whisper_available():
    """Checks whether faster-whisper is installed and importable."""
    global _WHISPER_AVAILABLE
    if _WHISPER_AVAILABLE is not None:
        return _WHISPER_AVAILABLE
    try:
        import faster_whisper
        _WHISPER_AVAILABLE = True
    except Exception:
        _WHISPER_AVAILABLE = False
    return _WHISPER_AVAILABLE

def detect_whisper_capabilities():
    """Detects whisper availability, CUDA support and GPU device name."""
    available = is_whisper_available()
    has_cuda = False
    device_name = None
    if available:
        try:
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            pass
        if not has_cuda:
            try:
                import torch
                has_cuda = torch.cuda.is_available()
                if has_cuda:
                    device_name = torch.cuda.get_device_name(0)
            except Exception:
                pass
        else:
            try:
                import torch
                if torch.cuda.is_available():
                    device_name = torch.cuda.get_device_name(0)
            except Exception:
                pass
    return {
        'available': bool(available),
        'has_cuda': bool(has_cuda),
        'device_name': device_name
    }

def get_whisper_model(model_size='base', device=None, compute_type=None, download_root=None):
    """
    Returns a cached WhisperModel instance.
    Defaults to device='cpu' with compute_type='int8' for fast, low-memory inference,
    or 'cuda' with 'float16' if a compatible GPU is detected.
    """
    if model_size not in VALID_WHISPER_MODELS:
        raise ValueError(f"Invalid Whisper model name: {model_size}")
    if not is_whisper_available():
        raise RuntimeError("faster-whisper is not installed. Run 'pip install faster-whisper'.")

    from faster_whisper import WhisperModel

    if download_root and isinstance(download_root, str):
        download_root = download_root.strip()
        if not download_root:
            download_root = None

    cache_key = f"{model_size}_{device}_{compute_type}_{download_root}"
    with _MODEL_LOCK:
        if cache_key in _LOADED_MODELS:
            return _LOADED_MODELS[cache_key]

        if device is None or device == 'auto':
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"

        if compute_type is None or compute_type == 'auto':
            compute_type = "float16" if device == "cuda" else "int8"

        logging.info(f"[Whisper] Loading model '{model_size}' on {device} ({compute_type}) [download_root={download_root}]...")
        kwargs = {
            'model_size_or_path': model_size,
            'device': device,
            'compute_type': compute_type
        }
        if download_root:
            kwargs['download_root'] = download_root

        model = WhisperModel(**kwargs)
        _LOADED_MODELS[cache_key] = model
        return model

def extract_audio_with_pyav(video_path):
    """
    Extracts raw audio track from container (.mp4, .mov, etc.) to a temporary 16kHz mono WAV file
    using PyAV without requiring external ffmpeg binary.
    """
    import av
    import wave

    container = av.open(video_path)
    audio_stream = next((s for s in container.streams if s.type == 'audio'), None)
    if not audio_stream:
        raise ValueError(f"No audio stream found in container: {video_path}")

    resampler = av.AudioResampler(
        format='s16',
        layout='mono',
        rate=16000
    )

    temp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    temp_wav_path = temp_wav.name
    temp_wav.close()

    try:
        with wave.open(temp_wav_path, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)

            for frame in container.decode(audio_stream):
                resampled_frames = resampler.resample(frame)
                for res_frame in resampled_frames:
                    wav_file.writeframes(res_frame.to_ndarray().tobytes())

        container.close()
        return temp_wav_path
    except Exception as e:
        if os.path.exists(temp_wav_path):
            try: os.remove(temp_wav_path)
            except Exception: pass
        container.close()
        raise e

def transcribe_file(file_path, language=None, model_size='base', device=None, compute_type=None, download_root=None, progress_callback=None):
    """
    Transcribes audio or video note to text.
    Returns:
        {
            'text': str,
            'language': str,
            'duration': float,
            'segments': list[dict]
        }
    """
    if model_size not in VALID_WHISPER_MODELS:
        raise ValueError(f"Invalid Whisper model name: {model_size}")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Media file not found: {file_path}")

    model = get_whisper_model(model_size=model_size, device=device, compute_type=compute_type, download_root=download_root)

    input_audio_path = file_path
    temp_audio_to_clean = None

    ext = os.path.splitext(file_path)[1].lower()
    # If video container (round video / video note), demux audio first
    if ext in ('.mp4', '.mov', '.mkv', '.avi', '.webm'):
        try:
            temp_audio_to_clean = extract_audio_with_pyav(file_path)
            input_audio_path = temp_audio_to_clean
        except Exception as e:
            logging.warning(f"[Whisper] PyAV demux failed, falling back to direct input: {e}")
            input_audio_path = file_path

    try:
        segments, info = model.transcribe(
            input_audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        total_duration = max(0.1, round(info.duration, 2))
        seg_list = []
        text_parts = []
        for s in segments:
            seg_list.append({
                'start': round(s.start, 2),
                'end': round(s.end, 2),
                'text': s.text.strip()
            })
            text_parts.append(s.text.strip())
            if progress_callback:
                try:
                    pct = min(99, max(1, int((s.end / total_duration) * 100)))
                    progress_callback(round(s.end, 2), total_duration, pct, s.text.strip())
                except Exception:
                    pass

        full_text = ' '.join(text_parts).strip()
        if progress_callback:
            try:
                progress_callback(total_duration, total_duration, 100, full_text[-50:] if full_text else '')
            except Exception:
                pass

        return {
            'text': full_text,
            'language': info.language,
            'duration': total_duration,
            'segments': seg_list
        }
    finally:
        if temp_audio_to_clean and os.path.exists(temp_audio_to_clean):
            try: os.remove(temp_audio_to_clean)
            except Exception: pass

def format_size(bytes_val):
    if not bytes_val or bytes_val <= 0:
        return '0 MB'
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
    return f"{bytes_val / (1024 * 1024):.1f} MB"

def _get_hub_folder_name(model_name):
    """Resolves standard HuggingFace Hub cached directory name for model."""
    try:
        import faster_whisper
        repo_id = getattr(faster_whisper.utils, '_MODELS', {}).get(model_name)
        if repo_id:
            return f"models--{repo_id.replace('/', '--')}"
    except Exception:
        pass
    if 'turbo' in model_name:
        return f"models--mobiuslabsgmbh--faster-whisper-{model_name}"
    return f"models--Systran--faster-whisper-{model_name}"

def get_installed_models_info(download_root=None):
    """Scans for installed faster-whisper model snapshots and sizes."""
    if not is_whisper_available():
        return {}
    import faster_whisper
    res = {}
    active_cache = download_root if (download_root and os.path.exists(download_root)) else None

    for m in _WHISPER_MODELS:
        m_info = {
            'installed': False,
            'size_bytes': 0,
            'size_str': 'Not installed',
            'path': None,
            'estimated_size_str': format_size(MODEL_ESTIMATED_BYTES.get(m, 150 * 1024 * 1024))
        }
        p = None
        # Check active cache directory (either custom download_root or default HF cache)
        try:
            p = faster_whisper.download_model(m, cache_dir=active_cache, local_files_only=True)
        except Exception:
            p = None

        if p and os.path.exists(p):
            total_sz = 0
            for root, dirs, files in os.walk(p):
                for f in files:
                    fp = os.path.join(root, f)
                    if os.path.exists(fp):
                        try:
                            total_sz += os.path.getsize(fp)
                        except Exception:
                            pass
            if total_sz > 5 * 1024 * 1024:
                m_info['installed'] = True
                m_info['size_bytes'] = total_sz
                m_info['size_str'] = format_size(total_sz)
                m_info['path'] = p
        res[m] = m_info
    return res

def get_download_progress():
    with _DOWNLOAD_LOCK:
        return dict(_DOWNLOAD_PROGRESS)

def download_whisper_model(model_name, download_root=None):
    """Downloads model weights with live progress broadcasting over SSE."""
    if model_name not in VALID_WHISPER_MODELS:
        raise ValueError(f"Invalid Whisper model name: {model_name}")
    if not is_whisper_available():
        raise RuntimeError("faster-whisper is not installed")
    import faster_whisper

    global _DOWNLOAD_PROGRESS
    with _DOWNLOAD_LOCK:
        if _DOWNLOAD_PROGRESS['downloading']:
            return _DOWNLOAD_PROGRESS
        _DOWNLOAD_PROGRESS = {
            'model': model_name,
            'downloading': True,
            'downloaded_bytes': 0,
            'total_bytes': MODEL_ESTIMATED_BYTES.get(model_name, 150 * 1024 * 1024),
            'percent': 0,
            'speed_mbps': 0.0,
            'error': None
        }

    def _broadcast():
        try:
            from . import worker
            worker.live_event_callback("whisper_download_progress", get_download_progress())
        except Exception:
            pass

    _broadcast()

    stop_monitor = threading.Event()
    hub_folder = _get_hub_folder_name(model_name)

    def _monitor():
        prev_bytes = 0
        last_time = time.time()
        while not stop_monitor.is_set():
            time.sleep(0.3)
            # Find directory being written
            sz = 0
            # Inspect download_root or default hub cache
            targets = []
            if download_root and os.path.exists(download_root):
                targets.append(os.path.join(download_root, hub_folder))
            hf_hub = os.path.expanduser(f"~/.cache/huggingface/hub/{hub_folder}")
            if os.path.exists(hf_hub):
                targets.append(hf_hub)
            for t in targets:
                if os.path.exists(t):
                    for root, dirs, files in os.walk(t):
                        for f in files:
                            try:
                                fp = os.path.join(root, f)
                                sz += os.path.getsize(fp)
                            except Exception:
                                pass
            now = time.time()
            dt = max(0.1, now - last_time)
            speed = max(0.0, ((sz - prev_bytes) / (1024 * 1024)) / dt)
            prev_bytes = sz
            last_time = now
            est_total = MODEL_ESTIMATED_BYTES.get(model_name, 150 * 1024 * 1024)
            pct = min(98, max(1, int((sz / max(est_total, sz, 1)) * 100))) if sz > 0 else 0
            with _DOWNLOAD_LOCK:
                _DOWNLOAD_PROGRESS['downloaded_bytes'] = sz
                _DOWNLOAD_PROGRESS['speed_mbps'] = round(speed, 2)
                _DOWNLOAD_PROGRESS['percent'] = pct
            _broadcast()

    mon_thread = threading.Thread(target=_monitor, daemon=True)
    mon_thread.start()

    try:
        logging.info(f"[Whisper] Starting background download of model '{model_name}' [cache_dir={download_root}]...")
        path = faster_whisper.download_model(model_name, cache_dir=download_root)
        stop_monitor.set()
        mon_thread.join(timeout=1.0)
        total_sz = 0
        if path and os.path.exists(path):
            for root, dirs, files in os.walk(path):
                for f in files:
                    try: total_sz += os.path.getsize(os.path.join(root, f))
                    except Exception: pass
        with _DOWNLOAD_LOCK:
            _DOWNLOAD_PROGRESS['downloading'] = False
            _DOWNLOAD_PROGRESS['downloaded_bytes'] = total_sz
            _DOWNLOAD_PROGRESS['total_bytes'] = total_sz
            _DOWNLOAD_PROGRESS['percent'] = 100
            _DOWNLOAD_PROGRESS['speed_mbps'] = 0.0
        _broadcast()
        return path
    except Exception as e:
        stop_monitor.set()
        mon_thread.join(timeout=1.0)
        with _DOWNLOAD_LOCK:
            _DOWNLOAD_PROGRESS['downloading'] = False
            _DOWNLOAD_PROGRESS['error'] = str(e)
        _broadcast()
        raise e

def _safe_rmtree_model_dir(target_dir, allowed_root, require_model_marker=False):
    if not target_dir or not allowed_root:
        return False
    if os.path.islink(target_dir) or not os.path.isdir(target_dir):
        return False
    try:
        real_root = os.path.realpath(allowed_root)
        real_target = os.path.realpath(target_dir)
        if real_target == real_root or os.path.commonpath([real_root, real_target]) != real_root:
            return False
    except Exception:
        return False
    if require_model_marker:
        markers = ('model.bin', 'config.json', 'vocabulary.txt', 'vocabulary.json')
        if not any(os.path.isfile(os.path.join(real_target, m)) for m in markers):
            return False
    shutil.rmtree(real_target)
    return True

def evict_and_delete_model(model_name, download_root=None):
    """Evicts model from memory cache and removes snapshot files from disk."""
    if model_name not in VALID_WHISPER_MODELS:
        raise ValueError(f"Invalid Whisper model name: {model_name}")

    import gc
    global _LOADED_MODELS
    # 1. Evict from memory
    with _MODEL_LOCK:
        to_delete_keys = [k for k in _LOADED_MODELS.keys() if k.startswith(f"{model_name}_")]
        for k in to_delete_keys:
            try:
                del _LOADED_MODELS[k]
            except KeyError:
                pass
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    # 2. Delete files from disk
    deleted_paths = []
    hub_folder = _get_hub_folder_name(model_name)
    candidate_folders = {hub_folder, f"models--Systran--faster-whisper-{model_name}", f"models--mobiuslabsgmbh--faster-whisper-{model_name}"}

    # Check default HF cache
    hf_hub_root = os.path.expanduser("~/.cache/huggingface/hub")
    if os.path.isdir(hf_hub_root):
        for fld in candidate_folders:
            hf_hub = os.path.join(hf_hub_root, fld)
            if os.path.exists(hf_hub):
                try:
                    if _safe_rmtree_model_dir(hf_hub, hf_hub_root, require_model_marker=False):
                        deleted_paths.append(hf_hub)
                except Exception as e:
                    logging.error(f"[Whisper] Failed to remove HF hub cache for {model_name} ({fld}): {e}")

    # Check custom download_root
    if download_root and os.path.isdir(download_root) and not os.path.islink(download_root):
        for fld in candidate_folders:
            custom_sub = os.path.join(download_root, fld)
            if os.path.exists(custom_sub):
                try:
                    if _safe_rmtree_model_dir(custom_sub, download_root, require_model_marker=False):
                        deleted_paths.append(custom_sub)
                except Exception as e:
                    logging.error(f"[Whisper] Failed to remove custom download_root model {model_name} ({fld}): {e}")
        direct_sub = os.path.join(download_root, model_name)
        if os.path.exists(direct_sub):
            try:
                if _safe_rmtree_model_dir(direct_sub, download_root, require_model_marker=True):
                    deleted_paths.append(direct_sub)
            except Exception as e:
                logging.error(f"[Whisper] Failed to remove direct subfolder model {model_name}: {e}")

    return {
        'status': 'ok',
        'model': model_name,
        'evicted_memory': len(to_delete_keys),
        'deleted_paths': deleted_paths
    }
