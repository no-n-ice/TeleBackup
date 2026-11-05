// folders.js — Telegram Dialog Filters & Custom Folders Management
(function () {
    let activeType = 'all';
    let activeFolderId = 'none';
    let foldersData = [];
    let currentAssignFolderId = null;

    function getCsrf() {
        return (window.getCsrfToken ? window.getCsrfToken() : '');
    }

    function initFolders() {
        const foldersBtn = document.getElementById('sidebar-folders-btn');
        const folderBar = document.getElementById('chat-folders-bar');
        const typeSelect = document.getElementById('folder-sort-type');
        const catSelect = document.getElementById('folder-category-select');
        const clearBtn = document.getElementById('folder-clear-btn');
        const manageBtn = document.getElementById('folder-manage-btn');

        if (!folderBar) return;

        // Restore open/closed state (default open so user immediately sees it)
        const storedState = localStorage.getItem('tb_folders_bar_open');
        const isBarOpen = (storedState === null) ? true : (storedState === '1');

        if (isBarOpen) {
            folderBar.classList.add('open');
            folderBar.style.display = 'flex';
            if (foldersBtn) foldersBtn.classList.add('active');
        } else {
            folderBar.classList.remove('open');
            folderBar.style.display = 'none';
            if (foldersBtn) foldersBtn.classList.remove('active');
        }

        // Toggle button handler
        if (foldersBtn) {
            foldersBtn.addEventListener('click', () => {
                const nowOpen = folderBar.classList.toggle('open');
                folderBar.style.display = nowOpen ? 'flex' : 'none';
                foldersBtn.classList.toggle('active', nowOpen);
                localStorage.setItem('tb_folders_bar_open', nowOpen ? '1' : '0');
            });
        }

        // 1. Sort / Filter by Type
        if (typeSelect) {
            typeSelect.addEventListener('change', () => {
                activeType = typeSelect.value;
                filterChatList();
            });
        }

        // 2. Categorise by Folder
        if (catSelect) {
            catSelect.addEventListener('change', () => {
                if (catSelect.value === '__manage__') {
                    catSelect.value = activeFolderId;
                    FoldersManager.openModal();
                    return;
                }
                activeFolderId = catSelect.value;
                filterChatList();
            });
        }

        // Manage button ⚙️
        if (manageBtn) {
            manageBtn.addEventListener('click', () => {
                FoldersManager.openModal();
            });
        }

        // 3. Clear Selection (simple X)
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                clearSelection();
            });
        }

        // Fetch stored folders
        loadFolders();

        // Bind right-click on chat items
        bindChatContextMenu();
    }

    function clearSelection() {
        activeType = 'all';
        activeFolderId = 'none';
        const typeSelect = document.getElementById('folder-sort-type');
        const catSelect = document.getElementById('folder-category-select');
        if (typeSelect) typeSelect.value = 'all';
        if (catSelect) catSelect.value = 'none';
        filterChatList();
    }

    async function loadFolders() {
        try {
            const res = await fetch('/api/folders');
            if (!res.ok) return;
            const data = await res.json();
            foldersData = data.folders || [];
            window.foldersData = foldersData;
            populateCategoryDropdown();
            filterChatList();
        } catch (e) {
            console.error('[Folders] Failed to load folders:', e);
        }
    }

    function populateCategoryDropdown() {
        const catSelect = document.getElementById('folder-category-select');
        if (!catSelect) return;

        let html = '<option value="none">Folder: All</option>';
        foldersData.forEach(f => {
            const icon = escapeHtml(f.emoticon || '📁');
            const countStr = f.chat_count !== undefined ? ` (${Number(f.chat_count) || 0})` : '';
            html += `<option value="${escapeHtml(f.folder_id)}">${icon} ${escapeHtml(f.title)}${countStr}</option>`;
        });
        html += '<option value="__manage__">➕ Manage Folders...</option>';

        catSelect.innerHTML = html;
        catSelect.value = activeFolderId || 'none';
    }

    // --- Main Filtering Logic ---
    function filterChatList() {
        const items = document.querySelectorAll('#custom-chat-list > .chat-list-item');
        if (!items.length) return;

        const currentFolder = foldersData.find(f => String(f.folder_id) === String(activeFolderId));
        const folderChatSet = currentFolder ? new Set(currentFolder.chat_ids.map(Number)) : null;

        items.forEach(item => {
            const cid = Number(item.dataset.chatId);
            const type = (item.dataset.chatType || 'user').toLowerCase();

            // 1. Check Chat Type Filter
            let passType = false;
            if (activeType === 'all') {
                passType = true;
            } else if (activeType === 'channels') {
                passType = (type === 'channel');
            } else if (activeType === 'groups') {
                passType = (type === 'group' || type === 'supergroup');
            } else if (activeType === 'dms') {
                passType = (type === 'user' || type === 'private');
            } else if (activeType === 'bots') {
                passType = (type === 'bot');
            }

            // 2. Check Folder Membership Filter
            let passFolder = true;
            if (activeFolderId !== 'none' && currentFolder) {
                if (folderChatSet && folderChatSet.has(cid)) {
                    passFolder = true;
                } else if (currentFolder.flags) {
                    const fl = currentFolder.flags;
                    let flagMatch = false;
                    if (fl.broadcasts && type === 'channel') flagMatch = true;
                    if (fl.groups && (type === 'group' || type === 'supergroup')) flagMatch = true;
                    if (fl.bots && type === 'bot') flagMatch = true;
                    if ((fl.contacts || fl.non_contacts) && (type === 'user' || type === 'private')) flagMatch = true;
                    passFolder = flagMatch;
                } else {
                    passFolder = false;
                }
            }

            const show = passType && passFolder;
            item.style.display = show ? 'flex' : 'none';

            // Also synchronize nested discussion groups
            const nextEl = item.nextElementSibling;
            if (nextEl && nextEl.classList.contains('nested-chat-list')) {
                if (!show) nextEl.style.display = 'none';
            }
        });
    }

    // --- Folders Manager Modal Controller ---
    const FoldersManager = {
        openModal() {
            const modal = document.getElementById('folders-manager-modal');
            if (!modal) return;
            modal.style.display = 'flex';
            this.renderFoldersList();
            this.closeAssignDrawer();
        },

        closeModal() {
            this.closeAssignDrawer();
            const modal = document.getElementById('folders-manager-modal');
            if (modal) modal.style.display = 'none';
            const contextMenu = document.getElementById('folder-context-menu');
            if (contextMenu) contextMenu.style.display = 'none';
        },

        toggleCreateForm(show) {
            const form = document.getElementById('fm-create-form');
            if (!form) return;
            const willShow = (show !== undefined) ? show : (form.style.display === 'none');
            form.style.display = willShow ? 'block' : 'none';
            if (willShow) {
                const titleInput = document.getElementById('fm-new-title');
                if (titleInput) {
                    titleInput.value = '';
                    titleInput.focus();
                }
            }
        },

        async submitCreateFolder() {
            const titleInput = document.getElementById('fm-new-title');
            const emoInput = document.getElementById('fm-new-emoticon');
            const title = titleInput ? titleInput.value.trim() : '';
            const emoticon = emoInput ? emoInput.value.trim() : '📁';

            if (!title) {
                alert('Please enter a folder title');
                return;
            }

            try {
                const csrf = getCsrf();
                const res = await fetch('/api/folders/create', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': csrf
                    },
                    body: JSON.stringify({ title, emoticon, auth_csrf_token: csrf })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(`Failed to create folder: ${data.error || 'Server error'}`);
                    return;
                }
                const createdFolder = data.folder;
                this.toggleCreateForm(false);
                await loadFolders();
                this.renderFoldersList();
                if (createdFolder && createdFolder.folder_id) {
                    this.openAssignDrawer(createdFolder.folder_id);
                }
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        async deleteFolder(folderId) {
            if (!confirm('Are you sure you want to delete this folder?')) return;
            try {
                const csrf = getCsrf();
                const res = await fetch('/api/folders/delete', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': csrf
                    },
                    body: JSON.stringify({ folder_id: folderId, auth_csrf_token: csrf })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(`Failed to delete folder: ${data.error || 'Server error'}`);
                    return;
                }
                if (activeFolderId === folderId) {
                    activeFolderId = 'none';
                }
                await loadFolders();
                this.renderFoldersList();
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        async syncTelegramFolders() {
            const syncBtn = document.getElementById('fm-sync-btn');
            if (syncBtn) {
                syncBtn.disabled = true;
                syncBtn.innerHTML = '⏳ <span>Syncing with Telegram...</span>';
            }
            try {
                const csrf = getCsrf();
                const res = await fetch('/api/folders/sync', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': csrf
                    },
                    body: JSON.stringify({ auth_csrf_token: csrf })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(`Telegram Sync failed: ${data.error || 'Client not online'}`);
                    return;
                }
                alert(`✅ Successfully synced ${data.synced_count} folder(s) from Telegram!`);
                await loadFolders();
                this.renderFoldersList();
            } catch (e) {
                alert(`Sync error: ${e.message}`);
            } finally {
                if (syncBtn) {
                    syncBtn.disabled = false;
                    syncBtn.innerHTML = '🔄 <span>Sync from Telegram</span>';
                }
            }
        },

        renderFoldersList() {
            const container = document.getElementById('fm-folders-list');
            if (!container) return;

            if (!foldersData.length) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 24px; opacity: 0.6; font-size: 12px;">
                        No folders configured yet.<br>Click <b>Sync from Telegram</b> to harvest your Telegram folders or <b>New Custom Folder</b> to create one locally.
                    </div>
                `;
                return;
            }

            container.innerHTML = foldersData.map(f => {
                const isTg = (f.source === 'telegram');
                const badgeColor = isTg ? '#2481cc' : '#8b5cf6';
                const badgeLabel = isTg ? 'Telegram' : 'Custom';
                const count = f.chat_count !== undefined ? f.chat_count : (f.chat_ids ? f.chat_ids.length : 0);

                const safeFid = escapeHtml(f.folder_id);
                return `
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); border-radius: 6px;">
                        <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
                            <span style="font-size: 16px;">${escapeHtml(f.emoticon || '📁')}</span>
                            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                <div style="font-weight: 600; font-size: 13px;">${escapeHtml(f.title)}</div>
                                <div style="font-size: 11px; opacity: 0.7; display: flex; gap: 6px; align-items: center;">
                                    <span style="background: ${badgeColor}; color: white; padding: 1px 5px; border-radius: 4px; font-size: 9px; font-weight: 600;">${badgeLabel}</span>
                                    <span class="fm-folder-chat-count" data-folder-id="${safeFid}">${Number(count) || 0} chat${count === 1 ? '' : 's'}</span>
                                </div>
                            </div>
                        </div>
                        <div style="display: flex; gap: 6px; flex-shrink: 0;">
                            <button type="button" class="action-btn secondary-btn" data-folder-id="${safeFid}" onclick="FoldersManager.openAssignDrawer(this.dataset.folderId)" style="padding: 3px 8px; font-size: 11px;">
                                ✏️ Manage
                            </button>
                            <button type="button" class="action-btn secondary-btn" data-folder-id="${safeFid}" onclick="FoldersManager.deleteFolder(this.dataset.folderId)" style="padding: 3px 6px; font-size: 11px; color: #ef4444;" title="Delete folder">
                                🗑️
                            </button>
                        </div>
                    </div>
                `;
            }).join('');
        },

        openAssignDrawer(folderId) {
            currentAssignFolderId = folderId;
            const folder = foldersData.find(f => String(f.folder_id) === String(folderId));
            if (!folder) return;

            const drawer = document.getElementById('fm-assign-drawer');
            const titleEl = document.getElementById('fm-assign-title');
            if (drawer) drawer.style.display = 'block';
            if (titleEl) titleEl.textContent = `Chats in ${folder.emoticon || '📁'} ${folder.title}:`;

            this.renderAssignChatsList();

            const searchInput = document.getElementById('fm-assign-search');
            if (searchInput) {
                searchInput.value = '';
                searchInput.oninput = () => this.renderAssignChatsList(searchInput.value.trim().toLowerCase());
            }

            if (drawer && typeof drawer.scrollIntoView === 'function') {
                drawer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        },

        closeAssignDrawer() {
            currentAssignFolderId = null;
            const drawer = document.getElementById('fm-assign-drawer');
            if (drawer) drawer.style.display = 'none';
            const searchInput = document.getElementById('fm-assign-search');
            if (searchInput) searchInput.value = '';
            this.renderFoldersList();
        },

        renderAssignChatsList(filterQuery = '') {
            const listEl = document.getElementById('fm-assign-chats-list');
            if (!listEl || !currentAssignFolderId) return;

            const folder = foldersData.find(f => String(f.folder_id) === String(currentAssignFolderId));
            if (!folder) return;

            const memberSet = new Set((folder.chat_ids || []).map(Number));
            const allChats = (window.chatListData || []);

            const matchingChats = allChats.filter(c => {
                if (!filterQuery) return true;
                const name = (c.chat_name || '').toLowerCase();
                const id = String(c.chat_id);
                return name.includes(filterQuery) || id.includes(filterQuery);
            });

            if (!matchingChats.length) {
                listEl.innerHTML = '<div style="font-size: 11px; opacity: 0.6; padding: 8px; text-align: center;">No matching chats found.</div>';
                return;
            }

            listEl.innerHTML = matchingChats.map(c => {
                const cid = Number(c.chat_id);
                const isMember = memberSet.has(cid);
                const typeIcon = c.chat_type === 'channel' ? '📢' : (c.chat_type === 'bot' ? '🤖' : (c.chat_type === 'user' ? '👤' : '👥'));

                return `
                    <label style="display: flex; align-items: center; justify-content: space-between; padding: 4px 6px; border-radius: 4px; background: rgba(255,255,255,0.02); font-size: 12px; cursor: pointer;">
                        <div style="display: flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                            <span>${typeIcon}</span>
                            <span style="font-weight: 500;">${escapeHtml(c.chat_name || `ID ${c.chat_id}`)}</span>
                        </div>
                        <input type="checkbox" ${isMember ? 'checked' : ''} data-folder-id="${escapeHtml(currentAssignFolderId)}" onchange="FoldersManager.toggleChatMembership(this.dataset.folderId, ${cid}, this.checked)">
                    </label>
                `;
            }).join('');
        },

        async toggleChatMembership(folderId, chatId, isMember) {
            try {
                const csrf = getCsrf();
                const action = isMember ? 'add' : 'remove';
                const res = await fetch('/api/folders/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': csrf
                    },
                    body: JSON.stringify({
                        folder_id: folderId,
                        chat_id: chatId,
                        action: action,
                        auth_csrf_token: csrf
                    })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(`Failed: ${data.error || 'Error'}`);
                    return;
                }
                const folder = foldersData.find(f => String(f.folder_id) === String(folderId));
                if (folder) {
                    folder.chat_ids = folder.chat_ids || [];
                    const cidNum = Number(chatId);
                    if (isMember && !folder.chat_ids.includes(cidNum)) {
                        folder.chat_ids.push(cidNum);
                    } else if (!isMember) {
                        folder.chat_ids = folder.chat_ids.filter(id => Number(id) !== cidNum);
                    }
                    folder.chat_count = folder.chat_ids.length;

                    // Immediately update live count badge in DOM
                    const countEl = Array.from(document.querySelectorAll('.fm-folder-chat-count')).find(el => el.dataset.folderId === String(folderId));
                    if (countEl) {
                        countEl.textContent = `${folder.chat_count} chat${folder.chat_count === 1 ? '' : 's'}`;
                    }
                }
                populateCategoryDropdown();
                filterChatList();
            } catch (e) {
                console.error('[Folders] Error updating chat membership:', e);
            }
        }
    };

    function bindChatContextMenu() {
        const chatList = document.getElementById('legacy-chat-list');
        const contextMenu = document.getElementById('folder-context-menu');
        if (!chatList || !contextMenu) return;

        chatList.addEventListener('contextmenu', (e) => {
            const item = e.target.closest('.chat-list-item');
            if (!item) return;
            e.preventDefault();

            const chatId = Number(item.dataset.chatId);
            const itemsContainer = document.getElementById('folder-context-menu-items');
            if (!itemsContainer) return;

            if (!foldersData.length) {
                itemsContainer.innerHTML = '<div style="font-size: 11px; opacity: 0.6; padding: 4px;">No folders created.</div>';
            } else {
                itemsContainer.innerHTML = foldersData.map(f => {
                    const isMember = (f.chat_ids || []).map(Number).includes(chatId);
                    const safeFid = escapeHtml(f.folder_id);
                    return `
                        <div style="display: flex; align-items: center; justify-content: space-between; padding: 4px 6px; font-size: 11px; border-radius: 4px; cursor: pointer; user-select: none;"
                             onmouseover="this.style.background='var(--hover-bg)'" onmouseout="this.style.background='transparent'"
                             data-folder-id="${safeFid}"
                             onclick="FoldersManager.toggleChatMembership(this.dataset.folderId, ${chatId}, ${!isMember}); FoldersManager.closeModal();">
                            <span>${escapeHtml(f.emoticon || '📁')} ${escapeHtml(f.title)}</span>
                            <span>${isMember ? '✅' : '⬜'}</span>
                        </div>
                    `;
                }).join('');
            }

            const x = Math.min(e.clientX, window.innerWidth - 200);
            const y = Math.min(e.clientY, window.innerHeight - 250);
            contextMenu.style.left = `${x}px`;
            contextMenu.style.top = `${y}px`;
            contextMenu.style.display = 'block';
        });

        document.addEventListener('click', (e) => {
            if (contextMenu && !contextMenu.contains(e.target)) {
                contextMenu.style.display = 'none';
            }
        });
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    window.filterChatList = filterChatList;
    window.initFolders = initFolders;
    window.FoldersManager = FoldersManager;
    window.loadFolders = loadFolders;

    document.addEventListener('DOMContentLoaded', initFolders);
})();
