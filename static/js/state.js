// state.js — Central reactive application state
(function() {
    window.AppState = {
        currentChatId: null,
        currentChatBaseName: '',
        allUsers: {},
        allCustomEmoji: {},
        myUserId: localStorage.getItem('myUserId'),
        chatListData: [],
        
        // Pagination & Loading
        oldestPageLoaded: 1,
        newestPageLoaded: 1,
        isLoading: false,
        noMoreOlderMessages: false,
        noMoreNewerMessages: false,
        isChatLoading: false,
        
        // Stats & counts
        allChatUsers: [],
        totalMessagesInChat: 0,
        renderedMessagesCount: 0,
        activityChartInstance: null,
        
        // Media viewer & pagination
        currentMediaType: 'photos',
        currentMediaPage: 1,
        
        // Archive & live
        selectedChatIds: [],
        archiveStatusCache: {},
        liveLogBuffer: [],
        
        // Rendering tracking
        lastRenderedDate: null,
        lastSenderId: null,
        lastBatchHadMedia: false,
        scrollAfterLoad: null,
        forceScrollToBottom: false,
        lastPreviewStrings: {}
    };

    // Forward compatibility getters/setters on window
    Object.defineProperty(window, 'currentChatId', {
        get() { return window.AppState.currentChatId; },
        set(val) { window.AppState.currentChatId = val; },
        configurable: true
    });
    Object.defineProperty(window, 'allUsers', {
        get() { return window.AppState.allUsers; },
        set(val) { window.AppState.allUsers = val; },
        configurable: true
    });
    Object.defineProperty(window, 'allCustomEmoji', {
        get() { return window.AppState.allCustomEmoji; },
        set(val) { window.AppState.allCustomEmoji = val; },
        configurable: true
    });
    Object.defineProperty(window, 'myUserId', {
        get() { return window.AppState.myUserId; },
        set(val) { window.AppState.myUserId = val; },
        configurable: true
    });
})();
