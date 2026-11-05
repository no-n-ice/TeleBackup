// static/js/resume.js — Dual-Strategy Resume Download Modal & O(1) HOR Gap Reconciliation Triggers

/* ==========================================================================

   TWO-WAY RESUME DOWNLOAD ENGINE MODAL & CONTROLLER

   ========================================================================== */

window._currentResumeChatId = null;



window.selectResumeStrategy = function (strategy) {

    const cardLatest = document.getElementById('strategy-card-latest');

    const cardOlder = document.getElementById('strategy-card-older');

    const radioLatest = cardLatest ? cardLatest.querySelector('input[type="radio"]') : null;

    const radioOlder = cardOlder ? cardOlder.querySelector('input[type="radio"]') : null;



    if (strategy === 'older_first') {

        if (cardOlder) cardOlder.classList.add('selected');

        if (cardLatest) cardLatest.classList.remove('selected');

        if (radioOlder) radioOlder.checked = true;

    } else {

        if (cardLatest) cardLatest.classList.add('selected');

        if (cardOlder) cardOlder.classList.remove('selected');

        if (radioLatest) radioLatest.checked = true;

    }

};



window.closeResumeModal = function () {

    const modal = document.getElementById('modal-resume-download');

    if (modal) modal.style.display = 'none';

    window._currentResumeChatId = null;

};



window.openResumeModal = async function (chatId, defaultStrategy = 'latest_first') {

    if (!chatId) return;

    window._currentResumeChatId = chatId;



    const modal = document.getElementById('modal-resume-download');

    if (!modal) return;



    window.selectResumeStrategy(defaultStrategy);



    // Populate chat name

    let chatName = `Chat #${chatId}`;

    if (window.allDialogsData) {

        const found = window.allDialogsData.find(d => String(d.id) === String(chatId));

        if (found && found.name) chatName = found.name;

    }

    if (chatName === `Chat #${chatId}` && window.chatListData) {

        const found = window.chatListData.find(c => String(c.chat_id) === String(chatId));

        if (found && found.chat_name) chatName = found.chat_name;

    }



    const nameEl = document.getElementById('resume-modal-chat-name');

    if (nameEl) nameEl.textContent = chatName;



    const cachedEl = document.getElementById('resume-summary-cached');

    const serverEl = document.getElementById('resume-summary-server');

    const oldestEl = document.getElementById('resume-summary-oldest');

    const newestEl = document.getElementById('resume-summary-newest');



    if (cachedEl) cachedEl.textContent = '...';

    if (serverEl) serverEl.textContent = '...';

    if (oldestEl) oldestEl.textContent = '...';

    if (newestEl) newestEl.textContent = '...';



    modal.style.display = 'flex';



    // Fetch live bounds from backend

    try {

        const res = await fetch(`/api/chat_sync_bounds?chat_id=${chatId}`);

        if (res.ok) {

            const b = await res.json();

            if (cachedEl) cachedEl.textContent = (b.total_cached_messages != null) ? b.total_cached_messages.toLocaleString() : '0';

            if (serverEl) serverEl.textContent = b.server_top_id ? `#${b.server_top_id}` : 'Unknown';

            if (oldestEl) {

                oldestEl.textContent = b.oldest_cached_date ? new Date(b.oldest_cached_date).toLocaleDateString() : 'N/A';

            }

            if (newestEl) {

                newestEl.textContent = b.newest_cached_date ? new Date(b.newest_cached_date).toLocaleDateString() : 'N/A';

            }



            if (defaultStrategy) {

                window.selectResumeStrategy(defaultStrategy);

            } else if (b.has_older_gap && !b.has_newer_gap) {

                window.selectResumeStrategy('older_first');

            } else {

                window.selectResumeStrategy('latest_first');

            }

        }

    } catch (err) {

        console.error('Error fetching chat sync bounds for modal:', err);

    }



    // Fetch inherited chat media settings

    try {

        const sRes = await fetch(`/api/get_chat_settings?chat_id=${chatId}`);

        if (sRes.ok) {

            const s = await sRes.json();

            if (s && Object.keys(s).length > 0) {

                const phEl = document.getElementById('resume-dl-photos');

                const viEl = document.getElementById('resume-dl-videos');

                const voEl = document.getElementById('resume-dl-voices');

                const fiEl = document.getElementById('resume-dl-files');

                if (phEl) phEl.checked = (s.photos?.enabled !== false && s.download_photos !== false);

                if (viEl) viEl.checked = (s.videos?.enabled !== false && s.download_videos !== false);

                if (voEl) voEl.checked = (s.voice?.enabled !== false && s.download_voices !== false);

                if (fiEl) fiEl.checked = (s.documents?.enabled !== false && s.download_files !== false);

            }

        }

    } catch (sErr) {

        console.debug('Error fetching chat settings for modal:', sErr);

    }

};



window.confirmResumeDownload = async function () {

    const chatId = window._currentResumeChatId;

    if (!chatId) return;



    const selectedRadio = document.querySelector('input[name="resume_strategy"]:checked');

    const strategy = selectedRadio ? selectedRadio.value : 'latest_first';



    const dlPhotos = document.getElementById('resume-dl-photos')?.checked ?? true;

    const dlVideos = document.getElementById('resume-dl-videos')?.checked ?? true;

    const dlVoices = document.getElementById('resume-dl-voices')?.checked ?? true;

    const dlFiles = document.getElementById('resume-dl-files')?.checked ?? true;



    const settings = {

        photos: { enabled: dlPhotos, max_mb: 0 },

        videos: { enabled: dlVideos, max_mb: 0 },

        voice: { enabled: dlVoices, max_mb: 0 },

        documents: { enabled: dlFiles, max_mb: 0 },

        download_photos: dlPhotos,

        download_videos: dlVideos,

        download_voices: dlVoices,

        download_files: dlFiles

    };



    const confirmBtn = document.getElementById('resume-confirm-btn');

    if (confirmBtn) {

        confirmBtn.disabled = true;

        confirmBtn.innerHTML = '<span>⏳ Starting...</span>';

    }



    try {

        const res = await fetch('/api/resume_chat_download', {

            method: 'POST',

            headers: {

                'Content-Type': 'application/json',

                'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '')

            },

            body: JSON.stringify({

                chat_id: chatId,

                strategy: strategy,

                settings: settings

            })

        });



        const data = await res.json();

        if (res.ok) {

            window.closeResumeModal();

            const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

            if (typeof notifyFn === 'function') {

                notifyFn(data.message || 'Resume task queued successfully!', 'success');

            }

        } else {

            alert(data.error || 'Failed to start resume');

        }

    } catch (err) {

        alert(`Error: ${err.message}`);

    } finally {

        if (confirmBtn) {

            confirmBtn.disabled = false;

            confirmBtn.innerHTML = '<span>▶️ Start Resume</span>';

        }

    }

};


// =========================================================================
// GLOBAL HANDLERS FOR O(1) HOR GAP RECONCILIATION
// =========================================================================

window.triggerHorReconcileFromPopover = async function(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (window._reconcilePending) return;
    const popover = document.getElementById('chat-settings-popover');
    const targetChat = popover?.dataset.chatId || window.currentChatId;
    if (!targetChat) return;
    if (popover) popover.style.display = 'none';
    const bubble = document.getElementById('chat-summary-bubble');
    if (bubble) bubble.style.display = 'none';

    window._reconcilePending = true;
    const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;
    try {
        const res = await fetch(`/api/reconcile_gaps/${targetChat}`, { 
            method: 'POST', 
            headers: { 
                'Content-Type': 'application/json', 
                'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') 
            } 
        });
        const data = await res.json();
        if (res.ok) {
            const count = parseInt(localStorage.getItem('tb_hor_scan_count_' + targetChat) || '0', 10);
            localStorage.setItem('tb_hor_scan_count_' + targetChat, String(count + 1));
            if (typeof notifyFn === 'function') {
                notifyFn('⚡ Started True O(1) HOR gap verification!', 'success');
            }
            if (typeof startArchiveStream === 'function') {
                startArchiveStream();
            }
        } else {
            if (typeof notifyFn === 'function') {
                notifyFn(data.error || 'Failed to start gap reconciliation', 'error');
            }
        }
    } catch (err) {
        console.error("Failed to start gap reconciliation:", err);
        if (typeof notifyFn === 'function') {
            notifyFn('Network error starting gap scan', 'error');
        }
    } finally {
        window._reconcilePending = false;
    }
};

window.triggerHorReconcileFromResumeModal = async function(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (window._reconcilePending) return;
    const targetChat = window._currentResumeChatId || window.currentResumeChatId || (typeof currentInspectorChatId !== 'undefined' ? currentInspectorChatId : null) || window.currentChatId;
    if (!targetChat) return;
    if (typeof closeResumeModal === 'function') closeResumeModal();

    window._reconcilePending = true;
    const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;
    try {
        const res = await fetch(`/api/reconcile_gaps/${targetChat}`, { 
            method: 'POST', 
            headers: { 
                'Content-Type': 'application/json', 
                'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') 
            } 
        });
        const data = await res.json();
        if (res.ok) {
            const count = parseInt(localStorage.getItem('tb_hor_scan_count_' + targetChat) || '0', 10);
            localStorage.setItem('tb_hor_scan_count_' + targetChat, String(count + 1));
            if (typeof notifyFn === 'function') {
                notifyFn('⚡ Started True O(1) HOR gap verification!', 'success');
            }
            if (typeof startArchiveStream === 'function') {
                startArchiveStream();
            }
        } else {
            if (typeof notifyFn === 'function') {
                notifyFn(data.error || 'Failed to start gap reconciliation', 'error');
            }
        }
    } catch (err) {
        console.error("Failed to start gap reconciliation:", err);
        if (typeof notifyFn === 'function') {
            notifyFn('Network error starting gap scan', 'error');
        }
    } finally {
        window._reconcilePending = false;
    }
};

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('resume-trigger-hor-reconcile-btn')?.addEventListener('click', (e) => {
        window.triggerHorReconcileFromResumeModal(e);
    });

    document.getElementById('asset-trigger-batch-transcribe-btn')?.addEventListener('click', async () => {
        const inspChatId = (typeof currentInspectorChatId !== 'undefined') ? currentInspectorChatId : null;
        if (!inspChatId) return;
        const chatterFilter = document.getElementById('asset-chatter-filter');
        const selectedChatter = chatterFilter ? chatterFilter.value : '';
        const senderIds = selectedChatter ? [parseInt(selectedChatter, 10)] : null;
        try {
            const res = await fetch('/api/transcribe/batch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '')
                },
                body: JSON.stringify({
                    chat_id: inspChatId,
                    sender_ids: senderIds
                })
            });
            if (res.ok) {
                const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;
                if (typeof notifyFn === 'function') {
                    notifyFn('🎙️ Batch voice transcription enqueued!', 'info');
                }
                const modal = document.getElementById('asset-inspector-modal');
                if (modal) modal.style.display = 'none';
            }
        } catch (e) {
            console.error("Failed to start batch transcribe:", e);
        }
    });
});
