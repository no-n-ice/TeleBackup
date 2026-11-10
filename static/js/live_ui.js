// static/js/live_ui.js — Toast Notifications, Live Presence Dots, Live Message DOM Appender & Global Settings Saver

let showNotification = function (message, type = 'info', persistent = false) {

    let container = document.getElementById('toast-container');

    if (!container) {

        container = document.createElement('div');

        container.id = 'toast-container';

        document.body.appendChild(container);

    }



    const toast = document.createElement('div');

    toast.className = `toast-notification ${type}`;



    const spinner = document.createElement('span');

    spinner.className = 'toast-spinner';

    if (persistent && (type === 'info' || type === 'loading' || type === 'progress')) {

        spinner.style.display = 'inline-block';

    } else {

        spinner.style.display = 'none';

    }

    toast.appendChild(spinner);



    const content = document.createElement('div');

    content.className = 'toast-content';

    if (typeof message === 'string') {

        content.textContent = message;

    } else if (message instanceof HTMLElement) {

        content.appendChild(message);

    }

    toast.appendChild(content);



    const closeBtn = document.createElement('button');

    closeBtn.className = 'toast-close-btn';

    closeBtn.innerHTML = '&times;';

    closeBtn.title = 'Dismiss';

    closeBtn.setAttribute('aria-label', 'Dismiss notification');

    closeBtn.addEventListener('click', (e) => {

        e.stopPropagation();

        toast.close();

    });

    toast.appendChild(closeBtn);



    container.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add('show'));



    let autoCloseTimer = null;



    toast.close = function () {

        if (autoCloseTimer) {

            clearTimeout(autoCloseTimer);

            autoCloseTimer = null;

        }

        toast.classList.remove('show');

        setTimeout(() => {

            if (toast.parentNode) toast.remove();

        }, 300);

    };



    toast.update = function (newMessage, newType = 'info', autoCloseMs = (newType === 'success' || newType === 'error' ? 4500 : 0)) {

        if (autoCloseTimer) {

            clearTimeout(autoCloseTimer);

            autoCloseTimer = null;

        }

        toast.className = `toast-notification ${newType} show`;

        if (newType === 'success' || newType === 'error') {

            spinner.style.display = 'none';

        } else if (spinner.style.display === 'inline-block') {

            spinner.style.display = 'inline-block';

        }

        if (typeof newMessage === 'string') {

            content.textContent = newMessage;

        } else if (newMessage instanceof HTMLElement) {

            content.innerHTML = '';

            content.appendChild(newMessage);

        }

        if (autoCloseMs > 0) {

            autoCloseTimer = setTimeout(() => toast.close(), autoCloseMs);

        }

    };



    toast.success = function (msg, autoCloseMs = 4500) {

        toast.update(msg, 'success', autoCloseMs);

    };



    toast.error = function (msg, autoCloseMs = 6000) {

        toast.update(msg, 'error', autoCloseMs);

    };



    if (!persistent) {

        autoCloseTimer = setTimeout(() => toast.close(), 5000);

    }



    return toast;

};

window.showNotification = showNotification;



window.updateLiveUserStatus = function (statusData) {

    if (!statusData || !statusData.user_id) return;

    const userId = String(statusData.user_id);

    const st = statusData.status;

    let dotColor = '#999';

    let dotTitle = 'Offline';

    if (st === 'online') {

        dotColor = '#00c853';

        dotTitle = 'Online';

    } else if (st === 'recently') {

        dotColor = '#8bc34a';

        dotTitle = 'Recently online';

    } else if (st === 'last_week' || st === 'last_month') {

        dotColor = '#999';

        dotTitle = st === 'last_week' ? 'Last week' : 'Last month';

    }

    document.querySelectorAll(`.user-status-dot[data-user-id="${userId}"]`).forEach(dot => {

        dot.style.display = 'inline-block';

        dot.style.background = dotColor;

        dot.title = dotTitle;

    });

    if (window.UserModal && window.UserModal._currentUserId && String(window.UserModal._currentUserId) === userId) {

        const modalPresence = document.querySelector('.user-profile-status');

        if (modalPresence) {

            modalPresence.textContent = `● ${dotTitle}`;

            modalPresence.className = `user-profile-status ${st === 'online' ? 'status-online' : (st === 'recently' ? 'status-recently' : 'status-offline')}`;

        }

    }

};


    async function appendLiveMessage(msgData, attempt = 1) {

        const chatContainer = document.getElementById('chat-container');

        if (!chatContainer) return;

        const existing = chatContainer.querySelector(`.message[data-id="${msgData.message_id}"]`);

        if (existing) return;



        try {

            const res = await fetch(`/api/single_message?chat_id=${msgData.chat_id}&message_id=${msgData.message_id}`);

            if (!res.ok) {

                if (res.status === 404 && attempt <= 6) {

                    // Message might still be processing/saving in background archiver

                    setTimeout(() => appendLiveMessage(msgData, attempt + 1), Math.min(attempt * 400, 1500));

                }

                return;

            }

            const data = await res.json();

            if (data.users) Object.assign(allUsers, data.users);

            if (data.custom_emoji) {

                Object.assign(allCustomEmoji, data.custom_emoji);

                if (window.EmojiEngine) window.EmojiEngine.registerMap(data.custom_emoji);

            }

            if (data.message && typeof createMessageElement === 'function') {

                const existing2 = chatContainer.querySelector(`.message[data-id="${msgData.message_id}"]`);

                if (existing2) return;



                // Check distance from bottom BEFORE adding message

                const distFromBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight;

                const wasAtBottom = distFromBottom <= 150;



                const msgElement = createMessageElement(data.message);

                chatContainer.appendChild(msgElement);

                if (window.VirtualChat) {

                    window.VirtualChat.registerElements([msgElement], false);

                }

                if (typeof renderCustomEmojisInContainer === 'function') {

                    renderCustomEmojisInContainer(msgElement, allCustomEmoji);

                }

                if (typeof updateMessageGrouping === 'function') updateMessageGrouping();

                if (typeof adjustMessageFooterWidths === 'function') adjustMessageFooterWidths(chatContainer, [msgElement]);



                if (wasAtBottom) {

                    chatContainer.scrollTop = chatContainer.scrollHeight;

                    unreadCount = 0;

                    if (scrollFabBadge) scrollFabBadge.style.display = 'none';

                    if (scrollFab) scrollFab.classList.remove('visible');

                } else {

                    // Do not jump! Preserve scroll position so user can continue reading older messages

                    unreadCount++;

                    if (scrollFab) scrollFab.classList.add('visible');

                    if (scrollFabBadge) {

                        scrollFabBadge.textContent = unreadCount > 99 ? '99+' : unreadCount;

                        scrollFabBadge.style.display = 'flex';

                    }

                }

            }

        } catch (e) {

            console.error("[LiveUpdate] Error appending live message:", e);

        }

    }

    window.appendLiveMessage = appendLiveMessage;



    async function updateLiveMessage(msgData) {

        const chatContainer = document.getElementById('chat-container');

        if (!chatContainer) return;

        const msgEl = chatContainer.querySelector(`.message[data-id="${msgData.message_id}"]`);

        if (msgEl) {

            try {

                const res = await fetch(`/api/single_message?chat_id=${msgData.chat_id}&message_id=${msgData.message_id}`);

                if (!res.ok) return;

                const data = await res.json();

                if (data.users) Object.assign(allUsers, data.users);

                if (data.custom_emoji) {

                    Object.assign(allCustomEmoji, data.custom_emoji);

                    if (window.EmojiEngine) window.EmojiEngine.registerMap(data.custom_emoji);

                }

                if (data.message && typeof createMessageElement === 'function') {

                    const newMsgElement = createMessageElement(data.message);

                    msgEl.replaceWith(newMsgElement);

                    if (typeof renderCustomEmojisInContainer === 'function') {

                        renderCustomEmojisInContainer(newMsgElement, allCustomEmoji);

                    }

                    if (typeof updateMessageGrouping === 'function') updateMessageGrouping();

                }

            } catch (e) {

                console.error("[LiveUpdate] Error updating live message:", e);

            }

        }

    }

    window.updateLiveMessage = updateLiveMessage;



    async function appendOrUpdateLiveMessage(msgData) {

        const chatContainer = document.getElementById('chat-container');

        if (!chatContainer) return;

        const existing = chatContainer.querySelector(`.message[data-id="${msgData.message_id}"]`);

        if (existing) {

            await updateLiveMessage(msgData);

        } else {

            await appendLiveMessage(msgData);

        }

    }

    window.appendOrUpdateLiveMessage = appendOrUpdateLiveMessage;



    function deleteLiveMessage(msgData) {

        const chatContainer = document.getElementById('chat-container');

        if (!chatContainer) return;

        const msgEl = chatContainer.querySelector(`.message[data-id="${msgData.message_id}"]`);

        if (msgEl) {

            msgEl.classList.add('is-deleted-msg');

            let header = msgEl.querySelector('.deleted-msg-header');

            if (!header) {

                header = document.createElement('div');

                header.className = 'deleted-msg-header';

                header.title = 'Нажмите, чтобы развернуть/свернуть';

                header.onclick = (e) => { e.stopPropagation(); msgEl.classList.toggle('collapsed-deleted'); };

                header.innerHTML = `<span class="deleted-badge">🗑 <span data-i18n="messages.deleted">${I18N.t('messages.deleted')}</span> (<span class="show-text" data-i18n="messages.show">${I18N.t('messages.show')}</span><span class="hide-text" data-i18n="messages.hide">${I18N.t('messages.hide')}</span>)</span>`;



                let bodyWrap = msgEl.querySelector('.deleted-msg-body');

                if (!bodyWrap) {

                    bodyWrap = document.createElement('div');

                    bodyWrap.className = 'deleted-msg-body';

                    while (msgEl.firstChild) {

                        bodyWrap.appendChild(msgEl.firstChild);

                    }

                    msgEl.appendChild(header);

                    msgEl.appendChild(bodyWrap);

                } else {

                    msgEl.insertBefore(header, msgEl.firstChild);

                }

            }

        }

    }

    window.deleteLiveMessage = deleteLiveMessage;

// Update dynamic texts on language change

document.addEventListener('languageChanged', () => {

    if (typeof updateRenderedMessageCount === 'function') {

        updateRenderedMessageCount();

    }

    if (typeof updateMessageGrouping === 'function') {

        updateMessageGrouping();

    }

});





function readArchiveGlobalSettings() {
    return {
        photos: { enabled: document.getElementById('set-dl-photos')?.checked ?? true, max_mb: parseFloat(document.getElementById('set-sz-photos')?.value) || 0 },
        videos: { enabled: document.getElementById('set-dl-videos')?.checked ?? true, max_mb: parseFloat(document.getElementById('set-sz-videos')?.value) || 0 },
        voices: { enabled: document.getElementById('set-dl-voices')?.checked ?? true, max_mb: parseFloat(document.getElementById('set-sz-voices')?.value) || 0 },
        audio: { enabled: document.getElementById('set-dl-audio')?.checked ?? true, max_mb: parseFloat(document.getElementById('set-sz-audio')?.value) || 0 },
        files: { enabled: document.getElementById('set-dl-files')?.checked ?? true, max_mb: parseFloat(document.getElementById('set-sz-files')?.value) || 0 }
    };
}
window.readArchiveGlobalSettings = readArchiveGlobalSettings;

async function saveGlobalSettings() {
    const settings = readArchiveGlobalSettings();
    try {
        await fetch('/api/save_global_settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
            },
            body: JSON.stringify(settings)
        });
    } catch (e) { console.error("Failed to save global settings", e); }
}
window.saveGlobalSettings = saveGlobalSettings;

async function loadGlobalSettings() {
    try {
        const res = await fetch('/api/get_global_settings');
        if (!res.ok) return;
        const g = await res.json();
        if (!g || typeof g !== 'object') return;
        const map = [
            ['photos', 'set-dl-photos', 'set-sz-photos'],
            ['videos', 'set-dl-videos', 'set-sz-videos'],
            ['voices', 'set-dl-voices', 'set-sz-voices', 'voice'],
            ['audio', 'set-dl-audio', 'set-sz-audio', 'voice'],
            ['files', 'set-dl-files', 'set-sz-files', 'documents']
        ];
        map.forEach(([key, cbId, szId, fallbackKey]) => {
            const obj = g[key] || (fallbackKey ? g[fallbackKey] : null);
            if (!obj || typeof obj !== 'object') return;
            const cb = document.getElementById(cbId);
            const sz = document.getElementById(szId);
            if (cb && obj.enabled !== undefined) cb.checked = Boolean(obj.enabled);
            if (sz) sz.value = (obj.max_mb !== undefined && obj.max_mb !== null && Number(obj.max_mb) > 0) ? obj.max_mb : '';
        });
    } catch (e) { console.error("Failed to load global settings", e); }
}

// Bind saveGlobalSettings to Archive-tab global controls
document.addEventListener('DOMContentLoaded', () => {
    loadGlobalSettings();
    let globalSaveTimer = null;
    const debouncedGlobalSave = () => {
        if (globalSaveTimer) clearTimeout(globalSaveTimer);
        globalSaveTimer = setTimeout(saveGlobalSettings, 250);
    };
    ['set-dl-photos', 'set-dl-videos', 'set-dl-voices', 'set-dl-audio', 'set-dl-files'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', saveGlobalSettings);
    });
    ['set-sz-photos', 'set-sz-videos', 'set-sz-voices', 'set-sz-audio', 'set-sz-files'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', saveGlobalSettings);
            el.addEventListener('input', debouncedGlobalSave);
        }
    });



    if (typeof loadPureDbStats === 'function') loadPureDbStats();



    // --- Scroll FAB Wiring ---

    if (chatContainer && scrollFab) {

        chatContainer.addEventListener('scroll', () => {

            const distanceFromBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight;

            if (distanceFromBottom > 350) {

                scrollFab.classList.add('visible');

            } else {

                scrollFab.classList.remove('visible');

                unreadCount = 0;

                if (scrollFabBadge) scrollFabBadge.style.display = 'none';

            }

        }, { passive: true });



        scrollFab.addEventListener('click', () => {

            chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });

            unreadCount = 0;

            if (scrollFabBadge) scrollFabBadge.style.display = 'none';

            scrollFab.classList.remove('visible');

        });

    }



    // Window resize handler: re-adjust responsive to layout changes via RAF (no 150ms delay)

    window.addEventListener('resize', () => {

        if (typeof handleContainerResize === 'function') {

            handleContainerResize();

        } else if (chatContainer && typeof adjustMessageFooterWidths === 'function') {

            adjustMessageFooterWidths(chatContainer);

        }

    });



});



