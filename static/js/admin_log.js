/**
 * admin_log.js — Admin Log viewer modal with action filters and setting diffs.
 */
(function (window) {
    'use strict';

    const AdminLogModal = {
        _currentChatId: null,
        _modalEl: null,
        _events: [],
        _currentFilter: 'all',

        init() {
            this._modalEl = document.getElementById('admin-log-modal');
            if (!this._modalEl) {
                this._createModalDom();
            }
        },

        _createModalDom() {
            const overlay = document.createElement('div');
            overlay.id = 'admin-log-modal';
            overlay.className = 'admin-log-overlay';
            overlay.style.display = 'none';
            overlay.innerHTML = `
                <div class="admin-log-dialog">
                    <div class="admin-log-header">
                        <div class="admin-log-title-row">
                            <h3 class="admin-log-title" data-i18n="admin_log.title">Admin Event Log</h3>
                            <button class="action-btn admin-sync-btn" id="btn-sync-admin-log" onclick="AdminLogModal.syncNow()">
                                <span class="sync-icon">🔄</span> <span id="btn-sync-admin-log-text" data-i18n="admin_log.sync_btn">${typeof I18N !== 'undefined' ? I18N.t('admin_log.sync_btn') : 'Sync Admin Log'}</span>
                            </button>
                        </div>
                        <button class="admin-log-close" onclick="AdminLogModal.close()">✕</button>
                    </div>
                    <div class="admin-log-filters">
                        <button class="admin-filter-pill active" data-filter="all" data-i18n="admin_log.filter_all" onclick="AdminLogModal.setFilter('all')">${typeof I18N !== 'undefined' ? I18N.t('admin_log.filter_all') : 'All'}</button>
                        <button class="admin-filter-pill" data-filter="bans" data-i18n="admin_log.filter_bans" onclick="AdminLogModal.setFilter('bans')">${typeof I18N !== 'undefined' ? I18N.t('admin_log.filter_bans') : 'Bans/Kicks'}</button>
                        <button class="admin-filter-pill" data-filter="edits" data-i18n="admin_log.filter_edits" onclick="AdminLogModal.setFilter('edits')">${typeof I18N !== 'undefined' ? I18N.t('admin_log.filter_edits') : 'Edits/Deletes'}</button>
                        <button class="admin-filter-pill" data-filter="settings" data-i18n="admin_log.filter_settings" onclick="AdminLogModal.setFilter('settings')">${typeof I18N !== 'undefined' ? I18N.t('admin_log.filter_settings') : 'Settings'}</button>
                    </div>
                    <div class="admin-log-body" id="admin-log-content">
                        <div class="admin-log-loading">Loading admin log...</div>
                    </div>
                </div>
            `;
            if (typeof I18N !== 'undefined' && typeof I18N.applyAll === 'function') {
                I18N.applyAll(overlay);
            }
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) AdminLogModal.close();
            });
            document.body.appendChild(overlay);
            this._modalEl = overlay;
        },

        async open(chatId) {
            if (!this._modalEl) this.init();
            this._currentChatId = chatId;
            this._currentFilter = 'all';
            this._modalEl.style.display = 'flex';
            this._updateFilterPills();
            const content = document.getElementById('admin-log-content');
            content.innerHTML = '<div class="admin-log-loading">Loading admin log...</div>';

            try {
                const resp = await fetch(`/api/admin_log?chat_id=${encodeURIComponent(chatId)}&limit=100`);
                if (!resp.ok) throw new Error(`HTTP error ${resp.status}`);
                this._events = await resp.json();
                this.render();
            } catch (err) {
                content.innerHTML = `<div class="admin-log-error">⚠️ ${escapeHTML(err.message)}</div>`;
            }
        },

        close() {
            if (this._modalEl) {
                this._modalEl.style.display = 'none';
            }
            this._currentChatId = null;
        },

        setFilter(filter) {
            this._currentFilter = filter;
            this._updateFilterPills();
            this.render();
        },

        _updateFilterPills() {
            if (!this._modalEl) return;
            const pills = this._modalEl.querySelectorAll('.admin-filter-pill');
            pills.forEach(p => {
                if (p.getAttribute('data-filter') === this._currentFilter) {
                    p.classList.add('active');
                } else {
                    p.classList.remove('active');
                }
            });
        },

        _matchesFilter(ev, filter) {
            if (filter === 'all') return true;
            const actionType = (ev.action_type || '').toLowerCase();
            if (filter === 'bans') {
                return actionType.includes('ban') || actionType.includes('kick') || actionType.includes('restrict');
            }
            if (filter === 'edits') {
                return actionType.includes('edit') || actionType.includes('delete');
            }
            if (filter === 'settings') {
                return actionType.includes('title') || actionType.includes('photo') || actionType.includes('settings') || actionType.includes('pin');
            }
            return true;
        },

        render() {
            const content = document.getElementById('admin-log-content');
            if (!content) return;

            const filtered = this._events.filter(e => this._matchesFilter(e, this._currentFilter));
            if (filtered.length === 0) {
                content.innerHTML = `<div class="admin-log-empty">${I18N.t('admin_log.empty') || 'No admin log events found.'}</div>`;
                return;
            }

            let html = '<div class="admin-event-list">';
            filtered.forEach(ev => {
                const dateStr = ev.date ? new Date(ev.date).toLocaleString(I18N.locale()) : '';
                const adminName = escapeHTML(`${ev.admin_first_name || ''} ${ev.admin_last_name || ''}`.trim() || `Admin #${ev.user_id}`);
                const actionName = escapeHTML(ev.action_type || 'Action');

                let diffHtml = '';
                if (ev.old_value || ev.new_value) {
                    diffHtml = `
                        <div class="admin-diff-box">
                            ${ev.old_value ? `<div class="diff-old"><s>${escapeHTML(ev.old_value)}</s></div>` : ''}
                            ${ev.new_value ? `<div class="diff-new">➔ <strong>${escapeHTML(ev.new_value)}</strong></div>` : ''}
                        </div>
                    `;
                }

                html += `
                    <div class="admin-event-card">
                        <div class="admin-event-icon">🛡️</div>
                        <div class="admin-event-content">
                            <div class="admin-event-header">
                                <span class="admin-user-link" onclick="if(window.UserModal) UserModal.open(${ev.user_id})">${adminName}</span>
                                <span class="admin-action-type">${actionName}</span>
                                <span class="admin-event-date">${dateStr}</span>
                            </div>
                            ${diffHtml}
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            content.innerHTML = html;
        },

        async syncNow() {
            if (!this._currentChatId) return;
            const btn = document.getElementById('btn-sync-admin-log');
            const btnText = document.getElementById('btn-sync-admin-log-text');
            if (btn) {
                btn.disabled = true;
                if (btnText) {
                    btnText.textContent = (typeof I18N !== 'undefined' ? I18N.t('admin_log.syncing') : 'Syncing...');
                } else {
                    btn.textContent = '⏳ ' + (typeof I18N !== 'undefined' ? I18N.t('admin_log.syncing') : 'Syncing...');
                }
            }

            try {
                const resp = await fetch('/api/archive_admin_log', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                    },
                    body: JSON.stringify({
                        chat_id: this._currentChatId,
                        auth_csrf_token: (window.getCsrfToken ? window.getCsrfToken() : '')
                    })
                });
                const data = await resp.json();
                if (!resp.ok || !data.ok) {
                    alert(data.error || 'Failed to sync admin log');
                } else {
                    await this.open(this._currentChatId);
                }
            } catch (err) {
                alert(`Error: ${err.message}`);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    if (btnText) {
                        btnText.textContent = (typeof I18N !== 'undefined' ? I18N.t('admin_log.sync_btn') : 'Sync Admin Log');
                    } else {
                        btn.innerHTML = `<span class="sync-icon">🔄</span> <span id="btn-sync-admin-log-text" data-i18n="admin_log.sync_btn">${typeof I18N !== 'undefined' ? I18N.t('admin_log.sync_btn') : 'Sync Admin Log'}</span>`;
                    }
                }
            }
        }
    };

    window.AdminLogModal = AdminLogModal;
    document.addEventListener('DOMContentLoaded', () => {
        AdminLogModal.init();
    });
})(window);
