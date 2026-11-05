// static/js/whisper_ui.js — Speech-to-Text (faster-whisper) Hardware Detection, Model Catalog & Settings UI

// =========================================================================

// SPEECH-TO-TEXT (WHISPER) SETTINGS & LIFECYCLE MANAGEMENT

// =========================================================================



window.loadWhisperSettings = async function() {

    try {

        const res = await fetch('/api/transcribe/status');

        if (!res.ok) return;

        const data = await res.json();

        

        const badge = document.getElementById('whisper-hardware-badge');

        if (badge) {

            if (!data.available) {

                badge.textContent = 'Not Available (faster-whisper missing)';

                badge.style.background = 'rgba(244, 67, 54, 0.15)';

                badge.style.color = '#f44336';

            } else if (data.has_cuda) {

                badge.textContent = `CUDA: ${data.device_name || 'GPU'} (float16)`;

                badge.style.background = 'rgba(76, 175, 80, 0.15)';

                badge.style.color = '#4caf50';

            } else {

                badge.textContent = 'CPU (int8)';

                badge.style.background = 'rgba(33, 150, 243, 0.15)';

                badge.style.color = '#2196f3';

            }

        }



        const pathInput = document.getElementById('whisper-storage-path');

        if (pathInput && data.config) {

            pathInput.value = data.config.download_root || '';

        }



        const catalog = document.getElementById('whisper-model-catalog');

        if (catalog && data.models) {

            catalog.innerHTML = '';

            const activeModel = data.config ? data.config.model : 'base';

            

            const dlProg = data.download_progress || {};
            const isCurrentlyDownloading = Boolean(dlProg.downloading);

            data.models.forEach(m => {

                const info = (data.models_info && data.models_info[m]) || {};

                const isActive = (m === activeModel);

                const isInstalled = Boolean(info.installed);
                const isDownloadingThis = isCurrentlyDownloading && (dlProg.model === m);

                const sizeStr = (info.size_str && info.size_str !== 'Not installed') ? info.size_str : (info.estimated_size_str || '~150 MB');

                const card = document.createElement('div');
                card.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); border-radius: 6px; font-size: 12px;';
                
                const leftDiv = document.createElement('div');
                leftDiv.style.cssText = 'display: flex; align-items: center; gap: 8px;';
                
                const nameSpan = document.createElement('span');
                nameSpan.style.fontWeight = '600';
                nameSpan.textContent = m;
                leftDiv.appendChild(nameSpan);

                const statusPill = document.createElement('span');
                statusPill.style.fontSize = '10px';
                statusPill.style.padding = '2px 6px';
                statusPill.style.borderRadius = '4px';

                if (isDownloadingThis) {
                    const curMb = (dlProg.downloaded_bytes / (1024 * 1024)).toFixed(1);
                    const totMb = (dlProg.total_bytes / (1024 * 1024)).toFixed(1);
                    const pct = dlProg.percent || 0;
                    const spd = dlProg.speed_mbps || 0;
                    statusPill.textContent = `Downloading ${curMb}/${totMb} MB (${pct}%) · ${spd} MB/s`;
                    statusPill.style.background = 'rgba(33, 150, 243, 0.25)';
                    statusPill.style.color = '#2196f3';
                    if (window.startWhisperDownloadPolling) window.startWhisperDownloadPolling(m);
                } else if (isActive) {
                    if (isInstalled) {
                        statusPill.textContent = `Active · ${sizeStr}`;
                        statusPill.style.background = 'rgba(76, 175, 80, 0.2)';
                        statusPill.style.color = '#4caf50';
                    } else {
                        statusPill.textContent = `Active (Not Downloaded · ${sizeStr})`;
                        statusPill.style.background = 'rgba(255, 152, 0, 0.2)';
                        statusPill.style.color = '#ff9800';
                    }
                } else if (isInstalled) {
                    statusPill.textContent = `Installed (${sizeStr})`;
                    statusPill.style.background = 'rgba(33, 150, 243, 0.15)';
                    statusPill.style.color = '#2196f3';
                } else {
                    statusPill.textContent = `Not Downloaded (${sizeStr})`;
                    statusPill.style.background = 'rgba(255, 255, 255, 0.08)';
                    statusPill.style.color = 'var(--text-color-light)';
                }
                leftDiv.appendChild(statusPill);

                card.appendChild(leftDiv);



                const actionsDiv = document.createElement('div');

                actionsDiv.style.cssText = 'display: flex; gap: 6px; align-items: center;';



                if (!isActive) {

                    const setBtn = document.createElement('button');

                    setBtn.className = 'action-btn';

                    setBtn.style.cssText = 'padding: 2px 8px; font-size: 11px; background: rgba(33, 150, 243, 0.2); color: #2196f3;';

                    setBtn.textContent = 'Set Active';

                    setBtn.onclick = async () => {

                        await fetch('/api/transcribe/settings', {

                            method: 'POST',

                            headers: { 'Content-Type': 'application/json', 'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') },

                            body: JSON.stringify({ model: m })

                        });

                        window.loadWhisperSettings();

                    };

                    actionsDiv.appendChild(setBtn);

                }



                if (!isInstalled) {
                    const dlBtn = document.createElement('button');
                    dlBtn.className = 'action-btn';
                    dlBtn.style.cssText = 'padding: 2px 8px; font-size: 11px; background: rgba(76, 175, 80, 0.2); color: #4caf50;';
                    dlBtn.textContent = isDownloadingThis ? 'Downloading...' : 'Pre-download';
                    if (isDownloadingThis) dlBtn.disabled = true;

                    dlBtn.onclick = async () => {
                        dlBtn.disabled = true;
                        dlBtn.textContent = 'Downloading...';
                        try {
                            await fetch('/api/transcribe/download_start', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json', 'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') },
                                body: JSON.stringify({ model: m })
                            });
                            if (typeof window.handleWhisperDownloadProgress === 'function') {
                                window.handleWhisperDownloadProgress({
                                    downloading: true,
                                    model: m,
                                    percent: 1,
                                    downloaded_bytes: 0,
                                    total_bytes: 150 * 1024 * 1024,
                                    speed_mbps: 0.0
                                });
                            }
                            if (typeof window.startWhisperDownloadPolling === 'function') {
                                window.startWhisperDownloadPolling(m);
                            }
                        } catch (err) {
                            console.error('Download start error:', err);
                            dlBtn.disabled = false;
                            dlBtn.textContent = 'Pre-download';
                        }
                    };
                    actionsDiv.appendChild(dlBtn);
                } else if (!isActive) {

                    const delBtn = document.createElement('button');

                    delBtn.className = 'action-btn';

                    delBtn.style.cssText = 'padding: 2px 8px; font-size: 11px; background: rgba(244, 67, 54, 0.15); color: #f44336;';

                    delBtn.textContent = 'Delete';

                    delBtn.onclick = async () => {

                        if (!confirm(`Delete model '${m}' to free disk space?`)) return;

                        await fetch('/api/transcribe/delete', {

                            method: 'POST',

                            headers: { 'Content-Type': 'application/json', 'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') },

                            body: JSON.stringify({ model: m })

                        });

                        window.loadWhisperSettings();

                    };

                    actionsDiv.appendChild(delBtn);

                }



                card.appendChild(actionsDiv);

                catalog.appendChild(card);

            });

        }

    } catch (e) {

        console.error("Failed to load whisper settings:", e);

    }

};



// Handle Whisper Storage Path Save

document.getElementById('whisper-save-path-btn')?.addEventListener('click', async () => {

    const input = document.getElementById('whisper-storage-path');

    if (!input) return;

    const pathVal = input.value.trim();

    try {

        await fetch('/api/transcribe/settings', {

            method: 'POST',

            headers: { 'Content-Type': 'application/json', 'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') },

            body: JSON.stringify({ download_root: pathVal })

        });

        if (typeof showNotification === 'function') {

            showNotification('Whisper storage path saved!', 'success');

        }

        window.loadWhisperSettings();

    } catch (e) {

        console.error("Failed to save whisper path:", e);

    }

});



// Intercept SSE & Polling for whisper_download_progress
let _whisperDlPollTimer = null;

function startWhisperDownloadPolling(modelName) {
    if (_whisperDlPollTimer) return;
    _whisperDlPollTimer = setInterval(async () => {
        try {
            const res = await fetch('/api/transcribe/status');
            const st = await res.json();
            const dp = st.download_progress;
            if (dp && dp.downloading) {
                handleWhisperDownloadProgress(dp);
            } else {
                if (_whisperDlPollTimer) {
                    clearInterval(_whisperDlPollTimer);
                    _whisperDlPollTimer = null;
                }
                handleWhisperDownloadProgress({ downloading: false, percent: 100, model: modelName || (dp ? dp.model : '') });
                if (typeof window.loadWhisperSettings === 'function') {
                    window.loadWhisperSettings();
                }
            }
        } catch (e) {
            console.error('Whisper dl poll error:', e);
        }
    }, 700);
}
window.startWhisperDownloadPolling = startWhisperDownloadPolling;

function handleWhisperDownloadProgress(data) {
    if (!data) return;
    const modal = document.getElementById('whisper-download-modal');

    if (data.downloading) {
        if (modal) {
            modal.style.display = 'flex';
            const modelNameEl = document.getElementById('whisper-dl-model-name');
            if (modelNameEl) modelNameEl.textContent = `Model: '${data.model}'`;
            
            const bar = document.getElementById('whisper-dl-progress-bar');
            if (bar) bar.style.width = `${data.percent || 0}%`;

            const bytesEl = document.getElementById('whisper-dl-bytes');
            if (bytesEl) {
                const curMb = (data.downloaded_bytes / (1024 * 1024)).toFixed(1);
                const totMb = (data.total_bytes / (1024 * 1024)).toFixed(1);
                bytesEl.textContent = `${curMb} MB / ${totMb} MB (${data.percent || 0}%)`;
            }

            const speedEl = document.getElementById('whisper-dl-speed');
            if (speedEl) speedEl.textContent = `${data.speed_mbps || 0} MB/s`;
        }
    } else if (data.percent >= 100 || !data.downloading) {
        if (_whisperDlPollTimer) {
            clearInterval(_whisperDlPollTimer);
            _whisperDlPollTimer = null;
        }
        if (modal && modal.style.display !== 'none') {
            const bar = document.getElementById('whisper-dl-progress-bar');
            if (bar) bar.style.width = '100%';
            const bytesEl = document.getElementById('whisper-dl-bytes');
            if (bytesEl) bytesEl.textContent = 'Download Complete! (100%)';
            setTimeout(() => {
                modal.style.display = 'none';
                if (typeof window.loadWhisperSettings === 'function') window.loadWhisperSettings();
            }, 800);
        } else {
            if (typeof window.loadWhisperSettings === 'function') window.loadWhisperSettings();
        }
    }
}
window.handleWhisperDownloadProgress = handleWhisperDownloadProgress;

// =========================================================================
// MASS SPEECH-TO-TEXT (BATCH TRANSCRIBE) TRIGGER
// =========================================================================

window.triggerBatchTranscribe = async function(chatId) {
    if (!chatId) chatId = window.currentChatId;
    if (!chatId) return;

    const chat = (window.chatListData || []).find(c => String(c.chat_id) === String(chatId));
    const chatTitle = chat ? (chat.chat_name || `Chat ${chatId}`) : `Chat ${chatId}`;

    const confirmMsg = (typeof I18N !== 'undefined' && typeof I18N.t === 'function')
        ? I18N.t('whisper.batch_confirm', chatTitle)
        : `Start mass speech-to-text (Whisper) for all audio and voice messages in "${chatTitle}"? Untranscribed messages will be processed in the background.`;

    if (!confirm(confirmMsg)) return;

    try {
        const csrf = (typeof window.getCsrfToken === 'function') ? window.getCsrfToken() : (window.CSRF_TOKEN || '');
        const res = await fetch('/api/transcribe/batch_start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-TeleBackup-Auth-Token': csrf
            },
            body: JSON.stringify({
                chat_id: chatId,
                auth_csrf_token: csrf
            })
        });
        const data = await res.json();
        if (res.ok && data.status === 'queued') {
            const note = (typeof I18N !== 'undefined' && typeof I18N.t === 'function')
                ? I18N.t('whisper.batch_queued', chatTitle)
                : `🎙️ Mass STT task queued for "${chatTitle}"`;
            if (typeof showNotification === 'function') {
                showNotification(note, 'info');
            } else {
                alert(note);
            }
        } else {
            const failMsg = (typeof I18N !== 'undefined' && typeof I18N.t === 'function')
                ? I18N.t('whisper.batch_failed', data.error || 'Unknown error')
                : `Failed to start mass STT: ${data.error || 'Unknown error'}`;
            alert(failMsg);
        }
    } catch (err) {
        alert(`Error starting mass STT: ${err.message}`);
    }
};
