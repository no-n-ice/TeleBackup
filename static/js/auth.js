// auth.js — Proactive Session Verification, Alert Banner, Multi-Session Management, and Secure WebUI Login
(function () {
    let authCsrfToken = '';
    let authModal, warningBanner, userBadge, userDot, userNameEl;
    let sessionsModal, sessionsList, sessionsCountBadge, sessionsAddBtn, restartOverlay;
    let currentStep = 1;
    let pollTimer = null;

    function getCsrfToken() {
        if (!authCsrfToken) {
            const meta = document.querySelector('meta[name="auth-csrf"]');
            authCsrfToken = meta ? meta.getAttribute('content') : '';
        }
        return authCsrfToken;
    }
    window.getCsrfToken = getCsrfToken;

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    async function checkAuthStatus(forceRefresh = false) {
        try {
            const res = await fetch(`/api/auth/status${forceRefresh ? '?refresh=1' : ''}`);
            if (!res.ok) return;
            const data = await res.json();
            updateAuthUI(data);
        } catch (e) {
            console.warn('Auth status check error:', e);
        }
    }

    function updateAuthUI(data) {
        warningBanner = document.getElementById('session-warning-banner');
        userBadge = document.getElementById('auth-user-badge');
        userDot = document.getElementById('auth-status-dot');
        userNameEl = document.getElementById('auth-user-name');

        if (data.authorized) {
            if (warningBanner) warningBanner.classList.remove('active');
            if (userDot) {
                userDot.classList.add('online');
                userDot.title = 'Telegram Session Active';
            }
            if (userNameEl) {
                const name = data.user && data.user.username 
                    ? `@${data.user.username}` 
                    : (data.user && data.user.first_name ? data.user.first_name : 'Authorized');
                userNameEl.textContent = name;
            }
            try {
                localStorage.setItem('telebackup_auth_state', JSON.stringify({
                    authorized: true,
                    configured: true,
                    user: data.user ? {
                        id: data.user.id,
                        first_name: data.user.first_name,
                        username: data.user.username
                    } : null,
                    time: Date.now()
                }));
            } catch (e) {}
        } else {
            if (warningBanner) warningBanner.classList.add('active');
            if (userDot) {
                userDot.classList.remove('online');
                userDot.title = 'Session Unauthorized';
            }
            if (userNameEl) {
                userNameEl.textContent = data.configured ? 'Unauthorized' : 'Config Missing';
            }
            try {
                localStorage.removeItem('telebackup_auth_state');
            } catch (e) {}
        }
    }

    // ==========================================
    // Multi-Session Management (Dialog & Switch)
    // ==========================================

    async function openSessionsModal() {
        if (!sessionsModal) sessionsModal = document.getElementById('sessions-modal');
        if (!sessionsModal) return;
        sessionsModal.style.display = 'flex';
        await loadSessionsList();
    }

    function closeSessionsModal() {
        if (!sessionsModal) sessionsModal = document.getElementById('sessions-modal');
        if (sessionsModal) sessionsModal.style.display = 'none';
    }

    async function loadSessionsList() {
        if (!sessionsList) sessionsList = document.getElementById('sessions-list');
        if (!sessionsCountBadge) sessionsCountBadge = document.getElementById('sessions-count-badge');
        if (!sessionsList) return;

        sessionsList.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-color-light);">Loading sessions...</div>';

        try {
            const res = await fetch('/api/auth/sessions');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const sessions = data.sessions || [];

            if (sessionsCountBadge) {
                const countText = sessions.length === 1 ? '1 session' : `${sessions.length} sessions`;
                sessionsCountBadge.textContent = countText;
            }

            if (sessions.length === 0) {
                sessionsList.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-color-light);">No sessions found on disk. Click "+ Add Session" below to log in.</div>';
                return;
            }

            sessionsList.innerHTML = '';
            sessions.forEach(sess => {
                const card = document.createElement('div');
                card.className = `session-card${sess.is_active ? ' active' : ''}`;

                // Avatar container
                const avatarWrap = document.createElement('div');
                avatarWrap.className = 'session-avatar-wrap';

                const displayName = sess.name || (sess.first_name ? `${sess.first_name}${sess.last_name ? ' ' + sess.last_name : ''}` : sess.session_name);
                const initial = (displayName || 'U').charAt(0).toUpperCase();

                const avatarSrc = sess.avatar_url || sess.avatar;
                if (avatarSrc) {
                    const img = document.createElement('img');
                    img.className = 'session-avatar';
                    img.src = avatarSrc;
                    img.alt = displayName;
                    img.onerror = () => {
                        img.style.display = 'none';
                        avatarWrap.innerHTML = `<div class="session-avatar-letter">${escapeHtml(initial)}</div>`;
                    };
                    avatarWrap.appendChild(img);
                } else {
                    avatarWrap.innerHTML = `<div class="session-avatar-letter">${escapeHtml(initial)}</div>`;
                }

                // Info details
                const info = document.createElement('div');
                info.className = 'session-info';

                const nameRow = document.createElement('div');
                nameRow.className = 'session-name-row';
                nameRow.innerHTML = `<span class="session-display-name">${escapeHtml(displayName)}</span>`;
                if (sess.is_active) {
                    const activeTag = typeof I18N !== 'undefined' ? I18N.t('sessions.active_tag') : 'Active';
                    nameRow.innerHTML += `<span class="session-active-badge">● ${activeTag}</span>`;
                }

                const metaRow = document.createElement('div');
                metaRow.className = 'session-meta-row';
                const usernameStr = sess.username ? `@${sess.username}` : (typeof I18N !== 'undefined' ? I18N.t('sessions.no_username') : 'no username');
                const phoneStr = sess.phone || (typeof I18N !== 'undefined' ? I18N.t('sessions.no_phone') : 'no phone');
                metaRow.innerHTML = `<span>${escapeHtml(usernameStr)}</span> • <span>${escapeHtml(phoneStr)}</span>`;
                if (sess.session_name && sess.session_name !== displayName) {
                    metaRow.innerHTML += ` • <span style="font-family:monospace; font-size:11px;">${escapeHtml(sess.session_name)}</span>`;
                }

                info.appendChild(nameRow);
                info.appendChild(metaRow);

                // Action buttons
                const actions = document.createElement('div');
                actions.className = 'session-actions';

                if (sess.is_active) {
                    const shutdownBtn = document.createElement('button');
                    shutdownBtn.type = 'button';
                    shutdownBtn.className = 'session-btn session-btn-shutdown';
                    shutdownBtn.textContent = typeof I18N !== 'undefined' ? I18N.t('sessions.shutdown_btn') : 'Shutdown';
                    shutdownBtn.onclick = () => handleShutdownSession(sess.session_name, true);
                    actions.appendChild(shutdownBtn);
                } else {
                    const switchBtn = document.createElement('button');
                    switchBtn.type = 'button';
                    switchBtn.className = 'session-btn session-btn-switch';
                    switchBtn.textContent = typeof I18N !== 'undefined' ? I18N.t('sessions.switch_btn') : 'Switch';
                    switchBtn.onclick = () => handleSwitchSession(sess.session_name);
                    actions.appendChild(switchBtn);

                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.className = 'session-btn session-btn-shutdown';
                    deleteBtn.textContent = typeof I18N !== 'undefined' ? I18N.t('sessions.delete_btn') : 'Delete';
                    deleteBtn.onclick = () => handleShutdownSession(sess.session_name, false);
                    actions.appendChild(deleteBtn);
                }

                card.appendChild(avatarWrap);
                card.appendChild(info);
                card.appendChild(actions);
                sessionsList.appendChild(card);
            });
        } catch (e) {
            console.error('Failed to load sessions:', e);
            sessionsList.innerHTML = `<div style="text-align:center; padding:20px; color:#ff5252;">Failed to load sessions: ${escapeHtml(e.message)}</div>`;
        }
    }

    async function handleSwitchSession(sessionName) {
        const confirmMsg = typeof I18N !== 'undefined' 
            ? I18N.t('sessions.confirm_switch', sessionName) 
            : `Switch active Telegram session to "${sessionName}"? Server will restart.`;
        if (!confirm(confirmMsg)) return;

        closeSessionsModal();
        showRestartOverlay(
            typeof I18N !== 'undefined' ? I18N.t('sessions.restarting_title') : 'Switching session...',
            typeof I18N !== 'undefined' ? I18N.t('sessions.restarting_desc') : 'Server is restarting with the selected Telegram session. Reconnecting automatically...'
        );

        try {
            const res = await fetch('/api/auth/switch_session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                },
                body: JSON.stringify({ session_name: sessionName })
            });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                hideRestartOverlay();
                alert('Switch failed: ' + (data.error || 'Unknown error'));
                openSessionsModal();
                return;
            }
            setTimeout(() => pollServerRestart(), 2000);
        } catch (e) {
            // Process might restart immediately during disconnect, continue polling
            setTimeout(() => pollServerRestart(), 2000);
        }
    }

    async function handleShutdownSession(sessionName, isActive) {
        const confirmMsg = typeof I18N !== 'undefined'
            ? I18N.t(isActive ? 'sessions.confirm_shutdown' : 'sessions.confirm_delete', sessionName)
            : (isActive ? `Shutdown active session "${sessionName}"?` : `Permanently delete session "${sessionName}" from disk?`);
        if (!confirm(confirmMsg)) return;

        try {
            const res = await fetch('/api/auth/shutdown_session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                },
                body: JSON.stringify({ session_name: sessionName })
            });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                alert('Action failed: ' + (data.error || 'Unknown error'));
                return;
            }
            if (typeof showNotification === 'function') {
                showNotification(data.message || 'Session closed', 'info');
            }
            await checkAuthStatus(true);
            await loadSessionsList();
        } catch (e) {
            alert('Network error: ' + e.message);
        }
    }

    function showRestartOverlay(title, desc) {
        restartOverlay = document.getElementById('session-restart-overlay');
        if (!restartOverlay) return;
        if (title) {
            const titleEl = document.getElementById('session-restart-title');
            if (titleEl) titleEl.textContent = title;
        }
        if (desc) {
            const descEl = document.getElementById('session-restart-desc');
            if (descEl) descEl.textContent = desc;
        }
        restartOverlay.style.display = 'flex';
    }

    function hideRestartOverlay() {
        restartOverlay = document.getElementById('session-restart-overlay');
        if (restartOverlay) restartOverlay.style.display = 'none';
    }

    function pollServerRestart(attempts = 0) {
        if (pollTimer) clearTimeout(pollTimer);
        if (attempts > 35) {
            const descEl = document.getElementById('session-restart-desc');
            if (descEl) {
                descEl.innerHTML = 'Server took longer than expected to restart.<br><button onclick="location.reload()" style="margin-top:10px; padding:6px 14px; border-radius:6px; background:#00c853; color:white; border:none; cursor:pointer;">Refresh Page</button>';
            }
            return;
        }

        pollTimer = setTimeout(async () => {
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 2000);
                const res = await fetch('/api/auth/status?refresh=1', { signal: controller.signal });
                clearTimeout(timeoutId);
                if (res.ok) {
                    const data = await res.json();
                    hideRestartOverlay();
                    updateAuthUI(data);
                    if (typeof showNotification === 'function') {
                        showNotification(
                            typeof I18N !== 'undefined' ? I18N.t('sessions.switch_success') || 'Session switched successfully!' : 'Session switched successfully!',
                            'info'
                        );
                    }
                    setTimeout(() => window.location.reload(), 600);
                    return;
                }
            } catch (e) {
                // Server rebooting, wait for next heartbeat attempt
            }
            pollServerRestart(attempts + 1);
        }, 1500);
    }

    // ==========================================
    // Core WebUI Login Modal Steps
    // ==========================================

    function initAuth() {
        authModal = document.getElementById('auth-modal');
        warningBanner = document.getElementById('session-warning-banner');
        sessionsModal = document.getElementById('sessions-modal');
        sessionsList = document.getElementById('sessions-list');
        sessionsCountBadge = document.getElementById('sessions-count-badge');
        sessionsAddBtn = document.getElementById('sessions-add-btn');
        restartOverlay = document.getElementById('session-restart-overlay');

        // Instant restore from local cache to prevent flash
        try {
            const cached = JSON.parse(localStorage.getItem('telebackup_auth_state') || 'null');
            if (cached && cached.authorized) {
                updateAuthUI(cached);
            }
        } catch (e) {}

        // Check on load (async background verification)
        checkAuthStatus();

        // Wire banner buttons
        const openLoginBtn = document.getElementById('banner-login-btn');
        if (openLoginBtn) {
            openLoginBtn.addEventListener('click', () => openAuthModal());
        }

        const refreshStatusBtn = document.getElementById('banner-refresh-btn');
        if (refreshStatusBtn) {
            refreshStatusBtn.addEventListener('click', async () => {
                refreshStatusBtn.disabled = true;
                refreshStatusBtn.textContent = typeof I18N !== 'undefined' ? I18N.t('status.loading') : 'Checking...';
                await checkAuthStatus(true);
                refreshStatusBtn.disabled = false;
                refreshStatusBtn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.refresh_status') : 'Refresh Status';
                if (typeof showNotification === 'function') {
                    showNotification(typeof I18N !== 'undefined' ? I18N.t('auth.status_updated') : 'Session status updated', 'info');
                }
            });
        }

        // Wire Sessions modal controls
        if (sessionsModal) {
            sessionsModal.addEventListener('click', (e) => {
                if (e.target === sessionsModal) closeSessionsModal();
            });
        }

        if (sessionsAddBtn) {
            sessionsAddBtn.addEventListener('click', () => {
                closeSessionsModal();
                openAuthModal();
            });
        }

        // Wire Login modal controls
        if (authModal) {
            const closeBtn = authModal.querySelector('.auth-close');
            if (closeBtn) closeBtn.addEventListener('click', closeAuthModal);

            authModal.addEventListener('click', (e) => {
                if (e.target === authModal) closeAuthModal();
            });

            // Tab Controls
            const tabQr = document.getElementById('auth-tab-qr');
            const tabPhone = document.getElementById('auth-tab-phone');
            if (tabQr) tabQr.addEventListener('click', () => switchAuthMode('qr'));
            if (tabPhone) tabPhone.addEventListener('click', () => switchAuthMode('phone'));

            // QR Refresh Button
            const qrRefreshBtn = document.getElementById('auth-qr-refresh-btn');
            if (qrRefreshBtn) qrRefreshBtn.addEventListener('click', () => startQrLogin());

            // Step 1: Send Code
            const sendCodeBtn = document.getElementById('auth-send-code-btn');
            if (sendCodeBtn) {
                sendCodeBtn.addEventListener('click', handleSendCode);
            }

            // Step 2: Sign In with Code
            const verifyCodeBtn = document.getElementById('auth-verify-code-btn');
            if (verifyCodeBtn) {
                verifyCodeBtn.addEventListener('click', handleVerifyCode);
            }

            // Step 3: Submit 2FA Password
            const submit2faBtn = document.getElementById('auth-submit-2fa-btn');
            if (submit2faBtn) {
                submit2faBtn.addEventListener('click', handleSubmit2fa);
            }

            // Step 2 Back Button
            const backBtn = document.getElementById('auth-back-btn');
            if (backBtn) {
                backBtn.addEventListener('click', () => setStep(1));
            }

            // Step 2 Resend Button
            const resendBtn = document.getElementById('auth-resend-btn');
            if (resendBtn) {
                resendBtn.addEventListener('click', handleResendCode);
            }
        }
    }

    let resendTimer = null;

    function renderDeliveryInfo(delivery) {
        const infoEl = document.getElementById('auth-delivery-info');
        const titleEl = document.getElementById('auth-delivery-title');
        const descEl = document.getElementById('auth-delivery-desc');
        const warnEl = document.getElementById('auth-delivery-warning');
        const lenEl = document.getElementById('auth-code-length');
        const resendBtn = document.getElementById('auth-resend-btn');

        if (!infoEl || !delivery) return;

        infoEl.style.display = 'block';

        const channelIcons = {
            'app': '📱',
            'sms': '💬',
            'email': '✉️',
            'email_setup_required': '⚠️',
            'call': '📞',
            'missed_call': '📲',
            'flash_call': '⚡',
            'fragment': '💎',
            'payment_required': '💳'
        };
        const icon = channelIcons[delivery.channel] || '🔑';

        if (titleEl) {
            titleEl.textContent = `${icon} ${delivery.title || 'Telegram Dispatch'}`;
        }

        if (descEl) {
            descEl.textContent = delivery.desc || '';
        }

        if (warnEl) {
            if (delivery.warning) {
                warnEl.textContent = delivery.warning;
                warnEl.style.display = 'block';
            } else {
                warnEl.style.display = 'none';
            }
        }

        if (lenEl) {
            lenEl.textContent = delivery.length ? `${delivery.length} digits` : '';
            lenEl.style.display = delivery.length ? 'inline-block' : 'none';
        }

        // Resend Button & Countdown
        if (resendBtn) {
            if (resendTimer) clearInterval(resendTimer);
            resendBtn.style.display = 'inline-block';
            let remaining = delivery.timeout && delivery.timeout > 0 ? delivery.timeout : 45;

            const updateBtn = () => {
                if (remaining <= 0) {
                    clearInterval(resendTimer);
                    resendTimer = null;
                    resendBtn.disabled = false;
                    resendBtn.textContent = 'Resend via SMS';
                } else {
                    resendBtn.disabled = true;
                    resendBtn.textContent = `Resend (${remaining}s)`;
                    remaining--;
                }
            };
            updateBtn();
            resendTimer = setInterval(updateBtn, 1000);
        }
    }

    async function handleResendCode() {
        const resendBtn = document.getElementById('auth-resend-btn');
        if (!resendBtn || resendBtn.disabled) return;

        resendBtn.disabled = true;
        resendBtn.textContent = 'Requesting...';
        clearError();

        try {
            const res = await fetch('/api/auth/resend_code', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                }
            });
            const data = await res.json();
            if (res.ok && data.ok) {
                if (data.delivery) {
                    renderDeliveryInfo(data.delivery);
                }
                if (typeof showNotification === 'function') {
                    showNotification(data.message || 'Verification code resent', 'info');
                }
            } else {
                showError(data.error || 'Failed to resend code');
                resendBtn.disabled = false;
                resendBtn.textContent = 'Resend via SMS';
            }
        } catch (e) {
            showError('Connection error: ' + e.message);
            resendBtn.disabled = false;
            resendBtn.textContent = 'Resend via SMS';
        }
    }

    let qrPollTimer = null;
    let qrCountdownTimer = null;

    function stopQrPolling() {
        if (qrPollTimer) {
            clearTimeout(qrPollTimer);
            qrPollTimer = null;
        }
        if (qrCountdownTimer) {
            clearInterval(qrCountdownTimer);
            qrCountdownTimer = null;
        }
    }

    async function startQrLogin() {
        stopQrPolling();
        const container = document.getElementById('auth-qr-container');
        const errEl = document.getElementById('auth-error-qr');
        const secondsEl = document.getElementById('auth-qr-seconds');
        const refreshBtn = document.getElementById('auth-qr-refresh-btn');

        if (errEl) {
            errEl.textContent = '';
            errEl.style.display = 'none';
        }
        if (container) {
            container.innerHTML = '<div class="spinner" style="width:36px; height:36px; border:3px solid #ccc; border-top-color:#2997e0; border-radius:50%; animation:spin 1s linear infinite;"></div>';
        }
        if (refreshBtn) refreshBtn.disabled = true;

        try {
            const res = await fetch('/api/auth/qr_start', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                },
                body: JSON.stringify({})
            });
            const data = await res.json();
            if (refreshBtn) refreshBtn.disabled = false;

            if (res.ok && data.ok && data.svg) {
                if (container) {
                    container.innerHTML = data.svg;
                    const svgEl = container.querySelector('svg');
                    if (svgEl) {
                        svgEl.style.width = '100%';
                        svgEl.style.height = '100%';
                        svgEl.style.display = 'block';
                    }
                }

                let remaining = data.expires_in || 60;
                if (secondsEl) secondsEl.textContent = remaining;

                qrCountdownTimer = setInterval(() => {
                    remaining--;
                    if (secondsEl) secondsEl.textContent = Math.max(0, remaining);
                    if (remaining <= 0) {
                        clearInterval(qrCountdownTimer);
                        qrCountdownTimer = null;
                        if (errEl) {
                            errEl.textContent = 'QR code expired. Click "Refresh QR Code" to generate a new code.';
                            errEl.style.display = 'block';
                        }
                    }
                }, 1000);

                pollQrStatus();
            } else {
                if (container) container.innerHTML = '<span style="color:#ff5252; font-size:12px;">Failed to load QR</span>';
                if (errEl) {
                    errEl.textContent = data.error || 'Failed to generate QR code';
                    errEl.style.display = 'block';
                }
            }
        } catch (e) {
            if (refreshBtn) refreshBtn.disabled = false;
            if (container) container.innerHTML = '<span style="color:#ff5252; font-size:12px;">Connection error</span>';
            if (errEl) {
                errEl.textContent = 'Connection error: ' + e.message;
                errEl.style.display = 'block';
            }
        }
    }

    async function pollQrStatus() {
        if (currentStep !== 'qr') return;
        try {
            const res = await fetch('/api/auth/qr_status');
            if (!res.ok) return;
            const data = await res.json();

            if (data.authorized && data.user) {
                stopQrPolling();
                onLoginSuccess(data.user, data.restarting);
                return;
            } else if (data.need_2fa) {
                stopQrPolling();
                setStep(3);
                const pwdInput = document.getElementById('auth-password-input');
                if (pwdInput) setTimeout(() => pwdInput.focus(), 100);
                if (typeof showNotification === 'function') {
                    showNotification('QR scanned! Enter your 2FA Cloud Password to complete login.', 'info');
                }
                return;
            } else if (data.error) {
                stopQrPolling();
                const errEl = document.getElementById('auth-error-qr');
                if (errEl) {
                    errEl.textContent = data.error;
                    errEl.style.display = 'block';
                }
                return;
            }

            if (!data.done && currentStep === 'qr') {
                qrPollTimer = setTimeout(pollQrStatus, 1500);
            }
        } catch (e) {
            if (currentStep === 'qr') {
                qrPollTimer = setTimeout(pollQrStatus, 2000);
            }
        }
    }

    function switchAuthMode(mode) {
        const qrTab = document.getElementById('auth-tab-qr');
        const phoneTab = document.getElementById('auth-tab-phone');
        if (mode === 'qr') {
            if (qrTab) {
                qrTab.classList.add('active');
                qrTab.style.borderBottomColor = 'var(--accent-color, #2997e0)';
                qrTab.style.color = 'var(--text-color)';
            }
            if (phoneTab) {
                phoneTab.classList.remove('active');
                phoneTab.style.borderBottomColor = 'transparent';
                phoneTab.style.color = 'var(--text-dim, #8a99ad)';
            }
            setStep('qr');
            startQrLogin();
        } else {
            if (phoneTab) {
                phoneTab.classList.add('active');
                phoneTab.style.borderBottomColor = 'var(--accent-color, #2997e0)';
                phoneTab.style.color = 'var(--text-color)';
            }
            if (qrTab) {
                qrTab.classList.remove('active');
                qrTab.style.borderBottomColor = 'transparent';
                qrTab.style.color = 'var(--text-dim, #8a99ad)';
            }
            setStep(1);
            const phoneInput = document.getElementById('auth-phone-input');
            if (phoneInput) setTimeout(() => phoneInput.focus(), 100);
        }
    }

    function setStep(step) {
        currentStep = step;
        document.querySelectorAll('.auth-step').forEach(s => s.classList.remove('active'));
        const active = document.getElementById(`auth-step-${step}`);
        if (active) active.classList.add('active');
        if (step !== 2 && resendTimer) {
            clearInterval(resendTimer);
            resendTimer = null;
        }
        if (step !== 'qr') {
            stopQrPolling();
        }
        clearError();
    }

    function showError(msg) {
        const errEl = document.getElementById(`auth-error-${currentStep}`);
        if (errEl) {
            errEl.textContent = msg;
            errEl.style.display = 'block';
        }
    }

    function clearError() {
        document.querySelectorAll('.auth-error').forEach(e => {
            e.textContent = '';
            e.style.display = 'none';
        });
    }

    function openAuthModal() {
        if (!authModal) return;
        authModal.classList.add('active');
        switchAuthMode('qr');
    }

    function closeAuthModal() {
        if (!authModal) return;
        authModal.classList.remove('active');
        stopQrPolling();
        if (resendTimer) {
            clearInterval(resendTimer);
            resendTimer = null;
        }
        clearError();
    }

    async function handleSendCode() {
        const phoneInput = document.getElementById('auth-phone-input');
        const phone = phoneInput ? phoneInput.value.trim() : '';
        if (!phone) {
            showError(typeof I18N !== 'undefined' ? I18N.t('auth.err_enter_phone') : 'Please enter your phone number with country code');
            return;
        }

        const aliasInput = document.getElementById('auth-session-name-input');
        const sessionAlias = aliasInput ? aliasInput.value.trim() : '';

        const btn = document.getElementById('auth-send-code-btn');
        btn.disabled = true;
        btn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.sending_code') : 'Sending code...';
        clearError();

        try {
            const res = await fetch('/api/auth/send_code', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                },
                body: JSON.stringify({ 
                    phone: phone,
                    session_name: sessionAlias
                })
            });

            const data = await res.json();
            if (res.ok && data.ok) {
                setStep(2);
                if (data.delivery) {
                    renderDeliveryInfo(data.delivery);
                }
                const codeInput = document.getElementById('auth-code-input');
                if (codeInput) setTimeout(() => codeInput.focus(), 100);
            } else {
                showError(data.error || 'Failed to send verification code');
            }
        } catch (e) {
            showError('Connection error: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.send_code_btn') : 'Next: Send Code';
        }
    }

    async function handleVerifyCode() {
        const codeInput = document.getElementById('auth-code-input');
        const code = codeInput ? codeInput.value.trim() : '';
        if (!code) {
            showError(typeof I18N !== 'undefined' ? I18N.t('auth.err_enter_code') : 'Please enter verification code');
            return;
        }

        const btn = document.getElementById('auth-verify-code-btn');
        btn.disabled = true;
        btn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.verifying') : 'Verifying...';
        clearError();

        try {
            const res = await fetch('/api/auth/sign_in', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                },
                body: JSON.stringify({ code: code })
            });

            const data = await res.json();
            if (res.ok && data.need_2fa) {
                setStep(3);
                const pwdInput = document.getElementById('auth-password-input');
                if (pwdInput) setTimeout(() => pwdInput.focus(), 100);
            } else if (res.ok && data.ok) {
                onLoginSuccess(data.user, data.restarting);
            } else {
                showError(data.error || 'Invalid code');
            }
        } catch (e) {
            showError('Connection error: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.verify_code_btn') : 'Verify Code';
        }
    }

    async function handleSubmit2fa() {
        const pwdInput = document.getElementById('auth-password-input');
        const password = pwdInput ? pwdInput.value : '';
        if (!password) {
            showError(typeof I18N !== 'undefined' ? I18N.t('auth.err_enter_pwd') : 'Please enter your 2FA cloud password');
            return;
        }

        const btn = document.getElementById('auth-submit-2fa-btn');
        btn.disabled = true;
        btn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.unlocking') : 'Unlocking...';
        clearError();

        try {
            const res = await fetch('/api/auth/2fa', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-TeleBackup-Auth-Token': getCsrfToken()
                },
                body: JSON.stringify({ password: password })
            });

            // Clear password immediately from memory/DOM
            if (pwdInput) pwdInput.value = '';

            const data = await res.json();
            if (res.ok && data.ok) {
                onLoginSuccess(data.user, data.restarting);
            } else {
                showError(data.error || 'Invalid 2FA password');
            }
        } catch (e) {
            showError('Connection error: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = typeof I18N !== 'undefined' ? I18N.t('auth.submit_2fa_btn') : 'Unlock Account';
        }
    }

    function onLoginSuccess(user, restarting) {
        closeAuthModal();
        if (restarting) {
            showRestartOverlay(
                typeof I18N !== 'undefined' ? I18N.t('sessions.restarting_title') : 'Switching session...',
                typeof I18N !== 'undefined' ? I18N.t('sessions.restarting_desc') : 'Server is restarting with the new session. Reconnecting automatically...'
            );
            pollServerRestart();
            return;
        }

        if (typeof showNotification === 'function') {
            const welcome = typeof I18N !== 'undefined' 
                ? I18N.t('auth.welcome_msg', user?.first_name || 'Telegram user') 
                : `Welcome, ${user?.first_name || 'Telegram user'}! Session saved.`;
            showNotification(welcome, 'info');
        }
        checkAuthStatus(true);
        if (typeof loadAllDialogs === 'function') {
            loadAllDialogs(true);
        }
    }

    // Global window exports
    window.openAuthModal = openAuthModal;
    window.closeAuthModal = closeAuthModal;
    window.openSessionsModal = openSessionsModal;
    window.closeSessionsModal = closeSessionsModal;
    window.loadSessionsList = loadSessionsList;
    window.checkAuthStatus = checkAuthStatus;

    document.addEventListener('DOMContentLoaded', initAuth);
})();
