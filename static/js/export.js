/**
 * static/js/export.js — WebUI Export Manager & Storage Maintenance Tools
 */

window.ExportManager = {
    currentChatId: null,
    currentChatName: 'Chat',

    init() {
        this.bindEvents();
    },

    bindEvents() {
        // Range mode change
        document.querySelectorAll('input[name="export-range-mode"]').forEach(radio => {
            radio.addEventListener('change', () => {
                const mode = radio.value;
                const daysBox = document.getElementById('export-latest-days-box');
                const customBox = document.getElementById('export-custom-date-box');
                if (daysBox) daysBox.style.display = (mode === 'latest_days') ? 'flex' : 'none';
                if (customBox) customBox.style.display = (mode === 'custom_range') ? 'flex' : 'none';
                this.updatePreviewCount();
            });
        });

        // Quick days pills
        document.querySelectorAll('.export-quick-day').forEach(pill => {
            pill.addEventListener('click', (e) => {
                e.preventDefault();
                const days = pill.dataset.days;
                const input = document.getElementById('export-latest-days');
                if (input) {
                    input.value = days;
                    this.updatePreviewCount();
                }
            });
        });

        // Date input changes
        ['export-latest-days', 'export-start-date', 'export-end-date'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', () => this.updatePreviewCount());
        });

        // Open modal button in chat header
        const headerExportBtn = document.getElementById('chat-header-export-btn');
        if (headerExportBtn) {
            headerExportBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.openModal(window.currentChatId, window.currentChatData?.chat_name);
            });
        }

        // Open modal button in left sidebar
        const sidebarExportBtn = document.getElementById('sidebar-export-btn');
        if (sidebarExportBtn) {
            sidebarExportBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.openModal(window.currentChatId, window.currentChatData?.chat_name);
            });
        }

        // Right sidebar tab switch
        const rsTabExport = document.getElementById('rs-tab-export');
        if (rsTabExport) {
            rsTabExport.addEventListener('click', () => {
                this.switchRightSidebarTab('export');
            });
        }

        // Run export button
        const runBtn = document.getElementById('export-run-btn');
        if (runBtn) {
            runBtn.addEventListener('click', () => this.runExport());
        }

        // Advanced Settings Tools Runner
        const runToolBtn = document.getElementById('tools-run-btn');
        if (runToolBtn) {
            runToolBtn.addEventListener('click', () => this.runMaintenanceTool());
        }
    },

    openModal(chatId, chatName) {
        this.currentChatId = chatId || window.currentChatId;
        this.currentChatName = chatName || window.currentChatData?.chat_name || (document.getElementById('chat-header-name')?.textContent?.trim()) || 'Chat';

        if (!this.currentChatId) {
            alert((typeof I18N !== 'undefined' && I18N.t('select.choose_chat')) || 'Please select a chat first.');
            return;
        }

        const modal = document.getElementById('chat-export-modal');
        if (!modal) return;

        const title = document.getElementById('export-modal-title');
        if (title) {
            title.textContent = `📤 ${this.currentChatName} — Export`;
        }

        modal.style.display = 'flex';
        this.updatePreviewCount();
        this.loadChatExportsHistory();
    },

    closeModal() {
        const modal = document.getElementById('chat-export-modal');
        if (modal) modal.style.display = 'none';
    },

    async updatePreviewCount() {
        if (!this.currentChatId) return;

        const mode = document.querySelector('input[name="export-range-mode"]:checked')?.value || 'all';
        const latestDays = document.getElementById('export-latest-days')?.value || '30';
        const startDate = document.getElementById('export-start-date')?.value || '';
        const endDate = document.getElementById('export-end-date')?.value || '';

        const badge = document.getElementById('export-preview-badge');
        if (badge) badge.textContent = '...';

        try {
            const url = `/api/export/preview_count?chat_id=${this.currentChatId}&range_mode=${mode}&latest_days=${latestDays}&start_date=${startDate}&end_date=${endDate}`;
            const res = await fetch(url);
            if (res.ok) {
                const data = await res.json();
                if (badge) {
                    badge.textContent = `${data.count} messages (${data.min_date ? data.min_date.split('T')[0] : '—'} to ${data.max_date ? data.max_date.split('T')[0] : '—'})`;
                }
            }
        } catch (e) {
            if (badge) badge.textContent = 'Error estimating';
        }
    },

    async runExport(customPayload = null) {
        const runBtn = document.getElementById('export-run-btn');
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.textContent = '⏳ Exporting...';
        }

        try {
            let payload = customPayload;
            if (!payload) {
                const mode = document.querySelector('input[name="export-range-mode"]:checked')?.value || 'all';
                payload = {
                    chat_id: this.currentChatId,
                    format: document.getElementById('export-format')?.value || 'md',
                    style: document.getElementById('export-style')?.value || 'verbose',
                    interval: document.getElementById('export-interval')?.value || 'single',
                    range_mode: mode,
                    latest_days: parseInt(document.getElementById('export-latest-days')?.value || '30', 10),
                    start_date: document.getElementById('export-start-date')?.value || null,
                    end_date: document.getElementById('export-end-date')?.value || null,
                    show_reactions: document.getElementById('export-opt-reactions')?.checked ?? true,
                    show_replies: document.getElementById('export-opt-replies')?.checked ?? true,
                    show_forwards: document.getElementById('export-opt-forwards')?.checked ?? true,
                    show_media: document.getElementById('export-opt-media')?.checked ?? true,
                    show_polls: document.getElementById('export-opt-polls')?.checked ?? true,
                    show_service: document.getElementById('export-opt-service')?.checked ?? true,
                    delivery: document.querySelector('input[name="export-delivery"]:checked')?.value || 'download',
                    lang: (typeof I18N !== 'undefined') ? I18N.lang() : 'en'
                };
            }

            const csrfToken = (window.getCsrfToken ? window.getCsrfToken() : '');
            if (csrfToken) payload.auth_csrf_token = csrfToken;

            if (payload.delivery === 'download') {
                const res = await fetch('/api/export/run', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': csrfToken
                    },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) {
                    const err = await res.json();
                    alert(`Export failed: ${err.error || res.statusText}`);
                    return;
                }

                // Extract filename from header
                const disposition = res.headers.get('Content-Disposition') || '';
                let filename = 'chat_export.md';
                const match = disposition.match(/filename="?([^";]+)"?/);
                if (match && match[1]) filename = match[1];

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
            } else {
                // Save to disk
                const res = await fetch('/api/export/run', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': csrfToken
                    },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    alert(`✅ Export saved to server disk!\nFiles (${data.exported_count} messages):\n${data.files.join('\n')}`);
                    this.loadChatExportsHistory();
                } else {
                    alert(`Export failed: ${data.error || 'Unknown error'}`);
                }
            }
        } catch (e) {
            alert(`Export failed: ${e.message}`);
        } finally {
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.textContent = '🚀 Start Export';
            }
        }
    },

    async loadChatExportsHistory() {
        const listEl = document.getElementById('export-history-list');
        const rsListEl = document.getElementById('rs-export-history-list');
        if (!listEl && !rsListEl) return;

        try {
            const res = await fetch('/api/export/history');
            if (!res.ok) return;
            const data = await res.json();
            const files = data.files || [];

            const render = (container) => {
                if (!container) return;
                if (!files.length) {
                    container.innerHTML = '<div style="opacity:0.6; padding:8px; font-size:12px;">No exported snapshots found in db_exports/.</div>';
                    return;
                }
                container.innerHTML = files.map(f => `
                    <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-bottom:1px solid var(--border-color); font-size:12px;">
                        <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:65%;">
                            <div style="font-weight:600;">${f.filename}</div>
                            <div style="font-size:10px; opacity:0.7;">${f.size_str} • ${f.mtime}</div>
                        </div>
                        <a href="${f.download_url}" download class="action-btn" style="padding:2px 8px; font-size:11px; text-decoration:none;">⬇️ Download</a>
                    </div>
                `).join('');
            };

            render(listEl);
            render(rsListEl);
        } catch (e) {
            console.error('Error loading export history:', e);
        }
    },

    switchRightSidebarTab(tabName) {
        const statsTab = document.getElementById('rs-tab-stats');
        const mediaTab = document.getElementById('rs-tab-media');
        const exportTab = document.getElementById('rs-tab-export');

        const statsPanel = document.getElementById('rs-stats');
        const mediaPanel = document.getElementById('rs-media');
        const exportPanel = document.getElementById('rs-export');

        if (statsTab) statsTab.classList.toggle('active', tabName === 'stats');
        if (mediaTab) mediaTab.classList.toggle('active', tabName === 'media');
        if (exportTab) exportTab.classList.toggle('active', tabName === 'export');

        if (statsPanel) statsPanel.style.display = (tabName === 'stats') ? 'block' : 'none';
        if (mediaPanel) mediaPanel.style.display = (tabName === 'media') ? 'block' : 'none';
        if (exportPanel) {
            exportPanel.style.display = (tabName === 'export') ? 'block' : 'none';
            if (tabName === 'export') {
                this.loadChatExportsHistory();
            }
        }
    },

    // --- Maintenance Tools Execution ---
    async runMaintenanceTool() {
        const select = document.getElementById('tools-select');
        const executeCheck = document.getElementById('tools-execute-check');
        const logBox = document.getElementById('tools-log-box');
        const runBtn = document.getElementById('tools-run-btn');

        if (!select) return;
        const tool = select.value;
        const execute = executeCheck ? executeCheck.checked : false;

        let args = {};
        if (tool === 'import_tg_export') {
            const path = prompt("Enter path to Telegram Desktop 'result.json':");
            if (!path) return;
            args.json_path = path;
            args.chat_id = window.currentChatId || null;
        }

        if (logBox) {
            logBox.style.display = 'block';
            logBox.textContent = `[${new Date().toLocaleTimeString()}] Submitting ${tool} (execute=${execute})...\n`;
        }

        if (runBtn) runBtn.disabled = true;

        try {
            const csrfToken = (window.getCsrfToken ? window.getCsrfToken() : '');
            const res = await fetch('/api/tools/run', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': csrfToken
                },
                body: JSON.stringify({ tool, execute, args, auth_csrf_token: csrfToken })
            });
            const data = await res.json();
            if (!res.ok) {
                alert(`Error: ${data.error}`);
                if (runBtn) runBtn.disabled = false;
                return;
            }

            const taskId = data.task_id;
            const poller = setInterval(async () => {
                try {
                    const stRes = await fetch(`/api/tools/status?task_id=${taskId}`);
                    if (stRes.ok) {
                        const stData = await stRes.json();
                        if (logBox && stData.logs) {
                            logBox.textContent = stData.logs.join('\n');
                            logBox.scrollTop = logBox.scrollHeight;
                        }
                        if (stData.status === 'completed' || stData.status === 'error') {
                            clearInterval(poller);
                            if (runBtn) runBtn.disabled = false;
                        }
                    }
                } catch (e) {
                    clearInterval(poller);
                    if (runBtn) runBtn.disabled = false;
                }
            }, 1000);

        } catch (e) {
            alert(`Failed: ${e.message}`);
            if (runBtn) runBtn.disabled = false;
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    ExportManager.init();
});

