// static/app.js — Core Chat Viewer, Message Renderer, Search, Sidebar & Media Gallery

let ChatManage;

let loadSidebarChats;

let archiveSettingsView, archiveCenterView, legacyChatList, centerHeader;

let searchTimeout;

let isDragging = false;

let offset = { x: 0, y: 0 };

let currentComplexCheckbox = null;

let selectedChatIds = [];

let archiveEventSource = null;

let archiveStatusCache = {};

let liveLogBuffer = [];

let liveLogAnimFrame = null;

let currentMediaType = 'photos';

let currentMediaPage = 1; // Текущая страница пагинации

let _pendingChatId = null;

let _pendingChatName = '';

let scrollFab = null;

let scrollFabBadge = null;

let unreadCount = 0;



// allUsers managed globally via window.AppState.allUsers / window.allUsers

let allCustomEmoji = {}; window.allCustomEmoji = allCustomEmoji;

let myUserId = localStorage.getItem('myUserId');



// currentChatId managed via window.AppState

let currentChatBaseName = '';



let oldestPageLoaded = 1;

let newestPageLoaded = 1;

let isLoading = false;

let noMoreOlderMessages = false;

let noMoreNewerMessages = false;

let isChatLoading = false;

const activePageFetches = new Set();

window.activePageFetches = activePageFetches;



// Переменные для статистики

let activityChartInstance = null;

let allChatUsers = [];

let totalMessagesInChat = 0;

let renderedMessagesCount = 0;



// --- Константы (DOM Элементы) ---

let chatContainer;

let chatSelect;

let searchInput;

let loadingStatus;

let chatAvatar;



let playerModal;

let modalVideo;

let modalAudio;



// Хак: создаем элементы-пустышки, если оригиналы были удалены в новом макете

let statsToggleBtn;

let chatContainerView;

let statsContainerView;

let globalStatsList;

let currentChatStatsView;

let statsPlaceholder;

let topUsersList;

let showAllUsersBtn;

let activityChartCanvas;

let messageCountDisplay;

let messageCountTotal;



let datePicker;

let cancelSearchBtn;



let archiveToggleBtn;

let dialogsList;

let startArchiveBtn;



let lastRenderedDate = null;

let lastSenderId = null;

let lastBatchHadMedia = false;

let scrollAfterLoad = null;

let lastPreviewStrings = {}; // Глобальный объект для трекинга последних логов



let forceScrollToBottom = false;



document.addEventListener('DOMContentLoaded', () => {

    // --- Глобальные переменные ---

    scrollFab = document.getElementById('scroll-fab');

    scrollFabBadge = document.getElementById('scroll-fab-badge');

    unreadCount = 0;



    chatContainer = document.getElementById('chat-container');

    let lastContainerWidth = 0;

    let containerResizeRaf = null;



    const handleContainerResize = () => {

        if (!chatContainer) return;

        const curWidth = chatContainer.clientWidth;

        const curHeight = chatContainer.clientHeight;

        chatContainer.style.setProperty('--chat-container-height', `${curHeight}px`);

        chatContainer.style.setProperty('--chat-container-width', `${curWidth}px`);



        if (curWidth !== lastContainerWidth) {

            lastContainerWidth = curWidth;

            if (containerResizeRaf) cancelAnimationFrame(containerResizeRaf);

            containerResizeRaf = requestAnimationFrame(() => {

                containerResizeRaf = null;

                if (typeof adjustMessageFooterWidths === 'function') {

                    adjustMessageFooterWidths(chatContainer);

                }

            });

        }

    };



    if (chatContainer && window.ResizeObserver) {

        handleContainerResize();

        new ResizeObserver(handleContainerResize).observe(chatContainer);

    }

    chatSelect = document.getElementById('chat-select');

    searchInput = document.getElementById('search-input');

    loadingStatus = document.getElementById('loading-status-bar');

    chatAvatar = document.getElementById('chat-avatar');

    playerModal = document.getElementById('player-modal');

    modalVideo = document.getElementById('modal-video');

    modalAudio = document.getElementById('modal-audio');

    statsToggleBtn = document.getElementById('stats-toggle-btn') || document.createElement('button');

    chatContainerView = document.getElementById('chat-container');

    statsContainerView = document.getElementById('stats-container') || document.createElement('div');

    globalStatsList = document.getElementById('global-stats-list');

    currentChatStatsView = document.getElementById('current-chat-stats');

    statsPlaceholder = document.getElementById('stats-placeholder');

    topUsersList = document.getElementById('top-users-list');

    showAllUsersBtn = document.getElementById('show-all-users-btn');

    activityChartCanvas = document.getElementById('activity-chart');

    messageCountDisplay = document.getElementById('message-count-display');

    messageCountTotal = document.getElementById('message-count-total');

    datePicker = document.getElementById('date-picker');

    cancelSearchBtn = document.getElementById('cancel-search-btn');

    archiveToggleBtn = document.getElementById('archive-toggle-btn');

    dialogsList = document.getElementById('dialogs-list');

    startArchiveBtn = document.getElementById('start-archive-btn');



    legacyChatList = document.getElementById('legacy-chat-list');

    archiveSettingsView = document.getElementById('archive-settings-view');

    centerHeader = document.getElementById('center-header');

    archiveCenterView = document.getElementById('archive-center-view');



    const chatRefreshBtn = document.getElementById('chat-refresh-btn');

    if (chatRefreshBtn) {

        chatRefreshBtn.addEventListener('click', async () => {

            if (!currentChatId) return;

            const thisChatId = currentChatId;

            chatRefreshBtn.classList.add('spin');

            const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

            try {

                // Instant pre-flight check to see if Telegram has new messages

                try {

                    const checkRes = await fetch(`/api/check_chat_updates?chat_id=${thisChatId}`);

                    if (checkRes.ok) {

                        const checkData = await checkRes.json();

                        if (!checkData.has_updates || checkData.new_count <= 0) {

                            chatRefreshBtn.classList.remove('spin');

                            if (typeof notifyFn === 'function') {

                                notifyFn('Chat is already up to date', 'info');

                            }

                            return;

                        }

                    }

                } catch (e_chk) {

                    console.debug('[chatRefreshBtn] pre-check error', e_chk);

                }



                const res = await fetch('/api/sync_chat_delta', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                    },

                    body: JSON.stringify({ chat_id: thisChatId })

                });

                const data = await res.json();

                if (data.ok && data.already_synced) {

                    chatRefreshBtn.classList.remove('spin');

                    if (typeof notifyFn === 'function') {

                        notifyFn('Chat is already up to date', 'info');

                    }

                    return;

                }

                if (data.ok) {

                    const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

                    const syncToast = (typeof notifyFn === 'function')

                        ? notifyFn('Syncing chat messages...', 'info', true)

                        : null;

                    let attempts = 0;

                    const pollInterval = setInterval(async () => {

                        attempts++;

                        try {

                            const stRes = await fetch('/api/archive_status');

                            const st = await stRes.json();

                            const chatSt = st[currentChatId];

                            const isDone = !chatSt || chatSt.status === 'done' || chatSt.status === 'error';

                            if (isDone || attempts > 60) {

                                clearInterval(pollInterval);

                                chatRefreshBtn.classList.remove('spin');

                                if (window.reloadCurrentChatMessages) {

                                    await window.reloadCurrentChatMessages(currentChatId);

                                }

                                if (syncToast && typeof syncToast.success === 'function') {

                                    syncToast.success('Chat refreshed!');

                                } else if (typeof notifyFn === 'function') {

                                    notifyFn('Chat refreshed!', 'success');

                                }

                            }

                        } catch {

                            clearInterval(pollInterval);

                            chatRefreshBtn.classList.remove('spin');

                            if (syncToast && typeof syncToast.error === 'function') {

                                syncToast.error('Chat sync failed');

                            }

                        }

                    }, 1000);

                } else {

                    chatRefreshBtn.classList.remove('spin');

                    const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

                    if (typeof notifyFn === 'function') {

                        notifyFn(data.error || 'Failed to start sync', 'error');

                    } else {

                        alert(data.error || 'Failed to start sync');

                    }

                }

            } catch (err) {

                chatRefreshBtn.classList.remove('spin');

                alert(`Error: ${err.message}`);

            }

        });

    }



    function pinScrollToBottom(durationMs = 800) {

        forceScrollToBottom = true;

        chatContainer.scrollTop = chatContainer.scrollHeight;



        let pinTimer = null;



        const cleanup = () => {

            forceScrollToBottom = false;

            if (pinTimer) clearTimeout(pinTimer);

            chatContainer.removeEventListener('wheel', onUserInterrupt);

            chatContainer.removeEventListener('touchstart', onUserInterrupt);

        };



        const onUserInterrupt = (e) => {

            if (e.deltaY < 0 || e.type === 'touchstart') {

                cleanup();

            }

        };



        chatContainer.addEventListener('wheel', onUserInterrupt, { passive: true });

        chatContainer.addEventListener('touchstart', onUserInterrupt, { passive: true });



        pinTimer = setTimeout(cleanup, durationMs);

    }



    chatContainer.addEventListener('load', (e) => {

        if (e.target && (e.target.tagName === 'IMG' || e.target.tagName === 'VIDEO')) {

            const msgEl = e.target.closest('.message, .date-separator');

            if (msgEl && window.VirtualChat && window.VirtualChat.items) {

                const item = window.VirtualChat.items.find(it => it.el === msgEl);

                if (item && msgEl.offsetHeight > 0) {

                    item.height = msgEl.offsetHeight;

                }

            }

            if (forceScrollToBottom) {

                chatContainer.scrollTop = chatContainer.scrollHeight;

            }

        }

    }, true);

    // Снятие текстовых спойлеров

    document.addEventListener('click', (e) => {

        if (e.target.classList.contains('spoiler')) {

            e.target.classList.toggle('revealed');

        }

    });



    // --- 1. initialisation ---



    window.chatListData = []; // Глобальный массив для хранения списка чатов

    window.chatIdMap = new Map(); // O(1) lookup index: String(chat_id) -> chat object

    // Helper: O(1) chat lookup by ID (falls back to .find for exotic ID formats)

    window.getChatById = function(chatId) {

        if (!chatId) return null;

        return window.chatIdMap.get(String(chatId)) || null;

    };



    window.updateChatRangeText = function(mode, days, start, end) {

        const el = document.getElementById('chat-range-info-text');

        if (!el) return;

        const m = mode || 'all';

        if (m === 'latest_days') {

            el.textContent = `⏳ Range: Latest ${days || 30} days`;

        } else if (m === 'oldest_days') {

            el.textContent = `⏳ Range: Oldest ${days || 30} days`;

        } else if (m === 'custom_range') {

            el.textContent = `⏳ Range: ${start || '...'} → ${end || '...'}`;

        } else {

            el.textContent = `⏳ Range: All messages`;

        }

    };



    async function selectAndLoadChat(chat, liElement) {

        document.querySelectorAll('.chat-list-item').forEach(el => el.classList.remove('active'));

        if (liElement) liElement.classList.add('active');



        currentChatId = chat.chat_id; window.currentChatId = currentChatId;

        currentChatBaseName = chat.safe_folder_name;

        const avatarPath = chat.chat_avatar_path;

        const chatName = chat.chat_name;



        isChatLoading = true;

        if (messageCountDisplay) messageCountDisplay.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.loading') : 'Loading...';



        const headerTitle = document.getElementById('chat-header-title');

        const chatHeaderInfo = document.getElementById('chat-header-info');

        if (headerTitle) headerTitle.style.display = 'none';

        if (chatHeaderInfo) chatHeaderInfo.style.display = 'flex';



        const chatHeaderName = document.getElementById('chat-header-name');

        if (chatHeaderName) {

            chatHeaderName.textContent = chatName;

            if (chat.emoji_status_document_id && window.EmojiEngine) {

                const badge = window.EmojiEngine.renderStatusBadge(chat.emoji_status_document_id);

                chatHeaderName.innerHTML = `${escapeHTML(chatName)} ${badge}`;

            }

        }



        if (typeof updateChatCutoffVisuals === 'function') {

            updateChatCutoffVisuals(chat);

        }



        const csBtn = document.getElementById('chat-settings-header-btn');
        if (csBtn) csBtn.style.display = 'inline-block';

        const exportHeaderBtn = document.getElementById('chat-header-export-btn');
        if (exportHeaderBtn) exportHeaderBtn.style.display = 'inline-block';

        const isDmOrBot = (chat.chat_type === 'user' || chat.chat_type === 'bot' || Number(chat.chat_id) > 0);
        const headerBadge = document.getElementById('chat-header-participants-badge');
        const churnWidget = document.getElementById('subscribers-churn-widget');
        if (isDmOrBot) {
            if (headerBadge) headerBadge.style.display = 'none';
            if (churnWidget) churnWidget.style.display = 'none';
        } else if (window.ParticipantsManager) {
            window.ParticipantsManager.updateHeaderBadge(chat.chat_id, chat.participants_count);
        }





        const refreshBtn = document.getElementById('chat-refresh-btn');

        if (refreshBtn) refreshBtn.style.display = 'inline-block';



        const searchToggleBtn = document.getElementById('chat-search-toggle-btn');

        if (searchToggleBtn) searchToggleBtn.style.display = 'inline-block';



        const chatSearchWrap = document.getElementById('chat-search-wrap');

        if (chatSearchWrap) chatSearchWrap.style.display = 'none';



        const chatSearchInput = document.getElementById('chat-search-input');

        if (chatSearchInput) chatSearchInput.value = '';



        const chatCancelSearchBtn = document.getElementById('chat-cancel-search-btn');

        if (chatCancelSearchBtn) chatCancelSearchBtn.style.display = 'none';



        const datePickerEl = document.getElementById('date-picker');

        if (datePickerEl) {

            datePickerEl.style.display = 'inline-block';

            datePickerEl.value = '';

        }



        // Auto-check for new messages in this chat

        (async () => {

            const thisChatId = chat.chat_id;

            try {

                const checkRes = await fetch(`/api/check_chat_updates?chat_id=${thisChatId}`);

                if (!checkRes.ok) return;

                const checkData = await checkRes.json();

                if (checkData.has_updates && checkData.new_count > 0 && String(window.currentChatId) === String(thisChatId)) {

                    const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

                    const syncToast = (typeof notifyFn === 'function')

                        ? notifyFn(`🔔 Found ${checkData.new_count} new messages. Syncing...`, 'info', true)

                        : null;

                    if (refreshBtn) refreshBtn.classList.add('spin');

                    await fetch('/api/sync_chat_delta', {

                        method: 'POST',

                        headers: {

                            'Content-Type': 'application/json',

                            'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                        },

                        body: JSON.stringify({ chat_id: thisChatId })

                    });

                    let attempts = 0;

                    const pollInterval = setInterval(async () => {

                        attempts++;

                        try {

                            const stRes = await fetch('/api/archive_status');

                            const st = await stRes.json();

                            const chatSt = st[thisChatId];

                            const isDone = !chatSt || chatSt.status === 'done' || chatSt.status === 'error';

                            if (isDone || attempts > 60) {

                                clearInterval(pollInterval);

                                if (refreshBtn) refreshBtn.classList.remove('spin');

                                if (String(window.currentChatId) === String(thisChatId)) {

                                    if (window.reloadCurrentChatMessages) {

                                        await window.reloadCurrentChatMessages(thisChatId);

                                    }

                                    if (syncToast && typeof syncToast.success === 'function') {

                                        syncToast.success('✅ Chat updated!');

                                    } else if (typeof notifyFn === 'function') {

                                        notifyFn('✅ Chat updated!', 'success');

                                    }

                                } else {

                                    if (syncToast && typeof syncToast.close === 'function') {

                                        syncToast.close();

                                    }

                                }

                            }

                        } catch {

                            clearInterval(pollInterval);

                            if (refreshBtn) refreshBtn.classList.remove('spin');

                            if (syncToast && typeof syncToast.error === 'function') {

                                syncToast.error('Chat sync failed');

                            }

                        }

                    }, 1000);

                }

            } catch (err) {

                console.debug('Background chat check error:', err);

            }

        })();



        const adminLogBtn = document.getElementById('chat-admin-log-btn');

        if (adminLogBtn) {

            adminLogBtn.style.display = (chat.chat_type === 'channel' || chat.chat_type === 'group' || chat.chat_type === 'supergroup') ? 'inline-block' : 'none';

        }



        if (typeof window.updateChatRangeText === 'function') {

            window.updateChatRangeText(chat.archive_range_mode, chat.archive_range_days, chat.archive_range_start, chat.archive_range_end);

        }

        if (typeof window.updateCurrentChatActiveTaskBanner === 'function') {

            window.updateCurrentChatActiveTaskBanner();

        }



        // Custom Chat Wallpaper

        if (chat.wallpaper_path && !chat.wallpaper_path.startsWith('{') && !chat.wallpaper_path.startsWith('[')) {

            const wpUrl = `/data/${chat.wallpaper_path.replace(/\\/g, '/')}`;

            chatContainer.style.backgroundImage = `url('${wpUrl}')`;

            chatContainer.style.backgroundSize = 'cover';

            chatContainer.style.backgroundPosition = 'center';

            chatContainer.style.backgroundAttachment = 'fixed';

        } else {

            chatContainer.style.backgroundImage = '';

            chatContainer.style.backgroundSize = '';

            chatContainer.style.backgroundPosition = '';

            chatContainer.style.backgroundAttachment = '';

        }



        // Sticky Pinned Message Bar

        const pinnedBar = document.getElementById('sticky-pinned-bar');

        if (pinnedBar) {

            if (chat.pinned_msg_ids && chat.pinned_msg_ids.length > 0) {

                window.PinnedEngine = {

                    ids: chat.pinned_msg_ids,

                    currentIndex: 0,

                    scrollToCurrentPin() {

                        const id = this.ids[this.currentIndex];

                        if (typeof scrollToMessage === 'function') scrollToMessage(Number(id));

                    },

                    nextPin() {

                        this.currentIndex = (this.currentIndex + 1) % this.ids.length;

                        this.updateSnippet();

                        this.scrollToCurrentPin();

                    },

                    updateSnippet() {

                        const snip = document.getElementById('pinned-bar-snippet');

                        if (snip) snip.textContent = `Pin ${this.currentIndex + 1} of ${this.ids.length} (#${this.ids[this.currentIndex]})`;

                    }

                };

                pinnedBar.style.display = 'flex';

                window.PinnedEngine.updateSnippet();

            } else {

                pinnedBar.style.display = 'none';

            }

        }



        if (chatAvatar) {

            chatAvatar.onerror = function () { this.style.display = 'none'; };

            if (avatarPath && avatarPath !== "None") {

                chatAvatar.src = encodeURI('/data/' + avatarPath.replace(/\\/g, '/')).replace(/'/g, "%27");

                chatAvatar.style.display = 'block';

            } else { chatAvatar.style.display = 'none'; }

        }



        if (window.VirtualChat) window.VirtualChat.reset();

        activePageFetches.clear();

        chatContainer.innerHTML = '';

        allUsers = {};

        if (window.AppState) {

            window.AppState.allUsers = {};

            if (window.AppState.allCustomEmoji) window.AppState.allCustomEmoji = {};

        }

        if (window.clearEditHistoryData) window.clearEditHistoryData();

        oldestPageLoaded = 1; newestPageLoaded = 1;

        lastRenderedDate = null; noMoreOlderMessages = false; noMoreNewerMessages = false;

        lastSenderId = null; lastBatchHadMedia = false;

        renderedMessagesCount = 0; totalMessagesInChat = 0;

        searchInput.value = '';

        chatContainer.dataset.mode = 'chat';

        chatContainer.style.opacity = '0';

        chatContainer.style.visibility = 'hidden';



        const currentChatStatsView = document.getElementById('current-chat-stats') || document.getElementById('chat-stats-block');

        const statsPlaceholder = document.getElementById('stats-placeholder');

        const topUsersList = document.getElementById('top-users-list');

        const showAllUsersBtn = document.getElementById('show-all-users-btn');



        if (statsPlaceholder) statsPlaceholder.style.display = 'none';

        if (currentChatStatsView) currentChatStatsView.style.display = 'block';



        const globalStatsBlock = document.getElementById('global-stats-block');

        const pureDbStatsBlock = document.getElementById('pure-db-stats-block');

        const chatStatsBlock = document.getElementById('chat-stats-block');

        const localStatsList = document.getElementById('local-stats-list');

        const topUsersBlock = document.getElementById('top-users-block');



        if (globalStatsBlock) globalStatsBlock.style.display = 'none';

        if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'none';

        if (chatStatsBlock) chatStatsBlock.style.display = 'block';



        // Always show the Media tab button and divider when a chat is selected

        const rsTabMediaBtn = document.getElementById('rs-tab-media');

        const rSplitDivider = document.querySelector('.r-split-divider');

        if (rsTabMediaBtn) rsTabMediaBtn.style.display = '';

        if (rSplitDivider) rSplitDivider.style.display = '';



        const mediaNoChatView = document.getElementById('media-no-chat-view');

        if (mediaNoChatView) mediaNoChatView.style.display = 'none';

        const mediaCategoriesView = document.getElementById('media-categories-view');

        if (mediaCategoriesView) mediaCategoriesView.style.display = 'flex';

        const mediaDetailView = document.getElementById('media-detail-view');

        if (mediaDetailView) mediaDetailView.style.display = 'none';



        const savedTab = localStorage.getItem('sidebar_active_tab') || 'stats';

        if (typeof switchRightSidebarTab === 'function') {

            switchRightSidebarTab(savedTab === 'media' ? 'media' : 'stats');

        }



        const currentChatData = getChatById(currentChatId) || {};

        window.currentChatData = currentChatData;

        const isChannel = currentChatData.chat_type === 'channel';

        const isDm = Boolean(currentChatData.chat_type === 'user' || currentChatData.chat_type === 'bot' || Number(currentChatId) > 0);

        if (chatContainer) {

            chatContainer.classList.toggle('is-pm', isDm);

        }



        if (topUsersBlock) {

            topUsersBlock.style.display = isChannel ? 'none' : 'block';

        }



        if (localStatsList) localStatsList.innerHTML = '<li>Loading...</li>';



        if (typeof loadMediaCategories === 'function') {

            loadMediaCategories();

        }



        if (topUsersList) topUsersList.innerHTML = '<li>Loading...</li>';

        if (showAllUsersBtn) showAllUsersBtn.style.display = 'none';

        if (activityChartInstance) activityChartInstance.destroy();



        if (messageCountDisplay) messageCountDisplay.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.loading_messages') : 'Loading messages...';



        if (!scrollAfterLoad) {

            if (window.VirtualChat) window.VirtualChat.isSuspended = true;

        }



        // Parallelize: load messages concurrently with stats and users

        const messagesPromise = scrollAfterLoad ? null : loadMessages(currentChatId, 1, false);



        try {

            const [userRes, chatStatsRes, topUsersRes, activityRes] = await Promise.all([

                fetch(`/api/users?chat_id=${currentChatId}`),

                fetch(`/api/stats/chat?chat_id=${currentChatId}`),

                fetch(`/api/stats/top_users?chat_id=${currentChatId}`),

                fetch(`/api/stats/activity?chat_id=${currentChatId}`)

            ]);



            if (!userRes.ok) throw new Error('Failed to load users');

            const fetchedUsers = await userRes.json();

            Object.assign(allUsers, fetchedUsers);

            if (window.AppState) Object.assign(window.AppState.allUsers, fetchedUsers);



            chatContainer.scrollTop = 1;



            const chatStats = await chatStatsRes.json();

            if (chatStats && !chatStats.error && chatStats.total_messages !== undefined) {

                totalMessagesInChat = chatStats.total_messages;

                window.currentChatStats = chatStats;

                renderLocalStatsList(chatStats);



                if (messageCountTotal) {

                    const formattedTotal = totalMessagesInChat.toLocaleString((typeof I18N !== 'undefined') ? I18N.locale() : 'en-US');

                    messageCountTotal.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.of_total', formattedTotal) : `of ${formattedTotal}`;

                    messageCountTotal.style.display = 'inline';

                }

                updateRenderedMessageCount();

            } else {

                totalMessagesInChat = 0;

                window.currentChatStats = null;

                renderLocalStatsList(null);

            }

            renderChatAboutPanel(currentChatData);



            const topUsers = await topUsersRes.json();

            allChatUsers = topUsers;

            window.allChatUsers = topUsers;

            renderTopUsersList(topUsers.slice(0, 5), true);

            if (allChatUsers.length > 5 && showAllUsersBtn) showAllUsersBtn.style.display = 'block';



            renderActivityChart(await activityRes.json());



            if (scrollAfterLoad) {

                const targetId = scrollAfterLoad;

                scrollAfterLoad = null;

                await scrollToMessage(targetId);

                chatContainer.style.visibility = 'visible';

                chatContainer.style.opacity = '1.0';

            } else {

                await messagesPromise;

                chatContainer.scrollTop = chatContainer.scrollHeight;



                requestAnimationFrame(() => {

                    if (window.VirtualChat && window.VirtualChat.items) {

                        window.VirtualChat.items.forEach(item => {

                            if (item.el.isConnected && item.el.offsetHeight > 0) {

                                item.height = item.el.offsetHeight;

                            }

                        });

                        window.VirtualChat.isSuspended = false;

                        window.VirtualChat.update();

                    }

                    chatContainer.scrollTop = chatContainer.scrollHeight;



                    requestAnimationFrame(() => {

                        chatContainer.scrollTop = chatContainer.scrollHeight;

                        pinScrollToBottom(800);

                        chatContainer.style.visibility = 'visible';

                        chatContainer.style.opacity = '1.0';

                        adjustMessageFooterWidths(chatContainer);

                        isChatLoading = false;

                    });

                });

            }



        } catch (error) {

            console.error('Error loading chat:', error);

            if (messageCountDisplay) messageCountDisplay.textContent = `Error: ${error.message}`;

            chatContainer.style.visibility = 'visible';

            chatContainer.style.opacity = '1.0';

            isChatLoading = false; scrollAfterLoad = null;

        }

    }



    loadSidebarChats = async function () {

        const legacyContainer = document.getElementById('legacy-chat-list');

        legacyContainer.style.padding = '0';

        legacyContainer.innerHTML = '<ul id="custom-chat-list"></ul>';

        const customChatList = document.getElementById('custom-chat-list');



        try {

            const response = await fetch('/api/chats');

            if (!response.ok) throw new Error(I18N.t('error.load_chats'));

            chatListData = await response.json();



            // Build O(1) lookup map

            window.chatIdMap = new Map();

            chatListData.forEach(c => window.chatIdMap.set(String(c.chat_id), c));



            // Find all linked child chat IDs whose parent channel is present in chatListData

            const childChatIds = new Set();

            const parentToChildMap = {};



            chatListData.forEach(c => {

                if (c.linked_chat_id) {

                    const linkedNorm = String(Number(c.linked_chat_id) < 0 ? c.linked_chat_id : -1000000000000 - Number(c.linked_chat_id));

                    const target = getChatById(linkedNorm);

                    if (target) {

                        const cId = String(c.chat_id);

                        if (c.chat_type === 'channel' && target.chat_type !== 'channel') {

                            childChatIds.add(linkedNorm);

                            parentToChildMap[cId] = target;

                        } else if (target.chat_type === 'channel' && c.chat_type !== 'channel') {

                            childChatIds.add(cId);

                            parentToChildMap[linkedNorm] = c;

                        } else if (!childChatIds.has(cId)) {

                            childChatIds.add(linkedNorm);

                            parentToChildMap[cId] = target;

                        }

                    }

                }

            });



            window.childChatIds = childChatIds;

            window.parentToChildMap = parentToChildMap;



            // Filter top-level chats: chats that are not children of an existing parent channel in chatListData

            const topLevelChats = chatListData.filter(c => !childChatIds.has(String(c.chat_id)));



            topLevelChats.forEach(chat => {

                const li = document.createElement('li');

                li.className = 'chat-list-item';

                li.dataset.chatId = chat.chat_id;
                li.dataset.chatType = (chat.chat_type || 'user').toLowerCase();



                const avatarUrl = (chat.chat_avatar_path && chat.chat_avatar_path !== "None")

                    ? encodeURI('/data/' + chat.chat_avatar_path.replace(/\\/g, '/')).replace(/'/g, "%27")

                    : '';



                const chatName = chat.chat_name || 'Unknown Chat';

                const avatarHtml = avatarUrl

                    ? `<img src="${avatarUrl}" class="chat-list-avatar" loading="lazy" onerror="this.style.display='none'">`

                    : `<div class="chat-list-avatar-placeholder">${escapeHTML(chatName[0] || '?')}</div>`;



                let chatBadgeHtml = '';

                if (chat.chat_type === 'group' && chat.linked_chat_id) {

                    chatBadgeHtml = `<span class="discussion-badge" title="Discussion Group">💬</span>`;

                } else if (chat.linked_chat_id) {

                    chatBadgeHtml = `<span class="channel-linked-badge" title="Has Discussion Group">💬</span>`;

                }

                if (chat.backup_status === 'deleted') {

                    chatBadgeHtml += `<span class="chat-item-badge deleted" style="background:#ef4444; color:#fff; font-size:11px; padding:2px 5px; border-radius:4px;" title="Account / Chat permanently deleted">🚫 Deleted</span>`;

                } else if (chat.backup_status === 'inaccessible') {

                    chatBadgeHtml += `<span class="chat-item-badge inaccessible" style="background:#f59e0b; color:#fff; font-size:11px; padding:2px 5px; border-radius:4px; cursor:pointer;" onclick="event.stopPropagation(); window.recheckChatAccess('${chat.chat_id}');" title="Temporarily blocked or private. Click to check access.">🔒 Inaccessible</span>`;

                } else if (chat.backup_status === 'halted' || chat.has_older_gap) {

                    const haltedTitle = window.I18N ? window.I18N.t('badge.halted_chat_title') : 'Partial / Incomplete Archive';

                    const haltedText = window.I18N ? window.I18N.t('badge.halted') : 'Halted';

                    chatBadgeHtml += `<span class="chat-item-badge halted" title="${escapeHTML(haltedTitle)}">${escapeHTML(haltedText)}</span>`;

                }



                li.innerHTML = `

                    ${avatarHtml}

                    <div class="chat-list-info">

                        <div class="chat-list-name" style="display:flex; align-items:center; gap: 4px;">

                            ${escapeHTML(chatName)} ${chatBadgeHtml}

                        </div>

                    </div>

                `;



                // Check if this top-level chat has a linked discussion group in DB

                let linkedChildChat = parentToChildMap[String(chat.chat_id)];



                let nestedUl = null;

                if (linkedChildChat) {

                    nestedUl = document.createElement('ul');

                    nestedUl.className = 'nested-chat-list';

                    nestedUl.style.display = 'none';



                    const childLi = document.createElement('li');

                    childLi.className = 'chat-list-item nested-chat-item';

                    childLi.dataset.chatId = linkedChildChat.chat_id;



                    const cAvatarUrl = (linkedChildChat.chat_avatar_path && linkedChildChat.chat_avatar_path !== "None")

                        ? encodeURI('/data/' + linkedChildChat.chat_avatar_path.replace(/\\/g, '/')).replace(/'/g, "%27")

                        : '';

                    const cAvatarHtml = cAvatarUrl

                        ? `<img src="${cAvatarUrl}" class="chat-list-avatar" loading="lazy" onerror="this.style.display='none'">`

                        : `<div class="chat-list-avatar-placeholder">${escapeHTML((linkedChildChat.chat_name || '?')[0])}</div>`;



                    let childBadgeHtml = '';

                    if (linkedChildChat.backup_status === 'deleted') {

                        childBadgeHtml = `<span class="chat-item-badge deleted" style="background:#ef4444; color:#fff; font-size:11px; padding:2px 5px; border-radius:4px;" title="Account / Chat permanently deleted">🚫 Deleted</span>`;

                    } else if (linkedChildChat.backup_status === 'inaccessible') {

                        childBadgeHtml = `<span class="chat-item-badge inaccessible" style="background:#f59e0b; color:#fff; font-size:11px; padding:2px 5px; border-radius:4px; cursor:pointer;" onclick="event.stopPropagation(); window.recheckChatAccess('${linkedChildChat.chat_id}');" title="Temporarily blocked or private. Click to check access.">🔒 Inaccessible</span>`;

                    } else if (linkedChildChat.backup_status === 'halted' || linkedChildChat.has_older_gap) {

                        const childHaltedTitle = window.I18N ? window.I18N.t('badge.halted_chat_title') : 'Partial / Incomplete Archive';

                        const childHaltedText = window.I18N ? window.I18N.t('badge.halted') : 'Halted';

                        childBadgeHtml = `<span class="chat-item-badge halted" title="${escapeHTML(childHaltedTitle)}">${escapeHTML(childHaltedText)}</span>`;

                    }



                    childLi.innerHTML = `

                        <span class="nested-tree-icon">↳</span>

                        ${cAvatarHtml}

                        <div class="chat-list-info">

                            <div class="chat-list-name" style="display:flex; align-items:center; gap: 4px;">

                                💬 ${escapeHTML(linkedChildChat.chat_name || 'Discussion')} ${childBadgeHtml}

                            </div>

                        </div>

                    `;



                    childLi.addEventListener('click', (e) => {

                        e.stopPropagation();

                        selectAndLoadChat(linkedChildChat, childLi);

                    });



                    nestedUl.appendChild(childLi);

                }



                li.addEventListener('click', async () => {

                    // Accordion behavior: collapse all nested lists, expand this one if present

                    document.querySelectorAll('.nested-chat-list').forEach(ul => {

                        if (ul !== nestedUl) ul.style.display = 'none';

                    });

                    if (nestedUl) nestedUl.style.display = 'block';



                    await selectAndLoadChat(chat, li);

                });



                customChatList.appendChild(li);

                if (nestedUl) customChatList.appendChild(nestedUl);

            });

            if (currentChatId) {

                const activeLi = customChatList.querySelector(`li[data-chat-id="${currentChatId}"]`);

                if (activeLi) activeLi.classList.add('active');

            } else if (messageCountDisplay) {

                messageCountDisplay.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.ready') : 'Ready. Select a chat on the left.';

            }

            if (typeof window.loadFolders === 'function') {
                window.loadFolders();
            } else if (typeof window.filterChatList === 'function') {
                window.filterChatList();
            }

        } catch (error) {

            console.error('Error initialising chats:', error);

            if (messageCountDisplay) messageCountDisplay.textContent = (typeof I18N !== 'undefined') ? I18N.t('error.generic', 'Failed to load chat index') : 'Error loading chat index!';

        }

    }



    async function init() {

        // ── UI System ──

        I18N.init();

        I18N.applyAll();

        ThemeEngine.init();

        SidebarResizer.init();

        BgEngine.init(); // async, loads in background

        if (!localStorage.getItem('tutorial_seen')) {

            setTimeout(() => window.FAQModal.open(), 500);

        }

        if (!myUserId) {

            myUserId = prompt(I18N.t('prompt.enter_user_id'));

            if (myUserId) localStorage.setItem('myUserId', myUserId);

        }



        // Establish initial UI state: show chat view, hide archive

        switchView('chat');



        await loadSidebarChats();



        async function updateGlobalStats() {

            try {

                const statsResponse = await fetch('/api/stats/global');

                if (!statsResponse.ok) throw new Error(I18N.t('error.global_stats'));

                const globalStats = await statsResponse.json();

                renderGlobalStats(globalStats);

            } catch (e) {

                console.error("Error updating global stats:", e);

                const globalStatsList = document.getElementById('global-stats-list');

                if (globalStatsList) globalStatsList.innerHTML = `<li>${I18N.t('error.global_stats')}</li>`;

            }

        }



        window.updateGlobalStats = updateGlobalStats; // Сделаем глобальной для вызова из других мест

        updateGlobalStats();



        loadAllDialogs(true);



        chatContainer.addEventListener('click', async (e) => {

            // 1. Jump to Comments (Discussion)

            // Try closest first, then check if target itself is the button (safest delegation)

            const jumpBtn = e.target.closest('.jump-to-comments') || (e.target.classList.contains('jump-to-comments') ? e.target : null);

            if (jumpBtn) {

                e.preventDefault();

                e.stopPropagation();

                const discussionId = jumpBtn.dataset.linkedId;

                const channelMsgId = jumpBtn.dataset.msgId;



                // Visual feedback

                jumpBtn.style.opacity = '0.5';



                try {

                    const res = await fetch(`/api/find_comment_root?discussion_chat_id=${discussionId}&channel_msg_id=${channelMsgId}`);

                    const data = await res.json();

                    if (res.ok && data.message_id) {

                        jumpToMessageFromSearch(discussionId, data.message_id);

                    } else {

                        showNotification(I18N.t('error.comments_not_found'), "error");

                    }

                } catch (err) { console.error("Jump error:", err); }

                finally { jumpBtn.style.opacity = '1'; }

                return;

            }



            // 2. Search result jump

            if (chatContainer.dataset.mode === 'search') {

                const messageElement = e.target.closest('.message.search-result');

                if (messageElement) {

                    const chatId = messageElement.dataset.chatId;

                    const messageId = parseInt(messageElement.dataset.id, 10);

                    if (chatId && messageId) {

                        jumpToMessageFromSearch(chatId, messageId);

                    }

                }

            }

        });

    }



    async function jumpToMessageFromSearch(chatId, messageId) {

        if (searchInput) searchInput.value = '';

        if (cancelSearchBtn) cancelSearchBtn.style.display = 'none';

        const chatSearchInput = document.getElementById('chat-search-input');

        if (chatSearchInput) chatSearchInput.value = '';

        const chatCancelSearchBtn = document.getElementById('chat-cancel-search-btn');

        if (chatCancelSearchBtn) chatCancelSearchBtn.style.display = 'none';

        if (datePicker) datePicker.value = '';

        document.body.classList.remove('search-mode-active');



        if (chatContainer) {

            chatContainer.dataset.mode = 'chat';

            chatContainer.dataset.searchScope = '';

            chatContainer.innerHTML = '';

        }

        if (window.VirtualChat) window.VirtualChat.reset();

        activePageFetches.clear();

        if (archiveToggleBtn) archiveToggleBtn.style.display = 'block';



        const targetChatStr = String(chatId);

        const currentChatStr = String(currentChatId || '');



        if (typeof areChatIdsEqual === 'function' ? areChatIdsEqual(targetChatStr, currentChatStr) : targetChatStr === currentChatStr) {

            if (typeof window.scrollToMessage === 'function') {

                await window.scrollToMessage(Number(messageId), chatId, true);

            }

        } else {

            scrollAfterLoad = Number(messageId);

            const targetItem = document.querySelector('.chat-list-item[data-chat-id="' + chatId + '"]');

            if (targetItem) targetItem.click();

        }

    }




    const VirtualChat = window.VirtualChat;

    function renderLowerCutoffCard() {

        if (!chatContainer) return;

        if (chatContainer.querySelector('.chat-cutoff-divider.lower-cutoff')) return;

        const bounds = window.currentSyncBounds;

        if (!bounds || !bounds.has_older_gap) return;

        if (typeof window.createLowerCutoffElement !== 'function') return;



        const cutoffDiv = window.createLowerCutoffElement(bounds);

        if (window.VirtualChat && window.VirtualChat.topSpacer && window.VirtualChat.topSpacer.parentNode === chatContainer) {

            window.VirtualChat.topSpacer.after(cutoffDiv);

        } else {

            chatContainer.prepend(cutoffDiv);

        }

        if (window.VirtualChat) {

            window.VirtualChat.registerElements([cutoffDiv], true);

        }

    }

    window.renderLowerCutoffCard = renderLowerCutoffCard;



    function updateChatCutoffVisuals(bounds) {

        window.currentSyncBounds = bounds || null;

        const banner = document.getElementById('chat-cutoff-banner');

        const bannerText = document.getElementById('chat-cutoff-banner-text');



        if (bounds && bounds.has_newer_gap) {

            if (banner) {

                banner.style.display = 'flex';

                const maxId = bounds.max_cached_id || bounds.max_downloaded_id || 0;

                const topId = bounds.server_top_id || 0;

                const diff = (topId && maxId) ? Math.max(0, topId - maxId) : '';

                const diffText = diff ? (window.I18N ? window.I18N.t('cutoff.upper_unarchived', diff) : ` (~${diff} unarchived)`) : '';

                if (bannerText) {

                    bannerText.textContent = window.I18N ? window.I18N.t('cutoff.upper_text', diffText) : `Newer messages available on Telegram${diffText}.`;

                }

            }

        } else {

            if (banner) banner.style.display = 'none';

        }



        // Header badge

        const headerName = document.getElementById('chat-header-name');

        if (headerName) {

            let badge = headerName.querySelector('.chat-status-badge');

            const isDeleted = bounds && bounds.backup_status === 'deleted';

            const isInaccessible = bounds && bounds.backup_status === 'inaccessible';

            const isHalted = bounds && (bounds.backup_status === 'halted' || bounds.has_older_gap || bounds.has_newer_gap);



            if (isDeleted || isInaccessible || isHalted) {

                if (!badge) {

                    badge = document.createElement('span');

                    badge.className = 'chat-status-badge';

                    headerName.appendChild(badge);

                }

                if (isDeleted) {

                    badge.className = 'chat-status-badge deleted-badge';

                    badge.style.background = '#ef4444';

                    badge.style.color = '#fff';

                    badge.style.padding = '2px 8px';

                    badge.style.borderRadius = '4px';

                    badge.title = 'Account or Chat permanently deleted on Telegram';

                    badge.textContent = '🚫 Deleted';

                    badge.onclick = null;

                } else if (isInaccessible) {

                    badge.className = 'chat-status-badge inaccessible-badge';

                    badge.style.background = '#f59e0b';

                    badge.style.color = '#fff';

                    badge.style.padding = '2px 8px';

                    badge.style.borderRadius = '4px';

                    badge.style.cursor = 'pointer';

                    badge.title = 'Temporarily blocked or private. Click to recheck access.';

                    badge.textContent = '🔒 Inaccessible (Re-check 🔄)';

                    badge.onclick = (e) => {

                        e.stopPropagation();

                        window.recheckChatAccess(window.currentChatId);

                    };

                } else {

                    badge.className = 'chat-status-badge halted-badge';

                    badge.style.background = '';

                    badge.style.color = '';

                    badge.title = window.I18N ? window.I18N.t('badge.halted_title') : 'Incomplete or halted archive. Click to resume.';

                    badge.onclick = (e) => {

                        e.stopPropagation();

                        if (window.openResumeModal) window.openResumeModal(window.currentChatId);

                    };

                    badge.textContent = (bounds.backup_status === 'halted')

                        ? (window.I18N ? window.I18N.t('badge.partial_archive') : 'Partial Archive')

                        : (window.I18N ? window.I18N.t('badge.sync_gaps') : 'Sync Gaps');

                }

            } else if (badge) {

                badge.remove();

            }

        }

    }

    window.updateChatCutoffVisuals = updateChatCutoffVisuals;



    window.recheckChatAccess = async function(chatId) {

        if (!chatId) return;

        try {

            const res = await fetch('/api/recheck_chat_access', {

                method: 'POST',

                headers: { 'Content-Type': 'application/json', 'X-TeleBackup-Auth-Token': (typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '') },

                body: JSON.stringify({chat_id: chatId})

            });

            const data = await res.json();

            if (data.ok && data.accessible) {

                alert(`✅ Chat "${data.title || chatId}" is accessible! Status restored to complete.`);

                if (window.loadChats) window.loadChats();

                if (window.fetchDialogs) window.fetchDialogs();

            } else {

                alert(`🔒 Chat is currently inaccessible: ${data.reason || data.status || 'unknown'}`);

            }

        } catch (err) {

            alert(`Error checking chat access: ${err.message}`);

        }

    };



    // --- 3. Рендеринг/Загрузка сообщений ---

    async function loadMessages(chatId, page, isPrepend = false) {

        const fetchKey = `${chatId}:${page}:${isPrepend ? 'p' : 'a'}`;

        if (activePageFetches.has(fetchKey)) return;

        if (isLoading) return;

        if (isPrepend && noMoreOlderMessages) return;

        if (!isPrepend && noMoreNewerMessages) return;

        activePageFetches.add(fetchKey);

        isLoading = true;



        const fragment = document.createDocumentFragment();



        try {

            const showSponsored = (localStorage.getItem('tg_show_sponsored') === 'true') ? '1' : '0';

            const msgResponse = await fetch(`/api/messages?chat_id=${chatId}&page=${page}&show_sponsored=${showSponsored}`);

            if (!msgResponse.ok) throw new Error(I18N.t('error.load_messages'));



            const responseData = await msgResponse.json();

            if (responseData.users) Object.assign(allUsers, responseData.users);

            if (responseData.custom_emoji) {

                Object.assign(allCustomEmoji, responseData.custom_emoji);

                if (window.EmojiEngine) window.EmojiEngine.registerMap(allCustomEmoji);

            }

            const messages = responseData.messages || responseData; // Страховка от старого кэша

            const hasOlderSameDate = responseData.has_older_same_date || false;



            if (responseData.sync_bounds) {

                window.currentSyncBounds = responseData.sync_bounds;

                if (typeof updateChatCutoffVisuals === 'function') {

                    updateChatCutoffVisuals(responseData.sync_bounds);

                }

            }



            if (messages.length === 0) {

                if (isPrepend) {

                    noMoreOlderMessages = true;

                    if (window.currentSyncBounds && window.currentSyncBounds.has_older_gap) {

                        renderLowerCutoffCard();

                    }

                } else {

                    noMoreNewerMessages = true;

                }

                updateRenderedMessageCount();

                isLoading = false;

                return;

            }



            if (messages.length < 50) {

                noMoreOlderMessages = true;

                if (window.currentSyncBounds && window.currentSyncBounds.has_older_gap) {

                    renderLowerCutoffCard();

                }

            }

            if (!isPrepend && page === 1) {

                noMoreNewerMessages = true;

            }



            // Deduplicate incoming messages against existing elements in DOM and VirtualChat

            const existingIds = new Set();

            chatContainer.querySelectorAll('.message[data-id]').forEach(el => {

                if (el.dataset.id) existingIds.add(String(el.dataset.id));

            });

            if (window.VirtualChat && window.VirtualChat.items) {

                window.VirtualChat.items.forEach(it => {

                    if (it.id && !it.isDate && !it.isCutoff) existingIds.add(String(it.id));

                });

            }



            const rawMessagesToProcess = messages.slice().reverse();

            const messagesToProcess = rawMessagesToProcess.filter(msg => !existingIds.has(String(msg.message_id)));



            if (messagesToProcess.length === 0) {

                if (isPrepend) oldestPageLoaded = Math.max(oldestPageLoaded, page);

                else newestPageLoaded = Math.min(newestPageLoaded, page);

                isLoading = false;

                return;

            }



            renderedMessagesCount += messagesToProcess.length;



            let messageBatches = [];

            let currentAlbum = null;



            messagesToProcess.forEach((msg) => {

                const mime = msg.media_mime_type || '';

                const isSticker = (mime === 'application/x-tgsticker' || mime === 'video/webm' || mime === 'image/webp');



                if (msg.group_id && currentAlbum && msg.group_id === currentAlbum.group_id && msg.sender_id === currentAlbum.sender_id) {

                    currentAlbum.messages.push(msg);

                } else {

                    if (currentAlbum) messageBatches.push(currentAlbum);

                    currentAlbum = null;

                    if (msg.group_id) {

                        currentAlbum = { type: 'album', group_id: msg.group_id, sender_id: msg.sender_id, date: msg.date, messages: [msg] };

                    } else {

                        messageBatches.push({ type: 'single', message: msg });

                    }

                }

            });

            if (currentAlbum) messageBatches.push(currentAlbum);



            const distFromBottom = chatContainer.scrollHeight - chatContainer.scrollTop;

            let dateOfElementBelow = null;



            // ПРОСТО СОБИРАЕМ HTML (БЕЗ ВЫЧИСЛЕНИЙ ГРУПП)

            messageBatches.forEach((batch, index) => {

                const rawDateStr = batch.date || (batch.message && batch.message.date);

                const validObj = rawDateStr ? new Date(rawDateStr) : new Date();

                const safeDateObj = isNaN(validObj.getTime()) ? new Date() : validObj;

                const batchDate = safeDateObj.toLocaleDateString('ru-RU');



                if (batchDate !== dateOfElementBelow) {

                    if (index === 0 && hasOlderSameDate) {

                        // Блокируем создание фантомной даты (Look-ahead с прошлого фикса)

                    } else {

                        fragment.appendChild(createDateSeparator(safeDateObj, true));

                    }

                    dateOfElementBelow = batchDate;

                }



                const element = (batch.type === 'album') ? createAlbumElement(batch) : createMessageElement(batch.message);

                fragment.appendChild(element);

            });



            // Рендерим спонсированные посты внизу ленты (если включено и это последняя страница/низ)

            if (!isPrepend && page === 1 && responseData.sponsored_messages && responseData.sponsored_messages.length > 0) {

                if (typeof window.createSponsoredMessageElement === 'function') {

                    responseData.sponsored_messages.forEach(sm => {

                        fragment.appendChild(window.createSponsoredMessageElement(sm));

                    });

                }

            }



            // OPTIMIZATION: Hydrate custom emojis on off-screen fragment BEFORE DOM insertion

            if (typeof renderCustomEmojisInContainer === 'function') {

                renderCustomEmojisInContainer(fragment, allCustomEmoji);

            }



            const newNodes = Array.from(fragment.children);



            // ВСТАВЛЯЕМ В DOM

            if (isPrepend) {

                if (window.VirtualChat) window.VirtualChat.isSuspended = true;

                if (window.VirtualChat && window.VirtualChat.topSpacer && window.VirtualChat.topSpacer.parentNode === chatContainer) {

                    window.VirtualChat.topSpacer.after(fragment);

                } else {

                    chatContainer.prepend(fragment);

                }

            } else {

                if (window.VirtualChat && window.VirtualChat.bottomSpacer && window.VirtualChat.bottomSpacer.parentNode === chatContainer) {

                    window.VirtualChat.bottomSpacer.before(fragment);

                } else {

                    chatContainer.appendChild(fragment);

                }

            }



            // Register with VirtualChat windowing engine BEFORE grouping

            if (window.VirtualChat) {

                window.VirtualChat.registerElements(newNodes.filter(n => n.isConnected), isPrepend);

            }



            // МАГИЯ 1: ЗАЧИСТКА ФАНТОМНЫХ ДАТ (в DOM и в VirtualChat.items)

            const separators = chatContainer.querySelectorAll('.date-separator');

            separators.forEach(sep => {

                const prevNode = sep.previousElementSibling;

                if (prevNode) {

                    const isPrevMsgSameDate = prevNode.classList.contains('message') && prevNode.getAttribute('data-date') === sep.getAttribute('data-date');

                    const isPrevDateSep = prevNode.classList.contains('date-separator');

                    if (isPrevMsgSameDate || isPrevDateSep) {

                        sep.remove();

                        if (window.VirtualChat && window.VirtualChat.items) {

                            window.VirtualChat.items = window.VirtualChat.items.filter(it => it.el !== sep);

                        }

                    }

                }

            });



            // Также зачищаем фантомные даты напрямую в VirtualChat.items (граница размонтированных блоков)

            if (window.VirtualChat && window.VirtualChat.items) {

                const cleanedVC = [];

                for (let i = 0; i < window.VirtualChat.items.length; i++) {

                    const cur = window.VirtualChat.items[i];

                    if (cur.isDate && cleanedVC.length > 0) {

                        const prev = cleanedVC[cleanedVC.length - 1];

                        if (prev && !prev.isDate && prev.el && cur.el && prev.el.getAttribute('data-date') === cur.el.getAttribute('data-date')) {

                            if (cur.el.parentNode) cur.el.remove();

                            continue;

                        }

                        if (prev && prev.isDate) {

                            if (cur.el.parentNode) cur.el.remove();

                            continue;

                        }

                    }

                    cleanedVC.push(cur);

                }

                window.VirtualChat.items = cleanedVC;

            }



            // МАГИЯ 2: ПОСТ-ОБРАБОТКА ГРУПП (Сшиваем монологи)

            updateMessageGrouping(newNodes);



            // МАГИЯ 3: ТОЧНЫЙ СКРОЛЛ

            if (isPrepend) {

                chatContainer.scrollTop = chatContainer.scrollHeight - distFromBottom;

                oldestPageLoaded = page;

                if (window.VirtualChat) {

                    window.VirtualChat.isSuspended = false;

                }

            } else {

                newestPageLoaded = page;

                if (page === 1) {

                    noMoreNewerMessages = true;

                    chatContainer.scrollTop = chatContainer.scrollHeight;

                }

            }



            updateRenderedMessageCount();

            isLoading = false;



            // Auto-fill viewport if content does not fill clientHeight to ensure scrollbar exists

            if (!isPrepend && !noMoreNewerMessages && newestPageLoaded > 1 && chatContainer.scrollHeight <= chatContainer.clientHeight + 150) {

                const nextPage = newestPageLoaded - 1;

                const nextKey = `${chatId}:${nextPage}:a`;

                if (!activePageFetches.has(nextKey)) {

                    setTimeout(() => {

                        if (!noMoreNewerMessages && newestPageLoaded > 1 && chatContainer.scrollHeight <= chatContainer.clientHeight + 150) {

                            loadMessages(chatId, newestPageLoaded - 1, false);

                        }

                    }, 50);

                }

            }



        } catch (error) {

            console.error('Error loading messages:', error);

            if (messageCountDisplay) messageCountDisplay.textContent = `Error: ${error.message}`;

            isLoading = false;

            if (window.VirtualChat) window.VirtualChat.isSuspended = false;

        } finally {

            activePageFetches.delete(fetchKey);

        }

    }



    /**

     * updateMessageGrouping(newNodes?)

     * When newNodes is provided (array of newly inserted DOM elements), only re-groups the

     * boundary region where old and new messages meet (+/- 1 group). Full rescan is still

     * used for live messages, language changes, and other callers that pass no argument.

     */

    function updateMessageGrouping(newNodes) {

        // Use VirtualChat.items if available so grouping is consistent across mounted/unmounted nodes

        const allElements = (window.VirtualChat && window.VirtualChat.items && window.VirtualChat.items.length > 0)

            ? window.VirtualChat.items.map(it => it.el)

            : Array.from(chatContainer.children);



        if (!allElements.length) return;



        const chatInfo = getChatById(currentChatId);

        const isChannel = chatInfo && chatInfo.chat_type === 'channel';



        function applyGroupClasses(g) {

            g.forEach(m => m.classList.remove('msg-single', 'msg-first', 'msg-middle', 'msg-last', 'grouped', 'show-avatar'));

            if (g.length === 1) {

                g[0].classList.add('msg-single');

                if (g[0].dataset.isPost !== '1' && !g[0].classList.contains('out')) g[0].classList.add('show-avatar');

            } else {

                g[0].classList.add('msg-first');

                for (let j = 1; j < g.length - 1; j++) g[j].classList.add('msg-middle', 'grouped');

                g[g.length - 1].classList.add('msg-last', 'grouped');

                if (g[g.length - 1].dataset.isPost !== '1' && !g[g.length - 1].classList.contains('out')) g[g.length - 1].classList.add('show-avatar');

            }

        }



        const isScoped = Array.isArray(newNodes) && newNodes.length > 0 && allElements.length > newNodes.length;



        // If not scoped, unwrap all existing bulk-deleted groups

        if (!isScoped) {

            const wrappers = chatContainer.querySelectorAll('.bulk-deleted-wrapper');

            wrappers.forEach(w => {

                const list = w.querySelector('.bulk-message-list');

                if (list) {

                    while (list.firstChild) {

                        w.parentNode.insertBefore(list.firstChild, w);

                    }

                }

                w.remove();

            });

        }



        let startIdx = 0;

        let endIdx = allElements.length;



        if (isScoped) {

            const firstIdx = allElements.indexOf(newNodes[0]);

            const lastIdx = allElements.indexOf(newNodes[newNodes.length - 1]);

            if (firstIdx !== -1 && lastIdx !== -1) {

                startIdx = Math.max(0, firstIdx);

                endIdx = Math.min(allElements.length, lastIdx + 1);



                // Expand backwards across same sender

                const targetSender = newNodes[0].dataset ? newNodes[0].dataset.senderId : null;

                while (startIdx > 0) {

                    const prev = allElements[startIdx - 1];

                    if (prev && prev.classList && prev.classList.contains('message') && prev.dataset && prev.dataset.senderId === targetSender) {

                        startIdx--;

                    } else {

                        break;

                    }

                }

                // Expand forwards across same sender

                const lastTargetSender = newNodes[newNodes.length - 1].dataset ? newNodes[newNodes.length - 1].dataset.senderId : null;

                while (endIdx < allElements.length) {

                    const next = allElements[endIdx];

                    if (next && next.classList && next.classList.contains('message') && next.dataset && next.dataset.senderId === lastTargetSender) {

                        endIdx++;

                    } else {

                        break;

                    }

                }

            }

        }



        let prevSender = null;

        let currentGroup = [];



        for (let i = startIdx; i < endIdx; i++) {

            const el = allElements[i];



            // Проверяем, является ли элемент обычным сообщением

            const isMessage = el && el.classList && el.classList.contains('message') && !el.classList.contains('service-message') && !el.classList.contains('search-result');

            const senderId = (isMessage && el.dataset && el.dataset.senderId) ? el.dataset.senderId : null;

            const isPost = isMessage && el.dataset && el.dataset.isPost === '1';



            if (!isMessage) {

                const isDateSep = el && el.classList && el.classList.contains('date-separator');

                let isPhantomSep = false;

                if (isDateSep && i > 0 && i < allElements.length - 1) {

                    const prevEl = allElements[i - 1];

                    const nextEl = allElements[i + 1];

                    if (prevEl && nextEl && prevEl.classList && prevEl.classList.contains('message') && nextEl.classList && nextEl.classList.contains('message')) {

                        const prevDate = prevEl.getAttribute('data-date');

                        const nextDate = nextEl.getAttribute('data-date');

                        const prevS = prevEl.dataset ? prevEl.dataset.senderId : null;

                        const nextS = nextEl.dataset ? nextEl.dataset.senderId : null;

                        if (prevDate && prevDate === nextDate && prevS && prevS === nextS) {

                            isPhantomSep = true;

                            if (el.parentNode) el.remove();

                        }

                    }

                }

                if (!isPhantomSep) {

                    if (currentGroup.length > 0) {

                        applyGroupClasses(currentGroup);

                    }

                    currentGroup = [];

                    prevSender = null;

                }

                continue;

            }



            if (senderId !== prevSender || (isChannel && isPost)) {

                if (currentGroup.length > 0) {

                    applyGroupClasses(currentGroup);

                }

                currentGroup = [];

            }



            currentGroup.push(el);

            prevSender = senderId;

        }



        if (currentGroup.length > 0) {

            applyGroupClasses(currentGroup);

        }



        // 2. Группировка 5+ подряд удаленных сообщений (только при полном проходе)

        if (!isScoped) {

            let deletedStreak = [];

            const childNodes = Array.from(chatContainer.children);



            function processBulkStreak() {

                if (deletedStreak.length >= 5) {

                    const wrapper = document.createElement('div');

                    wrapper.className = 'bulk-deleted-wrapper collapsed-deleted';

                    if (deletedStreak[0].classList.contains('out')) {

                        wrapper.classList.add('out');

                    }



                    const header = document.createElement('div');

                    header.className = 'bulk-header';

                    header.onclick = (e) => { e.stopPropagation(); wrapper.classList.toggle('collapsed-deleted'); };

                    header.innerHTML = `<span class="deleted-badge">🗑 ${deletedStreak.length} <span data-i18n="messages.bulk_deleted">${I18N.t('messages.bulk_deleted')}</span> (<span class="show-text" data-i18n="messages.show">${I18N.t('messages.show')}</span><span class="hide-text" data-i18n="messages.hide">${I18N.t('messages.hide')}</span>)</span>`;



                    const listContainer = document.createElement('div');

                    listContainer.className = 'bulk-message-list';



                    wrapper.appendChild(header);

                    wrapper.appendChild(listContainer);



                    chatContainer.insertBefore(wrapper, deletedStreak[0]);

                    deletedStreak.forEach(m => listContainer.appendChild(m));

                }

                deletedStreak = [];

            }



            for (let i = 0; i <= childNodes.length; i++) {

                const el = childNodes[i];

                const isDeletedMsg = el && el.classList.contains('message') && el.classList.contains('is-deleted-msg');



                if (isDeletedMsg) {

                    const isOut = el.classList.contains('out');

                    if (deletedStreak.length > 0 && deletedStreak[0].classList.contains('out') !== isOut) {

                        processBulkStreak();

                    }

                    deletedStreak.push(el);

                } else {

                    processBulkStreak();

                }

            }

        }



        adjustMessageFooterWidths(chatContainer, newNodes);

    }

    window.updateMessageGrouping = updateMessageGrouping;







    /**

     * adjustMessageFooterWidths(container, scopeElements?)

     * When scopeElements is provided, only measures those elements instead of querying

     * the entire container. Falls back to full querySelectorAll for callers that don't

     * pass scoped elements (live messages, language change).

     *

     * In addition to ensuring footer min-width, also collapses overstretched message

     * bubbles where multi-line wrapped text (e.g. long unbroken links or words) causes

     * CSS flex shrinkwrap to leave empty space on the right of the longest line.

     */

    function adjustMessageFooterWidths(container = chatContainer, scopeElements = null) {

        if (!container) return;

        if (window.VirtualChat && window.VirtualChat.isSuspended) return;

        let messages;

        if (scopeElements && scopeElements.length > 0) {

            // Scoped: only measure new elements (filter to actual message elements)

            messages = scopeElements.filter(el => el && el.classList && el.classList.contains('message') && !el.classList.contains('sticker-message'));

        } else {

            messages = container.querySelectorAll('.message:not(.sticker-message)');

        }

        const msgIter = messages.forEach ? messages : Array.from(messages);



        // Pre-measurement reset: clear script-applied inline constraints so we evaluate natural layout at current width

        msgIter.forEach(msgEl => {

            if (msgEl.dataset && msgEl.dataset.collapsedByScript === '1') {

                msgEl.style.width = '';

                msgEl.style.removeProperty('max-width');

                msgEl.removeAttribute('data-collapsed-by-script');

            }

        });



        const toAdjust = [];

        const toCollapse = [];

        const parentWidth = chatContainer ? chatContainer.clientWidth : window.innerWidth;

        const maxAllowed = parentWidth > 0 ? parentWidth * 0.42 : 450;

        const range = document.createRange();



        // Batch read phase (no style writes)

        msgIter.forEach(msgEl => {

            if (!msgEl.isConnected) return;

            const footer = msgEl.querySelector('.message-footer');

            const mainContent = msgEl.querySelector('.message-main-content');

            const bodyWrapper = msgEl.querySelector('.message-body-wrapper');

            if (!footer || !mainContent || !bodyWrapper) return;



            let neededFooterWidth = 0;

            const children = Array.from(footer.children).filter(c => c.style.display !== 'none' && c.innerHTML.trim() !== '');

            children.forEach(child => {

                neededFooterWidth += child.scrollWidth || child.offsetWidth || 0;

            });

            if (children.length > 1) {

                neededFooterWidth += (children.length - 1) * 8; // gap between footer children

            }



            const mainWidth = mainContent.scrollWidth || mainContent.offsetWidth || 0;

            if (neededFooterWidth > 0 && mainWidth < neededFooterWidth) {

                const targetWidth = Math.min(neededFooterWidth, maxAllowed);

                toAdjust.push({ bodyWrapper, targetWidth });

            }



            // Collapse overstretched message bubble if wrapped text (links / long words) leaves empty space

            const textEl = mainContent.querySelector('.text');

            if (textEl && !mainContent.querySelector('details, blockquote')) {

                range.selectNodeContents(textEl);

                const rects = range.getClientRects();



                // Group rects by distinct vertical lines (Math.round(r.top) within 3px tolerance)

                // Prevents Chrome multi-rect per element bug from triggering on single-line links

                const lineMap = new Map();

                for (let i = 0; i < rects.length; i++) {

                    const r = rects[i];

                    if (r.width <= 0 || r.height <= 0) continue;

                    const topKey = Math.round(r.top);

                    let foundKey = null;

                    for (const k of lineMap.keys()) {

                        if (Math.abs(k - topKey) <= 3) {

                            foundKey = k;

                            break;

                        }

                    }

                    if (foundKey !== null) {

                        lineMap.set(foundKey, Math.max(lineMap.get(foundKey), r.width));

                    } else {

                        lineMap.set(topKey, r.width);

                    }

                }



                // Truly multi-line only if 2 or more distinct vertical lines exist

                if (lineMap.size > 1) {

                    let maxLineWidth = 0;

                    for (const w of lineMap.values()) {

                        if (w > maxLineWidth) maxLineWidth = w;

                    }



                    if (maxLineWidth > 0) {

                        let otherWidth = neededFooterWidth;



                        const sender = msgEl.querySelector('.sender');

                        if (sender) {

                            range.selectNodeContents(sender);

                            const sw = range.getBoundingClientRect().width;

                            if (sw > otherWidth) otherWidth = sw;

                        }



                        const reply = msgEl.querySelector('.reply');

                        if (reply) {

                            const replySender = reply.querySelector('.sender, .reply-sender');

                            let minReplyWidth = 140;

                            if (replySender) {

                                range.selectNodeContents(replySender);

                                const rsw = range.getBoundingClientRect().width;

                                minReplyWidth = Math.max(minReplyWidth, rsw + 40);

                            }

                            if (minReplyWidth > otherWidth) otherWidth = minReplyWidth;

                        }



                        const forward = msgEl.querySelector('.forward');

                        if (forward) {

                            range.selectNodeContents(forward);

                            const fw = range.getBoundingClientRect().width;

                            if (fw > otherWidth) otherWidth = fw;

                        }



                        const media = msgEl.querySelector('.video-container, .media-thumb-container, .album-container, .poll-container, .audio-player, .web-preview, .document-container, .photo, video');

                        if (media) {

                            const mw = media.offsetWidth || media.scrollWidth || 0;

                            if (mw > otherWidth) otherWidth = mw;

                        }



                        const commentsBar = msgEl.querySelector('.comments-bar');

                        if (commentsBar) {

                            let cbWidth = 0;

                            Array.from(commentsBar.children).forEach(c => {

                                cbWidth += c.scrollWidth || c.offsetWidth || 0;

                            });

                            if (cbWidth > otherWidth) otherWidth = cbWidth;

                        }



                        const editHistory = msgEl.querySelector('.edit-history-content:not([style*="display: none"])');

                        if (editHistory) {

                            const ew = editHistory.scrollWidth || editHistory.offsetWidth || 0;

                            if (ew > otherWidth) otherWidth = ew;

                        }



                        const neededContentWidth = Math.max(maxLineWidth, otherWidth);

                        const computed = window.getComputedStyle(msgEl);

                        const isBorderBox = computed.boxSizing === 'border-box';

                        const padLeft = parseFloat(computed.paddingLeft) || 14;

                        const padRight = parseFloat(computed.paddingRight) || 14;

                        const borderLeft = parseFloat(computed.borderLeftWidth) || 1;

                        const borderRight = parseFloat(computed.borderRightWidth) || 1;

                        const extraH = padLeft + padRight + borderLeft + borderRight;



                        const currentContentWidth = msgEl.clientWidth - padLeft - padRight;

                        if (neededContentWidth < currentContentWidth - 8) {

                            const targetMaxWidth = Math.ceil(neededContentWidth + (isBorderBox ? extraH : 0) + 2);

                            toCollapse.push({ msgEl, targetMaxWidth });

                        }

                    }

                }

            }

        });



        // Batch write phase (no layout queries)

        toAdjust.forEach(({ bodyWrapper, targetWidth }) => {

            bodyWrapper.style.minWidth = `${Math.ceil(targetWidth)}px`;

        });

        toCollapse.forEach(({ msgEl, targetMaxWidth }) => {

            msgEl.dataset.collapsedByScript = '1';

            msgEl.style.width = 'fit-content';

            msgEl.style.setProperty('max-width', `min(${targetMaxWidth}px, 42%)`, 'important');

        });

    }

    window.adjustMessageFooterWidths = adjustMessageFooterWidths;





    function updateRenderedMessageCount() {

        if (!messageCountDisplay) return;

        const lang = (typeof I18N !== 'undefined') ? I18N.lang() : 'en';

        if (noMoreOlderMessages && noMoreNewerMessages) {

            const formattedTotal = totalMessagesInChat.toLocaleString((typeof I18N !== 'undefined') ? I18N.locale() : 'en-US');

            if (lang === 'ru' && typeof I18N !== 'undefined') {

                const word = I18N.plural(totalMessagesInChat, ['сообщение', 'сообщения', 'сообщений']);

                messageCountDisplay.textContent = `Показаны все ${formattedTotal} ${word}`;

            } else if (typeof I18N !== 'undefined') {

                messageCountDisplay.textContent = I18N.t('status.showing_all', formattedTotal);

            } else {

                messageCountDisplay.textContent = `Showing all ${formattedTotal} messages`;

            }

            if (messageCountTotal) messageCountTotal.style.display = 'none';

        } else {

            const displayCount = (window.VirtualChat && window.VirtualChat.items.length > 0) ? window.VirtualChat.items.filter(i => !i.isDate).length : renderedMessagesCount;

            const formattedRendered = displayCount.toLocaleString((typeof I18N !== 'undefined') ? I18N.locale() : 'en-US');

            const formattedTotal = totalMessagesInChat.toLocaleString((typeof I18N !== 'undefined') ? I18N.locale() : 'en-US');

            if (typeof I18N !== 'undefined') {

                messageCountDisplay.textContent = I18N.t('status.showing', formattedRendered);

                if (messageCountTotal) {

                    messageCountTotal.textContent = I18N.t('status.of_total', formattedTotal);

                    messageCountTotal.style.display = 'inline';

                }

            } else {

                messageCountDisplay.textContent = `Showing ${formattedRendered}`;

                if (messageCountTotal) {

                    messageCountTotal.textContent = `of ${formattedTotal}`;

                    messageCountTotal.style.display = 'inline';

                }

            }

        }

    }



    // --- renderChatAboutPanel: collapsible О чате section ---

    function renderChatAboutPanel(chat) {

        const panel = document.getElementById('chat-about-panel');

        const body = document.getElementById('chat-about-body');

        const text = document.getElementById('chat-about-text');

        const linkedRow = document.getElementById('chat-linked-row');

        const linkedLabel = document.getElementById('chat-linked-label');

        const arrow = document.getElementById('chat-about-arrow');

        const toggle = document.getElementById('chat-about-toggle');

        if (!panel) return;



        const hasAbout = chat.chat_about && chat.chat_about.trim();

        const hasLinked = chat.linked_chat_id;

        if (!hasAbout && !hasLinked) { panel.style.display = 'none'; return; }



        panel.style.display = 'block';

        body.style.display = 'none'; // collapsed on every chat switch

        arrow.textContent = '▶';



        if (text) { text.textContent = hasAbout ? chat.chat_about : ''; text.style.display = hasAbout ? 'block' : 'none'; }

        if (linkedRow) linkedRow.style.display = hasLinked ? 'flex' : 'none';

        if (linkedLabel && hasLinked) {

            const targetId = normalizeId(chat.linked_chat_id);

            const linkedChat = getChatById(targetId);

            if (linkedChat) {

                linkedLabel.innerHTML = `${I18N.t('chat.linked')}: <a href="#" class="linked-chat-jump jump-to-comments" style="margin-left:5px; text-decoration:none;" data-id="${targetId}">${escapeHTML(linkedChat.chat_name)}</a>`;

                const jumpLink = linkedLabel.querySelector('.linked-chat-jump');

                jumpLink.addEventListener('click', (e) => {

                    e.preventDefault();

                    const targetItem = document.querySelector(`.chat-list-item[data-chat-id="${targetId}"]`);

                    if (targetItem) targetItem.click();

                });

            } else {

                linkedLabel.textContent = `${I18N.t('chat.linked')} ID: ${chat.linked_chat_id}`;

            }

        }



        if (!toggle._aboutWired) {

            toggle._aboutWired = true;

            toggle.addEventListener('click', () => {

                const open = body.style.display !== 'none';

                body.style.display = open ? 'none' : 'block';

                arrow.textContent = open ? '▶' : '▼';

            });

        }

    }



    // buildSenderBadges: user flag icons after sender name





    // --- 4. Создание HTML-элемента сообщения ---





    // Создание HTML для альбома













    // Helper to build the info items array (Discussion button, views, bot info, etc.)





    // Helper to build the final metadata HTML block





    // Helper to build a dedicated comments bar at the bottom of channel posts










    const renderInlineCommentsList = (...args) => window.renderInlineCommentsList(...args);

    // "Бесконечная прокрутка"

    chatContainer.addEventListener('scroll', () => {

        if (isChatLoading) { return; }



        if (window.VirtualChat) {

            window.VirtualChat.scheduleUpdate();

        }



        if (isLoading || chatContainer.dataset.mode === 'search') return;

        if (chatContainer.scrollHeight <= chatContainer.clientHeight) return;



        // Load older messages (threshold 400px from top)

        if (chatContainer.scrollTop < 400 && !noMoreOlderMessages) {

            const nextPage = oldestPageLoaded + 1;

            const fetchKey = `${currentChatId}:${nextPage}:p`;

            if (!activePageFetches.has(fetchKey)) {

                if (messageCountDisplay) messageCountDisplay.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.loading_older') : 'Loading older messages...';

                loadMessages(currentChatId, nextPage, true);

            }

        }



        // Load newer messages (threshold 400px from bottom)

        const isNearBottom = (chatContainer.scrollTop + chatContainer.clientHeight >= chatContainer.scrollHeight - 400);

        if (isNearBottom && !noMoreNewerMessages) {

            const nextPage = newestPageLoaded - 1;



            if (nextPage < 1) {

                noMoreNewerMessages = true;

                return;

            }

            const fetchKey = `${currentChatId}:${nextPage}:a`;

            if (!activePageFetches.has(fetchKey)) {

                if (messageCountDisplay) messageCountDisplay.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.loading_newer') : 'Loading newer messages...';

                loadMessages(currentChatId, nextPage, false);

            }

        }

    }, { passive: true });



    chatContainer.addEventListener('wheel', (e) => {

        if (isChatLoading || isLoading || chatContainer.dataset.mode === 'search') return;

        if (chatContainer.scrollHeight <= chatContainer.clientHeight) {

            if (e.deltaY > 0 && !noMoreNewerMessages && newestPageLoaded > 1) {

                const nextPage = newestPageLoaded - 1;

                const fetchKey = `${currentChatId}:${nextPage}:a`;

                if (!activePageFetches.has(fetchKey)) {

                    loadMessages(currentChatId, nextPage, false);

                }

            } else if (e.deltaY < 0 && !noMoreOlderMessages) {

                const nextPage = oldestPageLoaded + 1;

                const fetchKey = `${currentChatId}:${nextPage}:p`;

                if (!activePageFetches.has(fetchKey)) {

                    loadMessages(currentChatId, nextPage, true);

                }

            }

        }

    }, { passive: true });



    chatContainer.addEventListener('toggle', (e) => {

        const details = e.target;

        if (!details || details.tagName !== 'DETAILS') return;

        const msgEl = details.closest('.message');

        if (msgEl) {

            msgEl.style.width = '';

            msgEl.style.maxWidth = '';

            msgEl.removeAttribute('data-collapsed-by-script');

            const bodyWrapper = msgEl.querySelector('.message-body-wrapper');

            if (bodyWrapper) {

                bodyWrapper.style.width = '';

                bodyWrapper.style.minWidth = '';

            }

            if (window.VirtualChat && window.VirtualChat.items) {

                const it = window.VirtualChat.items.find(item => item.el === msgEl || (item.id && String(item.id) === String(msgEl.dataset.id)));

                if (it) {

                    it._needsMeasure = true;

                    const newHeight = msgEl.offsetHeight;

                    if (newHeight > 0) it.height = newHeight;

                }

                window.VirtualChat.scheduleUpdate();

            }

        }

    }, true);



    // --- 10. Вспомогательные функции ---

















    window.openPlayer = (filePath, mimeType) => {

        modalVideo.style.display = 'none';

        modalAudio.style.display = 'none';



        if (mimeType.startsWith('video/')) {

            modalVideo.src = filePath;

            modalVideo.style.display = 'block';

            modalVideo.play();

        } else if (mimeType.startsWith('audio/')) {

            modalAudio.src = filePath;

            modalAudio.style.display = 'block';

            modalAudio.play();

        }

        playerModal.style.display = 'block';

    }



    window.closePlayer = () => {

        playerModal.style.display = 'none';

        modalVideo.pause();

        modalVideo.src = '';

        modalAudio.pause();

        modalAudio.src = '';

    }



    // Распаковка .tgs (Gzip)





    // Логика для перетаскивания плеера

    const modalWindow = document.getElementById('player-modal');

    const modalHeader = modalWindow.querySelector('.modal-header');



    isDragging = false;

    offset = { x: 0, y: 0 };



    if (modalHeader) {

        modalHeader.addEventListener('mousedown', (e) => {

            if (e.target.classList.contains('modal-close')) {

                return;

            }

            if (modalWindow.style.transform !== 'none') {

                const rect = modalWindow.getBoundingClientRect();

                modalWindow.style.transform = 'none';

                modalWindow.style.left = rect.left + 'px';

                modalWindow.style.top = rect.top + 'px';

            }

            isDragging = true;

            offset.x = e.clientX - modalWindow.getBoundingClientRect().left;

            offset.y = e.clientY - modalWindow.getBoundingClientRect().top;

            modalHeader.style.cursor = 'grabbing';

        });

    }



    document.addEventListener('mouseup', () => {

        isDragging = false;

        if (modalHeader) {

            modalHeader.style.cursor = 'move';

        }

    });



    document.addEventListener('mousemove', (e) => {

        if (!isDragging) return;

        e.preventDefault();

        let newX = e.clientX - offset.x;

        let newY = e.clientY - offset.y;



        const maxX = window.innerWidth - modalWindow.offsetWidth;

        const maxY = window.innerHeight - modalWindow.offsetHeight;



        if (newX < 0) newX = 0;

        if (newY < 0) newY = 0;

        if (newX > maxX) newX = maxX;

        if (newY > maxY) newY = maxY;



        modalWindow.style.left = newX + 'px';

        modalWindow.style.top = newY + 'px';

    });







    window.scrollToMessage = async (msgId, targetChatId = null, forceReload = false) => {

        if (msgId === null || msgId === undefined || isNaN(msgId)) return;



        if (typeof switchView === 'function') {

            switchView('chat');

        }



        if (targetChatId && String(targetChatId).trim() !== '' && typeof areChatIdsEqual === 'function' && !areChatIdsEqual(targetChatId, currentChatId)) {

            scrollAfterLoad = Number(msgId);

            const chatItem = document.querySelector(`.chat-list-item[data-chat-id="${targetChatId}"]`);

            if (chatItem) {

                chatItem.click();

                return;

            }

        }



        if (!currentChatId) return;

        if (isLoading) return;



        if (!forceReload && chatContainer.dataset.mode !== 'search') {

            if (window.VirtualChat) {

                const mounted = window.VirtualChat.findAndMount(msgId);

                if (mounted) {

                    highlightAndScrollTo(mounted);

                    return;

                }

            }



            let messageElement = chatContainer.querySelector(`[data-id="${msgId}"], [data-album-ids*="${msgId}"]`);

            if (messageElement) {

                highlightAndScrollTo(messageElement);

                return;

            }

        }



        if (messageCountDisplay) messageCountDisplay.textContent = I18N.t('status.finding_message', msgId);

        if (messageCountTotal) messageCountTotal.style.display = 'none';



        try {

            const response = await fetch(`/api/message_page?chat_id=${currentChatId}&message_id=${msgId}`);

            if (!response.ok) {

                const err = await response.json();

                throw new Error(err.error || I18N.t('error.msg_not_found'));

            }

            const data = await response.json();



            if (messageCountDisplay) messageCountDisplay.textContent = I18N.t('status.loading_page', data.page);



            if (window.VirtualChat) window.VirtualChat.reset();

            activePageFetches.clear();

            chatContainer.innerHTML = '';

            chatContainer.dataset.mode = 'chat';

            chatContainer.dataset.searchScope = '';

            oldestPageLoaded = data.page;

            newestPageLoaded = data.page;

            noMoreOlderMessages = false;

            noMoreNewerMessages = false; // Сбрасываем, чтобы loadMessages не вышел раньше времени

            lastRenderedDate = null;

            lastSenderId = null;

            lastBatchHadMedia = false;

            renderedMessagesCount = 0;

            isChatLoading = true;

            isLoading = false; // Сбрасываем флаг пагинации, чтобы разрешить загрузку целевой страницы



            await loadMessages(currentChatId, data.page, false);

            // Оставляем isChatLoading = true, чтобы заблокировать бесконечный скролл во время прокрутки к цели



            setTimeout(() => {

                messageElement = chatContainer.querySelector(`[data-id="${msgId}"], [data-album-ids*="${msgId}"]`);



                // --- NEAREST NEIGHBOR FALLBACK ---

                if (!messageElement) {

                    const allMessages = Array.from(chatContainer.querySelectorAll('.message[data-id]'));

                    if (allMessages.length > 0) {

                        allMessages.sort((a, b) => {

                            const idA = parseInt(a.dataset.id, 10);

                            const idB = parseInt(b.dataset.id, 10);

                            return Math.abs(idA - msgId) - Math.abs(idB - msgId);

                        });

                        messageElement = allMessages[0];

                    }

                }



                if (messageElement) {

                    highlightAndScrollTo(messageElement);

                } else {

                    // ФИНАЛЬНАЯ ДИАГНОСТИКА

                    const allMessagesInDom = chatContainer.querySelectorAll('.message, .service-message');

                    const idsInDom = [...allMessagesInDom].map(m => m.dataset.id).filter(Boolean);

                    console.group(`Диагностика поиска сообщения ${msgId}`);

                    console.log(`Всего в DOM: ${chatContainer.children.length}, Сообщений: ${allMessagesInDom.length}`);

                    console.groupEnd();

                }



                // Разблокируем скролл только после того, как прокрутка к цели должна была начаться

                setTimeout(() => { isChatLoading = false; }, 500);

            }, 300);



        } catch (error) {

            console.error('Error jumping to message:', error);

            if (messageCountDisplay) messageCountDisplay.textContent = `Error: ${error.message}`;

            if (messageCountTotal) messageCountTotal.style.display = 'none';

            isChatLoading = false;

        }

    }



    window.loadMessages = loadMessages;



    window.reloadCurrentChatMessages = async function(chatId) {

        const targetChatId = chatId || currentChatId;

        if (!targetChatId || String(targetChatId) !== String(currentChatId)) return;

        try {

            // 1. Update stats in sidebar and header message counter synchronously

            try {

                const sRes = await fetch(`/api/stats/chat?chat_id=${targetChatId}`);

                const stats = await sRes.json();

                if (stats && !stats.error && stats.total_messages !== undefined) {

                    totalMessagesInChat = stats.total_messages;

                    window.currentChatStats = stats;

                    if (typeof renderLocalStatsList === 'function') renderLocalStatsList(stats);

                    if (messageCountTotal) {

                        const formattedTotal = (typeof I18N !== 'undefined') ? totalMessagesInChat.toLocaleString(I18N.locale()) : totalMessagesInChat.toLocaleString();

                        messageCountTotal.textContent = (typeof I18N !== 'undefined') ? I18N.t('status.of_total', formattedTotal) : `of ${formattedTotal}`;

                        messageCountTotal.style.display = 'inline';

                    }

                    if (typeof updateRenderedMessageCount === 'function') updateRenderedMessageCount();

                }

            } catch (statsErr) {

                console.warn('[reloadCurrentChatMessages] Stats refresh warning:', statsErr);

            }



            // 2. Clear and reload messages from page 1

            if (window.VirtualChat) window.VirtualChat.reset();

            chatContainer.innerHTML = '';

            chatContainer.style.opacity = '0';

            chatContainer.style.visibility = 'hidden';

            allUsers = allUsers || {};

            oldestPageLoaded = 1;

            newestPageLoaded = 1;

            lastRenderedDate = null;

            noMoreOlderMessages = false;

            noMoreNewerMessages = false;

            lastSenderId = null;

            lastBatchHadMedia = false;

            renderedMessagesCount = 0;

            if (window.VirtualChat) window.VirtualChat.isSuspended = true;

            await loadMessages(targetChatId, 1, false);

            chatContainer.scrollTop = chatContainer.scrollHeight;



            requestAnimationFrame(() => {

                if (window.VirtualChat && window.VirtualChat.items) {

                    window.VirtualChat.items.forEach(item => {

                        if (item.el.isConnected && item.el.offsetHeight > 0) {

                            item.height = item.el.offsetHeight;

                        }

                    });

                    window.VirtualChat.isSuspended = false;

                    window.VirtualChat.update();

                }

                chatContainer.scrollTop = chatContainer.scrollHeight;



                requestAnimationFrame(() => {

                    chatContainer.scrollTop = chatContainer.scrollHeight;

                    pinScrollToBottom(800);

                    chatContainer.style.visibility = 'visible';

                    chatContainer.style.opacity = '1.0';

                });

            });

        } catch (e) {

            console.error('Error reloading chat messages:', e);

            chatContainer.style.visibility = 'visible';

            chatContainer.style.opacity = '1.0';

        }

    };







    // --- Переключение вкладок ---

    archiveSettingsView = document.getElementById('archive-settings-view');

    archiveCenterView = document.getElementById('archive-center-view');

    legacyChatList = document.getElementById('legacy-chat-list');

    centerHeader = document.getElementById('center-header');







    if (statsToggleBtn && statsToggleBtn.parentNode) {

        statsToggleBtn.addEventListener('click', () => {

            if (statsContainerView && statsContainerView.classList.contains('active-view')) {

                switchView('chat');

            } else {

                switchView('stats');

            }

        });

    }



    archiveToggleBtn.addEventListener('click', () => {

        const archiveSettings = document.getElementById('archive-settings-view');

        // Проверяем по новому элементу макета, открыт ли режим архивации

        if (archiveSettings && archiveSettings.style.display === 'flex') {

            switchView('chat');

        } else {

            switchView('archive');

        }

    });



















    // ─── REFRESH BUTTONS (Quick vs Full Telegram) ────────────────

    const quickRefreshBtn = document.getElementById('quick-refresh-btn');

    const fullRefreshBtn = document.getElementById('full-refresh-btn');

    const modalTriggerFullBtn = document.getElementById('modal-trigger-full-refresh-btn');

    const refreshHintModalEl = document.getElementById('refresh-hint-modal');



    // Close modal when clicking outside of modal content

    if (refreshHintModalEl) {

        refreshHintModalEl.addEventListener('click', (e) => {

            if (e.target === refreshHintModalEl && window.RefreshHintModal) {

                window.RefreshHintModal.close();

            }

        });

    }



    function updateQuickRefreshButtonState() {
        const btn = document.getElementById('quick-refresh-btn');
        if (!btn) return;
        const hasDialogs = Array.isArray(window.allDialogsData) && window.allDialogsData.length > 0;
        if (!hasDialogs) {
            btn.disabled = true;
            btn.setAttribute('disabled', 'disabled');
            btn.style.opacity = '0.45';
            btn.style.cursor = 'not-allowed';
            const tooltip = (typeof I18N !== 'undefined' && I18N.t)
                ? I18N.t('archive.quick_requires_full')
                : 'Initial scan requires Full refresh first';
            btn.setAttribute('title', tooltip);
        } else {
            btn.disabled = false;
            btn.removeAttribute('disabled');
            btn.style.opacity = '';
            btn.style.cursor = '';
            const defaultTooltip = (typeof I18N !== 'undefined' && I18N.t)
                ? I18N.t('archive.quick_refresh_title')
                : 'Quick refresh (Telegram delta scan for new chats, ~1s)';
            btn.setAttribute('title', defaultTooltip);
        }
    }
    window.updateQuickRefreshButtonState = updateQuickRefreshButtonState;
    document.addEventListener('languageChanged', updateQuickRefreshButtonState);
    setTimeout(updateQuickRefreshButtonState, 100);

    let isFullRefreshing = false;
    window.isFullRefreshing = false;
    let isQuickRefreshing = false;
    window.isQuickRefreshing = false;

    // Quick Refresh: Telegram delta scan (~1s), checks recent dialogs, exempts cached chats

    async function handleQuickRefresh() {
        if (isQuickRefreshing || isFullRefreshing) return;
        const hasDialogs = Array.isArray(window.allDialogsData) && window.allDialogsData.length > 0;
        if (!hasDialogs) return;
        isQuickRefreshing = true;

        const listEl = (typeof dialogsList !== 'undefined' && dialogsList) ? dialogsList : document.getElementById('dialogs-list');

        const origText = quickRefreshBtn ? quickRefreshBtn.innerHTML : '';

        const refreshingText = typeof I18N !== 'undefined' ? I18N.t('archive.refreshing') : 'Refreshing...';



        if (quickRefreshBtn) {

            quickRefreshBtn.disabled = true;

            quickRefreshBtn.innerHTML = `<span>⚡</span> <span>${refreshingText}</span>`;

        }

        if (fullRefreshBtn) fullRefreshBtn.disabled = true;



        try {

            const res = await fetch('/api/quick_refresh_dialogs');

            if (!res.ok) {

                const err = await res.json();

                throw new Error(err.error || 'Quick refresh failed');

            }

            const dialogs = await res.json();

            const renderFn = (typeof renderDialogsList === 'function') ? renderDialogsList : window.renderDialogsList;
            if (typeof renderFn === 'function') {
                renderFn(dialogs || []);
            }
            if (typeof updateArchiveUI === 'function') {
                updateArchiveUI();
            }

        } catch (err) {

            console.error('Error during quick refresh:', err);

            if (listEl && (!listEl.children || listEl.children.length === 0)) {

                listEl.innerHTML = `<li style="padding: 15px; color: #a13434; text-align: center;">Error: ${(window.escapeHTML || String)(err.message)}</li>`;

            }

        } finally {

            isQuickRefreshing = false;

            if (quickRefreshBtn) {

                quickRefreshBtn.disabled = false;

                quickRefreshBtn.innerHTML = origText;

            }

            if (fullRefreshBtn) fullRefreshBtn.disabled = false;

            updateQuickRefreshButtonState();

        }

    }



    if (quickRefreshBtn) {

        quickRefreshBtn.addEventListener('click', handleQuickRefresh);

    }



    // Full Refresh: query Telegram server, rebuilds cache, 10–30s

    async function handleFullRefresh() {
        if (isFullRefreshing) return;
        isFullRefreshing = true;
        window.isFullRefreshing = true;

        const listEl = (typeof dialogsList !== 'undefined' && dialogsList) ? dialogsList : document.getElementById('dialogs-list');

        const origFullHtml = fullRefreshBtn ? fullRefreshBtn.innerHTML : '';

        const refreshingText = typeof I18N !== 'undefined' ? I18N.t('archive.refreshing') : 'Refreshing...';



        if (fullRefreshBtn) {

            fullRefreshBtn.disabled = true;

            fullRefreshBtn.innerHTML = `<span>🔄</span> <span>${refreshingText}</span>`;

        }

        if (quickRefreshBtn) quickRefreshBtn.disabled = true;

        if (modalTriggerFullBtn) modalTriggerFullBtn.disabled = true;

        const availBadge = document.getElementById('available-chats-badge');
        if (availBadge) availBadge.textContent = ' (🔄)';



        if (listEl) {

            const isRu = typeof I18N !== 'undefined' && I18N.currentLang === 'ru';

            const collectingText = typeof I18N !== 'undefined' ? I18N.t('archive.collecting') : 'Collecting data from Telegram...';

            const noticeText = isRu 

                ? 'Это может занять 10-30 секунд в зависимости от количества чатов.'

                : 'This may take 10-30 seconds depending on chat count.';

            listEl.innerHTML = `<li style="padding: 15px; text-align: center;">🔄 ${collectingText}<br><span style="font-size: 11px; color: var(--text-color-light, #888);">${noticeText}</span></li>`;

        }



        try {

            const response = await fetch('/api/refresh_dialogs');

            if (!response.ok) {

                const err = await response.json();

                throw new Error(err.error || 'Failed to refresh dialogs');

            }

            const dialogs = await response.json();

            // Clear refreshing state before rendering so window.applyArchiveFilters() executes
            isFullRefreshing = false;
            window.isFullRefreshing = false;

            const renderFn = (typeof renderDialogsList === 'function') ? renderDialogsList : window.renderDialogsList;
            if (typeof renderFn === 'function') {
                renderFn(dialogs);
            }
            if (typeof updateArchiveUI === 'function') {
                updateArchiveUI();
            }

        } catch (error) {

            console.error('Error refreshing dialogs from Telegram:', error);

            if (listEl) {

                listEl.innerHTML = `<li style="padding: 15px; color: #a13434; text-align: center;">Error: ${(window.escapeHTML || String)(error.message)}</li>`;

            }

        } finally {

            isFullRefreshing = false;
            window.isFullRefreshing = false;

            if (fullRefreshBtn) {

                fullRefreshBtn.disabled = false;

                fullRefreshBtn.innerHTML = origFullHtml;

            }

            if (quickRefreshBtn) quickRefreshBtn.disabled = false;

            if (modalTriggerFullBtn) modalTriggerFullBtn.disabled = false;

            updateQuickRefreshButtonState();

        }

    }



    if (fullRefreshBtn) {

        fullRefreshBtn.addEventListener('click', handleFullRefresh);

    }



    // Modal pivot button: trigger full refresh directly from hint modal

    if (modalTriggerFullBtn) {

        modalTriggerFullBtn.addEventListener('click', () => {

            if (window.RefreshHintModal) window.RefreshHintModal.close();

            handleFullRefresh();

        });

    }



    // Backward compatibility fallback if legacy button exists

    const legacyRefreshBtn = document.getElementById('refresh-dialogs-btn');

    if (legacyRefreshBtn) {

        legacyRefreshBtn.addEventListener('click', handleFullRefresh);

    }





    selectedChatIds = [];

    archiveEventSource = null;

    archiveStatusCache = {};

    liveLogBuffer = [];

    liveLogAnimFrame = null;



    if (typeof startArchiveStream === 'function') {

        startArchiveStream();

    }



    dialogsList.addEventListener('change', (e) => {

        if (e.target.classList.contains('dialog-checkbox')) {

            const cb = e.target;







            if (!cb.checked) cb.removeAttribute('data-selected-parts');



            let totalTasksCount = 0;

            dialogsList.querySelectorAll('.dialog-checkbox:checked').forEach(checkedCb => {

                if (checkedCb.dataset.selectedParts) totalTasksCount += JSON.parse(checkedCb.dataset.selectedParts).length;

                else totalTasksCount += 1;

            });



            if (totalTasksCount > 0) {

                if (startArchiveBtn.dataset.isArchiving !== "true") {

                    startArchiveBtn.disabled = false;

                    startArchiveBtn.textContent = `Начать архивацию (${totalTasksCount})`;

                }

            } else {

                if (startArchiveBtn.dataset.isArchiving !== "true") {

                    startArchiveBtn.disabled = true;

                    startArchiveBtn.textContent = 'Начать архивацию (0)';

                }

            }

        }

    });



    startArchiveBtn.addEventListener('click', async () => {

        if (startArchiveBtn.dataset.isArchiving === "true") {

            try {

                await fetch('/api/stop_archive', {

                    method: 'POST',

                    headers: { 'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '') }

                });

                startArchiveBtn.textContent = 'Останавливаем...';

                startArchiveBtn.disabled = true;

            } catch (e) { console.error('Ошибка остановки:', e); }

            return;

        }



        const settings = {

            photos: { enabled: document.getElementById('set-dl-photos').checked, max_mb: parseFloat(document.getElementById('set-sz-photos').value) || 0 },

            videos: { enabled: document.getElementById('set-dl-videos').checked, max_mb: parseFloat(document.getElementById('set-sz-videos').value) || 0 },

            voices: { enabled: document.getElementById('set-dl-voices').checked, max_mb: parseFloat(document.getElementById('set-sz-voices').value) || 0 },

            audio: { enabled: document.getElementById('set-dl-audio').checked, max_mb: parseFloat(document.getElementById('set-sz-audio').value) || 0 },

            files: { enabled: document.getElementById('set-dl-files').checked, max_mb: parseFloat(document.getElementById('set-sz-files').value) || 0 }

        };

        const logEl = document.getElementById('live-preview-log');

        if (logEl) logEl.innerHTML = ''; // Очистка лога

        lastPreviewStrings = {}; // Сброс трекера



        const tasks = [];

        

        if (window.selectedArchiveChats && window.selectedArchiveChats.size > 0) {

            window.selectedArchiveChats.forEach(chatId => {

                const d = (window.allDialogsData || []).find(x => String(x.id) === String(chatId));

                if (d) {

                    if (d.is_complex && d.parts) {

                        d.parts.forEach(part => tasks.push({ id: parseInt(part.id, 10), total: part.total_messages || 0 }));

                    } else {

                        tasks.push({ id: parseInt(d.id, 10), total: d.total_messages || 0 });

                    }

                }

                

                // Update DOM if it's currently rendered

                const cb = document.querySelector(`.dialog-checkbox[data-chat-id="${chatId}"]`);

                if (cb) {

                    const dialogItem = cb.closest('.dialog-item');

                    cb.disabled = true;

                    cb.checked = false;

                    const infoDiv = dialogItem.querySelector('.dialog-info');

                    if(infoDiv){

                        const statusEl = infoDiv.querySelector('.status');

                        if (statusEl) statusEl.style.display = 'none';

                    }

                    const barContainer = dialogItem.querySelector('.progress-bar-container');

                    if (barContainer) {

                        barContainer.style.display = 'block';

                        if (barContainer.querySelector('.messages-text')) barContainer.querySelector('.messages-text').textContent = 'Запуск...';

                        if (barContainer.querySelector('.media-text')) barContainer.querySelector('.media-text').textContent = 'Запуск...';

                        if (barContainer.querySelector('.messages-fill')) barContainer.querySelector('.messages-fill').style.width = '0%';

                        if (barContainer.querySelector('.media-fill')) barContainer.querySelector('.media-fill').style.width = '0%';

                    }

                    dialogItem.style.backgroundImage = `linear-gradient(90deg, rgba(76, 175, 80, 0.15) 0%, transparent 0%)`;

                }

            });

            window.selectedArchiveChats.clear();

        } else {

            const selectedCheckboxes = dialogsList.querySelectorAll('.dialog-checkbox:checked');

            selectedCheckboxes.forEach(cb => {

                const dialogItem = cb.closest('.dialog-item');

    

                if (cb.dataset.parts) {

                    const parts = JSON.parse(cb.dataset.parts);

                    parts.forEach(part => tasks.push({ id: parseInt(part.id, 10), total: part.total_messages || 0 }));

                } else {

                    const chat_id = cb.dataset.chatId;

                    const statusSpan = dialogItem.querySelector('.status.messages');

                    let total_messages = 0;

                    if (statusSpan) total_messages = parseInt(statusSpan.textContent.replace(/[^0-9]/g, ''), 10) || 0;

                    tasks.push({ id: parseInt(chat_id, 10), total: total_messages });

                }

    

                cb.disabled = true;

                cb.checked = false;

                const infoDiv = dialogItem.querySelector('.dialog-info');

                const statusEl = infoDiv.querySelector('.status');

                if (statusEl) statusEl.style.display = 'none';

                const barContainer = dialogItem.querySelector('.progress-bar-container');

                if (barContainer) {

                    barContainer.style.display = 'block';

                    if (barContainer.querySelector('.messages-text')) barContainer.querySelector('.messages-text').textContent = 'Запуск...';

                    if (barContainer.querySelector('.media-text')) barContainer.querySelector('.media-text').textContent = 'Запуск...';

                    if (barContainer.querySelector('.messages-fill')) barContainer.querySelector('.messages-fill').style.width = '0%';

                    if (barContainer.querySelector('.media-fill')) barContainer.querySelector('.media-fill').style.width = '0%';

                }

                dialogItem.style.backgroundImage = `linear-gradient(90deg, rgba(76, 175, 80, 0.15) 0%, transparent 0%)`;

            });

        }



        startArchiveBtn.disabled = true;

        startArchiveBtn.textContent = 'Архивация запущена...';

        selectedChatIds = [];



        try {

            const response = await fetch('/api/start_archive', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                },

                body: JSON.stringify({ tasks: tasks, settings: settings })

            });



            if (!response.ok) {

                const errData = await response.json().catch(() => ({}));

                throw new Error(errData.error || `HTTP ${response.status}`);

            }



            startArchiveStream();



        } catch (error) {

            console.error("Archive start error:", error);

            showNotification(`Ошибка: ${error.message}`, 'error');

            startArchiveBtn.textContent = 'Ошибка авторизации';



            // Re-select items so user can try again easily after fixing auth

            tasks.forEach(t => {

                const cb = document.querySelector(`.dialog-checkbox[data-chat-id="${t.id}"]`);

                if (cb) {

                    cb.checked = true;

                    selectedChatIds.push(t.id);

                }

            });

            startArchiveBtn.disabled = false;

        }

    });



    if (datePicker) {

        datePicker.addEventListener('change', performDateSearch);

    }



    // --- In-Chat Search in Header ---

    let chatSearchTimeout;

    const chatSearchToggleBtn = document.getElementById('chat-search-toggle-btn');

    const chatSearchWrap = document.getElementById('chat-search-wrap');

    const chatSearchInput = document.getElementById('chat-search-input');

    const chatCancelSearchBtn = document.getElementById('chat-cancel-search-btn');



    function closeInChatSearch() {

        if (chatSearchInput) chatSearchInput.value = '';

        if (chatCancelSearchBtn) chatCancelSearchBtn.style.display = 'none';

        if (chatSearchWrap) chatSearchWrap.style.display = 'none';

        if (chatSearchToggleBtn && currentChatId) chatSearchToggleBtn.style.display = 'inline-block';

        if (chatContainer && chatContainer.dataset.mode === 'search') {

            chatContainer.dataset.mode = 'chat';

            chatContainer.dataset.searchScope = '';

            document.body.classList.remove('search-mode-active');

            const activeItem = document.querySelector('.chat-list-item.active');

            if (activeItem) activeItem.click();

        }

    }



    if (chatSearchToggleBtn) {

        chatSearchToggleBtn.addEventListener('click', () => {

            if (chatSearchWrap) {

                const isHidden = (chatSearchWrap.style.display === 'none' || getComputedStyle(chatSearchWrap).display === 'none');

                if (isHidden) {

                    chatSearchWrap.style.display = 'flex';

                    chatSearchToggleBtn.style.display = 'none';

                    if (chatSearchInput) {

                        chatSearchInput.focus();

                        chatSearchInput.select();

                    }

                } else {

                    closeInChatSearch();

                }

            }

        });

    }



    if (chatSearchInput) {

        chatSearchInput.addEventListener('input', () => {

            clearTimeout(chatSearchTimeout);

            const val = chatSearchInput.value.trim();

            if (val.length > 0) {

                if (chatCancelSearchBtn) chatCancelSearchBtn.style.display = 'inline-flex';

            } else {

                if (chatCancelSearchBtn && !document.body.classList.contains('search-mode-active')) {

                    chatCancelSearchBtn.style.display = 'none';

                }

            }

            chatSearchTimeout = setTimeout(() => {

                if (typeof performChatSearch === 'function') performChatSearch();

            }, 300);

        });



        chatSearchInput.addEventListener('keydown', (e) => {

            if (e.key === 'Escape') {

                closeInChatSearch();

            } else if (e.key === 'Enter') {

                clearTimeout(chatSearchTimeout);

                if (typeof performChatSearch === 'function') performChatSearch();

            }

        });

    }



    if (chatCancelSearchBtn) {

        chatCancelSearchBtn.addEventListener('click', closeInChatSearch);

    }



    if (searchInput) {

        searchInput.addEventListener('input', () => {

            clearTimeout(searchTimeout);

            const val = searchInput.value.trim();

            if (val.length > 0) {

                if (cancelSearchBtn) cancelSearchBtn.style.display = 'inline-flex';

            } else {

                if (cancelSearchBtn && !document.body.classList.contains('search-mode-active')) {

                    cancelSearchBtn.style.display = 'none';

                }

            }

            searchTimeout = setTimeout(performSearch, 300);

        });



        searchInput.addEventListener('keydown', (e) => {

            if (e.key === 'Escape') {

                if (cancelSearchBtn) cancelSearchBtn.click();

            } else if (e.key === 'Enter') {

                clearTimeout(searchTimeout);

                performSearch();

            }

        });

    }



    if (cancelSearchBtn) {

        cancelSearchBtn.addEventListener('click', () => {

            document.body.classList.remove('search-mode-active');

            if (searchInput) searchInput.value = '';

            cancelSearchBtn.style.display = 'none';

            if (datePicker) datePicker.value = '';

            if (chatContainer) {

                chatContainer.dataset.mode = 'chat';

                chatContainer.dataset.searchScope = '';

            }

            if (archiveToggleBtn) archiveToggleBtn.style.display = 'block';

            const activeItem = document.querySelector('.chat-list-item.active');

            if (activeItem) activeItem.click(); else if (chatContainer) chatContainer.innerHTML = '';

        });

    }



    // --- Telegram Sidebar Menu (☰) and Flyout Wiring ---

    const sidebarMenuBtn = document.getElementById('sidebar-menu-btn');

    const sidebarMenuFlyout = document.getElementById('sidebar-menu-flyout');



    if (sidebarMenuBtn && sidebarMenuFlyout) {

        const toggleMenuFlyout = (e) => {

            e.stopPropagation();

            const isOpen = sidebarMenuFlyout.style.display !== 'none';

            if (isOpen) {

                sidebarMenuFlyout.style.display = 'none';

                sidebarMenuBtn.classList.remove('active');

            } else {

                sidebarMenuFlyout.style.display = 'flex';

                sidebarMenuBtn.classList.add('active');

            }

        };



        const closeMenuFlyout = () => {

            if (sidebarMenuFlyout.style.display !== 'none') {

                sidebarMenuFlyout.style.display = 'none';

                sidebarMenuBtn.classList.remove('active');

            }

        };



        sidebarMenuBtn.addEventListener('click', toggleMenuFlyout);



        // Close on clicking outside

        document.addEventListener('click', (e) => {

            if (!sidebarMenuFlyout.contains(e.target) && !sidebarMenuBtn.contains(e.target)) {

                closeMenuFlyout();

            }

        });



        // Close on Esc key

        document.addEventListener('keydown', (e) => {

            if (e.key === 'Escape') {

                closeMenuFlyout();

            }

        });



        // Close flyout when any flyout action button is clicked

        sidebarMenuFlyout.querySelectorAll('.flyout-btn').forEach(btn => {

            btn.addEventListener('click', () => {

                setTimeout(closeMenuFlyout, 150);

            });

        });

    }







































    if (showAllUsersBtn) {

        showAllUsersBtn.addEventListener('click', () => {

            renderTopUsersList(allChatUsers, false);

        });

    }





    // --- ЛОГИКА ПРАВОЙ ПАНЕЛИ (Сплит-вкладки Stats / Media) ---

    const rsTabStats = document.getElementById('rs-tab-stats');
    const rsTabMedia = document.getElementById('rs-tab-media');
    const rsTabExport = document.getElementById('rs-tab-export');

    const rsContentStats = document.getElementById('rs-stats');
    const rsContentMedia = document.getElementById('rs-media');
    const rsContentExport = document.getElementById('rs-export');

    function switchRightSidebarTab(target) {
        const mediaNoChatView = document.getElementById('media-no-chat-view');
        const mediaCategoriesView = document.getElementById('media-categories-view');
        const mediaDetailView = document.getElementById('media-detail-view');
        const pureDbStatsBlock = document.getElementById('pure-db-stats-block');
        const chatStatsBlock = document.getElementById('chat-stats-block');

        if (target === 'export') {
            if (rsTabExport) rsTabExport.classList.add('active');
            if (rsTabStats) rsTabStats.classList.remove('active');
            if (rsTabMedia) rsTabMedia.classList.remove('active');
            if (rsContentExport) rsContentExport.style.display = 'block';
            if (rsContentStats) rsContentStats.style.display = 'none';
            if (rsContentMedia) rsContentMedia.style.display = 'none';
            localStorage.setItem('sidebar_active_tab', 'export');
            if (window.ExportManager) {
                window.ExportManager.loadChatExportsHistory();
            }
        } else if (target === 'media') {
            if (rsTabMedia) rsTabMedia.classList.add('active');
            if (rsTabStats) rsTabStats.classList.remove('active');
            if (rsTabExport) rsTabExport.classList.remove('active');
            if (rsContentMedia) rsContentMedia.style.display = 'flex';
            if (rsContentStats) rsContentStats.style.display = 'none';
            if (rsContentExport) rsContentExport.style.display = 'none';
            localStorage.setItem('sidebar_active_tab', 'media');

            if (currentChatId) {
                if (mediaNoChatView) mediaNoChatView.style.display = 'none';
                if (!mediaDetailView || mediaDetailView.style.display === 'none') {
                    if (mediaCategoriesView) mediaCategoriesView.style.display = 'flex';
                }
                if (typeof loadMediaCategories === 'function') {
                    loadMediaCategories();
                }
            } else {
                if (mediaNoChatView) mediaNoChatView.style.display = 'block';
                if (mediaCategoriesView) mediaCategoriesView.style.display = 'none';
                if (mediaDetailView) mediaDetailView.style.display = 'none';
            }
        } else {
            // Default to 'stats'
            if (rsTabStats) rsTabStats.classList.add('active');
            if (rsTabMedia) rsTabMedia.classList.remove('active');
            if (rsTabExport) rsTabExport.classList.remove('active');
            if (rsContentStats) rsContentStats.style.display = 'block';
            if (rsContentMedia) rsContentMedia.style.display = 'none';
            if (rsContentExport) rsContentExport.style.display = 'none';
            localStorage.setItem('sidebar_active_tab', 'stats');

            if (!currentChatId) {
                if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'block';
                if (chatStatsBlock) chatStatsBlock.style.display = 'none';
                if (typeof loadPureDbStats === 'function') {
                    loadPureDbStats();
                }
            } else {
                if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'none';
                if (chatStatsBlock) chatStatsBlock.style.display = 'block';
            }
        }
    }

    window.switchRightSidebarTab = switchRightSidebarTab;

    if (rsTabStats) {
        rsTabStats.addEventListener('click', () => {
            switchRightSidebarTab('stats');
        });
    }

    if (rsTabMedia) {
        rsTabMedia.addEventListener('click', () => {
            switchRightSidebarTab('media');
        });
    }

    if (rsTabExport) {
        rsTabExport.addEventListener('click', () => {
            switchRightSidebarTab('export');
        });
    }



    // Remembered or 'stats' by default

    const savedSidebarTab = localStorage.getItem('sidebar_active_tab') || 'stats';

    switchRightSidebarTab(savedSidebarTab === 'media' ? 'media' : 'stats');







    // --- ЛОГИКА СКРЫТИЯ ПАНЕЛЕЙ (Адаптивность) ---

    const rightSidebar = document.getElementById('right-sidebar');

    const closeInfoBtn = document.getElementById('close-info-btn');

    const chatHeaderInfo = document.getElementById('chat-header-info');



    // Включаем отображение крестика в правой панели (в HTML он был скрыт style="display: none;")

    if (closeInfoBtn) {

        closeInfoBtn.style.display = 'block';



        closeInfoBtn.addEventListener('click', () => {

            // Скрываем панель

            rightSidebar.classList.add('hidden-panel');

            rightSidebar.classList.remove('mobile-active');

        });

    }



    // Клик по имени чата в центре открывает правую панель обратно

    if (chatHeaderInfo) {

        chatHeaderInfo.addEventListener('click', () => {

            rightSidebar.classList.remove('hidden-panel');



            // Если экран маленький, показываем панель поверх чата

            if (window.innerWidth <= 850) {

                rightSidebar.classList.add('mobile-active');

            }

        });

    }



    // --- Глобальная функция для открытия чата из пузырька ---

    window.openChatFromBubble = function (chatId) {

        // Ищем элемент чата в левом меню по всем возможным селекторам

        const sidebarItem = document.querySelector(`#custom-chat-list [data-chat-id="${chatId}"]`) ||

            document.querySelector(`#custom-chat-list [data-id="${chatId}"]`) ||

            document.querySelector(`.chat-list-item[data-chat-id="${chatId}"]`);



        if (sidebarItem) {

            sidebarItem.click(); // Имитируем физический клик

        } else {

            // Фоллбэк: если сайдбар работает через выпадающий список <select>

            const select = document.getElementById('chat-select');

            if (select) {

                select.value = chatId;

                select.dispatchEvent(new Event('change'));

            }

        }

        // Переключаем интерфейс обратно на окно чтения

        switchView('chat');

    };



    // --- Глобальный детектор Личных Сообщений (ЛС) ---

    document.addEventListener('click', (e) => {

        const chatItem = e.target.closest('.chat-list-item');

        if (chatItem && chatItem.dataset.chatId) {

            const chatId = parseInt(chatItem.dataset.chatId, 10);

            const chatContainer = document.getElementById('chat-container');

            if (chatContainer) {

                // Если ID > 0, значит это человек (ЛС)

                if (chatId > 0) {

                    chatContainer.classList.add('is-pm');

                } else {

                    chatContainer.classList.remove('is-pm');

                }

            }

        }

    });





    // ──────────────────────────────────────────────────────────────────

    // ── ChatManage: delete / reload archived chats ────────────────────

    // ──────────────────────────────────────────────────────────────────

    ChatManage = (() => {

        const modal = document.getElementById('chat-manage-modal');

        const chatnameEl = document.getElementById('manage-modal-chatname');

        const input = document.getElementById('manage-confirm-input');

        const confirmBtn = document.getElementById('manage-confirm-delete-btn');

        const cancelBtn = document.getElementById('manage-cancel-btn');



        function openDeleteModal(chatId, chatName) {

            _pendingChatId = chatId;

            _pendingChatName = chatName;

            chatnameEl.textContent = chatName;

            input.value = '';

            confirmBtn.disabled = true;

            modal.classList.add('open');

            setTimeout(() => input.focus(), 80);

        }



        function closeModal() {

            modal.classList.remove('open');

            _pendingChatId = null;

            _pendingChatName = '';

            input.value = '';

            confirmBtn.disabled = true;

        }



        let _reloadPendingChatId = null;
        let _reloadPendingChatName = '';
        let _reloadPendingSavedCount = 0;

        const reloadModal = document.getElementById('chat-reload-modal');
        const reloadChatnameEl = document.getElementById('reload-modal-chatname');
        const reloadWarningEl = document.getElementById('reload-modal-warning');
        const reloadCancelBtn = document.getElementById('reload-cancel-btn');
        const reloadFastBtn = document.getElementById('reload-action-fast-btn');
        const reloadFullBtn = document.getElementById('reload-action-full-btn');

        function openReloadModal(chatId, chatName, savedCount) {
            _reloadPendingChatId = chatId;
            _reloadPendingChatName = chatName;
            _reloadPendingSavedCount = savedCount;

            const scanCount = parseInt(localStorage.getItem('tb_hor_scan_count_' + chatId) || '0', 10);
            if (scanCount === 0) {
                // First time / main mode: run fast update + forced O(1) gap scan directly
                _executeReload(chatId, chatName, savedCount, true);
                return;
            }

            if (!reloadModal) {
                _executeReload(chatId, chatName, savedCount, false);
                return;
            }

            if (reloadChatnameEl) reloadChatnameEl.textContent = chatName;

            if (reloadWarningEl) {
                reloadWarningEl.style.display = 'block';
            }

            reloadModal.style.display = 'flex';
        }

        function closeReloadModal() {
            if (reloadModal) reloadModal.style.display = 'none';
            _reloadPendingChatId = null;
            _reloadPendingChatName = '';
            _reloadPendingSavedCount = 0;
        }

        if (reloadCancelBtn) {
            reloadCancelBtn.addEventListener('click', closeReloadModal);
        }
        if (reloadModal) {
            reloadModal.addEventListener('click', (e) => {
                if (e.target === reloadModal) closeReloadModal();
            });
        }
        if (reloadFastBtn) {
            reloadFastBtn.addEventListener('click', () => {
                const cId = _reloadPendingChatId;
                const cName = _reloadPendingChatName;
                const sCount = _reloadPendingSavedCount;
                closeReloadModal();
                if (cId) _executeReload(cId, cName, sCount, false);
            });
        }
        if (reloadFullBtn) {
            reloadFullBtn.addEventListener('click', () => {
                const cId = _reloadPendingChatId;
                const cName = _reloadPendingChatName;
                const sCount = _reloadPendingSavedCount;
                closeReloadModal();
                if (cId) _executeReload(cId, cName, sCount, true);
            });
        }

        async function _executeReload(chatId, chatName, savedCount, reconcileGaps) {
            const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;
            const qToast = (typeof notifyFn === 'function')
                ? notifyFn(I18N.t('manage.adding_queue', chatName), 'info', true)
                : null;
            try {
                const res = await fetch('/api/reload_chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                    },
                    body: JSON.stringify({ 
                        chat_id: chatId, 
                        total: savedCount, 
                        settings: _getSettings(),
                        reconcile_gaps: reconcileGaps
                    })
                });
                const data = await res.json();
                if (!res.ok) {
                    if (qToast && typeof qToast.error === 'function') {
                        qToast.error(I18N.t('error.generic', data.error || res.statusText));
                    } else if (typeof notifyFn === 'function') {
                        notifyFn(I18N.t('error.generic', data.error || res.statusText), 'error');
                    }
                    return;
                }

                if (reconcileGaps) {
                    const count = parseInt(localStorage.getItem('tb_hor_scan_count_' + chatId) || '0', 10);
                    localStorage.setItem('tb_hor_scan_count_' + chatId, String(count + 1));
                }

                const successMsg = reconcileGaps
                    ? (I18N.t('manage.added_queue', chatName) + ' + O(1) Gap Reconcile')
                    : I18N.t('manage.added_queue', chatName);

                if (qToast && typeof qToast.success === 'function') {
                    qToast.success(successMsg);
                } else if (typeof notifyFn === 'function') {
                    notifyFn(successMsg, 'success');
                }
                switchView('archive');
                startArchiveStream();
                loadAllDialogs(true);
            } catch (e) {
                if (qToast && typeof qToast.error === 'function') {
                    qToast.error(I18N.t('error.generic', e.message));
                } else if (typeof notifyFn === 'function') {
                    notifyFn(I18N.t('error.generic', e.message), 'error');
                }
            }
        }

        function reload(chatId, chatName, savedCount) {
            openReloadModal(chatId, chatName, savedCount);
        }



        async function _executeDelete() {

            const chatId = _pendingChatId;

            const chatName = _pendingChatName;

            confirmBtn.disabled = true;

            confirmBtn.textContent = I18N.t('manage.deleting');



            const notifyFn = (typeof showNotification === 'function') ? showNotification : window.showNotification;

            const delToast = (typeof notifyFn === 'function')

                ? notifyFn(I18N.t('manage.deleting') || 'Deleting chat...', 'info', true)

                : null;



            try {

                const res = await fetch('/api/delete_chat', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')

                    },

                    body: JSON.stringify({ chat_id: chatId })

                });

                const data = await res.json();

                closeModal();

                confirmBtn.textContent = I18N.t('btn.delete_plain');



                if (!res.ok) {

                    if (delToast && typeof delToast.error === 'function') {

                        delToast.error(I18N.t('error.delete', data.error || res.statusText));

                    } else if (typeof notifyFn === 'function') {

                        notifyFn(I18N.t('error.delete', data.error || res.statusText), 'error');

                    }

                    return;

                }



                if (delToast && typeof delToast.success === 'function') {

                    delToast.success(I18N.t('manage.deleted_ok', chatName));

                } else if (typeof notifyFn === 'function') {

                    notifyFn(I18N.t('manage.deleted_ok', chatName), 'success');

                }



                if (String(currentChatId) === String(chatId)) {

                    chatContainer.innerHTML = '';

                    currentChatId = null; window.currentChatId = null;

                    const ht = document.getElementById('chat-header-title');

                    if (ht) ht.textContent = I18N.t('header.chat');

                    if (chatAvatar) chatAvatar.style.display = 'none';

                    const pureDbStatsBlock = document.getElementById('pure-db-stats-block');

                    const chatStatsBlock = document.getElementById('chat-stats-block');

                    if (pureDbStatsBlock) pureDbStatsBlock.style.display = 'block';

                    if (chatStatsBlock) chatStatsBlock.style.display = 'none';

                    if (typeof loadPureDbStats === 'function') loadPureDbStats();

                }

                await loadSidebarChats();

                await loadAllDialogs(true);

                if (typeof updateGlobalStats === 'function') updateGlobalStats();

            } catch (e) {

                showNotification(I18N.t('error.generic', e.message), 'error');

                confirmBtn.disabled = false;

                confirmBtn.textContent = I18N.t('btn.delete_plain');

            }

        }



        function _getSettings() {

            return {

                photos: { enabled: document.getElementById('set-dl-photos')?.checked ?? true, max_mb: parseInt(document.getElementById('set-sz-photos')?.value || 0) || 0 },

                videos: { enabled: document.getElementById('set-dl-videos')?.checked ?? true, max_mb: parseInt(document.getElementById('set-sz-videos')?.value || 0) || 0 },

                voices: { enabled: document.getElementById('set-dl-voices')?.checked ?? true, max_mb: parseInt(document.getElementById('set-sz-voices')?.value || 0) || 0 },

                audio: { enabled: document.getElementById('set-dl-audio')?.checked ?? true, max_mb: parseInt(document.getElementById('set-sz-audio')?.value || 0) || 0 },

                files: { enabled: document.getElementById('set-dl-files')?.checked ?? true, max_mb: parseInt(document.getElementById('set-sz-files')?.value || 0) || 0 },

            };

        }



        input.addEventListener('input', () => {

            confirmBtn.disabled = (input.value.trim() !== _pendingChatName.trim());

        });

        confirmBtn.addEventListener('click', () => {

            if (input.value.trim() === _pendingChatName.trim()) _executeDelete();

        });

        cancelBtn.addEventListener('click', closeModal);

        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });



        return { openDeleteModal, reload };

    })();



    window.updateRenderedMessageCount = updateRenderedMessageCount;
    init();
});
