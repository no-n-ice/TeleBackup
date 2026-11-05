// static/js/chat_settings.js — Per-Chat Settings Popover, User Exclusion Rules & Download Estimator

function positionFloatingElement(el, targetRect) {

    el.style.display = 'block';

    const elWidth = el.offsetWidth || 250;

    const elHeight = el.offsetHeight || 220;

    const viewportHeight = window.innerHeight;

    const viewportWidth = window.innerWidth;

    

    let top;

    if (targetRect.bottom + elHeight + 10 > viewportHeight && targetRect.top - elHeight - 10 > 0) {

        top = targetRect.top + window.scrollY - elHeight - 5;

    } else {

        top = Math.min(targetRect.bottom + window.scrollY + 5, window.scrollY + viewportHeight - elHeight - 10);

    }

    top = Math.max(window.scrollY + 10, top);



    let left = targetRect.left + window.scrollX;

    if (targetRect.left + elWidth + 10 > viewportWidth) {

        left = Math.max(10, viewportWidth - elWidth - 15) + window.scrollX;

    }

    left = Math.max(window.scrollX + 10, left);

    

    el.style.top = `${top}px`;

    el.style.left = `${left}px`;

}



function initChatSettingsPopover() {

    const popover = document.getElementById('chat-settings-popover');

    if (!popover) return;



    window._currentUserRules = window._currentUserRules || {};

    let currentEstimatedCatchup = { count: 0, bytes: 0, chatId: null, settings: {} };

    let estimateTimer = null;



    function renderUserRulesList() {

        const container = document.getElementById('cs-user-rules-list');

        if (!container) return;

        const rules = window._currentUserRules || {};

        const keys = Object.keys(rules);

        if (keys.length === 0) {

            container.innerHTML = '<span style="opacity:0.6; font-size:10px;">No exclusion rules configured.</span>';

            return;

        }

        container.innerHTML = keys.map(uid => {

            const r = rules[uid] || {};

            const icon = r.is_bot ? '🤖' : '👤';

            const name = r.user_name || `ID ${uid}`;

            let desc = (r.mode === 'exclude_all') ? 'All media' : 'Custom';

            if (r.mode === 'custom') {

                const types = [];

                ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(t => {

                    if (r[t] && r[t].enabled === false) types.push(t);

                    else if (r[t] && r[t].max_mb > 0) types.push(`${t}≤${r[t].max_mb}MB`);

                });

                if (types.length) desc = types.join(', ');

            }

            return `

                <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.04); border:1px solid var(--border-color); border-radius:4px; padding:3px 6px;">

                    <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:180px;" title="${escapeHTML(name)} (${uid}): ${escapeHTML(desc)}">

                        <span>${icon} <b>${escapeHTML(name)}</b></span>

                        <span style="opacity:0.6; font-size:10px; margin-left:4px;">(${escapeHTML(desc)})</span>

                    </div>

                    <button type="button" class="cs-delete-rule-btn" data-user-id="${uid}" style="background:none; border:none; color:#ff5252; cursor:pointer; font-size:12px; padding:0 2px;" title="Remove rule">✕</button>

                </div>

            `;

        }).join('');



        container.querySelectorAll('.cs-delete-rule-btn').forEach(btn => {

            btn.addEventListener('click', async (e) => {

                e.preventDefault();

                e.stopPropagation();

                const uid = btn.dataset.userId;

                if (window._currentUserRules) {

                    delete window._currentUserRules[uid];

                    renderUserRulesList();

                    debouncedUpdateEstimate();



                    // Auto-persist rule deletion to backend

                    const currentChatId = popover.dataset.chatId;

                    if (currentChatId) {

                        try {

                            const pSettings = collectPopoverSettings();

                            pSettings.user_rules = window._currentUserRules || {};

                            await fetch('/api/save_chat_settings', {

                                method: 'POST',

                                headers: {

                                    'Content-Type': 'application/json',

                                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                                },

                                body: JSON.stringify({ chat_id: currentChatId, settings: pSettings })

                            });

                        } catch (err) {

                            console.error('Failed to auto-save rule deletion:', err);

                        }

                    }

                }

            });

        });

    }

    window.renderUserRulesList = renderUserRulesList;



    function collectPopoverSettings() {

        const settings = {};

        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

            const elEnabled = document.getElementById(`cs-${key}-enabled`);

            const elMax = document.getElementById(`cs-${key}-max`);

            settings[key] = {

                enabled: elEnabled ? elEnabled.checked : true,

                max_mb: elMax ? (parseInt(elMax.value) || 0) : 0

            };

        });

        const csEmojiEl = document.getElementById('cs-custom_emoji-enabled');

        if (csEmojiEl) {

            settings.custom_emoji = { enabled: csEmojiEl.checked };

        }

        const skipLinkedEl = document.getElementById('cs-skip-linked');

        if (skipLinkedEl) settings.skip_linked = skipLinkedEl.checked;



        const linkedSettings = {};

        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

            const elEn = document.getElementById(`cs-linked-${key}-enabled`);

            const elMax = document.getElementById(`cs-linked-${key}-max`);

            linkedSettings[key] = {

                enabled: elEn ? elEn.checked : true,

                max_mb: elMax ? (parseInt(elMax.value) || 0) : 0

            };

        });

        settings.linked_settings = linkedSettings;



        const rangeModeEl = document.querySelector('input[name="cs-range-mode"]:checked');

        const rangeMode = rangeModeEl ? rangeModeEl.value : 'all';

        settings.archive_range_mode = rangeMode;

        if (rangeMode === 'latest_days') {

            settings.archive_range_days = parseInt(document.getElementById('cs-range-latest-days')?.value) || 30;

        } else if (rangeMode === 'oldest_days') {

            settings.archive_range_days = parseInt(document.getElementById('cs-range-oldest-days')?.value) || 30;

        } else if (rangeMode === 'custom_range') {

            settings.archive_range_start = document.getElementById('cs-range-start-date')?.value || null;

            settings.archive_range_end = document.getElementById('cs-range-end-date')?.value || null;

        }

        return settings;

    }



    function updatePopoverSummaryStrip(settings, chatId) {
        const cspSummaryEl = document.getElementById('csp-summary-text');
        const cspStatusEl = document.getElementById('csp-summary-status');
        if (!cspSummaryEl) return;

        const chat = (window.chatListData || []).find(c => String(c.chat_id) === String(chatId)) || window.currentChatData;

        // 1. Status badge on the right
        if (cspStatusEl) {
            if (chat && chat.backup_status) {
                const st = String(chat.backup_status);
                cspStatusEl.textContent = st.charAt(0).toUpperCase() + st.slice(1);
                cspStatusEl.style.color = (st === 'complete') ? '#10b981' : ((st === 'halted' || st === 'error') ? '#ef4444' : 'var(--link-color)');
            } else if (chat && chat.last_backup_offset_id > 0) {
                cspStatusEl.textContent = 'Archived';
                cspStatusEl.style.color = '#10b981';
            } else {
                cspStatusEl.textContent = '';
            }
        }

        // 2. Summary text on the left
        settings = settings || {};
        const isGlobal = !settings || Object.keys(settings).length === 0;
        if (isGlobal) {
            const globalDefStr = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.using_global_default') : 'Using Global Default';
            cspSummaryEl.innerHTML = `<span>⚙️ ${globalDefStr}</span>`;
            return;
        }

        const emojis = { photos: '📷', videos: '🎥', voice: '🎤', video_notes: '⏺', documents: '📄' };
        let items = [];

        if (settings.skip_linked) {
            const skipLabel = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.skip_linked') : 'Skip linked';
            items.push(`🚫 ${skipLabel}`);
        }

        const uRules = settings.user_rules || window._currentUserRules || {};
        const rCount = Object.keys(uRules).length;
        if (rCount > 0) {
            items.push(`🤖 ${rCount} rule${rCount > 1 ? 's' : ''}`);
        }

        let enabledMedia = [];
        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(k => {
            const v = settings[k];
            if (v && v.enabled) {
                const cap = (v.max_mb !== undefined && v.max_mb !== null && v.max_mb > 0) ? `${v.max_mb}M` : '∞';
                enabledMedia.push(`${emojis[k] || ''}${cap}`);
            }
        });

        if (enabledMedia.length > 0) {
            items.push(enabledMedia.join(' '));
        } else {
            const textOnlyStr = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.text_only') : 'Text only';
            items.push(`📝 ${textOnlyStr}`);
        }

        const rMode = settings.archive_range_mode || 'all';
        if (rMode === 'latest_days' && settings.archive_range_days) {
            items.push(`📅 Last ${settings.archive_range_days}d`);
        } else if (rMode === 'custom_range') {
            items.push('📅 Custom range');
        }

        cspSummaryEl.innerHTML = items.join(' · ');
    }
    window.updatePopoverSummaryStrip = updatePopoverSummaryStrip;

    async function updatePopoverEstimate() {

        const currentChatId = popover.dataset.chatId;

        if (!currentChatId) return;



        const settings = collectPopoverSettings();

        settings.user_rules = window._currentUserRules || {};
        updatePopoverSummaryStrip(settings, currentChatId);



        try {

            const res = await fetch('/api/estimate_media_catchup', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                },

                body: JSON.stringify({ chat_id: currentChatId, settings: settings })

            });

            if (res.ok) {

                const data = await res.json();

                currentEstimatedCatchup = {

                    count: data.applicable_count || 0,

                    bytes: data.estimated_bytes || 0,

                    chatId: currentChatId,

                    settings: settings

                };

                const cntEl = document.getElementById('cs-est-count');

                const szEl = document.getElementById('cs-est-size');

                if (cntEl) cntEl.textContent = currentEstimatedCatchup.count;

                if (szEl) szEl.textContent = `${(currentEstimatedCatchup.bytes / (1024 * 1024)).toFixed(1)} MB`;



                const exclCntEl = document.getElementById('cs-excluded-count');

                const exclSzEl = document.getElementById('cs-excluded-size');

                const exclCount = data.excluded_by_rules_count || 0;

                const exclBytes = data.excluded_by_rules_bytes || 0;

                if (exclCntEl) exclCntEl.textContent = exclCount;

                if (exclSzEl) exclSzEl.textContent = `${(exclBytes / (1024 * 1024)).toFixed(1)} MB`;

            }

        } catch (e) {

            console.error('Estimate error:', e);

        }

    }



    function debouncedUpdateEstimate() {

        if (estimateTimer) clearTimeout(estimateTimer);

        estimateTimer = setTimeout(updatePopoverEstimate, 300);

    }



    popover.querySelectorAll('input').forEach(inp => {

        inp.addEventListener('input', debouncedUpdateEstimate);

        inp.addEventListener('change', debouncedUpdateEstimate);

    });

    window.updatePopoverEstimate = updatePopoverEstimate;



    // Range mode radio toggle

    document.querySelectorAll('input[name="cs-range-mode"]').forEach(radio => {

        radio.addEventListener('change', (e) => {

            const customDateBox = document.getElementById('cs-custom-date-box');

            if (customDateBox) {

                customDateBox.style.display = (e.target.value === 'custom_range') ? 'block' : 'none';

            }

        });

    });



    // Add User Rule Button Click & Modal Setup

    document.getElementById('cs-add-user-rule-btn')?.addEventListener('click', async (e) => {

        e.preventDefault();

        const currentChatId = popover.dataset.chatId;

        if (!currentChatId) return;



        const modal = document.getElementById('modal-user-rule');

        if (!modal) return;

        modal.style.display = 'flex';



        const select = document.getElementById('user-rule-sender-select');

        const manualInput = document.getElementById('user-rule-manual-id');

        const customBox = document.getElementById('user-rule-custom-box');

        if (select) select.innerHTML = '<option value="">Loading senders...</option>';

        if (manualInput) manualInput.value = '';

        const allRadio = document.querySelector('input[name="user-rule-mode"][value="exclude_all"]');

        if (allRadio) allRadio.checked = true;

        if (customBox) customBox.style.display = 'none';



        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(t => {

            const cb = document.getElementById(`ur-${t}-exclude`);

            const mx = document.getElementById(`ur-${t}-max`);

            if (cb) cb.checked = (t === 'photos' || t === 'videos');

            if (mx) mx.value = '';

        });



        try {

            const res = await fetch(`/api/chat_senders?chat_id=${currentChatId}`);

            if (res.ok) {

                const data = await res.json();

                const senders = data.senders || [];

                window._lastFetchedSenders = senders;

                if (select) {

                    if (senders.length === 0) {

                        select.innerHTML = '<option value="">No senders recorded yet (enter ID manually)</option>';

                    } else {

                        select.innerHTML = '<option value="">-- Choose Bot or User --</option>' + senders.map(s => {

                            const uid = s.sender_id || s.user_id;

                            const botBadge = s.is_bot ? '🤖 [Bot]' : '👤';

                            const bytes = (s.total_media_bytes !== undefined) ? s.total_media_bytes : (s.excluded_bytes || 0);

                            const mb = (bytes / (1024 * 1024)).toFixed(1);

                            const name = s.display_name || s.name || s.username || `User ${uid}`;

                            const label = `${botBadge} ${name} (${s.media_messages || 0} media, ${mb} MB)`;

                            return `<option value="${uid}" data-name="${escapeHTML(name)}" data-is-bot="${s.is_bot ? 1 : 0}">${escapeHTML(label)}</option>`;

                        }).join('');

                    }

                }

            }

        } catch (err) {

            if (select) select.innerHTML = '<option value="">Failed to load senders (enter ID manually)</option>';

        }

    });



    document.getElementById('user-rule-sender-select')?.addEventListener('change', (e) => {

        const val = e.target.value;

        const manualInput = document.getElementById('user-rule-manual-id');

        if (manualInput && val) {

            manualInput.value = val;

        }

    });



    document.querySelectorAll('input[name="user-rule-mode"]').forEach(r => {

        r.addEventListener('change', (e) => {

            const customBox = document.getElementById('user-rule-custom-box');

            if (customBox) {

                customBox.style.display = (e.target.value === 'custom') ? 'block' : 'none';

            }

        });

    });



    document.getElementById('user-rule-save-btn')?.addEventListener('click', async (e) => {

        e.preventDefault();

        const select = document.getElementById('user-rule-sender-select');

        const manualInput = document.getElementById('user-rule-manual-id');

        let uid = (manualInput && manualInput.value.trim()) ? manualInput.value.trim() : (select ? select.value : '');

        if (!uid || uid === 'undefined') {

            alert('Please select a sender or enter a User/Bot ID.');

            return;

        }



        let selectedOpt = select ? select.querySelector(`option[value="${CSS.escape ? CSS.escape(uid) : uid}"]`) : null;

        let userName = selectedOpt ? (selectedOpt.dataset.name || selectedOpt.textContent.split('(')[0].trim()) : `User ${uid}`;

        let isBot = selectedOpt ? (selectedOpt.dataset.isBot === '1') : false;



        const cleanInput = uid.replace(/^@/, '').toLowerCase();

        const matchingSender = (window._lastFetchedSenders || []).find(s =>

            String(s.user_id || s.sender_id) === uid ||

            (s.username && s.username.toLowerCase() === cleanInput) ||

            (s.name && s.name.toLowerCase() === cleanInput) ||

            (s.display_name && s.display_name.toLowerCase() === cleanInput)

        );

        if (matchingSender) {

            uid = String(matchingSender.user_id || matchingSender.sender_id);

            userName = matchingSender.display_name || matchingSender.name || userName;

            isBot = Boolean(matchingSender.is_bot);

        }



        const modeEl = document.querySelector('input[name="user-rule-mode"]:checked');

        const mode = modeEl ? modeEl.value : 'exclude_all';



        const ruleObj = {

            user_name: userName,

            is_bot: isBot,

            mode: mode

        };



        if (mode === 'custom') {

            ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(t => {

                const cb = document.getElementById(`ur-${t}-exclude`);

                const mx = document.getElementById(`ur-${t}-max`);

                const isExcluded = cb ? cb.checked : false;

                const maxMb = mx ? (parseInt(mx.value) || 0) : 0;

                ruleObj[t] = {

                    enabled: !isExcluded,

                    max_mb: maxMb

                };

            });

        }



        if (!window._currentUserRules) window._currentUserRules = {};

        window._currentUserRules[String(uid)] = ruleObj;



        const modal = document.getElementById('modal-user-rule');

        if (modal) modal.style.display = 'none';



        renderUserRulesList();

        debouncedUpdateEstimate();



        // Auto-persist immediately so rules are never lost if popover is closed or reopened

        const currentChatId = popover.dataset.chatId;

        if (currentChatId) {

            try {

                const pSettings = collectPopoverSettings();

                pSettings.user_rules = window._currentUserRules || {};

                await fetch('/api/save_chat_settings', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                    },

                    body: JSON.stringify({ chat_id: currentChatId, settings: pSettings })

                });

            } catch (err) {

                console.error('Failed to auto-save user rule:', err);

            }

        }

    });



    // Inspect Media Assets button click

    const handleOpenAssetInspector = (e) => {
        e.preventDefault();
        const currentChatId = popover.dataset.chatId;
        const chatTitle = document.getElementById('chat-settings-title')?.textContent || '';
        const chatName = chatTitle.split(' — ')[0] || '';
        if (currentChatId) {
            popover.style.display = 'none';
            const bubble = document.getElementById('chat-summary-bubble');
            if (bubble) bubble.style.display = 'none';
            openAssetInspector(currentChatId, chatName);
        }
    };

    document.getElementById('cs-inspect-assets-btn')?.addEventListener('click', handleOpenAssetInspector);
    document.getElementById('cs-estimate-badge')?.addEventListener('click', handleOpenAssetInspector);
    document.getElementById('cs-excluded-badge')?.addEventListener('click', handleOpenAssetInspector);

    document.getElementById('cs-reconcile-gaps-btn')?.addEventListener('click', (e) => {

        if (typeof window.triggerHorReconcileFromPopover === 'function') window.triggerHorReconcileFromPopover(e);

    });



    document.getElementById('cs-save-btn')?.addEventListener('click', async (e) => {

        e.preventDefault();

        const currentChatId = popover.dataset.chatId;

        if (!currentChatId) return;



        const settings = collectPopoverSettings();

        // Attach user rules

        settings.user_rules = window._currentUserRules || {};



        await fetch('/api/save_chat_settings', {

            method: 'POST',

            headers: {

                'Content-Type': 'application/json',

                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

            },

            body: JSON.stringify({chat_id: currentChatId, settings})

        });

        const trackCheck = document.getElementById('cs-track-participants-check');
        if (trackCheck) {
            const csrfToken = (window.getCsrfToken ? window.getCsrfToken() : '');
            fetch('/api/participants/toggle', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': csrfToken
                },
                body: JSON.stringify({ chat_id: currentChatId, enabled: trackCheck.checked, auth_csrf_token: csrfToken })
            }).catch(() => {});
        }

        popover.style.display = 'none';

        const bubble = document.getElementById('chat-summary-bubble');

        if (bubble) bubble.style.display = 'none';

        const rangeMode = settings.archive_range_mode || 'all';

        if (typeof window.updateChatRangeText === 'function') {

            window.updateChatRangeText(rangeMode, settings.archive_range_days, settings.archive_range_start, settings.archive_range_end);

        }

        if (typeof loadAllDialogs === 'function') loadAllDialogs(true);

        else if (typeof window.loadAllDialogs === 'function') window.loadAllDialogs(true);



        const isRunningTask = Boolean(

            archiveStatusCache &&

            archiveStatusCache[currentChatId] &&

            ['running', 'queued'].includes(archiveStatusCache[currentChatId].status)

        );

        if (!isRunningTask && currentEstimatedCatchup && currentEstimatedCatchup.count > 0 && currentEstimatedCatchup.chatId === currentChatId) {

            const modal = document.getElementById('chat-settings-download-modal');

            const summaryText = document.getElementById('cs-modal-summary');

            if (summaryText) {

                summaryText.textContent = `${currentEstimatedCatchup.count} files (~${(currentEstimatedCatchup.bytes / (1024 * 1024)).toFixed(1)} MB)`;

            }

            if (modal) modal.style.display = 'flex';

        }

    });



    document.getElementById('cs-modal-download-now-btn')?.addEventListener('click', async () => {

        const modal = document.getElementById('chat-settings-download-modal');

        if (modal) modal.style.display = 'none';

        if (!currentEstimatedCatchup || !currentEstimatedCatchup.chatId) return;



        const targetChatId = currentEstimatedCatchup.chatId;

        const targetSettings = currentEstimatedCatchup.settings || {};



        if (archiveStatusCache[targetChatId] && ['running', 'queued'].includes(archiveStatusCache[targetChatId].status)) {

            if (typeof showNotification === 'function') {

                showNotification('A task is already running for this chat!', 'info');

            }

            return;

        }



        archiveStatusCache[targetChatId] = {

            status: 'queued',

            processed: 0,

            total: currentEstimatedCatchup.count || 0,

            status_text: 'Queued (Media Catch-up)...',

            phase: 'catchup'

        };

        if (typeof updateArchiveUI === 'function') updateArchiveUI();

        if (typeof updateCurrentChatActiveTaskBanner === 'function') updateCurrentChatActiveTaskBanner();



        try {

            const resp = await fetch('/api/trigger_media_catchup', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                },

                body: JSON.stringify({ chat_id: targetChatId, settings: targetSettings })

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

                delete archiveStatusCache[targetChatId];

                if (typeof updateArchiveUI === 'function') updateArchiveUI();

                if (typeof updateCurrentChatActiveTaskBanner === 'function') updateCurrentChatActiveTaskBanner();

            }

        } catch (e) {

            alert(`Error: ${e.message}`);

            delete archiveStatusCache[targetChatId];

            if (typeof updateArchiveUI === 'function') updateArchiveUI();

            if (typeof updateCurrentChatActiveTaskBanner === 'function') updateCurrentChatActiveTaskBanner();

        }

    });



    document.getElementById('cs-modal-only-save-btn')?.addEventListener('click', () => {

        const modal = document.getElementById('chat-settings-download-modal');

        if (modal) modal.style.display = 'none';

    });



    document.getElementById('cs-reset-btn')?.addEventListener('click', async (e) => {

        e.preventDefault();

        const currentChatId = popover.dataset.chatId;

        if (!currentChatId) return;

        await fetch('/api/save_chat_settings', {

            method: 'POST',

            headers: {

                'Content-Type': 'application/json',

                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

            },

            body: JSON.stringify({chat_id: currentChatId, settings: null})

        });

        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

            const elEnabled = document.getElementById(`cs-${key}-enabled`);

            const elMax = document.getElementById(`cs-${key}-max`);

            if (elEnabled) elEnabled.checked = false;

            if (elMax) elMax.value = '';



            const elLnkEnabled = document.getElementById(`cs-linked-${key}-enabled`);

            const elLnkMax = document.getElementById(`cs-linked-${key}-max`);

            if (elLnkEnabled) elLnkEnabled.checked = true;

            if (elLnkMax) elLnkMax.value = '';

        });

        const skipLinkedEl = document.getElementById('cs-skip-linked');

        if (skipLinkedEl) skipLinkedEl.checked = false;



        const rangeAll = document.getElementById('cs-range-all');

        if (rangeAll) rangeAll.checked = true;

        const customDateBox = document.getElementById('cs-custom-date-box');

        if (customDateBox) customDateBox.style.display = 'none';



        window._currentUserRules = {};

        renderUserRulesList();

        debouncedUpdateEstimate();



        popover.style.display = 'none';

        const bubble = document.getElementById('chat-summary-bubble');

        if (bubble) bubble.style.display = 'none';

        if (typeof loadAllDialogs === 'function') loadAllDialogs(true);

        else if (typeof window.loadAllDialogs === 'function') window.loadAllDialogs(true);

    });

}




const bubbleStyle = document.createElement('style');

bubbleStyle.textContent = `

    #chat-summary-bubble {

        display: none; position: absolute; z-index: 3001; background: var(--panel-bg);

        border: 1px solid var(--border-color); border-radius: 8px; padding: 10px;

        box-shadow: 0 4px 12px rgba(0,0,0,0.3); font-size: 12px;

    }

    .summary-item { margin-right: 8px; }

    .asset-sort-th:hover { background: rgba(255,255,255,0.06); }

`;

document.head.appendChild(bubbleStyle);



const summaryHTML = `

    <div id="chat-summary-bubble">

        <div id="cs-summary-content" style="margin-bottom: 8px;"></div>

        <button id="cs-edit-btn" style="padding: 4px 8px; font-size: 12px; cursor: pointer; background: var(--link-color); border:none; color:white; border-radius:4px;" data-i18n="archive.edit_settings">Edit Settings</button>

    </div>

`;

document.body.insertAdjacentHTML('beforeend', summaryHTML);



document.addEventListener('click', async (e) => {

    const deleteListBtn = e.target.closest('.delete-chat-btn-list');

    if (deleteListBtn) {

        e.stopPropagation();

        const chatId = deleteListBtn.dataset.chatId;

        const chatName = deleteListBtn.dataset.chatName;

        if (typeof ChatManage !== 'undefined' && ChatManage.openDeleteModal) {

            ChatManage.openDeleteModal(chatId, chatName);

        } else if (window.ChatManage && window.ChatManage.openDeleteModal) {

            window.ChatManage.openDeleteModal(chatId, chatName);

        }

        return;

    }



    const btn = e.target.closest('.chat-settings-btn');

    const popover = document.getElementById('chat-settings-popover');

    const bubble = document.getElementById('chat-summary-bubble');

    

    if (btn) {

        e.stopPropagation();

        const currentChatId = btn.dataset.chatId || window.currentChatId;

        const chatName = btn.dataset.chatName || window.currentChatData?.chat_name || document.getElementById('chat-header-name')?.textContent?.trim() || 'Chat';

        const rect = btn.getBoundingClientRect();

        

        const hasLinked = Boolean(btn.dataset.linkedId || (window.chatListData && window.chatListData.find(c => String(c.chat_id) === String(currentChatId))?.linked_chat_id));

        const linkedContainer = document.getElementById('cs-linked-chat-container');

        if (linkedContainer) linkedContainer.style.display = hasLinked ? 'block' : 'none';



        if (popover) {
            popover.dataset.chatId = currentChatId;
            const settingsTitle = document.getElementById('chat-settings-title');
            if (settingsTitle) {
                const titleText = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.chat_settings') : 'Settings';
                settingsTitle.textContent = `${chatName} — ${titleText}`;
            }
            positionFloatingElement(popover, rect);
            popover.style.display = 'block';
            if (bubble) bubble.style.display = 'none';
        }

        try {
            fetch(`/api/participants/summary?chat_id=${currentChatId}`)
                .then(r => r.ok ? r.json() : null)
                .then(pData => {
                    if (pData) {
                        const pCheck = document.getElementById('cs-track-participants-check');
                        if (pCheck) pCheck.checked = Boolean(pData.track_participants);
                    }
                }).catch(() => {});
        } catch (_) {}



        const editBtn = document.getElementById('cs-edit-btn');

        if (editBtn && typeof I18N !== 'undefined' && I18N.t) {

            editBtn.textContent = I18N.t('archive.edit_settings');

        }

        

        if (editBtn) {

            editBtn.onclick = (ev) => {

                ev.stopPropagation();

                bubble.style.display = 'none';

                const settingsTitle = document.getElementById('chat-settings-title');

                if (settingsTitle) {

                    const titleText = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.chat_settings') : 'Settings';

                    settingsTitle.textContent = `${chatName} — ${titleText}`;

                }

                positionFloatingElement(popover, rect);

                if (typeof window.updatePopoverEstimate === 'function') {

                    window.updatePopoverEstimate();

                }

                if (typeof I18N !== 'undefined' && I18N.applyAll) {

                    I18N.applyAll();

                }

            };

        }



        let settings = {};

        try {

            const res = await fetch(`/api/chat_settings?chat_id=${currentChatId}`);

            if (res.ok) {

                const data = await res.json();

                settings = data || {}; 

            }

            

            window._currentUserRules = settings.user_rules || {};

            if (typeof renderUserRulesList === 'function') renderUserRulesList();



            ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

                const obj = settings[key] || {};

                const elEnabled = document.getElementById(`cs-${key}-enabled`);

                const elMax = document.getElementById(`cs-${key}-max`);

                if (elEnabled) elEnabled.checked = (obj.enabled !== undefined) ? Boolean(obj.enabled) : true;

                if (elMax) elMax.value = (obj.max_mb !== undefined && obj.max_mb !== null && obj.max_mb > 0) ? obj.max_mb : '';

            });

            const csEmojiEl = document.getElementById('cs-custom_emoji-enabled');

            if (csEmojiEl) {

                const ceObj = settings.custom_emoji || {};

                csEmojiEl.checked = (ceObj.enabled !== undefined) ? Boolean(ceObj.enabled) : true;

            }

            const skipLinkedEl = document.getElementById('cs-skip-linked');

            if (skipLinkedEl) skipLinkedEl.checked = Boolean(settings.skip_linked);



            const linked = settings.linked_settings || {};

            ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

                const obj = linked[key] || {};

                const elEnabled = document.getElementById(`cs-linked-${key}-enabled`);

                const elMax = document.getElementById(`cs-linked-${key}-max`);

                if (elEnabled) elEnabled.checked = (obj.enabled !== undefined) ? Boolean(obj.enabled) : true;

                if (elMax) elMax.value = (obj.max_mb !== undefined && obj.max_mb !== null && obj.max_mb > 0) ? obj.max_mb : '';

            });



            const updateLinkedOptionsState = () => {

                const linkedOptEl = document.getElementById('cs-linked-media-options');

                if (linkedOptEl && skipLinkedEl) {

                    if (skipLinkedEl.checked) {

                        linkedOptEl.style.opacity = '0.4';

                        linkedOptEl.style.pointerEvents = 'none';

                    } else {

                        linkedOptEl.style.opacity = '1.0';

                        linkedOptEl.style.pointerEvents = 'auto';

                    }

                }

            };

            if (skipLinkedEl) {

                skipLinkedEl.onchange = updateLinkedOptionsState;

                updateLinkedOptionsState();

            }



            // Load range settings into popover

            const rMode = settings.archive_range_mode || 'all';

            const rRadio = document.querySelector(`input[name="cs-range-mode"][value="${rMode}"]`);

            if (rRadio) rRadio.checked = true;

            if (settings.archive_range_days) {

                if (rMode === 'latest_days') {

                    const el = document.getElementById('cs-range-latest-days');

                    if (el) el.value = settings.archive_range_days;

                } else if (rMode === 'oldest_days') {

                    const el = document.getElementById('cs-range-oldest-days');

                    if (el) el.value = settings.archive_range_days;

                }

            }

            if (settings.archive_range_start) {

                const el = document.getElementById('cs-range-start-date');

                if (el) el.value = settings.archive_range_start;

            }

            if (settings.archive_range_end) {

                const el = document.getElementById('cs-range-end-date');

                if (el) el.value = settings.archive_range_end;

            }

            const customDateBox = document.getElementById('cs-custom-date-box');

            if (customDateBox) {

                customDateBox.style.display = (rMode === 'custom_range') ? 'block' : 'none';

            }



            const emojis = { photos: '📷', videos: '🎥', voice: '🎤', video_notes: '⏺', documents: '📄' };

            let html = '';

            for(const [k, v] of Object.entries(settings)) {

                if(k === 'skip_linked' && v) {

                    const label = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.skip_linked') : 'Skip linked chat';

                    html += `<span class="summary-item">🚫 ${label}</span>`;

                } else if(k === 'user_rules' && v && Object.keys(v).length > 0) {

                    const rCount = Object.keys(v).length;

                    html += `<span class="summary-item">🤖 ${rCount} rule${rCount > 1 ? 's' : ''}</span>`;

                } else if(v && v.enabled) {

                    const labelKey = `archive.${k}`;

                    const label = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t(labelKey) : k;

                    html += `<span class="summary-item">${emojis[k]||''} ${label}: ${v.max_mb>0 ? v.max_mb+'MB' : '∞'}</span>`;

                }

            }

            const textOnlyStr = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.text_only') : 'Text only (No Media)';

            const globalDefStr = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.using_global_default') : 'Using Global Default';

            

            if (!html) html = `<span>${textOnlyStr}</span>`;

            if (Object.keys(settings).length === 0) html = `<span>${globalDefStr}</span>`;

            

            const summaryEl = document.getElementById('cs-summary-content');
            if (summaryEl) summaryEl.innerHTML = html;
            const cspSummaryEl = document.getElementById('csp-summary-text');
            if (cspSummaryEl) cspSummaryEl.innerHTML = html;

            if (typeof window.updatePopoverEstimate === 'function') {

                window.updatePopoverEstimate();

            }

        } catch (err) {

            console.error(err);

        }

    } else if (

        !e.target.closest('#chat-summary-bubble') &&

        !e.target.closest('#chat-settings-popover') &&

        !e.target.closest('#chat-settings-header-btn') &&

        !e.target.closest('.chat-settings-btn') &&

        !e.target.closest('#modal-user-rule') &&

        !e.target.closest('#chat-settings-download-modal') &&

        !e.target.closest('#catchup-warning-modal') &&

        !e.target.closest('#asset-inspector-modal')

    ) {

        if(bubble) bubble.style.display = 'none';

        if(popover) popover.style.display = 'none';

    }

});



document.addEventListener('DOMContentLoaded', () => {

    const sInput = document.getElementById('archive-search-input');

    const sSort = document.getElementById('archive-sort-select');

    const sType = document.getElementById('archive-type-select');

    

    let archiveSearchTimeout = null;

    if(sInput) sInput.addEventListener('input', () => {

        clearTimeout(archiveSearchTimeout);

        archiveSearchTimeout = setTimeout(() => {

            if(window.applyArchiveFilters) window.applyArchiveFilters();

        }, 150);

    });

    if(sSort) sSort.addEventListener('change', () => { if(window.applyArchiveFilters) window.applyArchiveFilters(); });

    if(sType) sType.addEventListener('change', () => { if(window.applyArchiveFilters) window.applyArchiveFilters(); });

    

    // Issue #5: Wire folder tabs

    const folderTabs = document.querySelectorAll('#archive-folder-tabs .folder-tab');

    folderTabs.forEach(tab => {

        tab.addEventListener('click', () => {

            folderTabs.forEach(t => t.classList.remove('active'));

            tab.classList.add('active');

            const folder = tab.dataset.folder;

            if (sType) sType.value = folder;

            if (window.applyArchiveFilters) window.applyArchiveFilters();

        });

    });



    // Issue #5: Wire 1-col / 2-col switcher with localStorage (default '1')

    const savedCols = localStorage.getItem('archive_view_cols') || '1';

    const colBtns = document.querySelectorAll('#archive-col-switch .col-switch-btn');

    

    function applyColsView(cols) {

        colBtns.forEach(btn => {

            if (btn.dataset.cols === cols) btn.classList.add('active');

            else btn.classList.remove('active');

        });

        const currentList = document.getElementById('dialogs-list');

        if (currentList) {

            if (cols === '2') currentList.classList.add('view-2-col');

            else currentList.classList.remove('view-2-col');

        }

    }

    

    colBtns.forEach(btn => {

        btn.addEventListener('click', () => {

            const cols = btn.dataset.cols;

            localStorage.setItem('archive_view_cols', cols);

            applyColsView(cols);

        });

    });

    applyColsView(savedCols);

    

    document.addEventListener('change', (e) => {

        if(e.target.classList.contains('dialog-checkbox')) {

            if (!window.selectedArchiveChats) {

                window.selectedArchiveChats = new Set();

            }

            const cid = e.target.dataset.chatId;

            if (e.target.checked) window.selectedArchiveChats.add(String(cid));

            else window.selectedArchiveChats.delete(String(cid));

            

            updateStartArchiveButton();

        }

    });

});



window.openActiveChatSettings = async function(btnEl, ev) {

    if (ev && ev.stopPropagation) ev.stopPropagation();

    const chatId = window.currentChatId;

    if (!chatId) return;

    const popover = document.getElementById('chat-settings-popover');

    const bubble = document.getElementById('chat-summary-bubble');

    if (!popover) return;

    if (bubble) bubble.style.display = 'none';



    popover.dataset.chatId = chatId;

    const hasLinked = Boolean(window.currentChatData?.linked_chat_id || (window.chatListData && window.chatListData.find(c => String(c.chat_id) === String(chatId))?.linked_chat_id));

    const linkedContainer = document.getElementById('cs-linked-chat-container');

    if (linkedContainer) linkedContainer.style.display = hasLinked ? 'block' : 'none';



    const chatName = window.currentChatData?.chat_name || document.getElementById('chat-header-name')?.textContent || 'Chat';

    const settingsTitle = document.getElementById('chat-settings-title');

    if (settingsTitle) {

        const titleText = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.chat_settings') : 'Settings';

        settingsTitle.textContent = `${chatName} — ${titleText}`;

    }



    const rect = btnEl ? btnEl.getBoundingClientRect() : { top: 60, left: 200, bottom: 90, right: 230, width: 30, height: 30 };

    positionFloatingElement(popover, rect);



    try {

        const res = await fetch(`/api/chat_settings?chat_id=${chatId}`);

        let settings = {};

        if (res.ok) {

            settings = await res.json() || {};

        }



        window._currentUserRules = settings.user_rules || {};

        if (typeof renderUserRulesList === 'function') renderUserRulesList();



        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

            const obj = settings[key] || {};

            const elEnabled = document.getElementById(`cs-${key}-enabled`);

            const elMax = document.getElementById(`cs-${key}-max`);

            if (elEnabled) elEnabled.checked = (obj.enabled !== undefined) ? Boolean(obj.enabled) : true;

            if (elMax) elMax.value = (obj.max_mb !== undefined && obj.max_mb !== null && obj.max_mb > 0) ? obj.max_mb : '';

        });

        const csEmojiEl = document.getElementById('cs-custom_emoji-enabled');

        if (csEmojiEl) {

            const ceObj = settings.custom_emoji || {};

            csEmojiEl.checked = (ceObj.enabled !== undefined) ? Boolean(ceObj.enabled) : true;

        }

        const skipLinkedEl = document.getElementById('cs-skip-linked');

        if (skipLinkedEl) skipLinkedEl.checked = Boolean(settings.skip_linked);



        const linked = settings.linked_settings || {};

        ['photos', 'videos', 'voice', 'video_notes', 'documents'].forEach(key => {

            const obj = linked[key] || {};

            const elEnabled = document.getElementById(`cs-linked-${key}-enabled`);

            const elMax = document.getElementById(`cs-linked-${key}-max`);

            if (elEnabled) elEnabled.checked = (obj.enabled !== undefined) ? Boolean(obj.enabled) : true;

            if (elMax) elMax.value = (obj.max_mb !== undefined && obj.max_mb !== null && obj.max_mb > 0) ? obj.max_mb : '';

        });



        // Load range settings into popover

        const rMode = settings.archive_range_mode || 'all';

        const rRadio = document.querySelector(`input[name="cs-range-mode"][value="${rMode}"]`);

        if (rRadio) rRadio.checked = true;

        if (settings.archive_range_days) {

            if (rMode === 'latest_days') {

                const el = document.getElementById('cs-range-latest-days');

                if (el) el.value = settings.archive_range_days;

            } else if (rMode === 'oldest_days') {

                const el = document.getElementById('cs-range-oldest-days');

                if (el) el.value = settings.archive_range_days;

            }

        }

        if (settings.archive_range_start) {

            const el = document.getElementById('cs-range-start-date');

            if (el) el.value = settings.archive_range_start;

        }

        if (settings.archive_range_end) {

            const el = document.getElementById('cs-range-end-date');

            if (el) el.value = settings.archive_range_end;

        }

        const customDateBox = document.getElementById('cs-custom-date-box');

        if (customDateBox) {

            customDateBox.style.display = (rMode === 'custom_range') ? 'block' : 'none';

        }

        if (typeof updatePopoverSummaryStrip === 'function') {
            updatePopoverSummaryStrip(settings, chatId);
        }

        if (typeof window.updatePopoverEstimate === 'function') {

            window.updatePopoverEstimate();

        }

    } catch (err) {

        console.error('Error fetching chat settings:', err);

    }

    if (typeof I18N !== 'undefined' && I18N.applyAll) {

        I18N.applyAll();

    }

};


