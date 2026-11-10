/**
 * static/js/participants.js — Subscriber & Participant Churn Manager
 */

window.ParticipantsManager = {
    currentChatId: null,
    currentTab: 'all',
    searchQuery: '',

    notify(message, type = 'info') {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
        } else {
            console.log(`[Participants] [${type}] ${message}`);
        }
    },

    getCsrfToken() {
        if (typeof window.getCsrfToken === 'function') {
            return window.getCsrfToken();
        }
        return window.CSRF_TOKEN || document.querySelector('meta[name="auth-csrf"]')?.getAttribute('content') || '';
    },

    init() {
        this.bindEvents();
    },

    bindEvents() {
        // Chat header badge click
        const headerBadge = document.getElementById('chat-header-participants-badge');
        if (headerBadge) {
            headerBadge.addEventListener('click', () => {
                this.openModal(window.currentChatId);
            });
        }

        // Stats tab view subscribers button
        const statsViewBtn = document.getElementById('stats-view-subscribers-btn');
        if (statsViewBtn) {
            statsViewBtn.addEventListener('click', () => {
                this.openModal(window.currentChatId);
            });
        }

        // Modal filter tabs
        document.querySelectorAll('.part-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.part-tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentTab = btn.dataset.status;
                if (this.currentTab === 'events') {
                    this.loadEvents();
                } else {
                    this.loadList();
                }
            });
        });

        // Search input
        const searchInput = document.getElementById('part-search-input');
        if (searchInput) {
            let timeout = null;
            searchInput.addEventListener('input', (e) => {
                clearTimeout(timeout);
                timeout = setTimeout(() => {
                    this.searchQuery = e.target.value.trim();
                    if (this.currentTab === 'events') {
                        this.loadEvents();
                    } else {
                        this.loadList();
                    }
                }, 300);
            });
        }

        // Tracker toggle checkbox in modal
        const trackerToggle = document.getElementById('part-tracker-toggle');
        if (trackerToggle) {
            trackerToggle.addEventListener('change', async (e) => {
                if (!this.currentChatId) return;
                const isChecked = e.target.checked;
                try {
                    const res = await fetch('/api/participants/toggle', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-TeleBackup-Auth-Token': this.getCsrfToken()
                        },
                        body: JSON.stringify({ chat_id: this.currentChatId, enabled: isChecked })
                    });
                    const data = await res.json().catch(() => ({}));
                    if (res.ok && data.status === 'ok') {
                        this.notify(isChecked ? 'Participant tracking enabled' : 'Participant tracking disabled', 'success');
                        this.loadSummary();
                    } else {
                        e.target.checked = !isChecked;
                        const errMsg = data.message || data.error || (res.status === 403 ? 'Action Forbidden: Session invalid or CSRF missing' : 'Failed to update tracking setting');
                        this.notify(errMsg, 'error');
                    }
                } catch (err) {
                    e.target.checked = !isChecked;
                    console.error('Failed to toggle participant tracking:', err);
                    this.notify(`Failed to toggle participant tracking: ${err.message}`, 'error');
                }
            });
        }

        // Sync button in modal
        const syncBtn = document.getElementById('part-sync-btn');
        if (syncBtn) {
            syncBtn.addEventListener('click', async () => {
                if (!this.currentChatId) return;
                syncBtn.disabled = true;
                syncBtn.textContent = '⏳ Syncing...';
                try {
                    const res = await fetch('/api/participants/sync', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-TeleBackup-Auth-Token': this.getCsrfToken()
                        },
                        body: JSON.stringify({ chat_id: this.currentChatId })
                    });
                    const data = await res.json().catch(() => ({}));
                    if (res.ok && data.status === 'ok') {
                        if (data.warning) {
                            this.notify(data.warning, 'info');
                        } else if (data.info) {
                            this.notify(data.info, 'info');
                        } else if (data.message) {
                            this.notify(data.message, 'success');
                        } else {
                            this.notify('Participants synced successfully', 'success');
                        }
                        this.loadSummary();
                        this.loadList();
                    } else {
                        const errMsg = data.message || data.error || (res.status === 403 ? 'Action Forbidden: Session invalid or chat inaccessible' : 'Sync failed');
                        this.notify(errMsg, 'error');
                    }
                } catch (e) {
                    this.notify(`Sync failed: ${e.message}`, 'error');
                } finally {
                    syncBtn.disabled = false;
                    syncBtn.textContent = '🔄 Sync Now';
                }
            });
        }
    },

    openModal(chatId) {
        this.currentChatId = chatId || window.currentChatId;
        if (!this.currentChatId) return;

        const modal = document.getElementById('participants-modal');
        if (!modal) return;

        const chatName = window.currentChatData?.chat_name || document.getElementById('chat-header-name')?.textContent?.trim() || 'Chat';
        const titleEl = document.getElementById('part-modal-title');
        if (titleEl) {
            titleEl.textContent = `👥 ${chatName} — Subscribers & Churn Log`;
        }

        modal.style.display = 'flex';
        this.loadSummary();
        this.loadList();
    },

    closeModal() {
        const modal = document.getElementById('participants-modal');
        if (modal) modal.style.display = 'none';
    },

    async updateHeaderBadge(chatId, count = null) {
        const badge = document.getElementById('chat-header-participants-badge');
        const churnWidget = document.getElementById('subscribers-churn-widget');

        if (!chatId) {
            if (badge) badge.style.display = 'none';
            if (churnWidget) churnWidget.style.display = 'none';
            return;
        }

        const chat = (window.chatListData || []).find(c => String(c.chat_id) === String(chatId));
        const chatType = chat ? chat.chat_type : (window.currentChatData ? window.currentChatData.chat_type : null);
        const isDmOrBot = (chatType === 'user' || chatType === 'bot' || Number(chatId) > 0);

        if (isDmOrBot) {
            if (badge) badge.style.display = 'none';
            if (churnWidget) churnWidget.style.display = 'none';
            return;
        }

        if (churnWidget) churnWidget.style.display = 'block';

        if (count !== null && count > 0) {
            if (badge) {
                badge.style.display = 'inline-flex';
                badge.textContent = `👥 ${Number(count).toLocaleString()}`;
            }
            const statsCountEl = document.getElementById('stats-subscribers-count');
            if (statsCountEl) statsCountEl.textContent = Number(count).toLocaleString();
            return;
        }

        try {
            const res = await fetch(`/api/participants/summary?chat_id=${chatId}`);
            if (res.ok) {
                const data = await res.json();
                const displayCount = data.participants_count || data.active_count || 0;
                if (data && displayCount > 0 && !isDmOrBot) {
                    if (badge) {
                        badge.style.display = 'inline-flex';
                        badge.textContent = `👥 ${Number(displayCount).toLocaleString()}`;
                        badge.title = `${displayCount} total members/subscribers. Click to inspect churn log.`;
                    }
                } else {
                    if (badge) badge.style.display = 'none';
                }

                // Update Stats tab widget if present
                const statsCountEl = document.getElementById('stats-subscribers-count');
                if (statsCountEl) {
                    statsCountEl.textContent = Number(displayCount).toLocaleString();
                }
                const statsActiveEl = document.getElementById('stats-subscribers-active');
                if (statsActiveEl) {
                    statsActiveEl.textContent = Number(data.active_count || 0).toLocaleString();
                }
                const statsLeftEl = document.getElementById('stats-subscribers-left');
                if (statsLeftEl) {
                    statsLeftEl.textContent = Number(data.left_count || 0).toLocaleString();
                }
            }
        } catch (e) {
            if (badge) badge.style.display = 'none';
        }
    },

    async loadSummary() {
        if (!this.currentChatId) return;
        try {
            const res = await fetch(`/api/participants/summary?chat_id=${this.currentChatId}`);
            if (!res.ok) return;
            const data = await res.json();

            const displayCount = data.participants_count || data.active_count || 0;

            const countEl = document.getElementById('part-total-count');
            if (countEl) countEl.textContent = Number(displayCount).toLocaleString();

            const activeEl = document.getElementById('part-active-count');
            if (activeEl) activeEl.textContent = Number(data.active_count || 0).toLocaleString();

            const leftEl = document.getElementById('part-left-count');
            if (leftEl) leftEl.textContent = Number(data.left_count || 0).toLocaleString();

            const kickedEl = document.getElementById('part-kicked-count');
            if (kickedEl) kickedEl.textContent = Number(data.kicked_count || 0).toLocaleString();

            const toggle = document.getElementById('part-tracker-toggle');
            if (toggle) toggle.checked = Boolean(data.track_participants);

            const syncedEl = document.getElementById('part-last-synced');
            if (syncedEl) {
                syncedEl.textContent = data.participants_synced_at ? `Synced: ${data.participants_synced_at.replace('T', ' ').split('.')[0]} UTC` : 'Never synced';
            }

            // Also update header badge if active chat
            if (this.currentChatId === window.currentChatId) {
                this.updateHeaderBadge(this.currentChatId, displayCount);
            }
        } catch (e) {
            console.error('Error loading participant summary:', e);
        }
    },

    async loadList() {
        const bodyEl = document.getElementById('part-table-body');
        if (!bodyEl || !this.currentChatId) return;

        bodyEl.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:16px; opacity:0.6;">⏳ Loading subscribers...</td></tr>';

        try {
            let url = `/api/participants/list?chat_id=${this.currentChatId}&status=${this.currentTab}&limit=200`;
            if (this.searchQuery) url += `&search=${encodeURIComponent(this.searchQuery)}`;

            const res = await fetch(url);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            const list = data.participants || [];

            if (!list.length) {
                bodyEl.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:20px; opacity:0.6;">No subscribers recorded matching this filter. Click "🔄 Sync Now" to reconcile participants from chat history.</td></tr>';
                return;
            }

            bodyEl.innerHTML = list.map(p => {
                const esc = window.escapeHTML || (s => String(s || '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[m])));
                const rawName = [p.first_name, p.last_name].filter(Boolean).join(' ') || (p.username ? `@${p.username}` : `User ${p.user_id}`);
                const name = esc(rawName);
                const initial = esc((Array.from(rawName)[0] || 'U').toUpperCase());
                const usernameStr = p.username ? `<span style="font-size:11px; opacity:0.75;">@${esc(p.username)}</span>` : '';
                const safeAvatarUrl = p.avatar_path ? esc(`/data/${String(p.avatar_path).replace(/\\/g, '/')}`) : '';
                const avatarImg = safeAvatarUrl ? `<img src="${safeAvatarUrl}" style="width:28px; height:28px; border-radius:50%; object-fit:cover;">` : `<div style="width:28px; height:28px; border-radius:50%; background:var(--link-color); color:white; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:600;">${initial}</div>`;

                let statusBadge = '<span style="background:rgba(16,185,129,0.15); color:#10b981; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:600;">🟢 Active</span>';
                if (p.status === 'left') {
                    statusBadge = '<span style="background:rgba(239,68,68,0.15); color:#ef4444; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:600;">🚶 Left (Own Will)</span>';
                } else if (p.status === 'kicked') {
                    const kicker = esc(p.kicker_username ? `@${p.kicker_username}` : (p.kicker_first_name || 'Admin'));
                    statusBadge = `<span style="background:rgba(245,158,11,0.15); color:#f59e0b; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:600;" title="Kicked by ${kicker}">🔨 Kicked (${kicker})</span>`;
                } else if (p.status === 'banned') {
                    const kicker = esc(p.kicker_username ? `@${p.kicker_username}` : (p.kicker_first_name || 'Admin'));
                    statusBadge = `<span style="background:rgba(220,38,38,0.2); color:#dc2626; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:600;" title="Banned by ${kicker}">⛔ Banned (${kicker})</span>`;
                }

                const enterDate = esc(p.entered_at ? String(p.entered_at).split('T')[0] : '—');
                const exitDate = esc(p.left_at ? String(p.left_at).split('T')[0] : '—');
                const exitReason = esc(p.exit_reason || '—');

                return `
                    <tr style="border-bottom:1px solid var(--border-color); font-size:12px;">
                        <td style="padding:6px 8px; display:flex; align-items:center; gap:8px;">
                            ${avatarImg}
                            <div>
                                <div style="font-weight:600;">${name}</div>
                                ${usernameStr}
                            </div>
                        </td>
                        <td style="padding:6px 8px;">${statusBadge}</td>
                        <td style="padding:6px 8px; font-size:11px; opacity:0.8;">${enterDate}</td>
                        <td style="padding:6px 8px; font-size:11px; opacity:0.8;">${exitDate}</td>
                        <td style="padding:6px 8px; font-size:11px; opacity:0.75;">${exitReason}</td>
                    </tr>
                `;
            }).join('');

        } catch (e) {
            const esc = window.escapeHTML || String;
            bodyEl.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:16px; color:#ef4444;">Error loading subscriber list: ${esc(e.message)}</td></tr>`;
        }
    },

    async loadEvents() {
        const bodyEl = document.getElementById('part-table-body');
        if (!bodyEl || !this.currentChatId) return;

        bodyEl.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:16px; opacity:0.6;">⏳ Loading event audit feed...</td></tr>';

        try {
            const res = await fetch(`/api/participants/events?chat_id=${this.currentChatId}&limit=200`);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            const events = data.events || [];

            if (!events.length) {
                bodyEl.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:20px; opacity:0.6;">No join/leave events recorded yet.</td></tr>';
                return;
            }

            bodyEl.innerHTML = events.map(ev => {
                const esc = window.escapeHTML || (s => String(s || '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[m])));
                const targetName = esc([ev.first_name, ev.last_name].filter(Boolean).join(' ') || (ev.username ? `@${ev.username}` : `User ${ev.user_id}`));
                const actorName = esc([ev.actor_first_name, ev.actor_last_name].filter(Boolean).join(' ') || (ev.actor_username ? `@${ev.actor_username}` : (ev.actor_user_id ? `User ${ev.actor_user_id}` : 'System')));

                let icon = 'ℹ️';
                let actionText = esc(ev.event_type || '');
                if (ev.event_type === 'join') { icon = '🟢'; actionText = 'Joined chat'; }
                else if (ev.event_type === 'invite') { icon = '📩'; actionText = `Invited by ${actorName}`; }
                else if (ev.event_type === 'leave') { icon = '🚶'; actionText = 'Left chat (Own will)'; }
                else if (ev.event_type === 'kick') { icon = '🔨'; actionText = `Kicked by ${actorName}`; }
                else if (ev.event_type === 'ban') { icon = '⛔'; actionText = `Banned by ${actorName}`; }

                const dateStr = esc(ev.date ? String(ev.date).replace('T', ' ').split('.')[0] : '—');
                const sourceStr = esc(ev.source || '');

                return `
                    <tr style="border-bottom:1px solid var(--border-color); font-size:12px;">
                        <td style="padding:6px 8px; font-weight:600;">${targetName}</td>
                        <td style="padding:6px 8px;">${icon} ${actionText}</td>
                        <td style="padding:6px 8px; font-size:11px; opacity:0.8;">${dateStr}</td>
                        <td style="padding:6px 8px; font-size:11px; opacity:0.8;">${sourceStr}</td>
                        <td style="padding:6px 8px; font-size:11px; opacity:0.75;">${actorName}</td>
                    </tr>
                `;
            }).join('');
        } catch (e) {
            const esc = window.escapeHTML || String;
            bodyEl.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:16px; color:#ef4444;">Error loading events: ${esc(e.message)}</td></tr>`;
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    ParticipantsManager.init();
});
