function renderSearchResultsList(results, scrollToTop = false) {
    chatContainer.innerHTML = '';
    if (!results || results.length === 0) {
        chatContainer.innerHTML = `<div class="service-message">${I18N.t('search.no_results')}</div>`;
        return;
    }

    // Sort chronologically (oldest at top, newest at bottom)
    results.sort((a, b) => {
        const da = new Date(a.date).getTime();
        const db = new Date(b.date).getTime();
        if (da !== db) return da - db;
        return (a.message_id || 0) - (b.message_id || 0);
    });

    const fragment = document.createDocumentFragment();
    let searchLastDate = null;

    results.forEach(msg => {
        const safeDate = new Date(msg.date);
        const msgDateStr = safeDate.toLocaleDateString(I18N.locale());
        if (msgDateStr !== searchLastDate) {
            const sep = createDateSeparator(safeDate, false);
            fragment.appendChild(sep);
            searchLastDate = msgDateStr;
        }

        const msgEl = createMessageElement(msg);
        msgEl.classList.add('msg-single');
        const isOut = msgEl.classList.contains('out');
        const isOfficialPost = msgEl.dataset.isPost === '1';
        if (!isOut && !isOfficialPost) {
            msgEl.classList.add('show-avatar');
        }

        fragment.appendChild(msgEl);
    });

    if (typeof renderCustomEmojisInContainer === 'function') {
        renderCustomEmojisInContainer(fragment, allCustomEmoji);
    }

    chatContainer.appendChild(fragment);

    if (typeof adjustMessageFooterWidths === 'function') {
        adjustMessageFooterWidths(chatContainer);
    }

    if (scrollToTop) {
        chatContainer.scrollTop = 0;
    } else {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
}

async function performSearch() {
    const query = searchInput.value.toLowerCase().trim();
    if (query.length < 3) {
        if (chatContainer.dataset.mode === 'search') {
            chatContainer.dataset.mode = 'chat';
            chatContainer.dataset.searchScope = '';
            if (currentChatId) {
                const targetItem = document.querySelector(`.chat-list-item[data-chat-id="${currentChatId}"]`);
                if (targetItem) targetItem.click();
                else chatSelect.dispatchEvent(new Event('change'));
            } else {
                chatSelect.dispatchEvent(new Event('change'));
            }
            if (archiveToggleBtn) archiveToggleBtn.style.display = 'block';
            document.body.classList.remove('search-mode-active');
            datePicker.value = '';
        }
        return;
    }

    document.body.classList.add('search-mode-active');
    datePicker.value = '';
    const chatSearchInput = document.getElementById('chat-search-input');
    if (chatSearchInput) chatSearchInput.value = '';
    const chatCancelBtn = document.getElementById('chat-cancel-search-btn');
    if (chatCancelBtn) chatCancelBtn.style.display = 'none';

    chatContainer.dataset.mode = 'search';
    chatContainer.dataset.searchScope = 'global';
    messageCountDisplay.textContent = I18N.t('search.searching');
    if (archiveToggleBtn) archiveToggleBtn.style.display = 'none';

    const chatHeaderTitle = document.getElementById('chat-header-title');
    const chatHeaderInfo = document.getElementById('chat-header-info');
    if (chatHeaderTitle) {
        chatHeaderTitle.textContent = I18N.t('sidebar.search_results') || 'Search Results';
        chatHeaderTitle.style.display = 'block';
    }
    if (chatHeaderInfo) {
        chatHeaderInfo.style.display = 'none';
    }

    switchView('chat');

    try {
        const response = await fetch(`/api/search?query=${encodeURIComponent(query)}`);
        if (!response.ok) throw new Error(I18N.t('error.search'));
        const results = await response.json();
        messageCountDisplay.textContent = I18N.t('search.found_n', results.length);
        messageCountTotal.style.display = 'none';

        renderSearchResultsList(results);

        if (results.length >= 100) messageCountDisplay.textContent += ` ${I18N.t('search.first_100')}`;
    } catch (error) {
        messageCountDisplay.textContent = I18N.t('error.generic', error.message);
    }
}

async function performChatSearch() {
    const chatSearchInput = document.getElementById('chat-search-input');
    if (!chatSearchInput) return;
    const query = chatSearchInput.value.toLowerCase().trim();

    if (query.length < 2) {
        if (chatContainer.dataset.mode === 'search') {
            chatContainer.dataset.mode = 'chat';
            chatContainer.dataset.searchScope = '';
            document.body.classList.remove('search-mode-active');
            if (currentChatId) {
                const targetItem = document.querySelector(`.chat-list-item[data-chat-id="${currentChatId}"]`);
                if (targetItem) targetItem.click();
                else if (typeof chatSelect !== 'undefined') chatSelect.dispatchEvent(new Event('change'));
            }
        }
        return;
    }

    if (!currentChatId) return;

    document.body.classList.add('search-mode-active');
    if (searchInput) searchInput.value = '';
    if (datePicker) datePicker.value = '';

    chatContainer.dataset.mode = 'search';
    chatContainer.dataset.searchScope = 'chat';
    messageCountDisplay.textContent = I18N.t('search.searching');
    messageCountTotal.style.display = 'none';

    const chatHeaderTitle = document.getElementById('chat-header-title');
    const chatHeaderInfo = document.getElementById('chat-header-info');
    if (chatHeaderTitle) {
        chatHeaderTitle.textContent = I18N.t('sidebar.search_results') || 'Search Results';
        chatHeaderTitle.style.display = 'block';
    }
    if (chatHeaderInfo) {
        chatHeaderInfo.style.display = 'none';
    }

    switchView('chat');

    try {
        const response = await fetch(`/api/search?chat_id=${encodeURIComponent(currentChatId)}&query=${encodeURIComponent(query)}`);
        if (!response.ok) throw new Error(I18N.t('error.search'));
        const results = await response.json();

        messageCountDisplay.textContent = (typeof I18N !== 'undefined' && I18N.has && I18N.has('search.found_chat_n'))
            ? I18N.t('search.found_chat_n', results.length)
            : I18N.t('search.found_n', results.length);
        messageCountTotal.style.display = 'none';

        renderSearchResultsList(results);

        if (results.length >= 100) messageCountDisplay.textContent += ` ${I18N.t('search.first_100')}`;
    } catch (error) {
        messageCountDisplay.textContent = I18N.t('error.generic', error.message);
    }
}
window.performChatSearch = performChatSearch;

async function performDateSearch() {
    const date = datePicker.value;
    if (!date) return;

    document.body.classList.add('search-mode-active');
    if (searchInput) searchInput.value = '';
    const chatSearchInput = document.getElementById('chat-search-input');
    if (chatSearchInput) chatSearchInput.value = '';

    chatContainer.dataset.mode = 'search';
    chatContainer.dataset.searchScope = 'chat';
    messageCountDisplay.textContent = I18N.t('search.date_searching', date);
    switchView('chat');
    chatContainer.innerHTML = '';

    try {
        const chatIdParam = currentChatId ? currentChatId : '';
        const response = await fetch(`/api/search_by_date?chat_id=${chatIdParam}&date=${date}`);
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || I18N.t('error.date_search'));
        }

        const data = await response.json();
        const results = data.messages || [];
        const notification = data.notification;

        if (notification === "too_old") {
            messageCountDisplay.textContent = I18N.t('search.too_old');
        } else if (notification === "too_new") {
            messageCountDisplay.textContent = I18N.t('search.too_new');
        } else if (notification === "empty_day") {
            messageCountDisplay.textContent = I18N.t('search.empty_day', date);
        } else {
            messageCountDisplay.textContent = I18N.t('search.found_date', results.length, date);
        }
        messageCountTotal.style.display = 'none';

        const scrollToTop = (notification === "too_old");
        renderSearchResultsList(results, scrollToTop);

        if (results.length >= 100) messageCountDisplay.textContent += ` ${I18N.t('search.first_100')}`;

    } catch (error) {
        messageCountDisplay.textContent = I18N.t('error.generic', error.message);
    }
}

function switchView(targetView) {
    const legList = legacyChatList || document.getElementById('legacy-chat-list');
    const archSetView = archiveSettingsView || document.getElementById('archive-settings-view');
    const cHeader = centerHeader || document.getElementById('center-header');
    const archCenterView = archiveCenterView || document.getElementById('archive-center-view');
    const cContainerView = chatContainerView || document.getElementById('chat-container');
    const aToggleBtn = archiveToggleBtn || document.getElementById('archive-toggle-btn');
    const sContainerView = statsContainerView || document.getElementById('stats-container');

    const leftSidebar = document.getElementById('left-sidebar');
    const mediaTabBtn = document.querySelector('.r-split-tab[data-target="rs-media"]') || document.querySelector('.r-tab[data-target="rs-media"]');
    const statsTabBtn = document.querySelector('.r-split-tab[data-target="rs-stats"]') || document.querySelector('.r-tab[data-target="rs-stats"]');
    const globalStatsBlock = document.getElementById('global-stats-block');
    const chatStatsBlock = document.getElementById('chat-stats-block');
    const pureDbStatsBlock = document.getElementById('pure-db-stats-block');
    const chatAboutPanel = document.getElementById('chat-about-panel');

    if (targetView === 'archive') {
        if (leftSidebar) leftSidebar.classList.add('archive-active');

        if (aToggleBtn) {
            aToggleBtn.classList.add('active');
            aToggleBtn.textContent = '💬';
        }

        if (legList) legList.style.display = 'none';
        const folderBar = document.getElementById('chat-folders-bar');
        if (folderBar) folderBar.style.display = 'none';
        if (archSetView) archSetView.style.display = 'flex';

        const currentLeftW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 320;
        if (currentLeftW < 220) {
            sessionStorage.setItem('tg_sidebar_was_collapsed', '1');
            document.documentElement.style.setProperty('--sidebar-width', '240px');
            localStorage.setItem('tg_sidebar_left_w', 240);
        }

        if (cHeader) cHeader.style.display = 'none';
        if (loadingStatus) loadingStatus.style.display = 'none';
        if (cContainerView) {
            cContainerView.classList.remove('active-view');
            cContainerView.style.setProperty('display', 'none', 'important');
        }
        if (archCenterView) archCenterView.style.display = 'block';

        // Revert right sidebar to "no chat selected" state
        const rSplitDivider = document.querySelector('.r-split-divider');
        if (mediaTabBtn) mediaTabBtn.style.display = 'none';
        if (rSplitDivider) rSplitDivider.style.display = 'none';
        if (typeof switchRightSidebarTab === 'function') {
            switchRightSidebarTab('stats');
        } else if (statsTabBtn) {
            statsTabBtn.click();
        }
        if (chatAboutPanel) chatAboutPanel.style.display = 'none';
        if (globalStatsBlock) globalStatsBlock.style.display = 'none';
        if (chatStatsBlock) chatStatsBlock.style.display = 'none';
        if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'block';
        if (typeof loadPureDbStats === 'function') loadPureDbStats();

    } else {
        if (leftSidebar) leftSidebar.classList.remove('archive-active');

        if (targetView === 'stats') {
            if (sContainerView) sContainerView.classList.add('active-view');
        } else {
            if (sContainerView) sContainerView.classList.remove('active-view');
        }
        if (aToggleBtn) {
            aToggleBtn.classList.remove('active');
            aToggleBtn.textContent = '📥';
        }

        if (sessionStorage.getItem('tg_sidebar_was_collapsed') === '1') {
            sessionStorage.removeItem('tg_sidebar_was_collapsed');
            document.documentElement.style.setProperty('--sidebar-width', '80px');
            localStorage.setItem('tg_sidebar_left_w', 80);
        } else {
            const currentLeftW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width')) || 320;
            if (currentLeftW > 80 && currentLeftW < 270) {
                document.documentElement.style.setProperty('--sidebar-width', '280px');
                localStorage.setItem('tg_sidebar_left_w', 280);
            }
        }

        if (archSetView) archSetView.style.display = 'none';
        if (legList) legList.style.display = 'block';
        const folderBar = document.getElementById('chat-folders-bar');
        if (folderBar) {
            const isBarOpen = localStorage.getItem('tb_folders_bar_open') === '1';
            folderBar.style.display = isBarOpen ? 'flex' : 'none';
            folderBar.classList.toggle('open', isBarOpen);
        }

        if (archCenterView) archCenterView.style.display = 'none';
        if (cHeader) cHeader.style.display = 'flex';
        if (loadingStatus) loadingStatus.style.display = 'flex';
        if (cContainerView) {
            cContainerView.classList.add('active-view');
            cContainerView.style.setProperty('display', 'flex', 'important');
        }

        const rSplitDivider = document.querySelector('.r-split-divider');
        if (globalStatsBlock) globalStatsBlock.style.display = 'none';
        if (window.currentChatId) {
            if (mediaTabBtn) mediaTabBtn.style.display = '';
            if (rSplitDivider) rSplitDivider.style.display = '';
            if (chatAboutPanel && chatAboutPanel.dataset.hasAbout === '1') chatAboutPanel.style.display = 'block';
            if (chatStatsBlock) chatStatsBlock.style.display = 'block';
            if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'none';
            const savedTab = localStorage.getItem('sidebar_active_tab') || 'stats';
            if (typeof switchRightSidebarTab === 'function') {
                switchRightSidebarTab(savedTab === 'media' ? 'media' : 'stats');
            }
        } else {
            if (mediaTabBtn) mediaTabBtn.style.display = 'none';
            if (rSplitDivider) rSplitDivider.style.display = 'none';
            if (chatAboutPanel) chatAboutPanel.style.display = 'none';
            if (chatStatsBlock) chatStatsBlock.style.display = 'none';
            if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'block';
            if (typeof switchRightSidebarTab === 'function') {
                switchRightSidebarTab('stats');
            }
            if (typeof loadPureDbStats === 'function') loadPureDbStats();
        }
    }
}
