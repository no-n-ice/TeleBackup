function openChatFromBubble(chatId) {

    const item = document.querySelector(`.chat-list-item[data-chat-id="${chatId}"]`);

    if (item) {

        item.click();

    }

}

window.openChatFromBubble = openChatFromBubble;



function areChatIdsEqual(id1, id2) {

    if (!id1 || !id2) return false;

    const s1 = String(id1).replace(/^-100/, '').replace(/^-/, '');

    const s2 = String(id2).replace(/^-100/, '').replace(/^-/, '');

    return s1 === s2;

}

window.areChatIdsEqual = areChatIdsEqual;



window.selectedArchiveChats = window.selectedArchiveChats || new Set();



let startArchiveStream;

let loadAllDialogs;

function getDialogById(id) {

    if (!id) return null;

    const strId = String(id);

    if (!window.dialogsByIdMap) {

        window.dialogsByIdMap = new Map((window.allDialogsData || []).map(d => [String(d.id), d]));

    }

    return window.dialogsByIdMap.get(strId) || (window.allDialogsData || []).find(d => areChatIdsEqual(d.id, id));

}



function renderDialogsList(dialogs, isUpdate = false) {

    if (!isUpdate) {

        window.allDialogsData = dialogs;

        window.dialogsByIdMap = new Map((dialogs || []).map(d => [String(d.id), d]));

    }

    if (typeof window.updateQuickRefreshButtonState === 'function') {

        window.updateQuickRefreshButtonState();

    }

    if (window.applyArchiveFilters) {

        window.applyArchiveFilters(true);

    } else {

        console.error("applyArchiveFilters not found!");

    }

}

window.renderDialogsList = renderDialogsList;



window.applyArchiveFilters = function (force = false) {
    if (window.isFullRefreshing && !force) return;

    const dialogs = window.allDialogsData || [];

    const bubblesContainer = document.getElementById('completed-bubbles') || document.getElementById('completed-chats-grid');

    const listEl = document.getElementById('dialogs-list');

    const archiveHeader = document.querySelector('.archive-header');



    const searchInput = document.getElementById('archive-search-input');

    const sortSelect = document.getElementById('archive-sort-select');

    const typeSelect = document.getElementById('archive-type-select');



    const searchQ = searchInput ? searchInput.value.toLowerCase() : '';

    const sortVal = sortSelect ? sortSelect.value : 'timestamp';

    const typeVal = typeSelect ? typeSelect.value : 'all';



    if (bubblesContainer) bubblesContainer.innerHTML = '';

    if (listEl) {

        listEl.innerHTML = '';

        const savedCols = localStorage.getItem('archive_view_cols') || '1';

        if (savedCols === '2') listEl.classList.add('view-2-col');

        else listEl.classList.remove('view-2-col');

    }



    if (dialogs.length === 0) {

        if (listEl) listEl.innerHTML = `<li style="padding: 15px; color: #666; font-size: 13px; text-align: center;">${I18N.t('archive.empty_hint')}</li>`;

        if (archiveHeader) archiveHeader.style.display = 'none';

        if (bubblesContainer) bubblesContainer.style.display = 'none';

        return;

    }



    let hasBubbles = false;



    // Process complex chats logic for completeness

    const partIds = new Set();

    dialogs.forEach(d => {

        if (d.is_complex && d.parts) {

            d.parts.forEach(p => { if (p.id !== d.id) partIds.add(p.id); });

        }

    });

    let uniqueDialogs = dialogs.filter(d => !partIds.has(d.id));



    const bubblesList = [];

    let availableList = [];



    uniqueDialogs.forEach(dialog => {

        let isCompletelyArchived = false;

        if (dialog.is_complex) {

            isCompletelyArchived = dialog.parts.every(p => p.is_archived);

        } else {

            isCompletelyArchived = dialog.is_archived;

        }



        const currentStatus = archiveStatusCache[dialog.id]?.status;

        const isProcessing = (currentStatus === 'running' || currentStatus === 'queued');



        if (isCompletelyArchived && !isProcessing) {

            bubblesList.push(dialog);

        } else {

            availableList.push(dialog);

        }

    });



    if (searchQ) {

        availableList = availableList.filter(d => d.name && d.name.toLowerCase().includes(searchQ));

    }

    if (typeVal !== 'all') {

        availableList = availableList.filter(d => d.chat_type === typeVal);

    }



    availableList.sort((a, b) => {

        if (sortVal === 'timestamp') {

            return (b.last_active || 0) - (a.last_active || 0);

        } else if (sortVal === 'name') {

            return (a.name || '').localeCompare(b.name || '');

        } else if (sortVal === 'message_count') {

            const aTotal = a.is_complex ? a.parts.reduce((sum, p) => sum + (p.total_messages || 0), 0) : (a.total_messages || 0);

            const bTotal = b.is_complex ? b.parts.reduce((sum, p) => sum + (p.total_messages || 0), 0) : (b.total_messages || 0);

            return bTotal - aTotal;

        }

        return 0;

    });



    bubblesList.forEach(dialog => {

        hasBubbles = true;

        const bubbleEl = document.createElement('div');

        bubbleEl.className = 'bubble-item';

        bubbleEl.title = dialog.name;

        bubbleEl.dataset.chatId = dialog.id;

        bubbleEl.dataset.chatName = dialog.name;

        bubbleEl.dataset.savedCount = dialog.is_complex ? dialog.parts.reduce((s, p) => s + p.saved_count, 0) : (dialog.saved_count || 0);



        let avUrl = dialog.avatar_url;

        if (!avUrl && dialog.avatar_path) {

            avUrl = '/data/' + String(dialog.avatar_path).replace(/\\/g, '/').replace(/^media_backup\//, '');

        }

        if (!avUrl && dialog.is_complex && dialog.parts) {

            for (const p of dialog.parts) {

                if (p.avatar_url) { avUrl = p.avatar_url; break; }

                if (p.avatar_path) { avUrl = '/data/' + String(p.avatar_path).replace(/\\/g, '/').replace(/^media_backup\//, ''); break; }

            }

        }

        if (!avUrl) {

            const sidebarItem = document.querySelector(`.chat-list-item[data-chat-id="${dialog.id}"] img`);

            if (sidebarItem && sidebarItem.src && !sidebarItem.src.includes('default')) {

                avUrl = sidebarItem.src;

            }

        }



        const firstLetter = (dialog.name || '?').trim().charAt(0).toUpperCase();

        const hue = Math.abs(String(dialog.id).split('').reduce((acc, c) => ((acc << 5) - acc) + c.charCodeAt(0), 0)) % 360;

        let avatarHtml = avUrl ? `

            <img src="${escapeHTML(avUrl)}" class="bubble-avatar" style="object-fit: cover;" alt="avatar" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';">

            <div class="bubble-avatar" style="background:hsl(${hue}, 60%, 45%); display:none;">${escapeHTML(firstLetter)}</div>

        ` : `

            <div class="bubble-avatar" style="background:hsl(${hue}, 60%, 45%);">${escapeHTML(firstLetter)}</div>

        `;

        bubbleEl.innerHTML = avatarHtml;



        const nameEl = document.createElement('div');

        nameEl.className = 'bubble-name';

        nameEl.textContent = dialog.name;

        bubbleEl.appendChild(nameEl);



        const overlay = document.createElement('div');

        overlay.className = 'bubble-actions-overlay';



        if (dialog.backup_status === 'deleted') {

            const dot = document.createElement('span');

            dot.className = 'bubble-halted-dot';

            dot.style.background = '#ef4444';

            dot.title = 'Account / Chat permanently deleted';

            bubbleEl.appendChild(dot);

        } else if (dialog.backup_status === 'inaccessible') {

            const recheckBtn = document.createElement('button');

            recheckBtn.className = 'bubble-action-btn recheck-btn';

            recheckBtn.title = 'Inaccessible or Private. Click to recheck access.';

            recheckBtn.textContent = '🔒';

            recheckBtn.style.color = '#f59e0b';

            recheckBtn.addEventListener('click', (e) => {

                e.stopPropagation();

                if (window.recheckChatAccess) window.recheckChatAccess(dialog.id);

            });

            overlay.appendChild(recheckBtn);



            const dot = document.createElement('span');

            dot.className = 'bubble-halted-dot';

            dot.style.background = '#f59e0b';

            dot.title = 'Temporarily Inaccessible / Blocked';

            bubbleEl.appendChild(dot);

        } else if (dialog.backup_status === 'halted' || dialog.has_older_gap) {

            const resumeBtn = document.createElement('button');

            resumeBtn.className = 'bubble-action-btn resume-btn';

            resumeBtn.title = (window.I18N ? window.I18N.t('badge.halted_title') : 'Incomplete or halted archive. Click to resume.');

            resumeBtn.textContent = '▶️';

            resumeBtn.style.color = '#f59e0b';

            resumeBtn.addEventListener('click', (e) => {

                e.stopPropagation();

                if (window.openResumeModal) window.openResumeModal(dialog.id);

            });

            overlay.appendChild(resumeBtn);



            const dot = document.createElement('span');

            dot.className = 'bubble-halted-dot';

            dot.title = (window.I18N ? window.I18N.t('badge.halted_chat_title') : 'Incomplete / Halted Archive');

            bubbleEl.appendChild(dot);

        }



        const reloadBtn = document.createElement('button');

        reloadBtn.className = 'bubble-action-btn reload-btn';

        reloadBtn.title = I18N.t('archive.reload_title') || 'Reload';

        reloadBtn.textContent = '🔄';

        reloadBtn.addEventListener('click', (e) => { e.stopPropagation(); ChatManage.reload(dialog.id, dialog.name, parseInt(bubbleEl.dataset.savedCount, 10)); });



        const deleteBtn = document.createElement('button');

        deleteBtn.className = 'bubble-action-btn delete-btn';

        deleteBtn.title = I18N.t('archive.delete_title') || 'Delete';

        deleteBtn.textContent = '🗑';

        deleteBtn.addEventListener('click', (e) => { e.stopPropagation(); ChatManage.openDeleteModal(dialog.id, dialog.name); });



        overlay.appendChild(reloadBtn);

        overlay.appendChild(deleteBtn);

        bubbleEl.appendChild(overlay);



        bubbleEl.addEventListener('click', () => openChatFromBubble(String(dialog.id)));

        if (bubblesContainer) bubblesContainer.appendChild(bubbleEl);

    });



    const listFrag = document.createDocumentFragment();

    availableList.forEach(dialog => {

        let isCompletelyArchived = false;

        let statusHtml = '';

        if (dialog.is_complex) {

            isCompletelyArchived = dialog.parts.every(p => p.is_archived);

            if (isCompletelyArchived) {

                let totalSaved = dialog.parts.reduce((sum, p) => sum + p.saved_count, 0);

                statusHtml = `<span class="status archived">${I18N.t('archive.update_was', totalSaved)}</span>`;

            } else {

                let totalMsgs = dialog.parts.reduce((sum, p) => sum + (p.total_messages || 0), 0);

                statusHtml = `<span class="status messages">${I18N.t('archive.n_messages', totalMsgs.toLocaleString(I18N.locale()))}</span>`;

            }

        } else {

            isCompletelyArchived = dialog.is_archived;

            statusHtml = dialog.is_archived

                ? `<span class="status archived">${I18N.t('archive.update_was', dialog.saved_count || 0)}</span>`

                : `<span class="status messages">${I18N.t('archive.n_messages', (dialog.total_messages || 0).toLocaleString(I18N.locale()))}</span>`;

        }



        if (dialog.backup_status === 'deleted') {

            statusHtml = `<span class="status deleted" style="color:#ef4444; font-weight:600;" title="Permanently deleted from Telegram">🚫 Deleted</span>`;

        } else if (dialog.backup_status === 'inaccessible') {

            statusHtml = `<span class="status inaccessible" style="color:#f59e0b; font-weight:600; cursor:pointer;" onclick="event.stopPropagation(); if(window.recheckChatAccess) window.recheckChatAccess('${dialog.id}');" title="Inaccessible or private. Click to recheck access.">🔒 Inaccessible [Check 🔄]</span>`;

        } else if (dialog.backup_status === 'halted' || dialog.has_older_gap) {

            const countStr = (dialog.saved_count || 0).toLocaleString((typeof I18N !== 'undefined') ? I18N.locale() : 'en-US');

            const haltedStatusText = (typeof I18N !== 'undefined')

                ? I18N.t('dialog.halted_status', countStr)

                : `⚠️ Halted (${countStr} msgs) [Resume ▶]`;

            statusHtml = `<span class="status halted" style="color:#f59e0b; font-weight:600; cursor:pointer;" onclick="event.stopPropagation(); if(window.openResumeModal) window.openResumeModal('${dialog.id}');">${escapeHTML(haltedStatusText)}</span>`;

        }



        const li = document.createElement('li');

        li.className = 'dialog-item';

        if (dialog.needs_flash) {

            li.classList.add('live-update-flash');

            dialog.needs_flash = false;

        }

        li.style.flexWrap = 'wrap';

        li.dataset.chatId = dialog.id;



        const st = archiveStatusCache[dialog.id];

        if (st) {

            if (st.status === 'archiving' || st.status === 'running') statusHtml = `<span style="font-size:12px;color:var(--link-color);"><span class="pulse-icon"></span>${escapeHTML(st.status_text || 'Archiving...')}</span>`;

            else if (st.status === 'done') statusHtml = `<span style="font-size:12px;color:#4caf50;">✓ Completed</span>`;

            else if (st.status === 'error') statusHtml = `<span style="font-size:12px;color:#f44336;">⚠ Error</span>`;

            else if (st.status === 'queued') statusHtml = `<span style="font-size:12px;color:var(--text-color-light);">⏳ Queued</span>`;

        }



        let tagHtml = '';

        if (typeVal === 'all' && dialog.chat_type && dialog.chat_type !== 'unknown') {

            tagHtml = `<span class="chat-tag tag-${dialog.chat_type}">${dialog.chat_type}</span>`;

        }



        const isChecked = (window.selectedArchiveChats && window.selectedArchiveChats.has(String(dialog.id))) ? 'checked' : '';

        let linkedDisclaimerHtml = '';

        if (dialog.linked_chat_id) {

            const linkedIdNorm = String(Number(dialog.linked_chat_id) < 0 ? dialog.linked_chat_id : -1000000000000 - Number(dialog.linked_chat_id));

            const linkedDialog = getDialogById(linkedIdNorm);

            const linkedName = dialog.linked_chat_name || (linkedDialog ? linkedDialog.name : null);

            let linkedMsgs = dialog.linked_chat_count;

            if (linkedMsgs === undefined || linkedMsgs === null) {

                linkedMsgs = linkedDialog ? (linkedDialog.total_messages || linkedDialog.saved_count || 0) : 0;

            }



            const linkedSt = archiveStatusCache[linkedIdNorm];

            let disclaimerText = '';

            if (linkedSt && (linkedSt.status === 'running' || linkedSt.status === 'queued')) {

                const actionText = linkedSt.status_text || (linkedSt.status === 'queued' ? '⏳ Queued' : 'Archiving...');

                disclaimerText = linkedName

                    ? `💬 Discussion "${linkedName}" — ${actionText}`

                    : `💬 Discussion — ${actionText}`;

            } else if (linkedName) {

                disclaimerText = (linkedMsgs > 0)

                    ? I18N.t('archive.linked_chat_disclaimer_name', linkedName, linkedMsgs)

                    : I18N.t('archive.linked_chat_disclaimer_name_unknown', linkedName);

            } else {

                disclaimerText = (linkedMsgs > 0)

                    ? I18N.t('archive.linked_chat_disclaimer', linkedMsgs)

                    : I18N.t('archive.linked_chat_disclaimer_unknown');

            }

            linkedDisclaimerHtml = `<div class="linked-chat-disclaimer" data-linked-id="${linkedIdNorm}" style="width:100%; flex-basis:100%; margin-top:4px; font-size:11px; color:#3a8ed0; background:rgba(58,142,208,0.1); padding:4px 8px; border-radius:4px; display:flex; align-items:center; gap:6px;">💬 <span class="disclaimer-span">${escapeHTML(disclaimerText)}</span></div>`;

        }



        let avUrl = dialog.avatar_url || (dialog.avatar_path ? ('/data/' + String(dialog.avatar_path).replace(/\\/g, '/').replace(/^media_backup\//, '')) : '');

        if (!avUrl && dialog.is_complex && dialog.parts) {

            for (const p of dialog.parts) {

                if (p.avatar_url) { avUrl = p.avatar_url; break; }

                if (p.avatar_path) { avUrl = '/data/' + String(p.avatar_path).replace(/\\/g, '/').replace(/^media_backup\//, ''); break; }

            }

        }

        if (!avUrl) {

            const sidebarItem = document.querySelector(`.chat-list-item[data-chat-id="${dialog.id}"] img`);

            if (sidebarItem && sidebarItem.src) avUrl = sidebarItem.src;

        }

        const firstLetter = (dialog.name || '?').trim().charAt(0).toUpperCase();

        const hue = Math.abs(String(dialog.id).split('').reduce((acc, c) => ((acc << 5) - acc) + c.charCodeAt(0), 0)) % 360;

        const avatarHtml = avUrl ? `

            <img class="dialog-avatar-thumb" src="${escapeHTML(avUrl)}" alt="" data-initial="${escapeHTML(firstLetter)}" onerror="this.onerror=null;const d=document.createElement('div');d.className='dialog-avatar-placeholder';d.style.background='hsl(${hue}, 60%, 45%)';d.textContent=this.dataset.initial||'?';this.replaceWith(d);">

        ` : `

            <div class="dialog-avatar-placeholder" style="background:hsl(${hue}, 60%, 45%)">${escapeHTML(firstLetter)}</div>

        `;



        li.innerHTML = `

            <label class="dialog-label" style="display:flex; align-items:center; flex:1; min-width:0;">

                <div class="dialog-avatar-wrap">

                    ${avatarHtml}

                </div>

                <div class="chat-name-container" style="display:flex; align-items:center; flex:1; min-width:0;">

                    <input type="checkbox" class="dialog-checkbox" data-chat-id="${dialog.id}" style="margin-right: 8px;" ${isChecked}>

                    <div class="gear-container" style="display:flex; align-items:center; overflow:hidden; max-width:0; opacity:0; transition: all 0.3s ease; white-space:nowrap;">

                        <button class="chat-settings-btn" data-chat-id="${dialog.id}" data-chat-name="${escapeHTML(dialog.name)}" data-linked-id="${dialog.linked_chat_id || ''}" title="Settings" style="background:var(--hover-bg); border:none; cursor:pointer; font-size:12px; padding:2px 4px; margin-right:4px; border-radius:4px; transition: background 0.2s;">⚙</button>

                    </div>

                    <span class="dialog-name" style="transition: transform 0.3s ease; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHTML(dialog.name)}</span>

                    ${dialog.has_custom_settings ? '<span style="width:5px;height:5px;border-radius:50%;background:var(--link-color);display:inline-block;margin-left:5px;flex-shrink:0;" title="Custom Settings"></span>' : ''}

                </div>

            </label>

            <div class="dialog-info" style="display: flex; align-items: center; gap: 10px;">

                ${tagHtml}

                ${statusHtml}

                ${(dialog.is_archived || (dialog.saved_count > 0)) ? `

                    <button class="action-btn-mini reload-btn-list" title="${I18N.t('archive.reload_title')}" style="background: none; border: none; cursor: pointer; padding: 2px; font-size: 14px; opacity: 0.6; transition: opacity 0.2s;">🔄</button>

                    <button class="action-btn-mini delete-chat-btn-list" title="Delete Archive" data-chat-id="${dialog.id}" data-chat-name="${escapeHTML(dialog.name)}" style="background: none; border: none; cursor: pointer; padding: 2px; font-size: 14px; opacity: 0.6; transition: opacity 0.2s;">🗑️</button>

                ` : ''}

            </div>

            ${linkedDisclaimerHtml}

            <div class="progress-bar-container" data-chat-id="${dialog.id}" style="display: none; width: 100%; flex-basis: 100%; margin-top: 10px;">

                <div style="display: flex; flex-direction: row; gap: 10px; width: 100%;">

                    <div style="flex: 1; display: flex; flex-direction: column; gap: 4px;">

                        <div style="font-size: 11px; color: var(--text-color); opacity: 0.8; display: flex; justify-content: space-between;">

                            <span>${I18N.t('archive.messages')}</span><span class="messages-text">${I18N.t('archive.waiting')}</span>

                        </div>

                        <div style="height: 8px; background: rgba(136,136,136,0.15); border-radius: 4px; overflow: hidden; position: relative;">

                            <div class="messages-fill" style="width: 0%; height: 100%; background: rgba(136,136,136,0.5); transition: width 0.3s;"></div>

                        </div>

                    </div>

                    <div style="flex: 1; display: flex; flex-direction: column; gap: 4px;">

                        <div style="font-size: 11px; color: var(--text-color); opacity: 0.8; display: flex; justify-content: space-between;">

                            <span>${I18N.t('archive.media')}</span><span class="media-text">${I18N.t('archive.waiting')}</span>

                        </div>

                        <div style="height: 8px; background: rgba(204,170,0,0.15); border-radius: 4px; overflow: hidden; position: relative;">

                            <div class="media-fill" style="width: 0%; height: 100%; background: rgba(204,170,0,0.6); transition: width 0.3s;"></div>

                        </div>

                    </div>

                </div>

            </div>

        `;

        if (dialog.is_complex) li.querySelector('.dialog-checkbox').dataset.parts = JSON.stringify(dialog.parts);



        const relBtn = li.querySelector('.reload-btn-list');

        if (relBtn) {

            relBtn.addEventListener('click', (e) => {

                e.stopPropagation();

                ChatManage.reload(dialog.id, dialog.name, dialog.saved_count);

            });

        }



        if (listFrag) listFrag.appendChild(li);

    });

    if (listEl && listFrag) listEl.appendChild(listFrag);

    const availBadge = document.getElementById('available-chats-badge');
    if (availBadge) {
        availBadge.textContent = availableList.length > 0 ? `(${availableList.length})` : '';
    }
    const completedBadge = document.getElementById('completed-bubbles-badge');
    if (completedBadge) {
        completedBadge.textContent = bubblesList.length > 0 ? `(${bubblesList.length})` : '';
    }



    if (hasBubbles) {

        if (archiveHeader) archiveHeader.style.display = 'block';

        if (bubblesContainer) bubblesContainer.style.display = 'flex';

    } else {

        if (archiveHeader) archiveHeader.style.display = 'none';

        if (bubblesContainer) bubblesContainer.style.display = 'none';

    }

    updateArchiveUI();

    if (typeof updateStartArchiveButton === 'function') updateStartArchiveButton();

}



loadAllDialogs = async function (silent = false) {
    if (window.isFullRefreshing) return;

    window.loadAllDialogs = loadAllDialogs;

    const listEl = (typeof dialogsList !== 'undefined' && dialogsList) ? dialogsList : document.getElementById('dialogs-list');

    if (!silent && listEl) listEl.innerHTML = `<li style="padding: 15px; text-align: center;">⚡ ${typeof I18N !== 'undefined' ? I18N.t('archive.loading_cache') : 'Loading cache...'}</li>`;

    try {

        // Request local cache only to avoid waking Telethon

        const response = await fetch('/api/get_all_dialogs?cache_only=1');

        if (!response.ok) {

            const err = await response.json();

            throw new Error(err.error || 'Failed to load dialogs');

        }

        const dialogs = await response.json();



        // Always render, even if empty

        renderDialogsList(dialogs || []);



    } catch (error) {

        console.error('Error loading dialogs:', error);

        if (!silent && listEl) listEl.innerHTML = `<li style="padding: 15px; color: #a13434; text-align: center;">Error: ${escapeHTML(error.message)}</li>`;

        if (typeof window.updateQuickRefreshButtonState === 'function') {

            window.updateQuickRefreshButtonState();

        }

    }

}



function debouncedUpdateArchiveUI() {

    if (window.uiUpdateTimer) {

        window.uiUpdatePending = true;

        return;

    }

    window.uiUpdateTimer = setTimeout(() => {

        window.uiUpdateTimer = null;

        updateArchiveUI();

        if (window.uiUpdatePending) {

            window.uiUpdatePending = false;

            debouncedUpdateArchiveUI();

        }

    }, 300);

}



startArchiveStream = function () {

    if (archiveEventSource) return;



    archiveEventSource = new EventSource('/api/archive_stream');



    archiveEventSource.onmessage = function (event) {

        const msg = JSON.parse(event.data);

        if (msg.type === 'ping') return;



        if (msg.type === 'init') {

            archiveStatusCache = msg.data;

            debouncedUpdateArchiveUI();

            return;

        }



        if (msg.type === 'live_new_message') {

            const liveData = msg.data;



            // Update timestamp in global dialogs cache to trigger live re-sorting

            if (window.allDialogsData) {

                const chat = window.allDialogsData.find(d => areChatIdsEqual(d.id, liveData.chat_id) || (d.is_complex && d.parts && d.parts.some(p => areChatIdsEqual(p.id, liveData.chat_id))));

                if (chat && liveData.timestamp) {

                    chat.last_active = liveData.timestamp;

                    chat.total_messages = (chat.total_messages || 0) + 1;

                    if (chat.is_archived) {

                        chat.saved_count = (chat.saved_count || 0) + 1;

                    }

                    if (chat.is_complex && chat.parts) {

                        const part = chat.parts.find(p => areChatIdsEqual(p.id, liveData.chat_id));

                        if (part) {

                            part.total_messages = (part.total_messages || 0) + 1;

                            if (part.is_archived) part.saved_count = (part.saved_count || 0) + 1;

                        }

                    }



                    const existingEl = document.querySelector(`.dialog-item[data-chat-id="${chat.id}"]`);

                    const bubbleEl = document.querySelector(`.bubble-item[data-chat-id="${chat.id}"]`);

                    const targetEl = existingEl || bubbleEl;



                    if (targetEl) {

                        // Update message/saved count in DOM immediately so count updates synchronously with the flash

                        if (existingEl) {

                            const archivedSpan = existingEl.querySelector('.status.archived');

                            const messagesSpan = existingEl.querySelector('.status.messages');

                            if (archivedSpan) {

                                const totalSaved = (chat.is_complex && chat.parts)

                                    ? chat.parts.reduce((s, p) => s + (p.saved_count || 0), 0)

                                    : (chat.saved_count || 0);

                                archivedSpan.textContent = I18N.t('archive.update_was', totalSaved);

                            } else if (messagesSpan) {

                                const totalMsgs = (chat.is_complex && chat.parts)

                                    ? chat.parts.reduce((s, p) => s + (p.total_messages || 0), 0)

                                    : (chat.total_messages || 0);

                                messagesSpan.textContent = I18N.t('archive.n_messages', totalMsgs.toLocaleString(I18N.locale()));

                            }

                        }



                        // If sorted by timestamp (default), move to top in-place without tearing down DOM

                        const sortSelect = document.getElementById('archive-sort-select');

                        const sortVal = sortSelect ? sortSelect.value : 'timestamp';

                        if (sortVal === 'timestamp' && targetEl.parentElement && targetEl.parentElement.firstElementChild !== targetEl) {

                            targetEl.parentElement.prepend(targetEl);

                        }



                        // Apply instant visual flash exactly once

                        targetEl.classList.remove('live-update-flash');

                        void targetEl.offsetWidth; // trigger reflow to restart animation cleanly

                        targetEl.classList.add('live-update-flash');



                        // Mark needs_flash false so any subsequent re-render does not re-flash

                        chat.needs_flash = false;



                        // If sorted by something other than timestamp (e.g. message_count), debounce re-sort until after flash completes

                        if (sortVal !== 'timestamp' && window.applyArchiveFilters) {

                            if (window._liveSortTimeout) clearTimeout(window._liveSortTimeout);

                            window._liveSortTimeout = setTimeout(() => {

                                window.applyArchiveFilters();

                            }, 1600);

                        }

                    } else {

                        // Target item not currently rendered in DOM (e.g. hidden by search or category filter)

                        chat.needs_flash = true;

                        if (window.applyArchiveFilters) {

                            if (window._liveSortTimeout) clearTimeout(window._liveSortTimeout);

                            window._liveSortTimeout = setTimeout(() => {

                                window.applyArchiveFilters();

                            }, 300);

                        }

                    }

                }

            }



            if (window.currentChatId && areChatIdsEqual(liveData.chat_id, window.currentChatId)) {

                if (liveData.is_archived !== false && typeof window.appendLiveMessage === 'function') {

                    window.appendLiveMessage(liveData);

                }

            }

            return;

        }



        if (msg.type === 'live_message_updated') {

            const liveData = msg.data;

            if (window.currentChatId && areChatIdsEqual(liveData.chat_id, window.currentChatId)) {

                if (typeof window.appendOrUpdateLiveMessage === 'function') {

                    window.appendOrUpdateLiveMessage(liveData);

                } else if (typeof window.appendLiveMessage === 'function') {

                    window.appendLiveMessage(liveData);

                }

            }

            return;

        }



        if (msg.type === 'live_message_edited') {

            const liveData = msg.data;

            if (window.currentChatId && areChatIdsEqual(liveData.chat_id, window.currentChatId)) {

                if (typeof window.updateLiveMessage === 'function') {

                    window.updateLiveMessage(liveData);

                }

            }

            return;

        }



        if (msg.type === 'live_message_reactions') {

            const liveData = msg.data;

            if (window.currentChatId && areChatIdsEqual(liveData.chat_id, window.currentChatId)) {

                if (typeof window.updateLiveMessageReactions === 'function') {

                    window.updateLiveMessageReactions(liveData);

                }

            }

            return;

        }



        if (msg.type === 'live_message_deleted') {

            const liveData = msg.data;



            // Decrement global message count for the chat

            if (window.allDialogsData && liveData.chat_id) {

                const chat = window.allDialogsData.find(d => areChatIdsEqual(d.id, liveData.chat_id) || (d.is_complex && d.parts && d.parts.some(p => areChatIdsEqual(p.id, liveData.chat_id))));

                if (chat && chat.total_messages > 0) {

                    chat.total_messages -= 1;

                    if (chat.is_archived && chat.saved_count && chat.saved_count > 0) {

                        chat.saved_count -= 1;

                    }

                    if (chat.is_complex && chat.parts) {

                        const part = chat.parts.find(p => areChatIdsEqual(p.id, liveData.chat_id));

                        if (part && part.total_messages > 0) {

                            part.total_messages -= 1;

                            if (part.is_archived && part.saved_count && part.saved_count > 0) part.saved_count -= 1;

                        }

                    }



                    const existingEl = document.querySelector(`.dialog-item[data-chat-id="${chat.id}"]`);

                    if (existingEl) {

                        const archivedSpan = existingEl.querySelector('.status.archived');

                        const messagesSpan = existingEl.querySelector('.status.messages');

                        if (archivedSpan) {

                            const totalSaved = (chat.is_complex && chat.parts)

                                ? chat.parts.reduce((s, p) => s + (p.saved_count || 0), 0)

                                : (chat.saved_count || 0);

                            archivedSpan.textContent = I18N.t('archive.update_was', totalSaved);

                        } else if (messagesSpan) {

                            const totalMsgs = (chat.is_complex && chat.parts)

                                ? chat.parts.reduce((s, p) => s + (p.total_messages || 0), 0)

                                : (chat.total_messages || 0);

                            messagesSpan.textContent = I18N.t('archive.n_messages', totalMsgs.toLocaleString(I18N.locale()));

                        }

                    }

                }

            }



            if (!liveData.chat_id || (window.currentChatId && areChatIdsEqual(liveData.chat_id, window.currentChatId))) {

                if (typeof window.deleteLiveMessage === 'function') {

                    window.deleteLiveMessage(liveData);

                }

            }

            return;

        }



        if (msg.type === 'chat_status_updated') {

            const sData = msg.data;

            if (sData && sData.chat_id) {

                if (window.chatListData && Array.isArray(window.chatListData)) {

                    const target = window.chatListData.find(c => areChatIdsEqual(c.chat_id, sData.chat_id));

                    if (target) {

                        target.backup_status = sData.status;

                        target.last_halt_reason = sData.reason;

                        if (typeof window.renderSidebarChats === 'function') {

                            window.renderSidebarChats(window.chatListData);

                        }

                    }

                }

                const activeId = window.AppState ? window.AppState.currentChatId : window.currentChatId;

                if (areChatIdsEqual(activeId, sData.chat_id) && typeof window.updateChatCutoffVisuals === 'function') {

                    window.updateChatCutoffVisuals({

                        backup_status: sData.status,

                        last_halt_reason: sData.reason

                    });

                }

            }

        }



        if (msg.type === 'live_dialogs_refreshed') {

            const dialogs = msg.data && msg.data.dialogs;

            if (dialogs && Array.isArray(dialogs)) {

                window.allDialogsData = dialogs;



                // Preserve checkbox selections if user is currently configuring an archive

                const checkedMap = new Map();

                document.querySelectorAll('#dialogs-list .dialog-checkbox:checked').forEach(cb => {

                    checkedMap.set(String(cb.dataset.chatId), cb.dataset.selectedParts || null);

                });



                if (typeof renderDialogsList === 'function') {

                    renderDialogsList(dialogs);

                }



                if (checkedMap.size > 0) {

                    document.querySelectorAll('#dialogs-list .dialog-checkbox').forEach(cb => {

                        const id = String(cb.dataset.chatId);

                        if (checkedMap.has(id)) {

                            cb.checked = true;

                            const parts = checkedMap.get(id);

                            if (parts) cb.dataset.selectedParts = parts;

                            cb.dispatchEvent(new Event('change', { bubbles: true }));

                        }

                    });

                }

            }



            if (typeof loadSidebarChats === 'function') {

                loadSidebarChats();

            }

            return;

        }



        if (msg.type === 'user_status_update') {

            if (typeof window.updateLiveUserStatus === 'function') {

                window.updateLiveUserStatus(msg.data);

            }

            return;

        }



        if (msg.type === 'whisper_download_progress') {
            if (typeof window.handleWhisperDownloadProgress === 'function') {
                window.handleWhisperDownloadProgress(msg.data);
            }
            return;
        }

        const jobId = String(msg.id);

        const isNewJob = !archiveStatusCache[jobId];

        if (!archiveStatusCache[jobId]) archiveStatusCache[jobId] = {};

        const prev = archiveStatusCache[jobId];

        const prevStatus = prev.status;

        Object.assign(prev, msg);

        if (!prev.status_text && msg.text) prev.status_text = msg.text;



        if (isNewJob && window.allDialogsData) {

            const exists = window.allDialogsData.some(x => String(x.id) === jobId || (x.is_complex && x.parts && x.parts.some(p => String(p.id) === jobId)));

            if (!exists && typeof loadAllDialogs === 'function') {

                if (window._autoReloadDialogsTimeout) clearTimeout(window._autoReloadDialogsTimeout);

                window._autoReloadDialogsTimeout = setTimeout(() => {

                    loadAllDialogs(true);

                }, 500);

            }

        }



        if (msg.status === 'done') {

            if (typeof updateGlobalStats === 'function') updateGlobalStats();

        }



        // Notification for auth errors or other backend failures

        if (msg.status === 'error' && prevStatus !== 'error') {

            showNotification(`Ошибка: ${msg.error || msg.status_text || msg.text || 'Неизвестная ошибка'}`, 'error');

        }



        // Auto-refresh open chat if this job finished, halted, or errored for currentChatId

        const isCurrentChat = Boolean(window.currentChatId && typeof areChatIdsEqual === 'function' && areChatIdsEqual(window.currentChatId, jobId));

        const isFinishedStatus = (msg.status === 'done' || msg.status === 'error' || msg.status === 'halted');

        const wasActive = (prevStatus === 'running' || prevStatus === 'queued' || isNewJob);

        if (isCurrentChat && isFinishedStatus && wasActive) {

            if (window._reloadCurrentChatTimeout) clearTimeout(window._reloadCurrentChatTimeout);

            window._reloadCurrentChatTimeout = setTimeout(() => {

                if (window.currentChatId && typeof areChatIdsEqual === 'function' && areChatIdsEqual(window.currentChatId, jobId)) {

                    if (typeof window.reloadCurrentChatMessages === 'function') {

                        window.reloadCurrentChatMessages(window.currentChatId);

                    }

                }

            }, 350);

        }



        // Persist phase totals: when entering Phase 2, save the msg total

        const statusText = msg.status_text || msg.text || '';

        const isPhase2Text = msg.phase === 2 || statusText.includes('Phase 2') || statusText.includes('Фаза 2');

        if (isPhase2Text && !prev._msg_total_locked) {

            prev._msg_total = prev.processed || 0;  // msgs done at end of phase 1

            prev._msg_total_locked = true;

        }

        if (msg.phase === 2) {

            prev._media_total = msg.total || prev._media_total || 0;

        }



        if (msg.last_item && msg.last_item !== lastPreviewStrings[jobId]) {

            lastPreviewStrings[jobId] = msg.last_item;

            queueLivePreviewLog(jobId, msg.last_item);

        }



        if (currentInspectorChatId && areChatIdsEqual(currentInspectorChatId, msg.id)) {

            updateInspectorLiveCatchup(msg);

        }



        debouncedUpdateArchiveUI();

    };



    archiveEventSource.onerror = function () {

        // Браузер сам переподключится в случае обрыва

    };

}



function queueLivePreviewLog(jobId, text) {

    liveLogBuffer.push({ jobId, text });

    if (!liveLogAnimFrame) {

        liveLogAnimFrame = setTimeout(flushLivePreviewLogs, 100);

    }

}



function flushLivePreviewLogs() {

    const logEl = document.getElementById('live-preview-log');

    if (!logEl) {

        liveLogBuffer = [];

        liveLogAnimFrame = null;

        return;

    }



    let LOG_POOL_SIZE = Math.max(10, Math.floor(logEl.clientHeight / 24));



    if (liveLogBuffer.length === 0) {

        liveLogAnimFrame = null;

        return;

    }



    while (logEl.children.length < LOG_POOL_SIZE) {

        const d = document.createElement('div');

        d.className = 'log-entry';

        d.style.display = 'none';

        logEl.appendChild(d);

    }

    if (logEl.children.length > LOG_POOL_SIZE) {

        Array.from(logEl.children).slice(LOG_POOL_SIZE).forEach(n => n.remove());

    }



    const recentLogs = liveLogBuffer.slice(-LOG_POOL_SIZE);

    const currentLogs = Array.from(logEl.children)

        .filter(node => node.style.display !== 'none')

        .map(node => node.innerHTML);



    let combinedLogs = [...currentLogs, ...recentLogs.map(item => {

        const typeClass = item.text.startsWith('msg') ? 'log-type-msg' : 'log-type-pic';

        let chatName = `ID:${item.jobId}`;



        let targetDialog = (window.allDialogsData || []).find(d => areChatIdsEqual(d.id, item.jobId) || (d.parts && d.parts.some(p => areChatIdsEqual(p.id, item.jobId))));

        if (targetDialog) {

            chatName = targetDialog.name;

        } else {

            const allItems = document.querySelectorAll('.dialog-item');

            for (let dItem of allItems) {

                if (areChatIdsEqual(dItem.dataset.chatId, item.jobId)) {

                    const nameEl = dItem.querySelector('.dialog-name');

                    if (nameEl) { chatName = nameEl.textContent; break; }

                }

            }

        }

        const finalText = escapeHTML(item.text);



        return `<span class="log-prefix">[${escapeHTML(chatName)}]</span> <span class="${typeClass}">${finalText}</span>`;

    })];



    combinedLogs = combinedLogs.slice(-LOG_POOL_SIZE);



    const poolNodes = Array.from(logEl.children);

    for (let i = 0; i < LOG_POOL_SIZE; i++) {

        const node = poolNodes[i];

        if (!node) continue;

        if (i < combinedLogs.length) {

            node.innerHTML = combinedLogs[i];

            node.style.display = '';

        } else {

            node.style.display = 'none';

        }

    }



    const isAtBottom = (logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 20);

    if (isAtBottom) {

        logEl.scrollTop = logEl.scrollHeight;

    }



    liveLogBuffer = [];

    liveLogAnimFrame = null;

}



function updateArchiveUI() {

    let activeJobs = 0;

    let allDone = true;

    const queuedChatIds = Object.keys(archiveStatusCache).filter(k => archiveStatusCache[k]?.status === 'queued');

    const allDialogItems = document.querySelectorAll('.dialog-item');

    allDialogItems.forEach(item => {

        const bar = item.querySelector('.progress-bar-container');

        if (!bar) return;



        const cb = item.querySelector('.dialog-checkbox');

        const messagesFill = bar.querySelector('.messages-fill');

        const mediaFill = bar.querySelector('.media-fill');

        const messagesText = bar.querySelector('.messages-text');

        const mediaText = bar.querySelector('.media-text');

        const statusSpan = item.querySelector('.dialog-info .status');



        let parts = cb.dataset.selectedParts ? JSON.parse(cb.dataset.selectedParts) : (cb.dataset.parts ? JSON.parse(cb.dataset.parts) : [{ id: item.dataset.chatId }]);

        let statusesOfParts = [];

        let itemAllDone = true;

        let isRunning = false;

        let isQueued = false;

        let hasError = false;

        let activeStatusText = '';



        let totalMsgProcessed = 0, totalMsgToProcess = 0;

        let totalMediaProcessed = 0, totalMediaToProcess = 0;



        parts.forEach(p => {

            const s = archiveStatusCache[p.id];

            if (s) {

                if (s.status === 'running') isRunning = true;

                if (s.status === 'queued') isQueued = true;

                if (s.status === 'error') {

                    hasError = true;

                    activeStatusText = s.error || s.status_text || s.text || 'Ошибка!';

                }

                if (s.status !== 'done') { itemAllDone = false; allDone = false; }

                statusesOfParts.push(s.status);



                if (s.phase === 2 && !s._msg_total_locked) {

                    s._msg_total = s._msg_total || s.total;

                    s._msg_total_locked = true;

                }

                if (s.phase === 2 && s.total > 0) {

                    s._media_total = s.total;

                }



                const isCatchup = (s.phase === 'catchup');

                const isPhase2 = (s.phase === 2) || isCatchup;

                const isPhase3OrDone = (s.phase === 3) || (s.phase === 'saving') || (s.status === 'done');



                if (s.status === 'done') {

                    const msgTotal = s._msg_total || s.total || 1;

                    const mediaTotal = s._media_total || 0;

                    totalMsgProcessed += msgTotal; totalMsgToProcess += msgTotal;

                    totalMediaProcessed += mediaTotal; totalMediaToProcess += mediaTotal;

                } else if (isCatchup) {

                    const mediaTotal = Math.max(1, s.total || 1);

                    totalMediaProcessed += (s.processed || 0);

                    totalMediaToProcess += mediaTotal;

                    totalMsgProcessed += (s.processed || 0);

                    totalMsgToProcess += mediaTotal;

                } else if (isPhase3OrDone) {

                    const msgTotal = s._msg_total || s.total || 1;

                    const mediaTotal = s._media_total || 0;

                    totalMsgProcessed += (s.processed || msgTotal); totalMsgToProcess += msgTotal;

                    totalMediaProcessed += mediaTotal; totalMediaToProcess += mediaTotal;

                } else if (isPhase2) {

                    const msgTotal = s._msg_total || 1;

                    totalMsgProcessed += msgTotal; totalMsgToProcess += msgTotal;

                    const mediaTotal = s._media_total || Math.max(1, s.total || 1);

                    totalMediaProcessed += (s.processed || 0); totalMediaToProcess += mediaTotal;

                } else {

                    // Phase 1: messages only

                    totalMsgProcessed += (s.processed || 0);

                    if (s.total > 0) totalMsgToProcess += s.total;

                    totalMediaToProcess += 0;

                }

            } else {

                itemAllDone = false;

                statusesOfParts.push('unknown');

            }

        });



        if (statusesOfParts.includes('running') || statusesOfParts.includes('queued')) activeJobs++;



        if (isRunning) {

            item.style.order = '-2';

            item.classList.remove('waitlist-queued');

        } else if (isQueued) {

            item.style.order = '-1';

            item.classList.add('waitlist-queued');

        } else {

            item.style.order = '0';

            item.classList.remove('waitlist-queued');

        }



        let qNum = 1;

        if (isQueued) {

            const itemCid = String(item.dataset.chatId);

            let idx = queuedChatIds.indexOf(itemCid);

            if (idx < 0) idx = queuedChatIds.findIndex(id => areChatIdsEqual(id, itemCid));

            qNum = (idx >= 0 ? idx + 1 : 1);

        }



        const msgPct = totalMsgToProcess > 0 ? Math.min(100, Math.round((totalMsgProcessed / totalMsgToProcess) * 100)) : 0;

        const mediaPct = totalMediaToProcess > 0 ? Math.min(100, Math.round((totalMediaProcessed / totalMediaToProcess) * 100)) : 0;

        const masterPct = totalMediaToProcess > 0 ? Math.round((msgPct + mediaPct) / 2) : Math.round(msgPct / 2);



        if (isRunning || (itemAllDone && masterPct > 0)) {

            item.style.backgroundImage = `linear-gradient(90deg, rgba(76, 175, 80, 0.15) ${masterPct}%, transparent ${masterPct}%)`;

        } else {

            item.style.backgroundImage = '';

        }



        const parentChatId = item.dataset.chatId;

        const parentDialog = getDialogById(parentChatId);

        const linkedIdVal = parentDialog ? parentDialog.linked_chat_id : null;



        if (linkedIdVal) {

            const linkedIdNorm = String(Number(linkedIdVal) < 0 ? linkedIdVal : -1000000000000 - Number(linkedIdVal));

            let disclaimerEl = item.querySelector('.linked-chat-disclaimer');

            if (!disclaimerEl) {

                disclaimerEl = document.createElement('div');

                disclaimerEl.className = 'linked-chat-disclaimer';

                disclaimerEl.style.cssText = 'width:100%; flex-basis:100%; margin-top:4px; font-size:11px; color:#3a8ed0; background:rgba(58,142,208,0.1); padding:4px 8px; border-radius:4px; display:flex; align-items:center; gap:6px;';

                item.appendChild(disclaimerEl);

            }

            disclaimerEl.dataset.linkedId = linkedIdNorm;



            const lSt = archiveStatusCache[linkedIdNorm];

            const linkedDialog = getDialogById(linkedIdNorm);

            const linkedName = (parentDialog && parentDialog.linked_chat_name) || (linkedDialog ? linkedDialog.name : null);

            let linkedMsgs = parentDialog ? parentDialog.linked_chat_count : null;

            if (linkedMsgs === undefined || linkedMsgs === null) {

                linkedMsgs = linkedDialog ? (linkedDialog.total_messages || linkedDialog.saved_count || 0) : 0;

            }



            let disclaimerText = '';

            if (lSt && (lSt.status === 'running' || lSt.status === 'queued')) {

                const actionText = lSt.status_text || (lSt.status === 'queued' ? '⏳ Queued' : 'Archiving...');

                disclaimerText = linkedName ? `Discussion "${linkedName}" — ${actionText}` : `Discussion — ${actionText}`;

            } else if (linkedName) {

                disclaimerText = (linkedMsgs > 0)

                    ? I18N.t('archive.linked_chat_disclaimer_name', linkedName, linkedMsgs)

                    : I18N.t('archive.linked_chat_disclaimer_name_unknown', linkedName);

            } else {

                disclaimerText = (linkedMsgs > 0)

                    ? I18N.t('archive.linked_chat_disclaimer', linkedMsgs)

                    : I18N.t('archive.linked_chat_disclaimer_unknown');

            }



            disclaimerEl.innerHTML = `💬 <span class="disclaimer-span">${escapeHTML(disclaimerText)}</span>`;

        }



        if (isRunning) {

            bar.style.display = 'flex';

            if (statusSpan) statusSpan.style.display = 'none';



            if (messagesFill) messagesFill.style.width = msgPct + '%';

            if (mediaFill) mediaFill.style.width = mediaPct + '%';

            if (messagesText) {

                if (statusesOfParts.some(stKey => archiveStatusCache[stKey]?.phase === 'catchup')) {

                    messagesText.textContent = 'Media Catch-up';

                } else if (totalMsgToProcess > 0) {

                    messagesText.textContent = `${totalMsgProcessed} / ${totalMsgToProcess}`;

                } else if (totalMsgProcessed > 0) {

                    messagesText.textContent = `${totalMsgProcessed} (range)`;

                } else {

                    messagesText.textContent = '...';

                }

            }

            if (mediaText) mediaText.textContent = totalMediaToProcess > 0 ? `(${totalMediaProcessed}/${Math.max(0, totalMediaToProcess - totalMediaProcessed)})` : '—';



        } else if (isQueued) {

            bar.style.display = 'none';

            if (statusSpan) {

                statusSpan.style.display = 'inline-block';

                statusSpan.innerHTML = `<span class="waitlist-badge">⏳ In Queue (#${qNum})</span>`;

            }

        } else if (hasError) {

            bar.style.display = 'none';

            item.style.backgroundImage = 'none';

            item.style.backgroundColor = 'rgba(161, 52, 52, 0.1)';

            if (statusSpan) {

                statusSpan.textContent = activeStatusText || I18N.t('error.generic_short');

                statusSpan.className = 'status error';

                statusSpan.style.display = 'block';

            }

        } else if (itemAllDone && parts.length > 0) {

            cb.removeAttribute('data-selected-parts');

            if (messagesText) messagesText.textContent = I18N.t('status.done');

            if (mediaText) mediaText.textContent = I18N.t('status.done');

            if (messagesFill) messagesFill.style.width = '100%';

            if (mediaFill) mediaFill.style.width = '100%';

            if (statusSpan) {

                statusSpan.style.display = 'inline';

                statusSpan.textContent = I18N.t('status.done_excl');

                statusSpan.className = 'status archived';

                item.dataset.archived = 'true';

            }

            setTimeout(() => {

                item.style.backgroundImage = '';

                item.style.backgroundColor = '';

                bar.style.display = 'none';

            }, 2000);

        }

    });



    if (activeJobs > 0) {

        const startBtn = document.getElementById('start-archive-btn');

        if (startBtn) {

            startBtn.textContent = (typeof I18N !== 'undefined' && I18N.t)

                ? I18N.t('archive.stop_n', activeJobs)

                : `Stop (${activeJobs})`;

            startBtn.style.backgroundColor = '#d32f2f';

            startBtn.style.color = '#fff';

            startBtn.dataset.isArchiving = "true";

            startBtn.disabled = false;

        }

    } else if (activeJobs === 0) {

        const liveCb = document.getElementById('set-live-update');

        const isLiveEnabled = liveCb && liveCb.checked;



        const allTasksFinished = Object.keys(archiveStatusCache).length === 0 ||

            Object.values(archiveStatusCache).every(s => s.status === 'done' || s.status === 'error');



        if (!isLiveEnabled && (allDone || allTasksFinished)) {

            setTimeout(() => {

                if (archiveEventSource) {

                    archiveEventSource.close();

                    archiveEventSource = null;

                }

            }, 1000);

        }



        const startBtn = document.getElementById('start-archive-btn');

        const wasArchiving = startBtn && startBtn.dataset.isArchiving === "true";



        if (wasArchiving) {

            if (startBtn) {

                startBtn.style.backgroundColor = '';

                startBtn.style.color = '';

                startBtn.dataset.isArchiving = "false";

                startBtn.disabled = true;

                startBtn.textContent = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('archive.start_0') : 'Start Archiving (0)';

            }

            loadAllDialogs(true);

            if (typeof loadSidebarChats === 'function') loadSidebarChats();

        }

    }



    if (typeof updateCurrentChatActiveTaskBanner === 'function') {

        updateCurrentChatActiveTaskBanner();

    }

}




function renderTaskRowContent(row, key, currentStatus) {
    const iconEl = row.querySelector('.chat-task-row-icon');
    const textEl = row.querySelector('.chat-task-row-text');
    const pctEl = row.querySelector('.chat-task-row-pct');
    const fillEl = row.querySelector('.chat-task-row-fill');
    const cancelBtn = row.querySelector('.chat-task-row-cancel-btn');
    const rowSub = row.querySelector('.chat-task-row-sub');
    const subTextEl = row.querySelector('.chat-task-row-sub-text');
    const subFillEl = row.querySelector('.chat-task-row-sub-fill');
    const subPctEl = row.querySelector('.chat-task-row-sub-pct');

    const status = currentStatus.status;
    const phase = currentStatus.phase;
    const isTranscribe = (phase === 'transcribe' || key.startsWith('transcribe'));
    const isCatchup = (phase === 'catchup');
    const isResume = Boolean(
        currentStatus.is_resume ||
        phase === 'resume' ||
        (currentStatus.status_text && currentStatus.status_text.toLowerCase().includes('resume')) ||
        (currentStatus.status_text && currentStatus.status_text.toLowerCase().includes('возобновлен'))
    );

    if (status === 'running') {
        if (cancelBtn) { cancelBtn.style.display = 'inline-block'; cancelBtn.disabled = false; }
        if (fillEl) fillEl.style.background = isTranscribe ? '#9c27b0' : 'var(--link-color)';
        if (pctEl) pctEl.style.color = isTranscribe ? '#ba68c8' : 'var(--link-color)';

        let pct = 0;
        let displayText = currentStatus.status_text || currentStatus.text || (window.I18N ? window.I18N.t('task.running') : 'Running...');

        if (isTranscribe) {
            if (iconEl) iconEl.textContent = '🎙️';
            const processed = currentStatus.processed || 0;
            const total = currentStatus.total || 0;
            pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : (currentStatus.pct || 0);
            displayText = window.I18N ? window.I18N.t('whisper.batch_progress', processed, total, pct) : `Transcribing voice notes: ${processed} / ${total} (${pct}%)`;
            if (currentStatus.last_item) displayText += ` — ${currentStatus.last_item}`;
        } else if (isCatchup) {
            if (iconEl) iconEl.textContent = '⚡';
            const processed = currentStatus.processed || 0;
            const total = currentStatus.total || 0;
            pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
            displayText = window.I18N ? window.I18N.t('task.media_catchup', processed, total || '?', pct) : `⚡ Media Catch-up: ${processed} / ${total || '?'} files (${pct}%)`;
            if (currentStatus.last_item) displayText += ` — ${currentStatus.last_item}`;
        } else if (phase === 'reconcile') {
            if (iconEl) iconEl.textContent = '⚡';
            pct = 100;
            displayText = currentStatus.status_text || currentStatus.text || 'Checking message gaps (O(1) HOR)...';
        } else if (phase === 2) {
            if (iconEl) iconEl.textContent = '📁';
            const processed = currentStatus.processed || 0;
            const total = currentStatus._media_total || currentStatus.total || 0;
            const remaining = Math.max(0, total - processed);
            pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
            displayText = window.I18N ? window.I18N.t('task.phase2', processed, remaining, total, pct) : `Phase 2: Downloading files (${processed}/${remaining}) [${total} new] (${pct}%)`;
            if (currentStatus.last_item) displayText += ` — ${currentStatus.last_item}`;
        } else if (phase === 3 || phase === 'saving') {
            if (iconEl) iconEl.textContent = '💾';
            pct = 100;
            displayText = currentStatus.status_text || currentStatus.text || (window.I18N ? window.I18N.t('task.finalizing') : 'Phase 3: Finalizing...');
            if (currentStatus.last_item) displayText += ` — ${currentStatus.last_item}`;
        } else if (isResume) {
            if (iconEl) iconEl.textContent = '🔄';
            const processed = currentStatus.processed || 0;
            const total = currentStatus.total || 0;
            pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
            if (total > 0) {
                displayText = window.I18N ? window.I18N.t('task.resume_messages_total', processed, total, pct) : `Resuming: Downloading remaining ${processed} / ${total} messages (${pct}%)`;
            } else {
                displayText = window.I18N ? window.I18N.t('task.resume_messages_count', processed) : `Resuming: Downloading remaining messages (${processed})...`;
            }
            if (currentStatus.last_item) displayText += ` — ${currentStatus.last_item}`;
        } else {
            if (iconEl) iconEl.textContent = '📥';
            const processed = currentStatus.processed || 0;
            const total = currentStatus.total || 0;
            pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
            if (total > 0) {
                displayText = window.I18N ? window.I18N.t('task.phase1_total', processed, total, pct) : `Phase 1: Downloading messages ${processed} / ${total} (${pct}%)`;
            } else {
                displayText = window.I18N ? window.I18N.t('task.phase1_count', processed) : `Phase 1: Downloading messages (${processed})...`;
            }
            if (currentStatus.last_item) displayText += ` — ${currentStatus.last_item}`;
        }

        if (textEl) textEl.textContent = displayText;
        if (pctEl) pctEl.textContent = pct > 0 ? `${pct}%` : '';
        if (fillEl) fillEl.style.width = `${pct}%`;

        // Sub-row live progress
        if (rowSub) {
            if (currentStatus.sub_text) {
                rowSub.style.display = 'flex';
                if (subTextEl) subTextEl.textContent = currentStatus.sub_text;
                const subPct = (currentStatus.sub_pct !== undefined) ? currentStatus.sub_pct : 0;
                if (subFillEl) {
                    subFillEl.style.width = `${subPct}%`;
                    subFillEl.style.background = isTranscribe ? '#ba68c8' : 'var(--link-color)';
                }
                if (subPctEl) {
                    subPctEl.textContent = `${subPct}%`;
                    subPctEl.style.color = isTranscribe ? '#ba68c8' : 'var(--link-color)';
                }
            } else {
                rowSub.style.display = 'none';
            }
        }
    } else if (status === 'queued') {
        if (cancelBtn) cancelBtn.style.display = 'inline-block';
        if (iconEl) iconEl.textContent = isTranscribe ? '🎙️' : '⏳';
        if (textEl) textEl.textContent = currentStatus.status_text || (window.I18N ? window.I18N.t('task.in_queue') : 'In queue...');
        if (pctEl) pctEl.textContent = window.I18N ? window.I18N.t('task.queued_short') : 'Queued';
        if (fillEl) {
            fillEl.style.width = '0%';
            fillEl.style.background = isTranscribe ? '#9c27b0' : 'var(--link-color)';
        }
        if (rowSub) rowSub.style.display = 'none';
    } else if (status === 'done') {
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (iconEl) iconEl.textContent = isTranscribe ? '🎙️' : '✅';
        const defDone = isTranscribe ? (window.I18N ? window.I18N.t('whisper.batch_complete', currentStatus.processed || 0) : `Transcription complete (${currentStatus.processed || 0} messages)`) : (window.I18N ? window.I18N.t('task.completed') : 'Completed!');
        if (textEl) textEl.textContent = currentStatus.status_text || defDone;
        if (pctEl) pctEl.textContent = '100%';
        if (fillEl) {
            fillEl.style.width = '100%';
            fillEl.style.background = '#27ae60';
        }
        if (rowSub) rowSub.style.display = 'none';
        if (!currentStatus._dismissTimer) {
            currentStatus._dismissTimer = setTimeout(() => {
                currentStatus._dismissed = true;
                updateCurrentChatActiveTaskBanner();
            }, 3000);
        }
    } else if (status === 'error') {
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (iconEl) iconEl.textContent = '⚠️';
        if (textEl) textEl.textContent = currentStatus.error || currentStatus.status_text || (window.I18N ? window.I18N.t('task.error') : 'Error occurred');
        if (pctEl) pctEl.textContent = window.I18N ? window.I18N.t('task.error_short') : 'Error';
        if (fillEl) {
            fillEl.style.width = '100%';
            fillEl.style.background = '#d32f2f';
        }
        if (rowSub) rowSub.style.display = 'none';
        if (!currentStatus._dismissTimer) {
            currentStatus._dismissTimer = setTimeout(() => {
                currentStatus._dismissed = true;
                updateCurrentChatActiveTaskBanner();
            }, 3500);
        }
    }
}

function updateCurrentChatActiveTaskBanner() {
    const container = document.getElementById('chat-active-tasks-container');
    const legacyBanner = document.getElementById('chat-active-task-banner');
    if (!container && !legacyBanner) return;

    const currentChatId = window.currentChatId;
    if (!currentChatId || typeof archiveStatusCache === 'undefined') {
        if (container) container.querySelectorAll('.chat-active-task-row').forEach(function (r) { r.remove(); });
        if (legacyBanner) legacyBanner.style.display = 'none';
        return;
    }

    // Collect all matching active or recent tasks for the current chat
    const activeTasks = [];
    const strChatId = String(currentChatId);

    for (const [key, item] of Object.entries(archiveStatusCache)) {
        if (!item || typeof item !== 'object') continue;
        const strKey = String(key);
        const itemChatId = item.chat_id ? String(item.chat_id) : null;

        const isMatch = (strKey === strChatId) ||
            (itemChatId && itemChatId === strChatId) ||
            strKey.startsWith(`transcribe_${strChatId}`) ||
            strKey.includes(strChatId);

        if (!isMatch) continue;

        const st = item.status;
        if (st === 'running' || st === 'queued' || (st === 'done' && !item._dismissed) || (st === 'error' && !item._dismissed)) {
            activeTasks.push({ key: strKey, data: item });
        }
    }

    if (activeTasks.length === 0) {
        if (container) container.querySelectorAll('.chat-active-task-row').forEach(function (r) { r.remove(); });
        if (legacyBanner) legacyBanner.style.display = 'none';
        return;
    }

    if (container) {
        if (legacyBanner) legacyBanner.style.display = 'none';

        const existingRows = container.querySelectorAll('.chat-active-task-row');
        const activeKeys = new Set(activeTasks.map(t => t.key));

        existingRows.forEach(row => {
            const k = row.dataset.taskKey;
            if (!activeKeys.has(k)) row.remove();
        });

        activeTasks.forEach(({ key, data }) => {
            let row = container.querySelector(`.chat-active-task-row[data-task-key="${key}"]`);
            if (!row) {
                row = document.createElement('div');
                row.className = 'chat-active-task-row';
                row.dataset.taskKey = key;
                row.style.cssText = 'display: flex; flex-direction: column; background: var(--panel-bg); border-bottom: 1px solid var(--border-color); padding: 8px 16px; gap: 6px; z-index: 50; transition: all 0.2s ease;';
                row.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%;">
                        <div style="display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;">
                            <span class="chat-task-row-icon" style="font-size: 16px; flex-shrink: 0;">⚡</span>
                            <div style="flex: 1; min-width: 0;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <span class="chat-task-row-text" style="font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text-color);">Starting task...</span>
                                    <span class="chat-task-row-pct" style="font-size: 11px; font-weight: bold; color: var(--link-color); margin-left: 8px; flex-shrink: 0;">0%</span>
                                </div>
                                <div style="width: 100%; height: 5px; background: rgba(127,127,127,0.2); border-radius: 3px; overflow: hidden;">
                                    <div class="chat-task-row-fill" style="width: 0%; height: 100%; background: var(--link-color); transition: width 0.2s ease; border-radius: 3px;"></div>
                                </div>
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
                            <button class="chat-task-row-cancel-btn" style="background: none; border: 1px solid var(--border-color); color: var(--text-color-light); border-radius: 4px; padding: 2px 8px; font-size: 11px; cursor: pointer;">✕ Cancel</button>
                        </div>
                    </div>
                    <div class="chat-task-row-sub" style="display: none; align-items: center; gap: 10px; font-size: 11px; color: var(--text-color-light); width: 100%;">
                        <span class="chat-task-row-sub-text" style="flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: monospace;"></span>
                        <div style="width: 100px; height: 3px; background: rgba(127,127,127,0.2); border-radius: 2px; overflow: hidden; flex-shrink: 0;">
                            <div class="chat-task-row-sub-fill" style="width: 0%; height: 100%; background: #ba68c8; transition: width 0.15s ease; border-radius: 2px;"></div>
                        </div>
                        <span class="chat-task-row-sub-pct" style="font-size: 10px; font-weight: 600; color: #ba68c8; min-width: 34px; text-align: right; flex-shrink: 0;"></span>
                    </div>
                `;
                container.appendChild(row);

                const cancelBtn = row.querySelector('.chat-task-row-cancel-btn');
                cancelBtn.onclick = async () => {
                    cancelBtn.disabled = true;
                    cancelBtn.style.opacity = '0.5';
                    cancelBtn.style.cursor = 'wait';
                    const textEl = row.querySelector('.chat-task-row-text');
                    if (textEl) textEl.textContent = window.I18N ? window.I18N.t('task.stopping') : 'Stopping task...';

                    if (archiveStatusCache[key]) {
                        archiveStatusCache[key].status = 'error';
                        archiveStatusCache[key].status_text = 'Stopped by user';
                        archiveStatusCache[key].error = 'Stopped by user';
                    }
                    updateCurrentChatActiveTaskBanner();

                    try {
                        await fetch('/api/stop_archive', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                            },
                            body: JSON.stringify({ chat_id: data.chat_id || currentChatId, task_key: key })
                        });
                    } catch (e) {
                        console.error('Stop task error:', e);
                    }
                };
            }

            renderTaskRowContent(row, key, data);
        });
    }
}

window.updateCurrentChatActiveTaskBanner = updateCurrentChatActiveTaskBanner;





document.addEventListener('DOMContentLoaded', () => {

    const liveCb = document.getElementById('set-live-update');

    const liveLabel = document.getElementById('live-update-btn-label');



    function updateLiveStyle(enabled) {

        if (liveLabel) {

            if (enabled) liveLabel.classList.add('active-live');

            else liveLabel.classList.remove('active-live');

        }

    }



    if (liveCb) {

        fetch('/api/live_status')

            .then(res => res.json())

            .then(data => {

                if (typeof data.enabled === 'boolean') {

                    liveCb.checked = data.enabled;

                    updateLiveStyle(data.enabled);

                }

            })

            .catch(() => { });



        liveCb.addEventListener('change', () => {

            updateLiveStyle(liveCb.checked);

            fetch('/api/toggle_live', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                },

                body: JSON.stringify({ enabled: liveCb.checked })

            }).catch(e => console.error('Error toggling live mode:', e));



            if (liveCb.checked) {

                if (typeof startArchiveStream === 'function') startArchiveStream();

            } else {

                if (typeof debouncedUpdateArchiveUI === 'function') debouncedUpdateArchiveUI();

            }

        });

    }

});



const style = document.createElement('style');

style.textContent = `

    .dialog-item:hover .gear-container {

        max-width: 30px !important;

        opacity: 1 !important;

    }

    .dialog-item:hover .dialog-name {

        transform: translateX(2px);

    }

    .chat-settings-btn:hover {

        background: var(--link-color) !important;

        color: white;

    }

    .chat-tag {

        font-size: 10px; padding: 2px 6px; border-radius: 4px; margin-left: 6px;

        font-weight: bold; text-transform: uppercase;

    }

    .tag-channel { background: rgba(58,142,208,0.2); color: #3a8ed0; }

    .tag-group { background: rgba(39,174,96,0.2); color: #27ae60; }

    .tag-dm { background: rgba(155,89,182,0.2); color: #9b59b6; }

    .tag-bot { background: rgba(224,126,26,0.2); color: #e07e1a; }

`;

document.head.appendChild(style);



function updateStartArchiveButton() {

    const btn = document.getElementById('start-archive-btn');

    if (!btn || btn.dataset.isArchiving === "true") return;

    const count = window.selectedArchiveChats ? window.selectedArchiveChats.size : 0;

    btn.textContent = (typeof I18N !== 'undefined' && I18N.t)

        ? (count > 0 ? I18N.t('archive.start_n', count) : I18N.t('archive.start_0'))

        : `Start Archiving (${count})`;

    btn.disabled = count === 0;

}

window.updateStartArchiveButton = updateStartArchiveButton;

document.addEventListener('languageChanged', updateStartArchiveButton);



