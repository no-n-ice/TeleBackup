// shortcuts.js — Global keyboard shortcuts and cheatsheet modal
(function () {
    let cheatsheetModal;

    function initShortcuts() {
        cheatsheetModal = document.getElementById('shortcuts-modal');

        document.addEventListener('keydown', (e) => {
            const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
            const isTyping = activeTag === 'input' || activeTag === 'textarea' || document.activeElement?.isContentEditable;

            // Escape closes modals
            if (e.key === 'Escape') {
                if (cheatsheetModal && cheatsheetModal.style.display !== 'none') {
                    cheatsheetModal.style.display = 'none';
                }
                const refreshHint = document.getElementById('refresh-hint-modal');
                if (refreshHint && refreshHint.style.display !== 'none') {
                    refreshHint.style.display = 'none';
                }
                return;
            }

            // Cheatsheet toggle: '?'
            if (!isTyping && e.key === '?') {
                e.preventDefault();
                toggleCheatsheet();
                return;
            }

            // Search focus: Ctrl+K or '/'
            if ((e.ctrlKey && e.key === 'k') || (!isTyping && e.key === '/')) {
                e.preventDefault();
                const searchInput = document.getElementById('search-input');
                if (searchInput) {
                    searchInput.focus();
                    searchInput.select();
                }
                return;
            }

            // In-chat search focus: Ctrl+F
            if (e.ctrlKey && e.key === 'f') {
                const searchToggle = document.getElementById('chat-search-toggle-btn') || document.getElementById('search-toggle-btn');
                const inChatInput = document.getElementById('chat-search-input') || document.getElementById('inchat-search-input');
                const chatWrap = document.getElementById('chat-search-wrap');

                e.preventDefault();
                if (chatWrap && (chatWrap.style.display === 'none' || getComputedStyle(chatWrap).display === 'none') && searchToggle) {
                    searchToggle.click();
                } else if (inChatInput) {
                    inChatInput.focus();
                    inChatInput.select();
                } else if (searchToggle) {
                    searchToggle.click();
                }
            }
        });
    }

    function toggleCheatsheet() {
        if (!cheatsheetModal) return;
        cheatsheetModal.style.display = cheatsheetModal.style.display === 'none' ? 'flex' : 'none';
    }

    window.toggleCheatsheet = toggleCheatsheet;
    document.addEventListener('DOMContentLoaded', initShortcuts);
})();
