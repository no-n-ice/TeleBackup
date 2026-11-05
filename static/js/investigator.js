/**
 * static/js/investigator.js — Lego Block Investigator frontend engine.
 * Interactive multi-dimensional Telegram archive analytics, drilldown, and charting.
 */

window.InvestigatorEngine = (function() {
    let barChartInstance = null;
    let donutChartInstance = null;
    let cachedGlobalMeta = null;
    let activePreset = 'heavyweight';
    let currentViewMode = 'split'; // 'split', 'charts', 'table'
    let currentRows = [];

    function safeEscape(str) {
        if (typeof window.escapeHTML === 'function') return window.escapeHTML(str);
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    async function loadMeta(chatId = null) {
        try {
            const url = chatId && chatId !== 'all' 
                ? `/api/investigate/meta?chat_id=${encodeURIComponent(chatId)}`
                : '/api/investigate/meta';
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Failed to fetch metadata (${res.status})`);
            const meta = await res.json();

            if (!cachedGlobalMeta && (!chatId || chatId === 'all')) {
                cachedGlobalMeta = meta;
            }

            // Populate chats dropdown once from global or current meta
            const chatsList = meta.chats || (cachedGlobalMeta ? cachedGlobalMeta.chats : []);
            if (chatsList && chatsList.length > 0) {
                populateChatsDropdown(chatsList, chatId);
            }

            // Always update sender and media dropdowns to match selected chat
            populateSendersDropdown(meta.senders);
            populateMediaTypesDropdown(meta.media_types);

            // Update date inputs for chat bounds
            const dStart = document.getElementById('inv-date-start');
            const dEnd = document.getElementById('inv-date-end');
            if (dStart && meta.min_date && (!dStart.value || chatId)) dStart.value = meta.min_date;
            if (dEnd && meta.max_date && (!dEnd.value || chatId)) dEnd.value = meta.max_date;

            return meta;
        } catch (e) {
            console.error('Investigator metadata error:', e);
            return null;
        }
    }

    function populateChatsDropdown(chats, selectedChatId = null) {
        const chatSelect = document.getElementById('inv-chat-select');
        if (!chatSelect || !chats) return;
        const currentVal = selectedChatId || chatSelect.value || 'all';

        // Keep existing options if already populated to prevent UI flicker
        if (chatSelect.options && chatSelect.options.length > 1) {
            if (currentVal && chatSelect.querySelector(`option[value="${currentVal}"]`)) {
                chatSelect.value = String(currentVal);
            }
            return;
        }

        chatSelect.innerHTML = '<option value="all">All Chats</option>';
        chats.forEach(c => {
            const opt = document.createElement('option');
            opt.value = String(c.chat_id);
            opt.textContent = `${c.chat_name} (${Number(c.msg_count).toLocaleString()})`;
            chatSelect.appendChild(opt);
        });
        if (currentVal && chatSelect.querySelector(`option[value="${currentVal}"]`)) {
            chatSelect.value = String(currentVal);
        } else {
            chatSelect.value = 'all';
        }
    }

    function populateSendersDropdown(senders) {
        const senderSelect = document.getElementById('inv-sender-select');
        if (!senderSelect) return;
        const currentVal = senderSelect.value;
        const chatSelect = document.getElementById('inv-chat-select');
        const isSingleChat = chatSelect && chatSelect.value !== 'all';

        senderSelect.innerHTML = isSingleChat 
            ? '<option value="all">All Senders in Chat</option>'
            : '<option value="all">All Senders (Archive)</option>';

        if (Array.isArray(senders)) {
            senders.forEach(s => {
                const opt = document.createElement('option');
                opt.value = String(s.sender_id);
                const botBadge = s.is_bot ? ' 🤖' : '';
                opt.textContent = `${s.name}${botBadge} (${Number(s.msg_count).toLocaleString()})`;
                senderSelect.appendChild(opt);
            });
        }

        // If previously selected sender exists in this chat's list, keep it; otherwise reset to 'all'
        if (currentVal && currentVal !== 'all' && senderSelect.querySelector(`option[value="${currentVal}"]`)) {
            senderSelect.value = currentVal;
        } else {
            senderSelect.value = 'all';
        }
    }

    function populateMediaTypesDropdown(types) {
        const mediaSelect = document.getElementById('inv-media-type');
        if (!mediaSelect) return;
        const currentVal = mediaSelect.value || 'all';
        mediaSelect.innerHTML = '<option value="all">All Media & Text</option>';

        const standardTypes = [
            { id: 'photo', label: 'Photos (🖼️)' },
            { id: 'video', label: 'Videos (📹)' },
            { id: 'voice', label: 'Voice Notes (🎤)' },
            { id: 'audio', label: 'Audio / Music (🎧)' },
            { id: 'document', label: 'Files / Docs (📎)' },
            { id: 'sticker', label: 'Stickers (🏷️)' },
            { id: 'animation', label: 'GIFs (🎞️)' }
        ];

        standardTypes.forEach(st => {
            const opt = document.createElement('option');
            opt.value = st.id;
            opt.textContent = st.label;
            mediaSelect.appendChild(opt);
        });

        if (currentVal && mediaSelect.querySelector(`option[value="${currentVal}"]`)) {
            mediaSelect.value = currentVal;
        } else {
            mediaSelect.value = 'all';
        }
    }

    async function onChatChange(chatId) {
        await loadMeta(chatId);
        // If user is currently browsing a preset, re-run that preset for this chat
        if (activePreset) {
            applyPreset(activePreset);
        } else {
            runQuery();
        }
    }

    function bindFilterEvents() {
        const filterIds = [
            'inv-sender-select', 'inv-media-type', 'inv-group-by',
            'inv-sort-by', 'inv-sort-order', 'inv-date-start', 'inv-date-end'
        ];
        filterIds.forEach(id => {
            const el = document.getElementById(id);
            if (el && !el._invBound) {
                el._invBound = true;
                el.addEventListener('change', () => {
                    activePreset = null;
                    document.querySelectorAll('.inv-preset-btn').forEach(btn => btn.classList.remove('active'));
                });
            }
        });
    }

    function openModal(targetChatId = null) {
        bindFilterEvents();
        const modal = document.getElementById('investigator-modal');
        if (!modal) return;
        modal.style.display = 'flex';

        // Detect initial chat: passed explicitly, or current active chat from main app
        const activeAppChat = targetChatId || window.AppState?.currentChatId || window.currentChatId || 'all';
        const chatSelect = document.getElementById('inv-chat-select');
        if (chatSelect && activeAppChat && activeAppChat !== 'all') {
            chatSelect.value = String(activeAppChat);
        }

        loadMeta(activeAppChat).then(() => {
            if (!currentRows || currentRows.length === 0) {
                applyPreset('heavyweight');
            }
        });
    }

    function closeModal() {
        const modal = document.getElementById('investigator-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    function setViewMode(mode) {
        currentViewMode = mode;
        const chartsWrap = document.getElementById('inv-charts-wrapper');
        const tableWrap = document.getElementById('inv-table-wrapper');

        document.querySelectorAll('.inv-view-toggle-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });

        if (mode === 'charts') {
            if (chartsWrap) {
                chartsWrap.style.display = 'grid';
                chartsWrap.style.minHeight = '380px';
                chartsWrap.style.maxHeight = '500px';
            }
            if (tableWrap) tableWrap.style.display = 'none';
        } else if (mode === 'table') {
            if (chartsWrap) chartsWrap.style.display = 'none';
            if (tableWrap) {
                tableWrap.style.display = 'block';
                tableWrap.style.maxHeight = '520px';
            }
        } else { // split
            if (chartsWrap) {
                chartsWrap.style.display = 'grid';
                chartsWrap.style.minHeight = '200px';
                chartsWrap.style.maxHeight = '220px';
            }
            if (tableWrap) {
                tableWrap.style.display = 'block';
                tableWrap.style.maxHeight = 'none';
            }
        }

        // Redraw Chart.js canvases smoothly on layout switch
        requestAnimationFrame(() => {
            if (barChartInstance) barChartInstance.resize();
            if (donutChartInstance) donutChartInstance.resize();
        });
    }

    function applyPreset(presetName) {
        activePreset = presetName;

        // Sync button states
        document.querySelectorAll('.inv-preset-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.preset === presetName);
        });

        // Sync dropdown selectors to match preset semantics
        const groupByEl = document.getElementById('inv-group-by');
        const sortByEl = document.getElementById('inv-sort-by');
        const sortOrderEl = document.getElementById('inv-sort-order');
        const mediaTypeEl = document.getElementById('inv-media-type');

        if (presetName === 'heavyweight') {
            if (groupByEl) groupByEl.value = 'sender';
            if (sortByEl) sortByEl.value = 'total_size';
            if (sortOrderEl) sortOrderEl.value = 'desc';
        } else if (presetName === 'activity_timeline') {
            if (groupByEl) groupByEl.value = 'month';
            if (sortByEl) sortByEl.value = 'label';
            if (sortOrderEl) sortOrderEl.value = 'asc';
        } else if (presetName === 'cross_chat_memes') {
            if (groupByEl) groupByEl.value = 'cross_chat';
            if (sortByEl) sortByEl.value = 'total_size';
            if (sortOrderEl) sortOrderEl.value = 'desc';
        } else if (presetName === 'bot_footprint') {
            if (groupByEl) groupByEl.value = 'bot';
            if (sortByEl) sortByEl.value = 'count';
            if (sortOrderEl) sortOrderEl.value = 'desc';
        } else if (presetName === 'peak_hours') {
            if (groupByEl) groupByEl.value = 'hour';
            if (sortByEl) sortByEl.value = 'label';
            if (sortOrderEl) sortOrderEl.value = 'asc';
        }

        const chatVal = document.getElementById('inv-chat-select')?.value || 'all';
        const senderVal = document.getElementById('inv-sender-select')?.value || 'all';

        runQuery({
            preset: presetName,
            chats: chatVal === 'all' ? 'all' : [parseInt(chatVal)],
            senders: senderVal === 'all' ? 'all' : [parseInt(senderVal)],
            limit: 25
        });
    }

    function resetFilters() {
        const chatVal = document.getElementById('inv-chat-select');
        const senderVal = document.getElementById('inv-sender-select');
        const mediaVal = document.getElementById('inv-media-type');
        const dStart = document.getElementById('inv-date-start');
        const dEnd = document.getElementById('inv-date-end');

        if (senderVal) senderVal.value = 'all';
        if (mediaVal) mediaVal.value = 'all';
        if (dStart) dStart.value = '';
        if (dEnd) dEnd.value = '';

        applyPreset('heavyweight');
    }

    async function runQuery(customPayload = null) {
        const loader = document.getElementById('inv-loading');
        if (loader) loader.style.display = 'flex';

        let payload = customPayload;
        if (!payload) {
            const chatVal = document.getElementById('inv-chat-select')?.value || 'all';
            const senderVal = document.getElementById('inv-sender-select')?.value || 'all';
            const mediaVal = document.getElementById('inv-media-type')?.value || 'all';
            const groupBy = document.getElementById('inv-group-by')?.value || 'sender';
            const sortBy = document.getElementById('inv-sort-by')?.value || 'total_size';
            const sortOrder = document.getElementById('inv-sort-order')?.value || 'desc';
            const dateStart = document.getElementById('inv-date-start')?.value || null;
            const dateEnd = document.getElementById('inv-date-end')?.value || null;

            payload = {
                chats: chatVal === 'all' ? 'all' : [parseInt(chatVal)],
                senders: senderVal === 'all' ? 'all' : [parseInt(senderVal)],
                media_types: mediaVal === 'all' ? 'all' : [mediaVal],
                group_by: groupBy,
                sort_by: sortBy,
                sort_order: sortOrder,
                date_start: dateStart,
                date_end: dateEnd,
                limit: 25
            };

            // Custom manual query — de-activate preset highlights unless matching
            activePreset = null;
            document.querySelectorAll('.inv-preset-btn').forEach(btn => btn.classList.remove('active'));
        }

        const csrf = typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '';
        payload.auth_csrf_token = csrf;

        try {
            const res = await fetch('/api/investigate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': csrf
                },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (loader) loader.style.display = 'none';

            if (data.status !== 'ok') {
                alert('Investigator Query Error: ' + (data.message || 'Unknown error'));
                return;
            }

            renderCharts(data);
            renderTable(data.rows, payload.group_by || activePreset || 'sender');
            renderSummary(data.summary);
            currentRows = data.rows;
        } catch (e) {
            if (loader) loader.style.display = 'none';
            console.error('Query execution failed:', e);
            alert('Investigation failed: ' + e.message);
        }
    }

    function renderCharts(data) {
        const barCanvas = document.getElementById('inv-bar-chart');
        const donutCanvas = document.getElementById('inv-donut-chart');
        if (!barCanvas || !donutCanvas) return;

        if (barChartInstance) barChartInstance.destroy();
        if (donutChartInstance) donutChartInstance.destroy();

        const maxChartItems = 25;
        const labels = (data.labels || []).slice(0, maxChartItems);
        const counts = data.datasets && data.datasets[0] ? data.datasets[0].data.slice(0, maxChartItems) : [];
        const sizesMb = data.datasets && data.datasets[1] ? data.datasets[1].data.slice(0, maxChartItems) : [];

        const palette = [
            '#2481cc', '#4fc3f7', '#00b0ff', '#00e676', '#ffb300',
            '#ff7043', '#ab47bc', '#ec407a', '#26a69a', '#7e57c2',
            '#8d6e63', '#78909c', '#66bb6a', '#ffa726', '#42a5f5',
            '#e91e63', '#9c27b0', '#673ab7', '#3f51b5', '#2196f3',
            '#00bcd4', '#009688', '#4caf50', '#8bc34a', '#cddc39'
        ];

        barChartInstance = new Chart(barCanvas.getContext('2d'), {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Messages',
                        data: counts,
                        backgroundColor: 'rgba(36, 129, 204, 0.75)',
                        borderColor: '#2481cc',
                        borderWidth: 1,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Storage (MB)',
                        data: sizesMb,
                        type: 'line',
                        borderColor: '#ff9800',
                        backgroundColor: 'rgba(255, 152, 0, 0.2)',
                        borderWidth: 2,
                        tension: 0.3,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#e0e0e0', font: { size: 10 } } },
                    tooltip: { mode: 'index', intersect: false }
                },
                scales: {
                    x: {
                        ticks: { color: '#aaa', maxRotation: 30, minRotation: 0, font: { size: 9.5 } },
                        grid: { color: 'rgba(255,255,255,0.05)' }
                    },
                    y: {
                        type: 'linear',
                        position: 'left',
                        ticks: { color: '#2481cc', font: { size: 9.5 } },
                        grid: { color: 'rgba(255,255,255,0.05)' }
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        ticks: { color: '#ff9800', font: { size: 9.5 } },
                        grid: { drawOnChartArea: false }
                    }
                }
            }
        });

        donutChartInstance = new Chart(donutCanvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: sizesMb.every(s => s === 0) ? counts : sizesMb,
                    backgroundColor: labels.map((_, i) => palette[i % palette.length]),
                    borderWidth: 1,
                    borderColor: 'rgba(0,0,0,0.3)'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: { color: '#e0e0e0', font: { size: 9.5 }, boxWidth: 10 }
                    }
                }
            }
        });
    }

    function renderTable(rows, currentGroupBy = 'sender') {
        const tbody = document.getElementById('inv-table-body');
        if (!tbody) return;
        tbody.innerHTML = '';

        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 30px; color: #888;">No results matched your query criteria.</td></tr>';
            return;
        }

        rows.forEach(r => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-color)';
            tr.style.transition = 'background 0.15s ease';

            // 1. Identity / Dimension Cell (Avatar, Name, Subtitle)
            let avatarHtml = '';
            if (r.avatar_path) {
                avatarHtml = `<img class="inv-avatar-mini" src="/data/${safeEscape(r.avatar_path.replace(/\\/g, '/'))}" onerror="this.outerHTML='<span class=\\'inv-avatar-fallback\\'>👤</span>'">`;
            } else if (r.ref_type === 'sender') {
                const initials = (r.label || '?').trim().slice(0, 2).toUpperCase();
                avatarHtml = `<span class="inv-avatar-fallback">${safeEscape(initials)}</span>`;
            } else if (r.ref_type === 'chat') {
                avatarHtml = `<span class="inv-avatar-fallback" style="background: rgba(0,200,83,0.25); color: #00e676; border-color: rgba(0,200,83,0.4);">💬</span>`;
            } else {
                avatarHtml = `<span class="inv-avatar-fallback" style="background: rgba(255,152,0,0.25); color: #ffa726; border-color: rgba(255,152,0,0.4);">📊</span>`;
            }

            const botBadge = r.is_bot ? ' <span style="font-size:10px; opacity:0.8;">🤖</span>' : '';
            const subTitle = r.username ? `@${safeEscape(r.username)}` : (r.ref_id ? `ID: ${r.ref_id}` : '');

            const identityHtml = `
                <div class="inv-table-cell-id">
                    ${avatarHtml}
                    <div class="inv-table-cell-text">
                        <div class="inv-table-cell-name" title="${safeEscape(r.label)}">
                            ${safeEscape(r.label)}${botBadge}
                        </div>
                        ${subTitle ? `<div class="inv-table-cell-sub">${safeEscape(subTitle)}</div>` : ''}
                    </div>
                </div>
            `;

            // 2. Sample / Linked File Cell
            let fileHtml = '<span style="opacity: 0.4;">—</span>';
            if (r.sample_media_path) {
                const encodedPath = encodeURIComponent(r.sample_media_path);
                const fileName = r.sample_media_name || r.sample_media_path.split(/[\\/]/).pop();
                const icon = getMediaIcon(r.sample_media_type, fileName);
                
                fileHtml = `
                    <div class="inv-file-preview-wrap" title="${safeEscape(fileName)}">
                        <span style="font-size: 13px;">${icon}</span>
                        <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 110px; font-weight: 500;">
                            ${safeEscape(fileName)}
                        </span>
                        <div style="display: flex; gap: 3px; margin-left: auto;">
                            <button type="button" class="inv-mini-action-btn" onclick="InvestigatorEngine.previewMedia('${encodedPath}')" title="Preview media file">
                                👁️
                            </button>
                            ${r.sample_chat_id && r.sample_msg_id ? `
                                <button type="button" class="inv-mini-action-btn" onclick="InvestigatorEngine.jumpToMsg(${r.sample_chat_id}, ${r.sample_msg_id})" title="Jump to message in chat">
                                    🎯
                                </button>
                            ` : ''}
                        </div>
                    </div>
                `;
            }

            // 3. Actions Cell
            let actionsHtml = '';
            if (r.ref_type === 'sender' && r.ref_id) {
                actionsHtml += `<button type="button" class="inv-mini-action-btn" onclick="InvestigatorEngine.openUser(${r.ref_id})" title="Open user full profile">👤 Profile</button> `;
            } else if (r.ref_type === 'chat' && r.ref_id) {
                actionsHtml += `<button type="button" class="inv-mini-action-btn" onclick="InvestigatorEngine.openChat(${r.ref_id})" title="Open this chat in viewer">💬 Chat</button> `;
            }
            if (r.ref_id && (r.ref_type === 'sender' || r.ref_type === 'chat')) {
                actionsHtml += `<button type="button" class="inv-mini-action-btn secondary" onclick="InvestigatorEngine.filterByRow('${r.ref_type}', '${r.ref_id}')" title="Filter Lego blocks by this item">🔍</button>`;
            }

            // Storage formatting
            const storageFormatted = r.total_mb > 1024 
                ? (r.total_mb / 1024).toFixed(2) + ' GB' 
                : r.total_mb.toLocaleString() + ' MB';

            tr.innerHTML = `
                <td style="padding: 6px 12px;">${identityHtml}</td>
                <td style="padding: 6px 10px; text-align: right; font-weight: 600;">${r.count.toLocaleString()}</td>
                <td style="padding: 6px 10px; text-align: right; color: #4fc3f7; font-weight: 700;">${storageFormatted}</td>
                <td style="padding: 6px 10px; text-align: right; opacity: 0.85;">${r.avg_kb.toLocaleString()} KB</td>
                <td style="padding: 6px 12px;">${fileHtml}</td>
                <td style="padding: 6px 10px; text-align: center;">${actionsHtml || '—'}</td>
            `;

            tr.addEventListener('mouseenter', () => tr.style.background = 'rgba(255,255,255,0.03)');
            tr.addEventListener('mouseleave', () => tr.style.background = 'transparent');

            tbody.appendChild(tr);
        });
    }

    function getMediaIcon(type, filename = '') {
        const t = (type || '').toLowerCase();
        const f = (filename || '').toLowerCase();
        if (t === 'photo' || f.endsWith('.jpg') || f.endsWith('.png') || f.endsWith('.jpeg')) return '🖼️';
        if (t === 'video' || f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mov')) return '📹';
        if (t === 'voice' || f.endsWith('.ogg')) return '🎤';
        if (t === 'audio' || f.endsWith('.mp3') || f.endsWith('.m4a') || f.endsWith('.flac')) return '🎧';
        if (t === 'sticker' || f.endsWith('.webp') || f.endsWith('.tgs')) return '🏷️';
        if (t === 'animation' || f.endsWith('.gif')) return '🎞️';
        return '📎';
    }

    function renderSummary(summary) {
        if (!summary) return;
        const msgEl = document.getElementById('inv-stat-msgs');
        const sizeEl = document.getElementById('inv-stat-size');
        const countEl = document.getElementById('inv-stat-rows');

        if (msgEl) msgEl.textContent = summary.total_messages.toLocaleString();
        if (sizeEl) sizeEl.textContent = summary.total_gb > 1 ? `${summary.total_gb} GB` : `${summary.total_mb} MB`;
        if (countEl) countEl.textContent = summary.rows_returned;
    }

    // --- Drilldown Interactivity Handlers ---
    function openUser(userId) {
        if (window.UserModal && typeof window.UserModal.open === 'function') {
            window.UserModal.open(userId);
        } else {
            alert(`User ID: ${userId}`);
        }
    }

    function openChat(chatId) {
        closeModal();
        if (typeof window.openChatFromBubble === 'function') {
            window.openChatFromBubble(String(chatId));
        } else {
            const item = document.querySelector(`.chat-list-item[data-chat-id="${chatId}"]`);
            if (item) item.click();
        }
    }

    function jumpToMsg(chatId, msgId) {
        closeModal();
        if (typeof window.scrollToMessage === 'function') {
            window.scrollToMessage(Number(msgId), String(chatId));
        } else if (typeof window.jumpToMessageFromSearch === 'function') {
            window.jumpToMessageFromSearch(chatId, msgId);
        }
    }

    function previewMedia(encodedPath) {
        if (!encodedPath) return;
        const relPath = decodeURIComponent(encodedPath).replace(/\\/g, '/');
        const safeUrl = '/data/' + relPath;
        if (typeof window.openLightbox === 'function') {
            window.openLightbox(safeUrl);
        } else if (window.Lightbox && typeof window.Lightbox.open === 'function') {
            window.Lightbox.open([safeUrl], 0);
        } else {
            window.open(safeUrl, '_blank');
        }
    }

    function filterByRow(refType, refId) {
        if (!refId) return;
        if (refType === 'sender') {
            const senderSelect = document.getElementById('inv-sender-select');
            if (senderSelect) {
                senderSelect.value = String(refId);
                const groupBy = document.getElementById('inv-group-by');
                if (groupBy) groupBy.value = 'media_type';
                runQuery();
            }
        } else if (refType === 'chat') {
            const chatSelect = document.getElementById('inv-chat-select');
            if (chatSelect) {
                chatSelect.value = String(refId);
                onChatChange(String(refId));
            }
        }
    }

    async function exportCsv() {
        const chatVal = document.getElementById('inv-chat-select')?.value || 'all';
        const senderVal = document.getElementById('inv-sender-select')?.value || 'all';
        const mediaVal = document.getElementById('inv-media-type')?.value || 'all';
        const groupBy = document.getElementById('inv-group-by')?.value || 'sender';
        const sortBy = document.getElementById('inv-sort-by')?.value || 'total_size';
        const sortOrder = document.getElementById('inv-sort-order')?.value || 'desc';
        const dateStart = document.getElementById('inv-date-start')?.value || null;
        const dateEnd = document.getElementById('inv-date-end')?.value || null;

        const csrf = typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '';
        const payload = {
            chats: chatVal === 'all' ? 'all' : [parseInt(chatVal)],
            senders: senderVal === 'all' ? 'all' : [parseInt(senderVal)],
            media_types: mediaVal === 'all' ? 'all' : [mediaVal],
            group_by: groupBy,
            sort_by: sortBy,
            sort_order: sortOrder,
            date_start: dateStart,
            date_end: dateEnd,
            auth_csrf_token: csrf
        };

        try {
            const res = await fetch('/api/investigate/export_csv', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': csrf
                },
                body: JSON.stringify(payload)
            });

            if (!res.ok) throw new Error('Export CSV failed');
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = `telebackup_investigation_${new Date().toISOString().slice(0, 10)}.csv`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
        } catch (e) {
            console.error('Export CSV error:', e);
            alert('Failed to export CSV: ' + e.message);
        }
    }

    return {
        open: openModal,
        close: closeModal,
        runQuery: runQuery,
        applyPreset: applyPreset,
        resetFilters: resetFilters,
        onChatChange: onChatChange,
        setViewMode: setViewMode,
        openUser: openUser,
        openChat: openChat,
        jumpToMsg: jumpToMsg,
        previewMedia: previewMedia,
        filterByRow: filterByRow,
        exportCsv: exportCsv
    };
})();

// Alias for direct template triggers
window.InvestigatorModal = window.InvestigatorEngine;
