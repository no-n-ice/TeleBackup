// ══════════════════════════════════════════════════════════════
// ── THEME ENGINE ─────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════
const ThemeEngine = {
    STORAGE_KEY: 'tg_viewer_theme',
    PALETTE_KEY: 'tg_viewer_palette',
    CUSTOM_COLOR_KEY: 'tg_viewer_custom_color',

    init() {
        const saved = localStorage.getItem(this.STORAGE_KEY) || 'dark';
        this.apply(saved, false);
    },

    _hexToRgb(hex) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? `${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}` : null;
    },

    apply(theme, animate = true) {
        if (animate) {
            document.body.classList.add('theme-transition');
            setTimeout(() => document.body.classList.remove('theme-transition'), 300);
        }

        document.documentElement.removeAttribute('data-theme-preload');
        document.body.setAttribute('data-theme', theme);
        localStorage.setItem(this.STORAGE_KEY, theme);

        const btn = document.getElementById('theme-toggle-btn');
        if (btn) btn.textContent = theme === 'dark' ? '\u2600\ufe0f' : '\uD83C\uDF19';

        this.applyPalette();
    },

    toggle() {
        const current = document.body.getAttribute('data-theme') || 'dark';
        this.apply(current === 'dark' ? 'light' : 'dark', true);
    },

    applyPalette() {
        const palette = localStorage.getItem(this.PALETTE_KEY) || 'default';
        document.body.setAttribute('data-palette', palette);

        const paletteSelect = document.getElementById('theme-palette-select');
        if (paletteSelect) paletteSelect.value = palette;

        const customColorPicker = document.getElementById('custom-bubble-color');

        if (palette === 'custom') {
            const customHex = localStorage.getItem(this.CUSTOM_COLOR_KEY) || '#a1a1aa';
            const rgb = this._hexToRgb(customHex);
            if (customColorPicker) customColorPicker.value = customHex;

            if (rgb) {
                // Adjust transparency based on theme
                const isDark = document.body.getAttribute('data-theme') === 'dark';
                const opacity = isDark ? '0.75' : '0.88';
                document.body.style.setProperty('--msg-bg-out', `rgba(${rgb}, ${opacity})`);
                document.body.style.setProperty('--msg-out-rgb', rgb);
                document.body.style.setProperty('--sender-name-color', customHex);
            }
            if (customColorPicker) customColorPicker.parentElement.style.display = 'block';
        } else {
            document.body.style.removeProperty('--msg-bg-out');
            document.body.style.removeProperty('--msg-out-rgb');
            document.body.style.removeProperty('--sender-name-color');
            if (customColorPicker) customColorPicker.parentElement.style.display = 'none';
        }
    },

    setPalette(palette) {
        document.body.classList.add('theme-transition');
        setTimeout(() => document.body.classList.remove('theme-transition'), 300);
        localStorage.setItem(this.PALETTE_KEY, palette);
        this.applyPalette();
    },

    setCustomColor(hex) {
        localStorage.setItem(this.CUSTOM_COLOR_KEY, hex);
        if (localStorage.getItem(this.PALETTE_KEY) === 'custom') {
            this.applyPalette();
        }
    }
};

// ══════════════════════════════════════════════════════════════
// ── SIDEBAR RESIZER ──────────────────────────────────────────
// ══════════════════════════════════════════════════════════════
const SidebarResizer = {
    MIN_W: 80,
    MAX_RATIO: 0.40,
    init() {
        // Restore saved dimensions
        const savedL = localStorage.getItem('tg_sidebar_left_w');
        const savedR = localStorage.getItem('tg_sidebar_right_w');
        if (savedL) document.documentElement.style.setProperty('--sidebar-width', savedL + 'px');
        if (savedR) {
            const finalR = Math.max(260, parseInt(savedR, 10));
            document.documentElement.style.setProperty('--right-panel-width', finalR + 'px');
        }

        this.bindResizer('resizer-left', '--sidebar-width', 'tg_sidebar_left_w', false);
        this.bindResizer('resizer-right', '--right-panel-width', 'tg_sidebar_right_w', true);
    },
    bindResizer(id, cssVar, storageKey, invert) {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('mousedown', (e) => {
            e.preventDefault();
            document.body.classList.add('resizing');
            el.classList.add('resizer-active');
            const startX = e.clientX;
            const startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue(cssVar)) || 320;
            const onMove = (e) => {
                const delta = invert ? (startX - e.clientX) : (e.clientX - startX);
                const maxW = window.innerWidth * this.MAX_RATIO;

                let currentMinW = this.MIN_W;
                if (id === 'resizer-left') {
                    const archView = document.getElementById('archive-settings-view');
                    if (archView && archView.style.display !== 'none') {
                        currentMinW = 200;
                    }
                } else if (id === 'resizer-right') {
                    currentMinW = 260;
                }

                let newW = startW + delta;

                if (id === 'resizer-left' && newW < 270 && currentMinW === this.MIN_W) {
                    newW = 80;
                }

                newW = Math.max(currentMinW, Math.min(newW, maxW));

                document.documentElement.style.setProperty(cssVar, newW + 'px');
            };
            const onUp = () => {
                document.body.classList.remove('resizing');
                el.classList.remove('resizer-active');
                const finalW = parseInt(getComputedStyle(document.documentElement).getPropertyValue(cssVar));
                localStorage.setItem(storageKey, finalW);
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }
};

// ══════════════════════════════════════════════════════════════
// ── CHAT BACKGROUND ENGINE ────────────────────────────────────
// ══════════════════════════════════════════════════════════════
const BgEngine = {
    async init() {
        const savedBlur = localStorage.getItem('tg_bg_blur') || "0";
        const savedDim = localStorage.getItem('tg_bg_dim') || "1.0";

        document.documentElement.style.setProperty('--bg-blur', savedBlur + 'px');
        document.documentElement.style.setProperty('--bg-dim', savedDim);

        const blurSlider = document.getElementById('bg-blur-slider');
        const dimSlider = document.getElementById('bg-dim-slider');
        if (blurSlider) blurSlider.value = savedBlur;
        if (dimSlider) dimSlider.value = savedDim;

        try {
            const res = await fetch('/api/get_bg');
            const data = await res.json();
            if (data.path) this.apply(data.path);
        } catch (e) { /* endpoint pending */ }
    },
    apply(url) {
        const layer = document.getElementById('global-bg-layer');
        if (!layer) return;
        layer.style.backgroundImage = `url('${url}')`;
        const col = document.getElementById('center-column');
        if (col) col.style.backgroundColor = 'transparent';
    },
    clear() {
        const layer = document.getElementById('global-bg-layer');
        if (layer) { layer.style.backgroundImage = ''; }
        const col = document.getElementById('center-column');
        if (col) col.style.backgroundColor = '';
    },
    async upload(file) {
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await fetch('/api/upload_bg', {
                method: 'POST',
                headers: { 'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '') },
                body: fd
            });
            const data = await res.json();
            if (data.path) this.apply(data.path + '?t=' + Date.now());
        } catch (e) { console.error('BgEngine upload error:', e); }
    },
    async remove() {
        try {
            await fetch('/api/delete_bg', {
                method: 'POST',
                headers: { 'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '') }
            });
        } catch (e) { }
        this.clear();
    }
};

// ══════════════════════════════════════════════════════════════
// ── SETTINGS MODAL ───────────────────────────────────────────
// ══════════════════════════════════════════════════════════════
const SettingsModal = {
    _wired: false,
    _activeTab: 'general',
    _mergePollTimer: null,
    open(tab) {
        const m = document.getElementById('settings-modal');
        if (m) { m.style.display = 'flex'; }
        if (!this._wired) this._wire();
        this.switchTab(tab || this._activeTab || 'general');
        if (window.loadWhisperSettings) window.loadWhisperSettings();
        this.checkMergeStatus();
    },
    close() {
        const m = document.getElementById('settings-modal');
        if (m) { m.style.display = 'none'; }
    },
    checkMergeStatus() {
        fetch('/api/settings/merge_db/status')
            .then(res => res.json())
            .then(state => {
                if (state && state.status === 'running') {
                    this.trackMergeProgress();
                }
            })
            .catch(() => {});
    },
    trackMergeProgress() {
        if (this._mergePollTimer) clearInterval(this._mergePollTimer);
        const box = document.getElementById('merge-db-progress-box');
        const stageEl = document.getElementById('merge-db-stage-label');
        const pctEl = document.getElementById('merge-db-percentage');
        const barEl = document.getElementById('merge-db-progress-bar');
        const detailEl = document.getElementById('merge-db-detail-label');
        const btn = document.getElementById('merge-db-trigger-btn');
        const srcInput = document.getElementById('merge-db-source-path');
        const mediaInput = document.getElementById('merge-media-source-path');

        if (box) box.style.display = 'block';
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Merging Database...'; }
        if (srcInput) srcInput.disabled = true;
        if (mediaInput) mediaInput.disabled = true;

        this._mergePollTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/settings/merge_db/status');
                if (!res.ok) return;
                const state = await res.json();

                if (pctEl) pctEl.textContent = `${state.percent || 0}%`;
                if (barEl) {
                    barEl.style.width = `${state.percent || 0}%`;
                    barEl.style.background = '#3b82f6';
                }
                if (stageEl) {
                    stageEl.style.color = '#3b82f6';
                    stageEl.textContent = state.stage ? (state.stage.toUpperCase() + '...') : 'Merging...';
                }
                if (detailEl) detailEl.textContent = state.detail || '';

                if (state.status === 'completed') {
                    clearInterval(this._mergePollTimer);
                    this._mergePollTimer = null;
                    if (stageEl) { stageEl.style.color = '#10b981'; stageEl.textContent = 'COMPLETED'; }
                    if (barEl) { barEl.style.width = '100%'; barEl.style.background = '#10b981'; }
                    if (pctEl) pctEl.textContent = '100%';
                    if (btn) { btn.disabled = false; btn.textContent = '🔀 Merge Database Non-Destructively'; }
                    if (srcInput) srcInput.disabled = false;
                    if (mediaInput) mediaInput.disabled = false;
                    if (typeof showNotification === 'function') {
                        showNotification('Database merged successfully!', 'success');
                    } else {
                        alert('Database merged successfully!');
                    }
                    if (typeof window.loadChats === 'function') window.loadChats();
                    if (typeof window.loadPureDbStats === 'function') window.loadPureDbStats();
                } else if (state.status === 'error') {
                    clearInterval(this._mergePollTimer);
                    this._mergePollTimer = null;
                    if (stageEl) { stageEl.style.color = '#ef4444'; stageEl.textContent = 'FAILED'; }
                    if (barEl) { barEl.style.background = '#ef4444'; }
                    if (btn) { btn.disabled = false; btn.textContent = '🔀 Merge Database Non-Destructively'; }
                    if (srcInput) srcInput.disabled = false;
                    if (mediaInput) mediaInput.disabled = false;
                    const err = state.error || 'Unknown error';
                    if (typeof showNotification === 'function') {
                        showNotification(`Merge failed: ${err}`, 'error');
                    } else {
                        alert(`Merge failed: ${err}`);
                    }
                }
            } catch (_) {}
        }, 400);
    },
    switchTab(tabName) {
        this._activeTab = tabName;
        const generalBtn = document.getElementById('settings-tab-btn-general');
        const advancedBtn = document.getElementById('settings-tab-btn-advanced');
        const generalView = document.getElementById('settings-view-general');
        const advancedView = document.getElementById('settings-view-advanced');

        if (generalBtn) generalBtn.classList.toggle('active', tabName === 'general');
        if (advancedBtn) advancedBtn.classList.toggle('active', tabName === 'advanced');

        if (generalView) generalView.style.display = (tabName === 'general') ? 'flex' : 'none';
        if (advancedView) {
            advancedView.style.display = (tabName === 'advanced') ? 'flex' : 'none';
            if (tabName === 'advanced' && window.loadWhisperSettings) {
                window.loadWhisperSettings();
            }
        }
    },
    toggleSection(cardId) {
        const card = document.getElementById(cardId);
        if (!card) return;
        const body = card.querySelector('.settings-collapsible-body');
        const chevron = card.querySelector('.collapsible-chevron');
        if (!body) return;
        const isCollapsed = (window.getComputedStyle(body).display === 'none' || body.style.display === 'none');
        body.style.display = isCollapsed ? 'block' : 'none';
        if (chevron) {
            chevron.textContent = isCollapsed ? '▼' : '▶';
        }
    },
    _wire() {
        this._wired = true;
        const dz = document.getElementById('bg-drop-zone');
        const fileInput = document.getElementById('bg-file-input');
        const removeBtn = document.getElementById('remove-bg-btn');
        const modal = document.getElementById('settings-modal');
        const paletteSelect = document.getElementById('theme-palette-select');
        const customColorSelect = document.getElementById('custom-bubble-color');
        const blurSlider = document.getElementById('bg-blur-slider');
        const dimSlider = document.getElementById('bg-dim-slider');

        if (blurSlider) {
            blurSlider.addEventListener('input', (e) => {
                document.documentElement.style.setProperty('--bg-blur', e.target.value + 'px');
                localStorage.setItem('tg_bg_blur', e.target.value);
            });
        }
        if (dimSlider) {
            dimSlider.addEventListener('input', (e) => {
                document.documentElement.style.setProperty('--bg-dim', e.target.value);
                localStorage.setItem('tg_bg_dim', e.target.value);
            });
        }

        if (paletteSelect) {
            paletteSelect.addEventListener('change', (e) => {
                ThemeEngine.setPalette(e.target.value);
            });
        }

        if (customColorSelect) {
            customColorSelect.addEventListener('input', (e) => {
                ThemeEngine.setCustomColor(e.target.value);
            });
        }

        // Drop zone click -> open file picker
        if (dz) { dz.addEventListener('click', () => fileInput && fileInput.click()); }

        // Drag-and-drop
        if (dz) {
            dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
            dz.addEventListener('dragleave', () => { dz.classList.remove('drag-over'); });
            dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag-over'); const f = e.dataTransfer.files[0]; if (f) BgEngine.upload(f); });
        }

        // File selection via input
        if (fileInput) { fileInput.addEventListener('change', () => { const f = fileInput.files[0]; if (f) BgEngine.upload(f); }); }

        // Remove background
        if (removeBtn) { removeBtn.addEventListener('click', () => BgEngine.remove()); }

        // Overlay click closes modal
        if (modal) { modal.addEventListener('click', (e) => { if (e.target === modal) this.close(); }); }

        // Database Merger Handler
        const mergeBtn = document.getElementById('merge-db-trigger-btn');
        if (mergeBtn && !mergeBtn._wired) {
            mergeBtn._wired = true;
            mergeBtn.addEventListener('click', async () => {
                const srcInput = document.getElementById('merge-db-source-path');
                const mediaInput = document.getElementById('merge-media-source-path');
                if (!srcInput || !srcInput.value.trim()) {
                    alert('Please specify the path to the source .db file');
                    return;
                }
                mergeBtn.disabled = true;
                mergeBtn.textContent = '⏳ Merging Database...';
                try {
                    const res = await fetch('/api/settings/merge_db', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '')
                        },
                        body: JSON.stringify({
                            source_db_path: srcInput.value.trim(),
                            source_media_dir: mediaInput ? mediaInput.value.trim() : null,
                            async_mode: true
                        })
                    });
                    const data = await res.json();
                    if (res.ok) {
                        SettingsModal.trackMergeProgress();
                    } else {
                        mergeBtn.disabled = false;
                        mergeBtn.textContent = '🔀 Merge Database Non-Destructively';
                        alert(`Merge failed: ${data.error || 'Unknown error'}`);
                    }
                } catch (e) {
                    mergeBtn.disabled = false;
                    mergeBtn.textContent = '🔀 Merge Database Non-Destructively';
                    alert(`Merge error: ${e.message}`);
                }
            });
        }
    }
};

// ══════════════════════════════════════════════════════════════
// ── FAQ MODAL ────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════
window.FAQModal = {
    open() {
        const m = document.getElementById('faq-modal');
        if (m) {
            m.style.display = 'flex';
            if (typeof I18N !== 'undefined' && I18N.applyAll) {
                I18N.applyAll();
            }
        }
    },
    close() {
        const m = document.getElementById('faq-modal');
        if (m) m.style.display = 'none';
        localStorage.setItem('tutorial_seen', 'true');
    },
    switchTab(tabId, btn) {
        document.querySelectorAll('.faq-tab-btn').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');

        document.querySelectorAll('.faq-tab-pane').forEach(p => p.style.display = 'none');
        const target = document.getElementById(`faq-tab-${tabId}`);
        if (target) target.style.display = 'block';
    }
};

window.switchChatSettingsTab = function(tabId, btn) {
    document.querySelectorAll('.csp-tab-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    document.querySelectorAll('.csp-tab-panel').forEach(p => p.style.display = 'none');
    const target = document.getElementById(`csp-tab-${tabId}`);
    if (target) target.style.display = 'block';
};

// ══════════════════════════════════════════════════════════════
// ── REFRESH HINT MODAL ───────────────────────────────────────
// ══════════════════════════════════════════════════════════════
window.RefreshHintModal = {
    open() {
        const m = document.getElementById('refresh-hint-modal');
        if (m) m.style.display = 'flex';
    },
    close() {
        const m = document.getElementById('refresh-hint-modal');
        if (m) m.style.display = 'none';
    }
};



window.SettingsModal = SettingsModal;
