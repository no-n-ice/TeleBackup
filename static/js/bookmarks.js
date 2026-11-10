// bookmarks.js — Bookmarked messages management
(function () {
    async function toggleBookmark(messageId, chatId, note = '') {
        if (!messageId || !chatId) return;
        try {
            const res = await fetch('/api/bookmark', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                },
                body: JSON.stringify({ message_id: messageId, chat_id: chatId, note: note })
            });
            const data = await res.json();
            if (data.ok && typeof showNotification === 'function') {
                showNotification('Bookmarked! ⭐', 'info');
            }
        } catch (e) {
            if (typeof showNotification === 'function') {
                showNotification('Error saving bookmark', 'error');
            }
        }
    }

    async function loadBookmarks(chatId) {
        const container = document.getElementById('bookmarks-list-container');
        if (!container) return;
        container.innerHTML = '<div style="font-size:12px;opacity:0.7;padding:8px;">Loading bookmarks...</div>';
        try {
            const url = chatId ? `/api/bookmarks?chat_id=${chatId}` : '/api/bookmarks';
            const res = await fetch(url);
            const bookmarks = await res.json();
            renderBookmarksList(container, bookmarks);
        } catch (e) {
            container.innerHTML = `<div style="font-size:12px;color:red;padding:8px;">${typeof escapeHTML === 'function' ? escapeHTML(e.message) : e.message}</div>`;
        }
    }

    function renderBookmarksList(container, bookmarks) {
        if (!bookmarks || bookmarks.length === 0) {
            container.innerHTML = '<div style="font-size:12px;opacity:0.6;padding:8px;">No bookmarks yet ⭐</div>';
            return;
        }

        container.innerHTML = '';
        bookmarks.forEach(b => {
            const item = document.createElement('div');
            item.className = 'bookmark-item';
            item.innerHTML = `
                <div class="bookmark-date">${b.date ? (typeof escapeHTML === 'function' ? escapeHTML(b.date.substring(0, 16)) : b.date.substring(0, 16)) : ''}</div>
                <div class="bookmark-text">${typeof escapeHTML === 'function' ? escapeHTML(b.text || '[Media]') : (b.text || '[Media]')}</div>
                <div class="bookmark-actions">
                    <button class="btn-jump-msg" style="padding:2px 6px;font-size:11px;">Jump</button>
                    <button class="btn-del-bm" style="padding:2px 6px;font-size:11px;background:none;border:none;cursor:pointer;">🗑️</button>
                </div>
            `;
            item.querySelector('.btn-jump-msg').addEventListener('click', () => {
                if (typeof window.scrollToMessage === 'function') {
                    window.scrollToMessage(b.message_id, b.chat_id);
                }
            });
            item.querySelector('.btn-del-bm').addEventListener('click', async () => {
                await fetch(`/api/bookmark?message_id=${b.message_id}&chat_id=${b.chat_id}`, {
                    method: 'DELETE',
                    headers: {
                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                    }
                });
                item.remove();
            });
            container.appendChild(item);
        });
    }

    window.toggleBookmark = toggleBookmark;
    window.loadBookmarks = loadBookmarks;
})();
