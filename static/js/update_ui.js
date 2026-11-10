// update_ui.js — Self-updater checks and notification banners
(function () {
    async function checkForUpdates(manual = false) {
        try {
            const url = manual ? '/api/check_update?force=1' : '/api/check_update';
            const res = await fetch(url);
            let data = null;
            try {
                data = await res.json();
            } catch (_) {}

            if (!res.ok || (data && data.error)) {
                if (manual && typeof showNotification === 'function') {
                    const errMsg = (data && data.error) ? data.error : `HTTP ${res.status}`;
                    const prefix = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('update.failed') : 'Update check failed';
                    showNotification(`${prefix}: ${errMsg}`, 'error');
                }
                return;
            }

            if (!data) return;

            if (data.update_available) {
                showUpdateToast(data);
            } else if (manual) {
                if (typeof showNotification === 'function') {
                    const latestMsg = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('update.latest') : 'You are running the latest version.';
                    showNotification(latestMsg, 'info');
                }
            }
        } catch (e) {
            if (manual && typeof showNotification === 'function') {
                const failMsg = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t('update.failed') : 'Update check failed';
                showNotification(failMsg, 'error');
            }
        }
    }

    function showUpdateToast(data) {
        if (document.getElementById('update-toast')) return;

        const toast = document.createElement('div');
        toast.id = 'update-toast';
        toast.className = 'toast-notification update-toast';

        const title = document.createElement('div');
        title.style.fontWeight = 'bold';
        title.style.marginBottom = '6px';
        title.textContent = `${I18N.t('update.new_version')} v${data.latest}`;

        const info = document.createElement('div');
        info.style.fontSize = '12px';
        info.style.opacity = '0.85';
        info.style.marginBottom = '8px';
        info.textContent = data.commit_msg || '';

        const btnRow = document.createElement('div');
        btnRow.style.display = 'flex';
        btnRow.style.gap = '8px';
        btnRow.style.alignItems = 'center';

        const applyBtn = document.createElement('button');
        applyBtn.className = 'btn-action btn-update-apply';
        applyBtn.textContent = I18N.t('update.apply');
        applyBtn.style.padding = '4px 12px';
        applyBtn.style.fontSize = '12px';
        applyBtn.style.cursor = 'pointer';

        const dismissBtn = document.createElement('button');
        dismissBtn.className = 'btn-action btn-update-dismiss';
        dismissBtn.textContent = I18N.t('update.dismiss');
        dismissBtn.style.padding = '4px 8px';
        dismissBtn.style.fontSize = '12px';
        dismissBtn.style.background = 'transparent';
        dismissBtn.style.border = '1px solid rgba(255,255,255,0.2)';
        dismissBtn.style.cursor = 'pointer';

        dismissBtn.addEventListener('click', () => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        });

        applyBtn.addEventListener('click', async () => {
            applyBtn.disabled = true;
            applyBtn.textContent = I18N.t('update.in_progress');
            try {
                const resp = await fetch('/api/apply_update', {
                    method: 'POST',
                    headers: {
                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                    }
                });
                const resData = await resp.json();
                if (resData.ok) {
                    if (typeof showNotification === 'function') {
                        showNotification(I18N.t('update.server_restart'), 'info', true);
                    }
                    setTimeout(() => window.location.reload(), 4000);
                } else {
                    applyBtn.disabled = false;
                    applyBtn.textContent = I18N.t('update.apply');
                    if (typeof showNotification === 'function') {
                        showNotification(resData.error || I18N.t('update.failed'), 'error');
                    }
                }
            } catch (err) {
                applyBtn.disabled = false;
                applyBtn.textContent = I18N.t('update.apply');
                if (typeof showNotification === 'function') {
                    showNotification(I18N.t('update.failed'), 'error');
                }
            }
        });

        btnRow.appendChild(applyBtn);
        btnRow.appendChild(dismissBtn);

        toast.appendChild(title);
        if (data.commit_msg) toast.appendChild(info);
        toast.appendChild(btnRow);

        document.body.appendChild(toast);
        setTimeout(() => toast.classList.add('show'), 10);
    }

    window.checkForUpdates = checkForUpdates;
    window.showUpdateToast = showUpdateToast;

    document.addEventListener('DOMContentLoaded', () => {
        const checkLink = document.getElementById('check-update-link');
        if (checkLink) {
            checkLink.addEventListener('click', (e) => {
                e.preventDefault();
                checkForUpdates(true);
            });
        }
        setTimeout(() => checkForUpdates(false), 2500);
    });
})();
