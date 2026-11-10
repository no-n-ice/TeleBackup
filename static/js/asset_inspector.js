// static/js/asset_inspector.js — Media Asset Inspector, Diagnostics Modal, Single & Catch-up Media Downloaders

// --- Chat Media Asset Inspector Modal ---

let currentInspectorChatId = null;

let currentInspectorAssets = [];

let currentInspectorChatSettings = {};

let currentAssetSort = { field: 'id', dir: 'desc' };



function isAssetUserExcluded(a) {

    if (!a) return false;

    if (a.media_skipped && a.media_skipped.includes('user_rule_excluded')) return true;

    const uRules = currentInspectorChatSettings?.user_rules || {};

    const sId = a.sender_id ? String(a.sender_id) : null;

    const vId = a.via_bot_id ? String(a.via_bot_id) : null;

    let fId = a.fwd_from_id ? String(a.fwd_from_id) : null;

    if (!fId && a.forward_info_json) {

        try {

            const fwd = (typeof a.forward_info_json === 'string') ? JSON.parse(a.forward_info_json) : a.forward_info_json;

            const rawId = fwd?.from_id || fwd?.from_id_raw?.user_id || fwd?.from_id_raw?.channel_id;

            if (rawId) fId = String(rawId);

        } catch (_) {}

    }

    const rKey = (sId && uRules[sId]) ? sId : ((vId && uRules[vId]) ? vId : ((fId && uRules[fId]) ? fId : null));

    if (!rKey) return false;



    const rule = uRules[rKey];

    if (rule.mode === 'exclude_all') return true;

    if (rule.mode === 'custom') {

        const mType = (a.media_type || '').toLowerCase();

        let cat = 'documents';

        if (['photo', 'photos'].includes(mType)) cat = 'photos';

        else if (['video', 'videos', 'gif'].includes(mType)) cat = 'videos';

        else if (['voice', 'audio'].includes(mType)) cat = 'voice';

        else if (['video_note', 'video_notes'].includes(mType)) cat = 'video_notes';



        const uCfg = rule[cat] || {};

        if (uCfg.enabled === false) return true;

        const sizeBytes = a.size_bytes || 0;

        if (uCfg.max_mb > 0 && sizeBytes > (uCfg.max_mb * 1024 * 1024)) return true;

    }

    return false;

}



function isAssetSettingsEligible(a) {

    if (!a || a.media_local_path) return false;

    if (isAssetUserExcluded(a)) return false;

    const skipReason = (a.media_skipped || '').toLowerCase();

    if (skipReason.includes('unavailable') || skipReason.includes('expired') || skipReason.startsWith('failed:') || skipReason.startsWith('download error:') || skipReason.includes('timeout')) {

        return false;

    }

    const settings = currentInspectorChatSettings || {};

    const mType = (a.media_type || '').toLowerCase();

    if (['web_page', 'webpage', 'poll', 'dice', 'geo', 'geolive', 'contact', 'venue', 'game'].includes(mType) && !a.media_skipped) return false;

    let cfgKey = 'documents';

    if (mType === 'photo') cfgKey = 'photos';

    else if (['video', 'gif'].includes(mType)) cfgKey = 'videos';

    else if (['voice', 'audio'].includes(mType)) cfgKey = 'voice';

    else if (mType === 'video_note') cfgKey = 'video_notes';



    const cfg = settings[cfgKey] || {};

    if (cfg.enabled === false) return false;

    const sizeBytes = a.size_bytes || 0;

    if (cfg.max_mb > 0 && sizeBytes > (cfg.max_mb * 1024 * 1024)) return false;

    return true;

}



window.closeAssetInspector = function () {

    const modal = document.getElementById('asset-inspector-modal');

    if (modal) modal.style.display = 'none';

    const warnModal = document.getElementById('catchup-warning-modal');

    if (warnModal) warnModal.style.display = 'none';

    currentInspectorChatId = null;

    currentInspectorAssets = [];

    currentInspectorChatSettings = {};

};



window.openAssetInspector = async function (chatId, chatName, targetMessageId) {

    currentInspectorChatId = chatId;

    const modal = document.getElementById('asset-inspector-modal');

    if (!modal) return;

    modal.style.display = 'flex';

    const titleEl = document.getElementById('asset-inspector-title');

    if (titleEl) titleEl.textContent = `📦 ${chatName || 'Chat'} — Media Asset Inspector`;



    const searchInput = document.getElementById('asset-search-input');

    const searchClear = document.getElementById('asset-search-clear');

    if (searchInput) {

        searchInput.value = targetMessageId ? String(targetMessageId) : '';

        if (searchClear) searchClear.style.display = searchInput.value ? 'inline-block' : 'none';

    }



    const isChatCatchupRunning = Boolean(

        archiveStatusCache &&

        archiveStatusCache[String(chatId)] &&

        ['running', 'queued'].includes(archiveStatusCache[String(chatId)].status) &&

        archiveStatusCache[String(chatId)].phase === 'catchup'

    );



    const banner = document.getElementById('asset-catchup-banner');

    if (banner) {

        banner.style.display = isChatCatchupRunning ? 'flex' : 'none';

    }



    const triggerBtn = document.getElementById('asset-trigger-catchup-btn');

    if (triggerBtn) {

        triggerBtn.disabled = true;

        triggerBtn.textContent = isChatCatchupRunning ? '⚡ Catch-up in progress...' : '⏳ Loading assets...';

        triggerBtn.style.display = 'inline-block';

    }

    const exclCatchupBtn = document.getElementById('asset-trigger-excluded-catchup-btn');

    if (exclCatchupBtn) {

        exclCatchupBtn.disabled = true;

        exclCatchupBtn.textContent = '⚡ Download Excluded Media';

        exclCatchupBtn.style.display = 'none';

    }



    const deltaEl = document.getElementById('asset-delta-size');

    if (deltaEl) deltaEl.textContent = 'Calculating...';



    // Reset not downloaded checkbox

    const notDlCheckbox = document.getElementById('asset-filter-not-downloaded');

    if (notDlCheckbox) notDlCheckbox.checked = false;



    // Reset filter pills to All

    document.querySelectorAll('.asset-filter-pill').forEach(b => {

        b.classList.remove('active');

        b.style.background = 'none';

        b.style.border = '1px solid var(--border-color)';

        b.style.color = (b.dataset.type === 'excluded') ? '#e91e63' : 'var(--text-color)';

    });

    const allPill = document.querySelector('.asset-filter-pill[data-type="all"]');

    if (allPill) {

        allPill.classList.add('active');

        allPill.style.background = 'var(--primary-color, #2481cc)';

        allPill.style.borderColor = 'transparent';

        allPill.style.color = 'white';

    }



    const tbody = document.getElementById('asset-table-body');

    if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 20px; color: var(--text-color-light);">Loading assets...</td></tr>`;



    try {

        const [assetsRes, settingsRes] = await Promise.all([

            fetch(`/api/chat_media_assets?chat_id=${chatId}`),

            fetch(`/api/chat_settings?chat_id=${chatId}`)

        ]);



        if (!assetsRes.ok) throw new Error('Failed to load assets');

        const assetsData = await assetsRes.json();

        currentInspectorAssets = assetsData.assets || [];



        currentInspectorChatSettings = {};

        if (settingsRes.ok) {

            const sData = await settingsRes.json();

            currentInspectorChatSettings = (sData && sData.settings) ? sData.settings : (sData || {});

        }



        updateInspectorSummaryCounters();

        

        // Populate chatter filter & voice stats

        const chatterSelect = document.getElementById('asset-chatter-filter');

        if (chatterSelect) {

            chatterSelect.innerHTML = '<option value="">All Chatters</option>';

            fetch(`/api/chat_senders?chat_id=${chatId}`).then(r => r.json()).then(senders => {

                if (Array.isArray(senders)) {

                    senders.forEach(s => {

                        const opt = document.createElement('option');

                        opt.value = s.id;

                        opt.textContent = `${s.name || s.id} (${s.message_count || 0})`;

                        chatterSelect.appendChild(opt);

                    });

                }

            }).catch(() => {});

        }



        const voiceAssets = currentInspectorAssets.filter(a => a.media_type === 'voice' || a.media_type === 'video_note' || a.is_voice == 1 || a.is_round_video == 1);

        const trDone = voiceAssets.filter(a => a.transcription_text && a.transcription_text.trim()).length;

        const trPending = voiceAssets.length - trDone;

        const voiceStatEl = document.getElementById('asset-voice-stat');

        if (voiceStatEl) {

            voiceStatEl.textContent = `${voiceAssets.length} (${trDone} done, ${trPending} pending)`;

        }

        const untrCountEl = document.getElementById('asset-untranscribed-count');

        if (untrCountEl) untrCountEl.textContent = String(trPending);



        renderAssetTable(currentInspectorAssets, 'all', targetMessageId ? String(targetMessageId) : '');



        if (!isChatCatchupRunning && triggerBtn) {

            triggerBtn.disabled = false;

            triggerBtn.textContent = '⚡ Download Missing Media';

        }

        if (!isChatCatchupRunning && exclCatchupBtn) {

            exclCatchupBtn.disabled = false;

        }



        if (targetMessageId) {

            setTimeout(() => {

                window.jumpToAssetRowInInspector(targetMessageId);

            }, 100);

        }



        // Check if there is an active catchup task running for this chat

        const currentJob = archiveStatusCache && archiveStatusCache[String(chatId)];

        if (currentJob && (currentJob.status === 'running' || currentJob.status === 'queued') && currentJob.phase === 'catchup') {

            updateInspectorLiveCatchup(currentJob);

        }

    } catch (e) {

        if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 20px; color: #ff5252;">Error: ${escapeHTML(e.message)}</td></tr>`;

        if (triggerBtn) {

            triggerBtn.disabled = false;

            triggerBtn.textContent = '⚡ Download Missing Media';

        }

    }

};



function updateInspectorSummaryCounters(deltaData) {

    if (!currentInspectorAssets) return;

    const totalCount = currentInspectorAssets.length;

    let downloadedCount = 0;

    let excludedCount = 0;

    let excludedBytes = 0;

    let missingBytes = 0;

    let eligibleMissingCount = 0;

    let eligibleMissingBytes = 0;



    for (let i = 0; i < currentInspectorAssets.length; i++) {

        const a = currentInspectorAssets[i];

        const sz = a.size_bytes || 0;

        const isExcluded = isAssetUserExcluded(a);

        if (isExcluded) {

            excludedCount++;

            excludedBytes += sz;

        }

        if (a.media_local_path) {

            downloadedCount++;

        } else {

            missingBytes += sz;

            if (isAssetSettingsEligible(a)) {

                eligibleMissingCount++;

                eligibleMissingBytes += sz;

            }

        }

    }

    const missingCount = totalCount - downloadedCount;



    const totalEl = document.getElementById('asset-total-count');

    const dlEl = document.getElementById('asset-downloaded-count');

    const missEl = document.getElementById('asset-missing-count');

    const deltaEl = document.getElementById('asset-delta-size');

    const exclStatEl = document.getElementById('asset-excluded-stat');

    const exclPillEl = document.getElementById('asset-pill-excluded-count');



    if (totalEl) totalEl.textContent = totalCount;

    if (dlEl) dlEl.textContent = downloadedCount;

    if (missEl) missEl.textContent = missingCount;

    if (exclStatEl) exclStatEl.textContent = `${excludedCount} (${(excludedBytes / (1024 * 1024)).toFixed(1)} MB)`;

    if (exclPillEl) exclPillEl.textContent = excludedCount;



    const missingMb = (missingBytes / (1024 * 1024)).toFixed(1);

    const eligibleCount = deltaData?.applicable_count !== undefined ? deltaData.applicable_count : eligibleMissingCount;

    const eligibleBytes = deltaData?.estimated_bytes !== undefined ? deltaData.estimated_bytes : eligibleMissingBytes;

    const eligibleMb = (eligibleBytes / (1024 * 1024)).toFixed(1);



    if (deltaEl) {

        deltaEl.textContent = `${missingMb} MB (${eligibleCount} eligible, ${eligibleMb} MB)`;

    }

}



function renderAssetTable(assets, filterType, searchQuery) {

    const tbody = document.getElementById('asset-table-body');

    if (!tbody) return;



    let filtered = (assets || []).slice();

    if (filterType && filterType === 'excluded') {

        filtered = filtered.filter(isAssetUserExcluded);

    } else if (filterType && filterType !== 'all') {

        filtered = filtered.filter(a => (a.media_type || '').toLowerCase() === filterType.toLowerCase());

    }



    // Filter by Not Downloaded status toggle

    const notDlCheckbox = document.getElementById('asset-filter-not-downloaded');

    if (notDlCheckbox && notDlCheckbox.checked) {

        filtered = filtered.filter(a => !a.media_local_path);

    }



    // Filter by Selected Chatter

    const chatterSelect = document.getElementById('asset-chatter-filter');

    if (chatterSelect && chatterSelect.value) {

        const chatterId = parseInt(chatterSelect.value, 10);

        filtered = filtered.filter(a => a.sender_id === chatterId);

    }



    const queryStr = (searchQuery !== undefined && searchQuery !== null)

        ? String(searchQuery).trim()

        : (document.getElementById('asset-search-input')?.value.trim() || '');



    if (queryStr) {

        const q = queryStr.toLowerCase().replace('#', '');

        const numericQ = parseInt(q, 10);

        filtered = filtered.filter(a => {

            const matchesId = !isNaN(numericQ) && a.message_id === numericQ;

            const matchesSubId = String(a.message_id).includes(q);

            const matchesName = (a.media_original_name || '').toLowerCase().includes(q);

            const matchesType = (a.media_type || '').toLowerCase().includes(q);

            const matchesSkip = (a.media_skipped || '').toLowerCase().includes(q);

            return matchesId || matchesSubId || matchesName || matchesType || matchesSkip;

        });

    }



    if (currentAssetSort.field === 'status') {

        filtered.forEach(item => {

            if (item.media_local_path) { item._statusRank = 1; return; }

            const excluded = isAssetUserExcluded(item);

            if (!item.media_skipped && !excluded) { item._statusRank = 2; return; }

            if (item.media_skipped) {

                const s = item.media_skipped.toLowerCase();

                if (s.startsWith('failed:') || s.startsWith('download error:') || s.includes('timeout')) { item._statusRank = 3; return; }

                if (s.includes('unavailable') || s.includes('expired')) { item._statusRank = 4; return; }

                item._statusRank = 5;

                return;

            }

            if (excluded) { item._statusRank = 6; return; }

            item._statusRank = 2;

        });

    }



    // Sort according to currentAssetSort

    filtered.sort((a, b) => {

        let diff = 0;

        const field = currentAssetSort.field;

        if (field === 'id') {

            diff = (a.message_id || 0) - (b.message_id || 0);

        } else if (field === 'date') {

            diff = (a.date || '') < (b.date || '') ? -1 : ((a.date || '') > (b.date || '') ? 1 : 0);

        } else if (field === 'type') {

            diff = (a.media_type || '').localeCompare(b.media_type || '');

        } else if (field === 'name') {

            diff = (a.media_original_name || '').localeCompare(b.media_original_name || '');

        } else if (field === 'size') {

            diff = (a.size_bytes || 0) - (b.size_bytes || 0);

        } else if (field === 'status') {

            diff = (a._statusRank || 0) - (b._statusRank || 0);

        }

        if (diff === 0) {

            // Secondary tie-breaker: stable sort by message_id descending

            return (b.message_id || 0) - (a.message_id || 0);

        }

        return currentAssetSort.dir === 'desc' ? -diff : diff;

    });



    // Update active sort indicator on table headers

    document.querySelectorAll('#asset-inspector-table thead th.asset-sort-th').forEach(th => {

        const sField = th.dataset.sort;

        const iconSpan = th.querySelector('.sort-icon');

        if (sField === currentAssetSort.field) {

            th.style.color = 'var(--link-color, #2481cc)';

            if (iconSpan) iconSpan.textContent = currentAssetSort.dir === 'asc' ? ' ▲' : ' ▼';

        } else {

            th.style.color = 'inherit';

            if (iconSpan) iconSpan.textContent = '';

        }

    });



    if (filtered.length === 0) {

        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 20px; color: var(--text-color-light);">No media assets found</td></tr>`;

        return;

    }



    const maxRows = 600;

    tbody.innerHTML = filtered.slice(0, maxRows).map(a => {

        const sizeMb = a.size_bytes ? (a.size_bytes / (1024 * 1024)).toFixed(2) + ' MB' : '-';

        const isDownloaded = Boolean(a.media_local_path);

        const isExcluded = isAssetUserExcluded(a);



        let statusBadge = '';

        if (isDownloaded) {

            statusBadge = '<span style="color: #00c853; font-weight: 500;">✓ Downloaded</span>';

        } else if (isExcluded) {

            statusBadge = '<span style="color: #e91e63;" title="Excluded by user/bot rule">🚫 Excluded (Rule)</span>';

        } else if (a.media_skipped) {

            const rawSkip = a.media_skipped || '';

            const skipLower = rawSkip.toLowerCase();

            if (skipLower.includes('unavailable') || skipLower.includes('expired')) {

                statusBadge = `<span style="color: #90a4ae; font-weight: 500;" title="${escapeHTML(rawSkip)}">❌ Unavailable</span>`;

            } else if (skipLower.startsWith('failed:') || skipLower.startsWith('download error:') || skipLower.includes('timeout')) {

                const reasonClean = rawSkip.replace(/^(Failed:\s*|Download error:\s*)/i, '');

                statusBadge = `<span style="color: #ff5252; font-weight: 500;" title="${escapeHTML(rawSkip)}">⚠️ Failed (${escapeHTML(reasonClean)})</span>`;

            } else {

                const skipReason = rawSkip.replace(/^skipped:\s*/i, '');

                statusBadge = `<span style="color: #ffa726;" title="${escapeHTML(rawSkip)}">⚠️ Skipped (${escapeHTML(skipReason)})</span>`;

            }

        } else {

            statusBadge = '<span style="color: #ff5252;">⏳ Missing</span>';

        }



        const dateStr = a.date ? new Date(a.date).toLocaleDateString() : '-';

        const typeStr = escapeHTML(a.media_type || 'other');

        const nameStr = escapeHTML(a.media_original_name || '-');



        const actionContent = isDownloaded

            ? '<span style="color: #00c853; font-size: 11px;">✓ Saved</span>'

            : `<button type="button" class="action-btn asset-row-action-btn" onclick="downloadSingleMedia(${currentInspectorChatId}, ${a.message_id}, this)" style="padding: 3px 8px; font-size: 11px; background: rgba(33, 150, 243, 0.15); border: 1px solid rgba(33, 150, 243, 0.4); color: #2196f3; cursor: pointer; border-radius: 4px; transition: all 0.2s;">Download</button>`;



        return `

            <tr id="asset-row-${a.message_id}" data-msg-id="${a.message_id}" style="border-bottom: 1px solid rgba(255,255,255,0.05); transition: background 0.3s ease;">

                <td style="padding: 6px 8px; white-space: nowrap;">

                    <a href="javascript:void(0)" class="asset-jump-link" onclick="jumpToMessageFromInspector(${currentInspectorChatId}, ${a.message_id})" title="Jump to message in chat" style="color: var(--link-color, #2481cc); text-decoration: none; font-weight: 500;">#${a.message_id}</a>

                    <button type="button" class="action-btn" onclick="openMediaDiagnosticsModal(${currentInspectorChatId}, ${a.message_id})" title="View Diagnostics & Raw Meta" style="margin-left: 5px; padding: 1px 5px; font-size: 10px; background: rgba(255,255,255,0.06); border: 1px solid var(--border-color); color: var(--text-color); border-radius: 3px; cursor: pointer;">📦</button>

                </td>

                <td style="padding: 6px 8px;">${dateStr}</td>

                <td style="padding: 6px 8px;"><span style="text-transform: capitalize;">${typeStr}</span></td>

                <td style="padding: 6px 8px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${nameStr}">${nameStr}</td>

                <td style="padding: 6px 8px;">${sizeMb}</td>

                <td style="padding: 6px 8px;" id="asset-status-${a.message_id}">${statusBadge}</td>

                <td style="padding: 6px 8px; text-align: right;" id="asset-action-${a.message_id}">${actionContent}</td>

            </tr>

        `;

    }).join('');



    if (filtered.length > maxRows) {

        tbody.innerHTML += `<tr><td colspan="7" style="text-align: center; padding: 8px; font-style: italic; color: var(--text-color-light);">Showing first ${maxRows} of ${filtered.length} records</td></tr>`;

    }

}



window.jumpToAssetRowInInspector = function(msgId) {

    if (!msgId) return;

    const row = document.getElementById(`asset-row-${msgId}`);

    if (row) {

        row.scrollIntoView({ behavior: 'smooth', block: 'center' });

        row.classList.remove('asset-row-jumped');

        void row.offsetWidth;

        row.classList.add('asset-row-jumped');

        setTimeout(() => row.classList.remove('asset-row-jumped'), 3000);

    }

};



window.handleAssetInspectorJump = function() {

    const input = document.getElementById('asset-search-input');

    if (!input || !currentInspectorAssets) return;

    const val = input.value.trim().replace('#', '');

    const targetId = parseInt(val, 10);

    if (!targetId || isNaN(targetId)) {

        const activePill = document.querySelector('.asset-filter-pill.active');

        renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', input.value);

        return;

    }



    const asset = currentInspectorAssets.find(a => a.message_id === targetId);

    if (!asset) {

        const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

        if (typeof notifyFn === 'function') {

            notifyFn(`Message #${targetId} not found in this chat's media assets.`, 'warning');

        } else {

            alert(`Message #${targetId} not found in this chat's media assets.`);

        }

        return;

    }



    const activePill = document.querySelector('.asset-filter-pill.active');

    const curFilter = activePill ? activePill.dataset.type : 'all';

    let needsResetPill = false;

    if (curFilter === 'excluded' && !isAssetUserExcluded(asset)) {

        needsResetPill = true;

    } else if (curFilter !== 'all' && curFilter !== 'excluded' && (asset.media_type || '').toLowerCase() !== curFilter.toLowerCase()) {

        needsResetPill = true;

    }



    if (needsResetPill) {

        document.querySelectorAll('.asset-filter-pill').forEach(b => {

            b.classList.remove('active');

            b.style.background = 'none';

            b.style.border = '1px solid var(--border-color)';

            b.style.color = (b.dataset.type === 'excluded') ? '#e91e63' : 'var(--text-color)';

        });

        const allPill = document.querySelector('.asset-filter-pill[data-type="all"]');

        if (allPill) {

            allPill.classList.add('active');

            allPill.style.background = 'var(--primary-color, #2481cc)';

            allPill.style.borderColor = 'transparent';

            allPill.style.color = 'white';

        }

    }



    renderAssetTable(currentInspectorAssets, 'all', String(targetId));

    setTimeout(() => {

        window.jumpToAssetRowInInspector(targetId);

    }, 50);

};



let currentDiagData = null;



window.closeMediaDiagnosticsModal = function() {

    const modal = document.getElementById('media-diagnostics-modal');

    if (modal) modal.style.display = 'none';

    currentDiagData = null;

};



window.openMediaDiagnosticsModal = async function(chatId, messageId) {

    if (!messageId) return;

    const modal = document.getElementById('media-diagnostics-modal');

    if (!modal) return;

    modal.style.display = 'flex';



    const titleEl = document.getElementById('media-diag-title');

    const bodyEl = document.getElementById('media-diag-body');

    const jumpBtn = document.getElementById('media-diag-jump-inspector-btn');

    const copyBtn = document.getElementById('media-diag-copy-btn');



    if (titleEl) titleEl.textContent = `📦 Media Diagnostics — Msg #${messageId}`;

    if (bodyEl) {

        bodyEl.innerHTML = `

            <div style="display: flex; align-items: center; justify-content: center; padding: 40px; gap: 12px; color: var(--text-color-light);">

                <span class="asset-spinner" style="display: inline-block; width: 20px; height: 20px; border: 2px solid rgba(33, 150, 243, 0.3); border-top-color: #2196f3; border-radius: 50%; animation: spin 0.8s linear infinite;"></span>

                <span>Fetching message diagnostics from database...</span>

            </div>

        `;

    }



    if (jumpBtn) {

        jumpBtn.onclick = () => {

            window.closeMediaDiagnosticsModal();

            window.openAssetInspector(chatId, '', messageId);

        };

    }



    if (copyBtn) {

        copyBtn.onclick = () => {

            if (!currentDiagData) return;

            const { chatId: cId, messageId: mId, msg: dMsg } = currentDiagData;

            const lines = [

                `=== Telegram Media Diagnostics ===`,

                `Message ID: #${mId}`,

                `Chat ID: ${cId}`,

                `Sender: ${[dMsg.sender_first_name, dMsg.sender_last_name].filter(Boolean).join(' ') || dMsg.sender_id || 'Unknown'}`,

                `Date: ${dMsg.date || 'Unknown'}`,

                `Media Type: ${dMsg.media_type || 'None'}`,

                `Original Filename: ${dMsg.media_original_name || 'None'}`,

                `MIME Type: ${dMsg.media_mime_type || 'None'}`,

                `Local Disk Path: ${dMsg.media_local_path || 'None'}`,

                `Skip Reason: ${dMsg.media_skipped || 'None'}`,

                `Telegram Media Class: ${dMsg.media_other ? dMsg.media_other._ : 'None'}`,

                `Telegram TTL Seconds: ${dMsg.media_other && dMsg.media_other.ttl_seconds !== undefined ? dMsg.media_other.ttl_seconds : 'None'}`,

                `Telegram Document: ${dMsg.media_other && dMsg.media_other.document !== undefined ? (dMsg.media_other.document === null ? 'null (Expired / Purged by Telegram)' : 'Present') : 'N/A'}`,

                `Raw media_other_json:\n${JSON.stringify(dMsg.media_other || {}, null, 2)}`

            ];

            const textToCopy = lines.join('\n');

            if (navigator.clipboard) {

                navigator.clipboard.writeText(textToCopy);

                const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

                if (typeof notifyFn === 'function') {

                    notifyFn('Diagnostics copied to clipboard!', 'info');

                } else {

                    alert('Diagnostics copied to clipboard!');

                }

            }

        };

    }



    try {

        const resp = await fetch(`/api/single_message?chat_id=${chatId}&message_id=${messageId}`);

        if (!resp.ok) {

            throw new Error(`Message #${messageId} not found in database (HTTP ${resp.status})`);

        }

        const resData = await resp.json();

        const msg = resData.message || resData;

        currentDiagData = { chatId, messageId, msg };



        const mediaOther = msg.media_other || {};

        const isTtlExpired = (

            (mediaOther._ === 'MessageMediaDocument' && mediaOther.document === null && mediaOther.ttl_seconds !== undefined && mediaOther.ttl_seconds !== null) ||

            (mediaOther._ === 'MessageMediaPhoto' && mediaOther.photo === null && mediaOther.ttl_seconds !== undefined && mediaOther.ttl_seconds !== null) ||

            (mediaOther.ttl_seconds && !msg.media_local_path && mediaOther.document === null)

        );



        const hasLocalFile = Boolean(msg.media_local_path);

        const isSkipped = Boolean(msg.media_skipped);



        let statusBannerHtml = '';

        if (hasLocalFile) {

            statusBannerHtml = `

                <div style="background: rgba(0, 200, 83, 0.12); border: 1px solid rgba(0, 200, 83, 0.4); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                    <span style="font-size: 18px; line-height: 1;">✅</span>

                    <div>

                        <div style="font-weight: 600; color: #00c853;">Downloaded to Local Disk</div>

                        <div style="font-size: 12px; margin-top: 4px; word-break: break-all; opacity: 0.9;">Path: <code>${escapeHTML(msg.media_local_path)}</code></div>

                    </div>

                </div>

            `;

        } else if (isTtlExpired) {

            statusBannerHtml = `

                <div style="background: rgba(255, 82, 82, 0.12); border: 1px solid rgba(255, 82, 82, 0.5); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                    <span style="font-size: 20px; line-height: 1;">⚠️</span>

                    <div>

                        <div style="font-weight: 600; color: #ff5252;">Telegram Expired TTL / Self-Destructing Media</div>

                        <div style="font-size: 12px; margin-top: 5px; line-height: 1.45;">

                            This message was sent with a self-destruct timer (<code>ttl_seconds: ${escapeHTML(String(mediaOther.ttl_seconds))}</code>).

                            Telegram servers set <code>document: null</code> once expired and purge the file binary.

                            <b>Telethon returns <code>None</code> for this message because the file is permanently unhosted on Telegram's end.</b>

                        </div>

                    </div>

                </div>

            `;

        } else if (isSkipped) {

            const rawSkip = msg.media_skipped || '';

            const skipLower = rawSkip.toLowerCase();

            if (skipLower.includes('unavailable') || skipLower.includes('expired')) {

                statusBannerHtml = `

                    <div style="background: rgba(176, 190, 197, 0.12); border: 1px solid rgba(176, 190, 197, 0.4); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                        <span style="font-size: 20px; line-height: 1;">❌</span>

                        <div>

                            <div style="font-weight: 600; color: #eceff1;">Media Unavailable on Telegram</div>

                            <div style="font-size: 12px; margin-top: 4px; opacity: 0.9;">Reason: <code>${escapeHTML(rawSkip)}</code></div>

                            <div style="font-size: 11px; margin-top: 4px; color: #90a4ae;">Telegram servers returned empty data or purged this file binary from the CDN.</div>

                        </div>

                    </div>

                `;

            } else if (skipLower.startsWith('failed:') || skipLower.startsWith('download error:') || skipLower.includes('timeout')) {

                statusBannerHtml = `

                    <div style="background: rgba(255, 82, 82, 0.12); border: 1px solid rgba(255, 82, 82, 0.5); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                        <span style="font-size: 20px; line-height: 1;">⚠️</span>

                        <div>

                            <div style="font-weight: 600; color: #ff5252;">Download Failed (Telegram Server Issue)</div>

                            <div style="font-size: 12px; margin-top: 4px; opacity: 0.9;">Reason: <code>${escapeHTML(rawSkip)}</code></div>

                            <div style="font-size: 11px; margin-top: 4px; color: #ff8a80;">Telegram encountered internal timeouts or RPC errors while serving chunk data. You can attempt a manual retry using the single download button.</div>

                        </div>

                    </div>

                `;

            } else {

                statusBannerHtml = `

                    <div style="background: rgba(255, 167, 38, 0.12); border: 1px solid rgba(255, 167, 38, 0.4); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                        <span style="font-size: 18px; line-height: 1;">⚠️</span>

                        <div>

                            <div style="font-weight: 600; color: #ffa726;">Skipped During Backup</div>

                            <div style="font-size: 12px; margin-top: 4px; opacity: 0.9;">Reason: <code>${escapeHTML(rawSkip)}</code></div>

                        </div>

                    </div>

                `;

            }

        } else if (msg.media_type) {

            statusBannerHtml = `

                <div style="background: rgba(33, 150, 243, 0.12); border: 1px solid rgba(33, 150, 243, 0.4); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                    <span style="font-size: 18px; line-height: 1;">⏳</span>

                    <div>

                        <div style="font-weight: 600; color: #42a5f5;">Pending / Not Downloaded</div>

                        <div style="font-size: 12px; margin-top: 4px; opacity: 0.9;">Media metadata exists, but file is not yet downloaded to local disk.</div>

                    </div>

                </div>

            `;

        } else {

            statusBannerHtml = `

                <div style="background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px;">

                    <span style="font-size: 18px; line-height: 1;">ℹ️</span>

                    <div>

                        <div style="font-weight: 600; color: var(--text-color);">No Media Attached</div>

                        <div style="font-size: 12px; margin-top: 4px; opacity: 0.8;">This message does not appear to contain any media attachment.</div>

                    </div>

                </div>

            `;

        }



        const senderName = [msg.sender_first_name, msg.sender_last_name].filter(Boolean).join(' ') || (msg.sender_id ? `User ${msg.sender_id}` : 'Channel Post');

        const formattedDate = msg.date ? new Date(msg.date).toLocaleString() : 'Unknown';

        const rawJsonStr = JSON.stringify({

            message_id: msg.message_id,

            chat_id: msg.chat_id,

            media_type: msg.media_type,

            media_original_name: msg.media_original_name,

            media_mime_type: msg.media_mime_type,

            media_local_path: msg.media_local_path,

            media_skipped: msg.media_skipped,

            media_other: msg.media_other

        }, null, 2);



        bodyEl.innerHTML = `

            ${statusBannerHtml}



            <!-- Metadata table -->

            <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; font-size: 12px; display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px 14px;">

                <div><span style="opacity: 0.65;">Message ID:</span> <b style="color: var(--primary-color, #2481cc);">#${msg.message_id}</b></div>

                <div><span style="opacity: 0.65;">Chat ID:</span> <b>${msg.chat_id}</b></div>

                <div><span style="opacity: 0.65;">Sender:</span> <b>${escapeHTML(senderName)}</b></div>

                <div><span style="opacity: 0.65;">Date:</span> <span>${escapeHTML(formattedDate)}</span></div>

                <div><span style="opacity: 0.65;">Media Type:</span> <b style="text-transform: capitalize;">${escapeHTML(msg.media_type || 'None')}</b></div>

                <div><span style="opacity: 0.65;">Original Name:</span> <span style="word-break: break-all;">${escapeHTML(msg.media_original_name || 'None')}</span></div>

                <div><span style="opacity: 0.65;">MIME Type:</span> <code>${escapeHTML(msg.media_mime_type || 'None')}</code></div>

                <div><span style="opacity: 0.65;">Telegram Type:</span> <code>${escapeHTML(mediaOther._ || 'None')}</code></div>

                <div><span style="opacity: 0.65;">TTL Seconds:</span> <span style="${isTtlExpired ? 'color: #ff5252; font-weight: 600;' : ''}">${mediaOther.ttl_seconds !== undefined ? escapeHTML(String(mediaOther.ttl_seconds)) : 'None'}</span></div>

                <div><span style="opacity: 0.65;">Document Object:</span> <span style="${mediaOther.document === null ? 'color: #ff5252; font-weight: 600;' : ''}">${mediaOther.document !== undefined ? (mediaOther.document === null ? 'null (Expired / Purged)' : 'Present') : 'N/A'}</span></div>

            </div>



            <!-- Download button if missing -->

            ${(!hasLocalFile && !isTtlExpired && msg.media_type) ? `

                <div style="display: flex; justify-content: flex-end;">

                    <button type="button" class="action-btn" onclick="downloadSingleMedia(${chatId}, ${messageId}, this)" style="background: #2196f3; color: white; padding: 6px 14px; font-size: 12px; font-weight: 500; cursor: pointer;">

                        ⚡ Download / Retry File Now

                    </button>

                </div>

            ` : ''}



            <!-- Raw Telegram Media JSON -->

            <div>

                <details style="border: 1px solid var(--border-color); border-radius: 6px; padding: 8px 12px; background: rgba(0,0,0,0.2);">

                    <summary style="cursor: pointer; font-size: 12px; font-weight: 500; opacity: 0.85;">View Raw Media Attributes (JSON)</summary>

                    <pre style="margin: 8px 0 0 0; padding: 10px; background: rgba(0,0,0,0.4); border-radius: 4px; overflow-x: auto; font-size: 11px; line-height: 1.4; color: #81c784;">${escapeHTML(rawJsonStr)}</pre>

                </details>

            </div>

        `;

    } catch (err) {

        if (bodyEl) {

            bodyEl.innerHTML = `

                <div style="background: rgba(255, 82, 82, 0.12); border: 1px solid rgba(255, 82, 82, 0.4); border-radius: 8px; padding: 14px; color: #ff5252;">

                    <b>Diagnostic Error:</b> ${escapeHTML(err.message)}

                </div>

            `;

        }

    }

};



window.jumpToMessageFromInspector = function(chatId, messageId) {

    if (!messageId) return;

    closeAssetInspector();

    if (typeof window.scrollToMessage === 'function') {

        window.scrollToMessage(Number(messageId), chatId);

    }

};



window.downloadSingleMedia = async function(chatId, messageId, btnEl) {

    if (!chatId || !messageId) return;

    const origHtml = btnEl ? btnEl.innerHTML : '';

    if (btnEl) {

        btnEl.disabled = true;

        btnEl.innerHTML = '<span class="asset-spinner" style="display: inline-block; width: 10px; height: 10px; border: 1.5px solid currentColor; border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite;"></span>';

    }



    const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

    const dlToast = (typeof notifyFn === 'function')

        ? notifyFn(`Downloading media for #${messageId}...`, 'info', true)

        : null;



    try {

        const resp = await fetch('/api/download_single_media', {

            method: 'POST',

            headers: {

                'Content-Type': 'application/json',

                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

            },

            body: JSON.stringify({

                chat_id: chatId,

                message_id: messageId,

                bypass_limits: true

            })

        });

        const data = await resp.json();

        if (data.ok) {

            if (dlToast && typeof dlToast.success === 'function') {

                dlToast.success(`Downloaded media for #${messageId}`);

            } else if (typeof notifyFn === 'function') {

                notifyFn(`Downloaded media for #${messageId}`, 'success');

            }



            // Update in-memory inspector data

            if (currentInspectorAssets && currentInspectorAssets.length > 0) {

                const asset = currentInspectorAssets.find(a => a.message_id === messageId);

                if (asset) {

                    asset.media_local_path = data.media_local_path;

                    asset.media_skipped = null;

                    if (data.file_size) asset.size_bytes = data.file_size;

                }

                const statusCell = document.getElementById(`asset-status-${messageId}`);

                const actionCell = document.getElementById(`asset-action-${messageId}`);

                if (statusCell) statusCell.innerHTML = '<span style="color: #00c853; font-weight: 500;">✓ Downloaded</span>';

                if (actionCell) actionCell.innerHTML = '<span style="color: #00c853; font-size: 11px;">✓ Saved</span>';

                updateInspectorSummaryCounters();

            }



            // Update inline download button in chat view if rendered

            const skippedCard = document.getElementById(`skipped-media-${messageId}`);

            if (skippedCard) {

                let replaced = false;

                try {

                    const msgEl = document.querySelector(`.message[data-id="${messageId}"]`) || document.getElementById(`msg-${messageId}`);

                    if (msgEl && typeof createMessageElement === 'function') {

                        const sResp = await fetch(`/api/single_message?chat_id=${chatId}&message_id=${messageId}`);

                        if (sResp.ok) {

                            const sJson = await sResp.json();

                            const fullMsg = sJson.message || sJson;

                            if (fullMsg) {

                                const newEl = createMessageElement(fullMsg);

                                msgEl.parentNode.replaceChild(newEl, msgEl);

                                replaced = true;

                            }

                        }

                    }

                } catch (e_ref) {

                    console.debug('Failed to live reload single message element:', e_ref);

                }



                if (!replaced) {

                    if (data.media_local_path && data.media_local_path.match(/\.(jpg|jpeg|png|webp|gif)$/i)) {

                        const relativePath = data.media_local_path.replace(/^(media_backup[\\/])/, '');

                        const mediaPath = '/data/' + relativePath.replace(/\\/g, '/');

                        const safeMediaPath = encodeURI(mediaPath).replace(/'/g, "%27");

                        const thumbDiv = document.createElement('div');

                        thumbDiv.className = 'media-thumb-container';

                        thumbDiv.innerHTML = `<img src="${safeMediaPath}" class="media-photo" loading="lazy" style="max-height: 420px; border-radius: 8px; cursor: pointer;" onclick="if(typeof openPhotoLightbox==='function') openPhotoLightbox('${safeMediaPath}');">`;

                        if (skippedCard.parentNode) {

                            skippedCard.parentNode.replaceChild(thumbDiv, skippedCard);

                        }

                    } else {

                        const inlineBtn = skippedCard.querySelector('.chat-inline-dl-btn');

                        if (inlineBtn) {

                            inlineBtn.textContent = '✓';

                            inlineBtn.style.color = '#00c853';

                            inlineBtn.disabled = true;

                        }

                    }

                }

            }



            if (btnEl) {

                btnEl.textContent = '✓';

                btnEl.style.color = '#00c853';

            }

        } else {

            if (data.media_skipped && currentInspectorAssets && currentInspectorAssets.length > 0) {

                const asset = currentInspectorAssets.find(a => a.message_id === messageId);

                if (asset) {

                    asset.media_skipped = data.media_skipped;

                }

                const statusCell = document.getElementById(`asset-status-${messageId}`);

                if (statusCell) {

                    const rawSkip = data.media_skipped || '';

                    const skipLower = rawSkip.toLowerCase();

                    if (skipLower.includes('unavailable') || skipLower.includes('expired')) {

                        statusCell.innerHTML = `<span style="color: #90a4ae; font-weight: 500;" title="${escapeHTML(rawSkip)}">❌ Unavailable</span>`;

                    } else if (skipLower.startsWith('failed:') || skipLower.startsWith('download error:') || skipLower.includes('timeout')) {

                        const reasonClean = rawSkip.replace(/^(Failed:\s*|Download error:\s*)/i, '');

                        statusCell.innerHTML = `<span style="color: #ff5252; font-weight: 500;" title="${escapeHTML(rawSkip)}">⚠️ Failed (${escapeHTML(reasonClean)})</span>`;

                    } else {

                        const skipReason = rawSkip.replace(/^skipped:\s*/i, '');

                        statusCell.innerHTML = `<span style="color: #ffa726;" title="${escapeHTML(rawSkip)}">⚠️ Skipped (${escapeHTML(skipReason)})</span>`;

                    }

                }

                updateInspectorSummaryCounters();

            }



            if (dlToast && typeof dlToast.error === 'function') {

                dlToast.error(data.error || 'Failed to download media');

            } else if (typeof notifyFn === 'function') {

                notifyFn(data.error || 'Failed to download media', 'error');

            }

            if (btnEl) {

                btnEl.disabled = false;

                btnEl.innerHTML = origHtml || 'Download';

            }

        }

    } catch (err) {

        if (dlToast && typeof dlToast.error === 'function') {

            dlToast.error(`Error downloading media: ${err.message}`);

        } else if (typeof notifyFn === 'function') {

            notifyFn(`Error downloading media: ${err.message}`, 'error');

        }

        if (btnEl) {

            btnEl.disabled = false;

            btnEl.innerHTML = origHtml || 'Download';

        }

    }

};



async function evaluateCatchupBounds(chatId) {

    if (!chatId) return;

    const currentJob = archiveStatusCache && archiveStatusCache[String(chatId)];

    if (currentJob && ['running', 'queued'].includes(currentJob.status) && currentJob.phase === 'catchup') {

        const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

        if (typeof notifyFn === 'function') {

            notifyFn('Media catch-up is already in progress for this chat!', 'info');

        } else {

            alert('Media catch-up is already in progress for this chat!');

        }

        return;

    }

    if (!currentInspectorAssets || currentInspectorAssets.length === 0) {

        const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

        if (typeof notifyFn === 'function') {

            notifyFn('Assets are still loading. Please wait a moment...', 'warning');

        }

        return;

    }



    let settings = currentInspectorChatSettings || {};

    try {

        if (!settings || Object.keys(settings).length === 0) {

            const setRes = await fetch(`/api/chat_settings?chat_id=${chatId}`);

            if (setRes.ok) {

                const setData = await setRes.json();

                settings = (setData && setData.settings) ? setData.settings : (setData || {});

                currentInspectorChatSettings = settings;

            }

        }

    } catch (e) {

        console.debug('Could not fetch chat settings for bounds check', e);

    }



    const missingAssets = currentInspectorAssets.filter(a => !a.media_local_path);

    if (missingAssets.length === 0) {

        if (typeof showNotification === 'function') {

            showNotification('All media assets are already downloaded!', 'info');

        } else {

            alert('All media assets are already downloaded!');

        }

        return;

    }



    const oversizeAssets = [];

    const withinLimitAssets = [];

    let oversizeBytes = 0;

    let withinLimitBytes = 0;



    for (const a of missingAssets) {

        const sizeBytes = a.size_bytes || 0;

        const sizeMb = sizeBytes / (1024 * 1024);



        const m_type = (a.media_type || '').toLowerCase();

        let cat = 'documents';

        if (['photo', 'photos'].includes(m_type)) cat = 'photos';

        else if (['video', 'videos', 'gif'].includes(m_type)) cat = 'videos';

        else if (['voice', 'audio'].includes(m_type)) cat = 'voice';

        else if (['video_note', 'video_notes'].includes(m_type)) cat = 'video_notes';



        const cfg = settings[cat] || {};

        const isEnabled = (cfg.enabled !== undefined) ? Boolean(cfg.enabled) : true;

        const catMaxMb = (cfg.max_mb !== undefined && cfg.max_mb !== null && cfg.max_mb > 0) ? Number(cfg.max_mb) : 0;



        const isUserExcluded = isAssetUserExcluded(a);

        let violatesLimits = (!isEnabled) || isUserExcluded;

        if (!violatesLimits && catMaxMb > 0) {

            if (sizeMb > 0) {

                violatesLimits = (sizeMb > catMaxMb);

            } else if (a.media_skipped) {

                const mbMatch = a.media_skipped.match(/(\d+)\s*(?:МБ|MB)/i);

                if (mbMatch) {

                    const oldThreshold = parseInt(mbMatch[1], 10);

                    if (oldThreshold >= catMaxMb) {

                        violatesLimits = true;

                    }

                } else if (a.media_skipped.includes('Больше') || a.media_skipped.includes('превышает лимит')) {

                    violatesLimits = true;

                }

            }

        }



        if (violatesLimits) {

            oversizeAssets.push(a);

            oversizeBytes += sizeBytes;

        } else {

            withinLimitAssets.push(a);

            withinLimitBytes += sizeBytes;

        }

    }



    if (oversizeAssets.length > 0) {

        const modal = document.getElementById('catchup-warning-modal');

        if (modal) {

            const warningText = document.getElementById('catchup-warning-text');

            const statsBox = document.getElementById('catchup-warning-stats');

            const withinMb = (withinLimitBytes / (1024 * 1024)).toFixed(1);

            const overMb = (oversizeBytes / (1024 * 1024)).toFixed(1);



            if (warningText) {

                warningText.textContent = `Found ${missingAssets.length} missing media files. ${oversizeAssets.length} file(s) exceed your configured size limits, are disabled by type permissions, or excluded by user/bot rules.`;

            }

            if (statsBox) {

                statsBox.innerHTML = `

                    <div style="display: flex; justify-content: space-between;">

                        <span>Within configured limits:</span>

                        <b>${withinLimitAssets.length} files (~${withinMb} MB)</b>

                    </div>

                    <div style="display: flex; justify-content: space-between; color: #ffa726;">

                        <span>Exceeding limits or excluded by rules:</span>

                        <b>${oversizeAssets.length} files (~${overMb} MB)</b>

                    </div>

                `;

            }



            modal.style.display = 'flex';



            const withinBtn = document.getElementById('catchup-within-limits-btn');

            const bypassBtn = document.getElementById('catchup-bypass-limits-btn');



            if (withinLimitAssets.length > 0) {

                withinBtn.disabled = false;

                withinBtn.textContent = `Download Within Limits (${withinLimitAssets.length} files)`;

                withinBtn.style.opacity = '1';

                withinBtn.style.cursor = 'pointer';

            } else {

                withinBtn.disabled = true;

                withinBtn.textContent = 'Download Within Limits (0 eligible)';

                withinBtn.style.opacity = '0.5';

                withinBtn.style.cursor = 'not-allowed';

            }



            withinBtn.onclick = () => {

                if (withinLimitAssets.length === 0) return;

                modal.style.display = 'none';

                startCatchupDownload(false);

            };

            bypassBtn.onclick = () => {

                modal.style.display = 'none';

                startCatchupDownload(true);

            };

            return;

        }

    }



    startCatchupDownload(false);

}



async function startCatchupDownload(bypassLimits, priorityMsgIds) {

    if (!currentInspectorChatId) return;

    const btn = document.getElementById('asset-trigger-catchup-btn');

    const exclBtn = document.getElementById('asset-trigger-excluded-catchup-btn');

    if (btn) {

        btn.disabled = true;

        btn.textContent = '⚡ Catch-up in progress...';

    }

    if (exclBtn) {

        exclBtn.disabled = true;

        exclBtn.textContent = '⚡ Downloading excluded...';

    }



    const banner = document.getElementById('asset-catchup-banner');

    if (banner) {

        banner.style.display = 'flex';

        const st = document.getElementById('asset-catchup-status-text');

        const li = document.getElementById('asset-catchup-last-item');

        const pr = document.getElementById('asset-catchup-progress-ratio');

        const pb = document.getElementById('asset-catchup-progress-bar');

        if (st) st.textContent = 'Queued (Media Catch-up)...';

        if (li) li.textContent = '';

        if (pr) pr.textContent = '0%';

        if (pb) pb.style.width = '0%';

    }



    try {

        const payload = {

            chat_id: currentInspectorChatId,

            bypass_limits: bypassLimits,

            settings: currentInspectorChatSettings || {}

        };

        if (priorityMsgIds && priorityMsgIds.length > 0) {

            payload.priority_msg_ids = priorityMsgIds;

            payload.include_msg_ids = priorityMsgIds;

        }



        const resp = await fetch('/api/trigger_media_catchup', {

            method: 'POST',

            headers: {

                'Content-Type': 'application/json',

                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

            },

            body: JSON.stringify(payload)

        });

        const data = await resp.json();

        if (data.ok) {

            if (typeof showNotification === 'function') {

                showNotification(data.message || 'Media catch-up task enqueued!', 'success');

            }

            if (typeof startArchiveStream === 'function') {

                startArchiveStream();

            }

        } else {

            alert(data.error || 'Failed to trigger media catch-up');

            if (btn) {

                btn.disabled = false;

                btn.textContent = '⚡ Download Missing Media';

            }

            if (exclBtn) {

                exclBtn.disabled = false;

                exclBtn.textContent = '⚡ Download Excluded Media';

            }

            if (banner) banner.style.display = 'none';

        }

    } catch (err) {

        alert(`Error: ${err.message}`);

        if (btn) {

            btn.disabled = false;

            btn.textContent = '⚡ Download Missing Media';

        }

        if (exclBtn) {

            exclBtn.disabled = false;

            exclBtn.textContent = '⚡ Download Excluded Media';

        }

        if (banner) banner.style.display = 'none';

    }

}



function updateInspectorLiveCatchup(msg) {

    const banner = document.getElementById('asset-catchup-banner');

    const statusTextEl = document.getElementById('asset-catchup-status-text');

    const lastItemEl = document.getElementById('asset-catchup-last-item');

    const ratioEl = document.getElementById('asset-catchup-progress-ratio');

    const barEl = document.getElementById('asset-catchup-progress-bar');

    const triggerBtn = document.getElementById('asset-trigger-catchup-btn');

    const exclBtn = document.getElementById('asset-trigger-excluded-catchup-btn');



    if (msg.status === 'running' || msg.status === 'queued') {

        if (banner) banner.style.display = 'flex';

        if (triggerBtn) {

            triggerBtn.disabled = true;

            triggerBtn.textContent = '⚡ Catch-up in progress...';

        }

        if (exclBtn) {

            exclBtn.disabled = true;

            exclBtn.textContent = '⚡ Downloading excluded...';

        }

        if (statusTextEl) statusTextEl.textContent = msg.text || msg.status_text || 'Catching up media...';

        if (lastItemEl && msg.last_item) lastItemEl.textContent = `File: ${msg.last_item}`;



        if (msg.total && msg.total > 0 && typeof msg.processed === 'number') {

            const pct = Math.min(100, Math.round((msg.processed / msg.total) * 100));

            if (ratioEl) ratioEl.textContent = `${pct}% (${msg.processed}/${msg.total})`;

            if (barEl) barEl.style.width = `${pct}%`;

        }



        // 1. Mark completed / downloaded items

        if (msg.downloaded_id) {

            const dMid = parseInt(msg.downloaded_id);

            const dRow = document.getElementById(`asset-row-${dMid}`);

            if (dRow) dRow.classList.remove('asset-row-downloading');

            const dStatus = document.getElementById(`asset-status-${dMid}`);

            if (dStatus) dStatus.innerHTML = '<span style="color: #00c853; font-weight: 500;">✓ Downloaded</span>';

            const dAction = document.getElementById(`asset-action-${dMid}`);

            if (dAction) dAction.innerHTML = '<span style="color: #00c853; font-size: 11px;">✓ Saved</span>';

            if (currentInspectorAssets && currentInspectorAssets.length > 0) {

                const item = currentInspectorAssets.find(a => a.message_id === dMid);

                if (item) {

                    item.media_local_path = 'saved';

                    item.media_skipped = null;

                }

                updateInspectorSummaryCounters();

            }

        }



        // 2. Mark failed items

        if (msg.failed_id) {

            const fMid = parseInt(msg.failed_id);

            const fRow = document.getElementById(`asset-row-${fMid}`);

            if (fRow) fRow.classList.remove('asset-row-downloading');

            const fStatus = document.getElementById(`asset-status-${fMid}`);

            if (fStatus) {

                const errTitle = escapeHTML(msg.error_text || 'Download failed');

                fStatus.innerHTML = `<span style="color: #ff5252; font-weight: 500;" title="${errTitle}">⚠️ Failed</span>`;

            }

        }



        // 3. Highlight currently downloading asset row & show byte progress

        if (msg.last_item) {

            const mIdMatch = msg.last_item.match(/^(\d+)/);

            if (mIdMatch) {

                const targetMid = parseInt(mIdMatch[1]);

                if (!msg.downloaded_id || parseInt(msg.downloaded_id) !== targetMid) {

                    const prevActive = document.querySelector('.asset-row-downloading');

                    if (prevActive && prevActive.id !== `asset-row-${targetMid}`) {

                        prevActive.classList.remove('asset-row-downloading');

                    }

                    const row = document.getElementById(`asset-row-${targetMid}`);

                    if (row) {

                        row.classList.add('asset-row-downloading');

                        const statusCell = document.getElementById(`asset-status-${targetMid}`);

                        if (statusCell && !statusCell.textContent.includes('Downloaded')) {

                            let progText = 'Downloading...';

                            if (msg.bytes_current && msg.bytes_total) {

                                const bPct = Math.round((msg.bytes_current / msg.bytes_total) * 100);

                                const currMb = (msg.bytes_current / (1024 * 1024)).toFixed(1);

                                const totMb = (msg.bytes_total / (1024 * 1024)).toFixed(1);

                                progText = `${currMb}/${totMb} MB (${bPct}%)`;

                            }

                            statusCell.innerHTML = `<span style="color: #2196f3; font-weight: 500;"><span class="asset-spinner" style="display:inline-block;width:10px;height:10px;border:1.5px solid #2196f3;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;margin-right:4px;"></span>${progText}</span>`;

                        }

                    }

                }

            }

        }

    } else if (msg.status === 'done') {

        const prevActive = document.querySelector('.asset-row-downloading');

        if (prevActive) prevActive.classList.remove('asset-row-downloading');

        if (statusTextEl) statusTextEl.textContent = '✓ Media catch-up complete!';

        if (lastItemEl) lastItemEl.textContent = '';

        if (ratioEl) ratioEl.textContent = '100%';

        if (barEl) barEl.style.width = '100%';

        if (triggerBtn) {

            triggerBtn.disabled = false;

            triggerBtn.textContent = '⚡ Download Missing Media';

        }

        if (exclBtn) {

            exclBtn.disabled = false;

            exclBtn.textContent = '⚡ Download Excluded Media';

        }

        refreshInspectorAssetsSilently();

        setTimeout(() => {

            if (banner) banner.style.display = 'none';

        }, 4000);

    } else if (msg.status === 'error') {

        const prevActive = document.querySelector('.asset-row-downloading');

        if (prevActive) prevActive.classList.remove('asset-row-downloading');

        if (statusTextEl) statusTextEl.textContent = `Error: ${msg.text || msg.status_text || 'Catch-up failed'}`;

        if (triggerBtn) {

            triggerBtn.disabled = false;

            triggerBtn.textContent = '⚡ Download Missing Media';

        }

        if (exclBtn) {

            exclBtn.disabled = false;

            exclBtn.textContent = '⚡ Download Excluded Media';

        }

    }

}



async function refreshInspectorAssetsSilently() {

    if (!currentInspectorChatId) return;

    try {

        const [assetsRes, settingsRes] = await Promise.all([

            fetch(`/api/chat_media_assets?chat_id=${currentInspectorChatId}`),

            fetch(`/api/chat_settings?chat_id=${currentInspectorChatId}`)

        ]);

        if (assetsRes.ok) {

            const assetsData = await assetsRes.json();

            currentInspectorAssets = assetsData.assets || [];

            if (settingsRes.ok) {

                const sData = await settingsRes.json();

                currentInspectorChatSettings = (sData && sData.settings) ? sData.settings : (sData || {});

            }

            updateInspectorSummaryCounters();

            const activePill = document.querySelector('.asset-filter-pill.active');

            renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all');

        }

    } catch (e) {

        console.debug('Failed to silently refresh inspector assets', e);

    }

}



document.addEventListener('DOMContentLoaded', () => {

    initChatSettingsPopover();



    // Asset filter pills

    document.querySelectorAll('.asset-filter-pill').forEach(btn => {

        btn.addEventListener('click', () => {

            document.querySelectorAll('.asset-filter-pill').forEach(b => {

                b.classList.remove('active');

                b.style.background = 'none';

                b.style.border = '1px solid var(--border-color)';

                b.style.color = (b.dataset.type === 'excluded') ? '#e91e63' : 'var(--text-color)';

            });

            btn.classList.add('active');

            btn.style.background = (btn.dataset.type === 'excluded') ? '#e91e63' : 'var(--primary-color, #2481cc)';

            btn.style.borderColor = 'transparent';

            btn.style.color = 'white';



            const isExcl = (btn.dataset.type === 'excluded');

            const exclBtn = document.getElementById('asset-trigger-excluded-catchup-btn');

            const catchupBtn = document.getElementById('asset-trigger-catchup-btn');

            if (exclBtn) exclBtn.style.display = isExcl ? 'inline-block' : 'none';

            if (catchupBtn) catchupBtn.style.display = isExcl ? 'none' : 'inline-block';



            const activePill = document.querySelector('.asset-filter-pill.active');

            renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', document.getElementById('asset-search-input')?.value || '');

        });

    });



    // Asset Inspector Search & Jump

    const assetSearchInput = document.getElementById('asset-search-input');

    const assetSearchClear = document.getElementById('asset-search-clear');

    const assetJumpBtn = document.getElementById('asset-jump-btn');



    if (assetSearchInput) {

        let searchTimer = null;

        assetSearchInput.addEventListener('input', () => {

            if (assetSearchClear) {

                assetSearchClear.style.display = assetSearchInput.value ? 'inline-block' : 'none';

            }

            clearTimeout(searchTimer);

            searchTimer = setTimeout(() => {

                if (!currentInspectorAssets) return;

                const activePill = document.querySelector('.asset-filter-pill.active');

                renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', assetSearchInput.value);

            }, 200);

        });



        assetSearchInput.addEventListener('keydown', (e) => {

            if (e.key === 'Enter') {

                e.preventDefault();

                window.handleAssetInspectorJump();

            }

        });

    }



    if (assetSearchClear) {

        assetSearchClear.addEventListener('click', () => {

            if (assetSearchInput) assetSearchInput.value = '';

            assetSearchClear.style.display = 'none';

            if (currentInspectorAssets) {

                const activePill = document.querySelector('.asset-filter-pill.active');

                renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', '');

            }

        });

    }



    if (assetJumpBtn) {

        assetJumpBtn.addEventListener('click', () => {

            window.handleAssetInspectorJump();

        });

    }



    const chatterFilterEl = document.getElementById('asset-chatter-filter');

    if (chatterFilterEl) {

        chatterFilterEl.addEventListener('change', () => {

            if (!currentInspectorAssets) return;

            const activePill = document.querySelector('.asset-filter-pill.active');

            renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', document.getElementById('asset-search-input')?.value || '');

        });

    }



    // Trigger Catch-up Download Button (with bounds evaluation)

    document.getElementById('asset-trigger-catchup-btn')?.addEventListener('click', async () => {

        if (!currentInspectorChatId) return;

        evaluateCatchupBounds(currentInspectorChatId);

    });



    // Trigger Excluded Media Catch-up Button

    document.getElementById('asset-trigger-excluded-catchup-btn')?.addEventListener('click', async () => {

        if (!currentInspectorChatId || !currentInspectorAssets) return;

        const excludedMissing = currentInspectorAssets.filter(a => !a.media_local_path && isAssetUserExcluded(a));

        if (excludedMissing.length === 0) {

            if (typeof showNotification === 'function') {

                showNotification('No missing excluded media found to download.', 'info');

            } else {

                alert('No missing excluded media found to download.');

            }

            return;

        }

        const ids = excludedMissing.map(a => a.message_id);

        const totalMb = (excludedMissing.reduce((sum, a) => sum + (a.size_bytes || 0), 0) / (1024 * 1024)).toFixed(1);

        if (confirm(`Download ${ids.length} excluded media file(s) (~${totalMb} MB), bypassing exclusion rules?`)) {

            startCatchupDownload(true, ids);

        }

    });



    // Not Downloaded Checkbox Filter Listener

    document.getElementById('asset-filter-not-downloaded')?.addEventListener('change', () => {

        if (!currentInspectorAssets) return;

        const activePill = document.querySelector('.asset-filter-pill.active');

        renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', document.getElementById('asset-search-input')?.value || '');

    });



    // Asset Inspector Table Column Sort Headers Listener

    document.addEventListener('click', (e) => {

        const th = e.target.closest('#asset-inspector-table thead th.asset-sort-th');

        if (!th || !currentInspectorAssets || currentInspectorAssets.length === 0) return;

        const sortField = th.dataset.sort;

        if (!sortField) return;



        if (currentAssetSort.field === sortField) {

            currentAssetSort.dir = (currentAssetSort.dir === 'asc') ? 'desc' : 'asc';

        } else {

            currentAssetSort.field = sortField;

            currentAssetSort.dir = (sortField === 'size' || sortField === 'date' || sortField === 'id') ? 'desc' : 'asc';

        }



        const activePill = document.querySelector('.asset-filter-pill.active');

        renderAssetTable(currentInspectorAssets, activePill ? activePill.dataset.type : 'all', document.getElementById('asset-search-input')?.value || '');

    });

});

