// context_menu.js — Custom right-click context menu for messages
(function () {
    let menu;
    let targetMessageEl = null;

    function initContextMenu() {
        menu = document.getElementById('msg-context-menu');
        if (!menu) return;

        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            chatContainer.addEventListener('contextmenu', (e) => {
                const msgEl = e.target.closest('.message, .service-message');
                if (!msgEl) {
                    hideContextMenu();
                    return;
                }
                e.preventDefault();
                targetMessageEl = msgEl;
                showContextMenu(e.clientX, e.clientY);
            });
        }

        document.addEventListener('click', (e) => {
            if (menu && !menu.contains(e.target)) {
                hideContextMenu();
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') hideContextMenu();
        });

        // Setup menu item actions
        const copyTextBtn = document.getElementById('ctx-copy-text');
        if (copyTextBtn) {
            copyTextBtn.addEventListener('click', () => {
                if (!targetMessageEl) return;
                const textEl = targetMessageEl.querySelector('.message-text') || targetMessageEl.querySelector('.text');
                const text = textEl ? textEl.innerText : '';
                if (text && navigator.clipboard) {
                    navigator.clipboard.writeText(text);
                    if (typeof showNotification === 'function') {
                        showNotification(I18N.t('msg.copied') || 'Copied to clipboard!', 'info');
                    }
                }
                hideContextMenu();
            });
        }

        const copyLinkBtn = document.getElementById('ctx-copy-link');
        if (copyLinkBtn) {
            copyLinkBtn.addEventListener('click', () => {
                if (!targetMessageEl) return;
                const msgId = targetMessageEl.dataset.messageId || targetMessageEl.getAttribute('id')?.replace('msg-', '');
                const chatId = window.AppState?.currentChatId || window.currentChatId || (typeof currentChatId !== 'undefined' ? currentChatId : null);
                if (msgId && chatId) {
                    const link = `${window.location.origin}${window.location.pathname}?chat_id=${chatId}&message_id=${msgId}`;
                    if (navigator.clipboard) {
                        navigator.clipboard.writeText(link);
                        if (typeof showNotification === 'function') {
                            showNotification(I18N.t('msg.link_copied') || 'Link copied to clipboard!', 'info');
                        }
                    }
                }
                hideContextMenu();
            });
        }

        const inspectMediaBtn = document.getElementById('ctx-inspect-media');
        if (inspectMediaBtn) {
            inspectMediaBtn.addEventListener('click', () => {
                if (!targetMessageEl) return;
                const msgId = targetMessageEl.dataset.messageId || targetMessageEl.dataset.msgId || targetMessageEl.dataset.id || targetMessageEl.getAttribute('id')?.replace('msg-', '');
                const chatId = window.AppState?.currentChatId || window.currentChatId || (typeof currentChatId !== 'undefined' ? currentChatId : null);
                hideContextMenu();
                if (msgId && chatId && typeof window.openMediaDiagnosticsModal === 'function') {
                    window.openMediaDiagnosticsModal(chatId, parseInt(msgId, 10));
                } else if (typeof showNotification === 'function') {
                    showNotification('Could not resolve message or chat ID for diagnostics.', 'warning');
                }
            });
        }

        const viewJsonBtn = document.getElementById('ctx-view-json');
        if (viewJsonBtn) {
            viewJsonBtn.addEventListener('click', () => {
                if (!targetMessageEl) return;
                const rawJson = targetMessageEl.dataset.rawJson;
                if (rawJson) {
                    try {
                        const parsed = JSON.parse(rawJson);
                        alert(JSON.stringify(parsed, null, 2));
                    } catch {
                        alert(rawJson);
                    }
                } else {
                    const text = targetMessageEl.innerText;
                    alert(text);
                }
                hideContextMenu();
            });
        }
    }

    function showContextMenu(x, y) {
        if (!menu) return;
        menu.style.display = 'block';

        const menuWidth = menu.offsetWidth || 180;
        const menuHeight = menu.offsetHeight || 140;
        const maxX = window.innerWidth - menuWidth - 10;
        const maxY = window.innerHeight - menuHeight - 10;

        menu.style.left = `${Math.min(x, maxX)}px`;
        menu.style.top = `${Math.min(y, maxY)}px`;
    }

    function hideContextMenu() {
        if (menu) menu.style.display = 'none';
        targetMessageEl = null;
    }

    document.addEventListener('DOMContentLoaded', initContextMenu);
})();
