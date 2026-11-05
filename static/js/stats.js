function renderGlobalStats(stats) {
    const globalStatsList = document.getElementById('global-stats-list');
    if (!globalStatsList) return;

    globalStatsList.innerHTML = `
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.total_messages')}</span> 
                <span style="font-weight: 500;">${stats.total_messages.toLocaleString(I18N.locale())}</span>
            </li>
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.archived_chats')}</span> 
                <span style="font-weight: 500;">${stats.total_chats}</span>
            </li>
            
            <li style="display: flex; justify-content: space-between; margin-top: 15px; padding-top: 15px; border-top: 1px dashed var(--border-color); margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.db_text')}</span> 
                <span style="font-weight: 500;">${stats.db_size_mb} ${I18N.t('stats.mb')}</span>
            </li>
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.media_files')}</span> 
                <span style="font-weight: 500;">${stats.media_size_mb} ${I18N.t('stats.mb')}</span>
            </li>
            <li style="display: flex; justify-content: space-between; margin-top: 8px; font-weight: bold; color: var(--sender-name-color); font-size: 14px;">
                <span>${I18N.t('stats.total_size')}</span> 
                <span>${stats.total_size_mb} ${I18N.t('stats.mb')}</span>
            </li>
        `;
}

function renderLocalStatsList(stats) {
    const localStatsList = document.getElementById('local-stats-list');
    if (!localStatsList) return;

    const churnWidget = document.getElementById('subscribers-churn-widget');
    if (churnWidget) {
        const chat = (window.chatListData || []).find(c => String(c.chat_id) === String(window.currentChatId));
        const chatType = chat ? chat.chat_type : (window.currentChatData ? window.currentChatData.chat_type : null);
        const isDmOrBot = (chatType === 'user' || chatType === 'bot' || Number(window.currentChatId) > 0);
        churnWidget.style.display = isDmOrBot ? 'none' : 'block';
    }

    if (!stats || typeof stats !== 'object' || stats.error || stats.total_messages === undefined) {
        localStatsList.innerHTML = `<li style="color: var(--text-color-light); font-size: 13px; padding: 8px 0;">${I18N.t('stats.no_stats') || 'Statistics unavailable'}</li>`;
        return;
    }

    const media = stats.media_counts || {};
    const totalMediaCount = Object.values(media).reduce((a, b) => a + b, 0);

    let viewsHtml = '';
    const isChannel = currentChatId && (
        stats.is_channel !== undefined 
            ? stats.is_channel 
            : (window.currentChatData ? window.currentChatData.chat_type === 'channel' : false)
    );

    if (isChannel && !stats.is_group && (stats.total_views || stats.total_forwards)) {
        viewsHtml = `
            <div style="height: 1px; background: var(--border-color); margin: 10px 0; opacity: 0.3;"></div>
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.views') || 'Total Views'}</span>
                <span style="font-weight: 500;">👁️ ${(stats.total_views || 0).toLocaleString(I18N.locale())}</span>
            </li>
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.forwards') || 'Total Forwards'}</span>
                <span style="font-weight: 500;">↗️ ${(stats.total_forwards || 0).toLocaleString(I18N.locale())}</span>
            </li>
        `;
    }

    let reactionsHtml = '';
    if (stats.top_reactions && stats.top_reactions.length > 0) {
        let pills = '';
        stats.top_reactions.forEach(([emoji, count]) => {
            pills += `<span class="stat-reaction-pill" style="display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:12px; background:var(--panel-bg); border:1px solid var(--border-color); font-size:12px;">${escapeHTML(emoji)} <small style="opacity:0.7;">${count}</small></span>`;
        });
        reactionsHtml = `
            <div style="height: 1px; background: var(--border-color); margin: 10px 0; opacity: 0.3;"></div>
            <div style="font-size: 12px; color: var(--text-color-light); margin-bottom: 6px;">${I18N.t('stats.top_reactions') || 'Top Reactions'}</div>
            <div style="display: flex; flex-wrap: wrap; gap: 6px;">${pills}</div>
        `;
    }

    let actionsHtml = '';
    const isChannelOrSupergroup = currentChatId && (
        String(currentChatId).startsWith('-100') ||
        (window.currentChatData && (window.currentChatData.chat_type === 'channel' || window.currentChatData.chat_type === 'supergroup'))
    );

    let sponsoredToggleHtml = '';
    if (isChannel && !stats.is_group) {
        const isShowSponsored = localStorage.getItem('tg_show_sponsored') === 'true';
        sponsoredToggleHtml = `
            <div style="margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border-color);">
                <label style="display: flex; align-items: center; justify-content: space-between; font-size: 12px; cursor: pointer; user-select: none;">
                    <span style="color: var(--text-color); display: flex; align-items: center; gap: 6px;">
                        📢 ${I18N.t('stats.show_sponsored') || 'Show Sponsored Posts'}
                    </span>
                    <input type="checkbox" id="toggle-sponsored-posts" ${isShowSponsored ? 'checked' : ''} onchange="toggleSponsoredPosts(this.checked)" style="cursor: pointer;">
                </label>
            </div>
        `;
    }

    if (isChannelOrSupergroup) {
        actionsHtml = `
            <div style="margin-top: 15px; display: flex; flex-direction: column; gap: 8px;">
                <button class="action-btn" style="padding: 6px 10px; font-size: 12px; width: 100%;" onclick="if(window.AdminLogModal && currentChatId) AdminLogModal.open(currentChatId);">
                    🛡️ ${I18N.t('admin_log.title') || 'Admin Event Log'}
                </button>
                <button class="action-btn action-btn-secondary" style="padding: 6px 10px; font-size: 12px; width: 100%;" onclick="refreshChatViews(currentChatId);">
                    🔄 ${I18N.t('stats.refresh_views') || 'Refresh Views & Reactions'}
                </button>
            </div>
            ${sponsoredToggleHtml}
        `;
    }

    localStatsList.innerHTML = `
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.messages')}</span>
                <span style="font-weight: 500;">${(stats.total_messages || 0).toLocaleString(I18N.locale())}</span>
            </li>
            <li style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.attachments')}</span>
                <span style="font-weight: 500;">${totalMediaCount.toLocaleString(I18N.locale())}</span>
            </li>
            <li style="display: flex; justify-content: space-between; margin-bottom: 15px;">
                <span style="color: var(--text-color-light);">${I18N.t('stats.disk_size')}</span>
                <span style="font-weight: 500;">${stats.media_size_mb} ${I18N.t('stats.mb')}</span>
            </li>
            
            <div style="height: 1px; background: var(--border-color); margin: 10px 0; opacity: 0.3;"></div>
            
            ${media.photo ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.photo')}</span> <span>${media.photo}</span></li>` : ''}
            ${media.video ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.video')}</span> <span>${media.video}</span></li>` : ''}
            ${media.sticker ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.sticker')}</span> <span>${media.sticker}</span></li>` : ''}
            ${media.gif ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.gif')}</span> <span>${media.gif}</span></li>` : ''}
            ${media.voice ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.voice')}</span> <span>${media.voice}</span></li>` : ''}
            ${media.audio ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.audio')}</span> <span>${media.audio}</span></li>` : ''}
            ${media.document ? `<li style="display: flex; justify-content: space-between; font-size: 0.9em;"><span>${I18N.t('stats.document')}</span> <span>${media.document}</span></li>` : ''}
            
            ${viewsHtml}
            ${reactionsHtml}
            ${actionsHtml}
        `;
}

async function refreshChatViews(chatId) {
    if (!chatId) return;
    try {
        const resp = await fetch('/api/refresh_views', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
            },
            body: JSON.stringify({ chat_id: chatId })
        });
        const data = await resp.json();
        if (data.ok) {
            if (typeof loadStats === 'function') loadStats(chatId);
            if (typeof window.reloadCurrentChatMessages === 'function') {
                await window.reloadCurrentChatMessages(chatId);
            }
            alert(`Refreshed views & reactions for ${data.updated_messages_count} messages!`);
        } else {
            alert(data.error || 'Failed to refresh views');
        }
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
}

function toggleSponsoredPosts(enabled) {
    localStorage.setItem('tg_show_sponsored', enabled ? 'true' : 'false');
    const targetChat = window.currentChatId || (typeof currentChatId !== 'undefined' ? currentChatId : null);
    if (typeof window.loadMessages === 'function' && targetChat) {
        window.loadMessages(targetChat, 1);
    }
}

function renderTopUsersList(users, isTop5) {
    if (!users || users.length === 0) {
        if (topUsersList) topUsersList.innerHTML = `<li>${I18N.t('stats.no_users')}</li>`;
        return;
    }
    // Build admin lookup from current chat
    const adminMap = {};
    const currentChatData = (typeof chatListData !== 'undefined') ? chatListData.find(c => String(c.chat_id) === String(currentChatId)) : null;
    if (currentChatData && currentChatData.admin_list) {
        currentChatData.admin_list.forEach(a => { adminMap[String(a.user_id)] = a; });
    }

    if (!topUsersList) return;
    topUsersList.innerHTML = '';
    users.forEach(user => {
        const li = document.createElement('li');
        li.style.cursor = 'pointer';
        li.style.display = 'flex';
        li.style.alignItems = 'center';
        li.style.justifyContent = 'space-between';
        li.title = 'Click to view full profile';
        li.onclick = () => { if (window.UserModal) UserModal.open(user.sender_id); };
        const name = user.first_name ? `${user.first_name} ${user.last_name || ''}`.trim() : `ID: ${user.sender_id}`;
        const adminInfo = adminMap[String(user.sender_id)];
        let adminBadge = '';
        if (adminInfo) {
            if (adminInfo.is_creator) {
                adminBadge = `<span class="admin-badge creator-badge" title="${I18N.t('rank.creator')}">👑</span>`;
            } else {
                const rankLabel = adminInfo.rank ? escapeHTML(adminInfo.rank) : I18N.t('rank.admin');
                adminBadge = `<span class="admin-badge" title="${rankLabel}">⚡</span>`;
            }
        }

        // Live online status indicator dot
        let statusDot = '';
        if (user.live_status) {
            const st = user.live_status.status;
            let dotColor = '#999';
            let dotTitle = 'Offline';
            if (st === 'online') {
                dotColor = '#00c853';
                dotTitle = 'Online';
            } else if (st === 'recently') {
                dotColor = '#8bc34a';
                dotTitle = 'Recently online';
            }
            statusDot = `<span class="user-status-dot" data-user-id="${user.sender_id}" title="${dotTitle}" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dotColor};margin-right:6px;flex-shrink:0;"></span>`;
        } else {
            statusDot = `<span class="user-status-dot" data-user-id="${user.sender_id}" style="display:none;width:8px;height:8px;border-radius:50%;background:#999;margin-right:6px;flex-shrink:0;"></span>`;
        }

        li.innerHTML = `
                <span class="stat-name" style="display:flex;align-items:center;">${statusDot}${escapeHTML(name)}${adminBadge}</span>
                <span class="stat-count">${user.message_count.toLocaleString(I18N.locale())} ${I18N.t('stats.msg_short')}</span>
            `;
        topUsersList.appendChild(li);
    });

    if (isTop5 && allChatUsers.length > 5) {
        if (showAllUsersBtn) {
            showAllUsersBtn.style.display = 'block';
            showAllUsersBtn.textContent = I18N.t('stats.show_all_n', allChatUsers.length);
        }
    } else {
        if (showAllUsersBtn) showAllUsersBtn.style.display = 'none';
    }
}

function renderActivityChart(data) {
    if (activityChartInstance) {
        activityChartInstance.destroy();
    }
    if (!data || data.length === 0) {
        if (activityChartCanvas) activityChartCanvas.style.display = 'none';
        return;
    }
    if (!activityChartCanvas) return;
    activityChartCanvas.style.display = 'block';

    let processedData = data;
    const MAX_POINTS = 365;
    if (data.length > MAX_POINTS) {
        const step = Math.ceil(data.length / MAX_POINTS);
        processedData = [];
        for (let i = 0; i < data.length; i += step) {
            processedData.push(data[i]);
        }
    }

    const labels = processedData.map(item => item.activity_date);
    const counts = processedData.map(item => item.message_count);

    const isSingleDay = processedData.length <= 1;
    const chartType = isSingleDay ? 'bar' : 'line';
    const ctx = activityChartCanvas.getContext('2d');

    activityChartInstance = new Chart(ctx, {
        type: chartType,
        data: {
            labels: labels,
            datasets: [{
                label: I18N.t('archive.messages'),
                data: counts,
                fill: !isSingleDay,
                backgroundColor: isSingleDay ? 'rgba(11, 138, 0, 0.65)' : 'rgba(11, 138, 0, 0.1)',
                borderColor: 'rgba(11, 138, 0, 1)',
                borderWidth: 1.5,
                barThickness: isSingleDay ? 48 : undefined,
                borderRadius: isSingleDay ? 6 : undefined,
                tension: 0,
                pointRadius: isSingleDay ? undefined : 0,
                pointHoverRadius: isSingleDay ? undefined : 4,
                spanGaps: true
            }]
        },
        options: {
            animation: false,
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    display: isSingleDay,
                    ticks: {
                        font: { size: 10 }
                    }
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        maxTicksLimit: 5,
                        font: { size: 10 }
                    }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    padding: 8,
                    bodyFont: { size: 12 }
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    });
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}
window.formatBytes = formatBytes;

window.lastPureDbStats = null;
let currentActiveDbCard = null;

function renderDbCardDetail(cardType) {
    const detailEl = document.getElementById('db-stat-card-detail');
    if (!detailEl) return;
    const data = window.lastPureDbStats || {};
    const cards = document.querySelectorAll('.db-stat-card');

    if (currentActiveDbCard === cardType && detailEl.style.display !== 'none') {
        currentActiveDbCard = null;
        detailEl.style.display = 'none';
        cards.forEach(c => c.classList.remove('active'));
        return;
    }

    currentActiveDbCard = cardType;
    cards.forEach(c => {
        if (c.dataset.card === cardType) c.classList.add('active');
        else c.classList.remove('active');
    });

    let headerTitle = '';
    let rowsHtml = '';
    const loc = (typeof I18N !== 'undefined') ? I18N.locale() : 'en-US';

    if (cardType === 'messages') {
        headerTitle = '💬 Message Details';
        const textCount = (data.text_messages !== undefined ? data.text_messages : (data.total_messages || 0)).toLocaleString(loc);
        const mediaCount = (data.total_media_count || 0).toLocaleString(loc);
        const serviceCount = (data.service_messages || 0).toLocaleString(loc);
        const chatsCount = (data.total_chats || 0).toLocaleString(loc);
        const avgPerChat = (data.total_chats > 0 ? Math.round((data.total_messages || 0) / data.total_chats) : 0).toLocaleString(loc);

        rowsHtml = `
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Text Messages:</span><span class="db-stat-detail-val">${textCount}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Media Messages:</span><span class="db-stat-detail-val">${mediaCount}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">System / Service:</span><span class="db-stat-detail-val">${serviceCount}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Archived Chats:</span><span class="db-stat-detail-val">${chatsCount}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Avg Messages / Chat:</span><span class="db-stat-detail-val">${avgPerChat}</span></div>
        `;
    } else if (cardType === 'media') {
        headerTitle = '📁 Media Breakdown';
        const mb = data.media_breakdown || {};
        const photos = (mb.photo || mb.photos || 0).toLocaleString(loc);
        const videos = (mb.video || mb.videos || 0).toLocaleString(loc);
        const roundVideos = (mb.video_note || 0).toLocaleString(loc);
        const gifs = (mb.gif || mb.gifs || 0).toLocaleString(loc);
        const audio = (mb.audio || 0).toLocaleString(loc);
        const voice = (mb.voice || 0).toLocaleString(loc);
        const stickers = (mb.sticker || mb.stickers || 0).toLocaleString(loc);
        const doc = (mb.document || mb.files || 0).toLocaleString(loc);

        rowsHtml = `
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">📷 Photos:</span><span class="db-stat-detail-val">${photos}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">🎥 Videos:</span><span class="db-stat-detail-val">${videos}</span></div>
            ${mb.video_note ? `<div class="db-stat-detail-row"><span class="db-stat-detail-label">📹 Video Notes:</span><span class="db-stat-detail-val">${roundVideos}</span></div>` : ''}
            ${mb.gif ? `<div class="db-stat-detail-row"><span class="db-stat-detail-label">👾 GIFs:</span><span class="db-stat-detail-val">${gifs}</span></div>` : ''}
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">🎵 Audio:</span><span class="db-stat-detail-val">${audio}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">🎙️ Voice Notes:</span><span class="db-stat-detail-val">${voice}</span></div>
            ${mb.sticker ? `<div class="db-stat-detail-row"><span class="db-stat-detail-label">🏷️ Stickers:</span><span class="db-stat-detail-val">${stickers}</span></div>` : ''}
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">📄 Files / Docs:</span><span class="db-stat-detail-val">${doc}</span></div>
        `;
    } else if (cardType === 'db') {
        headerTitle = '💾 Database Details';
        const fts = (data.fts_count || 0).toLocaleString(loc);
        const dbMb = data.db_size_mb || 0;
        rowsHtml = `
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">File:</span><span class="db-stat-detail-val">telegram_backup.db</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Search Index (FTS):</span><span class="db-stat-detail-val">${fts} msgs</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Size on Disk:</span><span class="db-stat-detail-val">${dbMb} MB (${formatBytes(data.db_size_bytes || 0)})</span></div>
        `;
    } else if (cardType === 'disk') {
        headerTitle = '📦 Storage Details';
        const filesCount = (data.media_files_on_disk || data.total_media_count || 0).toLocaleString(loc);
        const totalMb = data.total_size_mb || 0;
        const countForAvg = (data.media_files_on_disk > 0 ? data.media_files_on_disk : (data.total_media_count || 0));
        const avgSize = (countForAvg > 0 ? formatBytes(Math.round((data.media_size_bytes || 0) / countForAvg)) : '0 B');
        rowsHtml = `
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Media Directory:</span><span class="db-stat-detail-val">media_backup/</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Files on Disk:</span><span class="db-stat-detail-val">${filesCount}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Avg File Size:</span><span class="db-stat-detail-val">${avgSize}</span></div>
            <div class="db-stat-detail-row"><span class="db-stat-detail-label">Total Footprint:</span><span class="db-stat-detail-val">${totalMb} MB</span></div>
        `;
    }

    detailEl.innerHTML = `
        <div class="db-stat-detail-header">
            <span>${headerTitle}</span>
            <span style="cursor: pointer; opacity: 0.7; font-size: 14px;" onclick="renderDbCardDetail('${cardType}')">✕</span>
        </div>
        ${rowsHtml}
    `;
    detailEl.style.display = 'block';
}
window.renderDbCardDetail = renderDbCardDetail;

async function loadPureDbStats() {
    try {
        const res = await fetch('/api/stats/global');
        if (!res.ok) return;
        const data = await res.json();
        window.lastPureDbStats = data;

        const msgEl = document.getElementById('pure-db-msg-count');
        const mediaEl = document.getElementById('pure-db-media-count');
        const dbSizeEl = document.getElementById('pure-db-size');
        const mediaFolderSizeEl = document.getElementById('pure-media-folder-size');

        if (msgEl) msgEl.textContent = (data.total_messages || 0).toLocaleString();
        if (mediaEl) mediaEl.textContent = (data.total_media_count || 0).toLocaleString();
        if (dbSizeEl) dbSizeEl.textContent = formatBytes(data.db_size_bytes || 0);
        if (mediaFolderSizeEl) mediaFolderSizeEl.textContent = formatBytes(data.media_size_bytes || 0);

        document.querySelectorAll('.db-stat-card').forEach(card => {
            if (!card.dataset.listenerAttached) {
                card.dataset.listenerAttached = '1';
                card.addEventListener('click', () => {
                    const cardType = card.dataset.card;
                    if (cardType) renderDbCardDetail(cardType);
                });
            }
        });

        if (currentActiveDbCard) {
            renderDbCardDetail(currentActiveDbCard);
        }
    } catch (err) {
        console.error('Failed to load pure DB stats:', err);
    }
}
window.loadPureDbStats = loadPureDbStats;

window.selectedMediaItems = window.selectedMediaItems || new Map();
let currentMediaCategory = null;
let currentCategoryPage = 1;

async function loadMediaCategories() {
    if (!window.currentChatId) return;
    try {
        const res = await fetch(`/api/media_counts?chat_id=${window.currentChatId}`);
        if (!res.ok) return;
        const counts = await res.json();
        ['photos', 'videos', 'files', 'audio', 'voices', 'links', 'surveys', 'gifs'].forEach(cat => {
            const el = document.getElementById(`cat-count-${cat}`);
            if (el) el.textContent = (counts[cat] || 0).toLocaleString();
        });
    } catch (e) {
        console.error('Failed to load media categories:', e);
    }
}
window.loadMediaCategories = loadMediaCategories;

function getCategoryDisplayName(catType) {
    const map = {
        photos: '🖼️ Photos',
        videos: '📹 Videos',
        files: '📎 Files',
        audio: '🎧 Audios',
        voices: '🎤 Voice messages',
        links: '🔗 Links',
        surveys: '📊 Surveys',
        gifs: '👾 GIFs'
    };
    return map[catType] || catType;
}

async function openMediaCategory(catType, page = 1) {
    if (!window.currentChatId) return;
    currentMediaCategory = catType;
    currentCategoryPage = page;

    const catView = document.getElementById('media-categories-view');
    const detailView = document.getElementById('media-detail-view');
    const titleEl = document.getElementById('media-detail-title');
    const grid = document.getElementById('media-gallery-grid');
    const list = document.getElementById('media-gallery-list');
    const surveys = document.getElementById('media-gallery-surveys');
    const pagination = document.getElementById('media-pagination');
    const prevBtn = document.getElementById('media-prev-btn');
    const nextBtn = document.getElementById('media-next-btn');
    const pageIndicator = document.getElementById('media-page-indicator');

    if (catView) catView.style.display = 'none';
    if (detailView) detailView.style.display = 'flex';
    if (titleEl) titleEl.textContent = getCategoryDisplayName(catType);
    const batchTranscribeBtn = document.getElementById('media-batch-transcribe-btn');
    if (batchTranscribeBtn) batchTranscribeBtn.style.display = (catType === 'voices' || catType === 'audios') ? 'inline-block' : 'none';

    if (grid) { grid.style.display = 'none'; grid.innerHTML = ''; }
    if (list) { list.style.display = 'none'; list.innerHTML = ''; }
    if (surveys) { surveys.style.display = 'none'; surveys.innerHTML = ''; }
    if (pagination) pagination.style.display = 'none';

    try {
        const res = await fetch(`/api/media?chat_id=${window.currentChatId}&type=${catType}&page=${page}`);
        const data = await res.json();
        const items = data.items || [];

        if (catType === 'photos' || catType === 'videos' || catType === 'gifs') {
            if (grid) {
                grid.style.display = 'grid';
                if (items.length === 0) {
                    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-color-light); padding: 30px 10px;">${I18N.t('search.no_results')}</div>`;
                } else {
                    items.forEach(item => {
                        const mediaRel = (item.media_local_path || '').replace(/^(media_backup[\\/])/, '').replace(/\\/g, '/');
                        const mediaPath = '/data/' + mediaRel;
                        const safePath = encodeURI(mediaPath).replace(/'/g, "%27");

                        let thumbUrl = '';
                        if (item.thumbnail_local_path) {
                            const thumbRel = item.thumbnail_local_path.replace(/^(media_backup[\\/])/, '').replace(/\\/g, '/');
                            thumbUrl = '/data/' + thumbRel;
                        } else if (catType === 'photos') {
                            thumbUrl = '/data_thumb/' + mediaRel;
                        } else {
                            thumbUrl = mediaPath;
                        }
                        const safeThumb = encodeURI(thumbUrl).replace(/'/g, "%27");
                        const isSel = window.selectedMediaItems.has(item.message_id);

                        const div = document.createElement('div');
                        div.className = `media-grid-item ${isSel ? 'selected' : ''}`;
                        div.dataset.messageId = item.message_id;

                        let badgeHtml = '';
                        if (catType === 'videos') badgeHtml = `<div class="play-icon-album" style="width:28px;height:28px;font-size:12px;">▶</div>`;
                        else if (catType === 'gifs') badgeHtml = `<div class="media-badge">GIF</div>`;

                        div.innerHTML = `
                            <img src="${safeThumb}" loading="lazy" alt="">
                            ${badgeHtml}
                            <div class="media-item-checkbox" title="Select item">${isSel ? '✓' : ''}</div>
                        `;

                        div.querySelector('.media-item-checkbox').addEventListener('click', (ev) => {
                            ev.stopPropagation();
                            toggleMediaSelection(item, div);
                        });

                        div.addEventListener('click', () => {
                            if (catType === 'videos') {
                                openPlayer(safePath, item.media_mime_type);
                            } else {
                                if (typeof openLightbox === 'function') openLightbox(safePath);
                                else window.open(safePath, '_blank');
                            }
                        });

                        div.addEventListener('contextmenu', (ev) => {
                            ev.preventDefault();
                            showMediaContextMenu(ev.clientX, ev.clientY, item, div);
                        });

                        grid.appendChild(div);
                    });
                }
            }
        } else if (catType === 'surveys') {
            if (surveys) {
                surveys.style.display = 'flex';
                if (items.length === 0) {
                    surveys.innerHTML = `<div style="text-align: center; color: var(--text-color-light); padding: 30px 10px;">${I18N.t('search.no_results')}</div>`;
                } else {
                    items.forEach(item => {
                        const isSel = window.selectedMediaItems.has(item.message_id);
                        const card = document.createElement('div');
                        card.className = `media-survey-card ${isSel ? 'selected' : ''}`;
                        card.dataset.messageId = item.message_id;

                        const dateStr = item.date ? new Date(item.date).toLocaleDateString() : '';
                        let pollHtml = '';
                        if (item.poll_data && typeof renderPoll === 'function') {
                            pollHtml = renderPoll(item.poll_data);
                        } else {
                            pollHtml = `<div class="poll">${escapeHTML(item.text || 'Survey')}</div>`;
                        }

                        card.innerHTML = `
                            <div class="media-survey-card-header">
                                <span>📅 ${dateStr}</span>
                                <div class="media-item-checkbox" style="position:static;display:flex;" title="Select item">${isSel ? '✓' : ''}</div>
                            </div>
                            ${pollHtml}
                        `;

                        card.querySelector('.media-item-checkbox').addEventListener('click', (ev) => {
                            ev.stopPropagation();
                            toggleMediaSelection(item, card);
                        });

                        card.addEventListener('contextmenu', (ev) => {
                            ev.preventDefault();
                            showMediaContextMenu(ev.clientX, ev.clientY, item, card);
                        });

                        surveys.appendChild(card);
                    });
                }
            }
        } else {
            // Files, Audios, Voices, Links
            if (list) {
                list.style.display = 'flex';
                if (items.length === 0) {
                    list.innerHTML = `<div style="text-align: center; color: var(--text-color-light); padding: 30px 10px;">${I18N.t('search.no_results')}</div>`;
                } else {
                    items.forEach(item => {
                        const isSel = window.selectedMediaItems.has(item.message_id);
                        const mediaPath = item.media_local_path ? ('/data/' + item.media_local_path.replace(/^(media_backup[\\/])/, '').replace(/\\/g, '/')) : '';
                        const safePath = encodeURI(mediaPath).replace(/'/g, "%27");

                        const row = document.createElement('div');
                        row.className = `media-plain-row ${isSel ? 'selected' : ''}`;
                        row.dataset.messageId = item.message_id;

                        let icon = '📎';
                        if (catType === 'audio') icon = '🎧';
                        else if (catType === 'voices') icon = '🎤';
                        else if (catType === 'links') icon = '🔗';

                        let name = item.media_original_name || 'File';
                        if (catType === 'links') {
                            name = item.text || 'Link';
                        }
                        const dateStr = item.date ? new Date(item.date).toLocaleDateString() : '';
                        const sizeStr = item.size_bytes ? formatBytes(item.size_bytes) : '';

                        row.innerHTML = `
                            <div class="media-plain-icon">${icon}</div>
                            <div class="media-plain-info">
                                <div class="media-plain-name">${escapeHTML(name)}</div>
                                <div class="media-plain-meta">
                                    <span>${dateStr}</span>
                                    ${sizeStr ? `<span>· ${sizeStr}</span>` : ''}
                                </div>
                            </div>
                            <div class="media-item-checkbox" style="position:static;display:flex;" title="Select item">${isSel ? '✓' : ''}</div>
                        `;

                        row.querySelector('.media-item-checkbox').addEventListener('click', (ev) => {
                            ev.stopPropagation();
                            toggleMediaSelection(item, row);
                        });

                        row.addEventListener('click', () => {
                            if (catType === 'voices' || catType === 'audio') {
                                openPlayer(safePath, item.media_mime_type);
                            } else if (catType === 'links') {
                                const urlMatch = item.text && item.text.match(/https?:\/\/[^\s]+/);
                                if (urlMatch) { const safeLink = (window.sanitizeUrl || String)(urlMatch[0]); if (safeLink && safeLink !== '#') window.open(safeLink, '_blank', 'noopener,noreferrer'); }
                            } else {
                                window.open(safePath, '_blank');
                            }
                        });

                        row.addEventListener('contextmenu', (ev) => {
                            ev.preventDefault();
                            showMediaContextMenu(ev.clientX, ev.clientY, item, row);
                        });

                        list.appendChild(row);
                    });
                }
            }
        }

        if (pagination && (page > 1 || data.has_more)) {
            pagination.style.display = 'flex';
            if (pageIndicator) pageIndicator.textContent = I18N.t('media.page', page);
            if (prevBtn) prevBtn.disabled = page === 1;
            if (nextBtn) nextBtn.disabled = !data.has_more;
        }

    } catch (err) {
        console.error('Failed to open media category:', err);
    }
}
window.openMediaCategory = openMediaCategory;

let currentContextItem = null;
let currentContextEl = null;

function showMediaContextMenu(x, y, item, el) {
    const menu = document.getElementById('media-context-menu');
    if (!menu) return;
    currentContextItem = item;
    currentContextEl = el;

    const isSel = window.selectedMediaItems.has(item.message_id);
    const selIcon = document.getElementById('ctx-select-icon');
    const selText = document.getElementById('ctx-select-text');
    if (selIcon) selIcon.textContent = isSel ? '☐' : '☑️';
    if (selText) selText.textContent = isSel ? 'Deselect item' : 'Select item';

    menu.style.display = 'block';

    const menuWidth = 180;
    const menuHeight = 85;
    let posX = x;
    let posY = y;
    if (posX + menuWidth > window.innerWidth) posX = window.innerWidth - menuWidth - 10;
    if (posY + menuHeight > window.innerHeight) posY = window.innerHeight - menuHeight - 10;
    menu.style.left = posX + 'px';
    menu.style.top = posY + 'px';
}

function hideMediaContextMenu() {
    const menu = document.getElementById('media-context-menu');
    if (menu) menu.style.display = 'none';
    currentContextItem = null;
    currentContextEl = null;
}

function toggleMediaSelection(item, el) {
    if (window.selectedMediaItems.has(item.message_id)) {
        window.selectedMediaItems.delete(item.message_id);
        if (el) {
            el.classList.remove('selected');
            const cb = el.querySelector('.media-item-checkbox');
            if (cb) cb.textContent = '';
        }
    } else {
        window.selectedMediaItems.set(item.message_id, item);
        if (el) {
            el.classList.add('selected');
            const cb = el.querySelector('.media-item-checkbox');
            if (cb) cb.textContent = '✓';
        }
    }
    updateMediaSelectionBar();
}

function updateMediaSelectionBar() {
    const bar = document.getElementById('media-selection-bar');
    const countEl = document.getElementById('media-sel-count');
    const count = window.selectedMediaItems.size;
    if (!bar) return;
    if (count > 0) {
        bar.style.display = 'flex';
        if (countEl) countEl.textContent = count;
    } else {
        bar.style.display = 'none';
    }
}

// Global initialization for rebuilt media panel controls
document.addEventListener('DOMContentLoaded', () => {
    // Back button in detail view
    document.getElementById('media-detail-back-btn')?.addEventListener('click', () => {
        const detailView = document.getElementById('media-detail-view');
        const catView = document.getElementById('media-categories-view');
        if (detailView) detailView.style.display = 'none';
        if (catView) catView.style.display = 'flex';
        currentMediaCategory = null;
    });

    // Category row clicks
    document.querySelectorAll('.media-cat-item').forEach(item => {
        item.addEventListener('click', () => {
            const catType = item.dataset.type;
            openMediaCategory(catType, 1);
        });
    });

    // Pagination
    document.getElementById('media-prev-btn')?.addEventListener('click', () => {
        if (currentCategoryPage > 1 && currentMediaCategory) {
            openMediaCategory(currentMediaCategory, currentCategoryPage - 1);
        }
    });
    document.getElementById('media-next-btn')?.addEventListener('click', () => {
        if (currentMediaCategory) {
            openMediaCategory(currentMediaCategory, currentCategoryPage + 1);
        }
    });

    // Context menu handlers
    document.getElementById('ctx-jump-to-msg')?.addEventListener('click', () => {
        if (currentContextItem && window.scrollToMessage) {
            window.scrollToMessage(currentContextItem.message_id, window.currentChatId);
        }
        hideMediaContextMenu();
    });

    document.getElementById('ctx-select-item')?.addEventListener('click', () => {
        if (currentContextItem) {
            toggleMediaSelection(currentContextItem, currentContextEl);
        }
        hideMediaContextMenu();
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('#media-context-menu')) {
            hideMediaContextMenu();
        }
    });

    // Selection bar Cancel button
    document.getElementById('media-sel-cancel-btn')?.addEventListener('click', () => {
        window.selectedMediaItems.clear();
        document.querySelectorAll('.media-grid-item.selected, .media-plain-row.selected, .media-survey-card.selected').forEach(el => {
            el.classList.remove('selected');
            const cb = el.querySelector('.media-item-checkbox');
            if (cb) cb.textContent = '';
        });
        updateMediaSelectionBar();
    });

    // Selection bar Export button
    document.getElementById('media-sel-export-btn')?.addEventListener('click', () => {
        const modal = document.getElementById('media-export-modal');
        if (!modal) return;
        const countEl = document.getElementById('export-items-count');
        if (countEl) countEl.textContent = window.selectedMediaItems.size;
        const destInput = document.getElementById('export-dest-input');
        if (destInput) {
            destInput.value = localStorage.getItem('last_export_dir') || '';
        }
        const msgEl = document.getElementById('export-progress-msg');
        if (msgEl) msgEl.style.display = 'none';
        modal.style.display = 'flex';
    });

    // Export confirmation button
    document.getElementById('export-confirm-btn')?.addEventListener('click', async () => {
        const destInput = document.getElementById('export-dest-input');
        const targetDir = destInput ? destInput.value.trim() : '';
        if (!targetDir) {
            alert('Please specify a destination directory path');
            return;
        }
        const msgEl = document.getElementById('export-progress-msg');
        if (msgEl) msgEl.style.display = 'block';
        const confirmBtn = document.getElementById('export-confirm-btn');
        if (confirmBtn) confirmBtn.disabled = true;

        const messageIds = Array.from(window.selectedMediaItems.keys());
        const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;
        const expToast = (typeof notifyFn === 'function')
            ? notifyFn(`Exporting ${messageIds.length} media files...`, 'info', true)
            : null;

        try {
            const res = await fetch('/api/export_media_batch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                },
                body: JSON.stringify({
                    chat_id: window.currentChatId,
                    target_dir: targetDir,
                    message_ids: messageIds
                })
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                throw new Error(data.error || 'Export failed');
            }
            localStorage.setItem('last_export_dir', targetDir);
            if (expToast && typeof expToast.success === 'function') {
                expToast.success(`Exported ${data.copied_count} files successfully!`);
            } else if (typeof notifyFn === 'function') {
                notifyFn(`Exported ${data.copied_count} files successfully!`, 'success');
            }
            document.getElementById('media-export-modal').style.display = 'none';
            window.selectedMediaItems.clear();
            document.querySelectorAll('.media-grid-item.selected, .media-plain-row.selected, .media-survey-card.selected').forEach(el => {
                el.classList.remove('selected');
                const cb = el.querySelector('.media-item-checkbox');
                if (cb) cb.textContent = '';
            });
            updateMediaSelectionBar();
        } catch (e) {
            if (expToast && typeof expToast.error === 'function') {
                expToast.error(`Export error: ${e.message}`);
            } else if (typeof notifyFn === 'function') {
                notifyFn(`Export error: ${e.message}`, 'error');
            }
        } finally {
            if (confirmBtn) confirmBtn.disabled = false;
            if (msgEl) msgEl.style.display = 'none';
        }
    });
});


// Update stats on language change
document.addEventListener('languageChanged', () => {
    if (window.currentChatStats && typeof renderLocalStatsList === 'function') {
        renderLocalStatsList(window.currentChatStats);
    }
    if (window.allChatUsers && window.allChatUsers.length > 0 && typeof renderTopUsersList === 'function') {
        renderTopUsersList(window.allChatUsers, true);
    }
    if (window.currentChatData && typeof renderChatAboutPanel === 'function') {
        renderChatAboutPanel(window.currentChatData);
    }
});
