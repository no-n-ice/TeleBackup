/**
 * users.js — User Profile Modal with full metadata (bio, birthday, business, star gifts, live status)
 * and on-demand background fetch capability.
 */
(function (window) {
    'use strict';

    const UserModal = {
        _currentUserId: null,
        _modalEl: null,

        init() {
            this._modalEl = document.getElementById('user-profile-modal');
            if (!this._modalEl) {
                this._createModalDom();
            }
        },

        _createModalDom() {
            const overlay = document.createElement('div');
            overlay.id = 'user-profile-modal';
            overlay.className = 'user-modal-overlay';
            overlay.style.display = 'none';
            overlay.innerHTML = `
                <div class="user-modal-dialog">
                    <div class="user-modal-header">
                        <h3 class="user-modal-title" data-i18n="user.profile_title">User Profile</h3>
                        <button class="user-modal-close" onclick="UserModal.close()">✕</button>
                    </div>
                    <div class="user-modal-body" id="user-modal-content">
                        <div class="user-modal-loading">Loading profile...</div>
                    </div>
                </div>
            `;
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) UserModal.close();
            });
            document.body.appendChild(overlay);
            this._modalEl = overlay;
        },

        async open(userId) {
            if (!this._modalEl) this.init();
            this._currentUserId = userId;
            this._modalEl.style.display = 'flex';
            const content = document.getElementById('user-modal-content');
            content.innerHTML = '<div class="user-modal-loading">Loading profile...</div>';

            try {
                const resp = await fetch(`/api/user_full_profile?user_id=${encodeURIComponent(userId)}`);
                if (!resp.ok) {
                    throw new Error(`Profile not found (${resp.status})`);
                }
                const profile = await resp.json();
                this.render(profile);
            } catch (err) {
                content.innerHTML = `
                    <div class="user-modal-error">
                        <p>⚠️ ${escapeHTML(err.message)}</p>
                        <button class="action-btn" data-uid="${escapeHTML(String(userId))}" onclick="UserModal.fetchFresh(this.dataset.uid)">
                            ${I18N.t('user.fetch_full') || 'Fetch Full Profile'}
                        </button>
                    </div>
                `;
            }
        },

        close() {
            if (this._modalEl) {
                this._modalEl.style.display = 'none';
            }
            this._currentUserId = null;
        },

        render(u) {
            const content = document.getElementById('user-modal-content');
            if (!content) return;

            const rawName = `${u.first_name || ''} ${u.last_name || ''}`.trim() || 'User';
            const name = escapeHTML(rawName);
            const initial = escapeHTML((Array.from(rawName)[0] || 'U').toUpperCase());
            const username = u.username ? `@${escapeHTML(u.username)}` : '';
            const vParam = encodeURIComponent(String(u.avatar_mtime || (u.full_profile_updated_at ? new Date(u.full_profile_updated_at).getTime() : Date.now())));
            const avatarUrl = u.avatar_path ? escapeHTML(`/data/${String(u.avatar_path).replace(/\\/g, '/')}?v=${vParam}`) : '';
            const avatarHtml = avatarUrl
                ? `<img class="user-profile-avatar" src="${avatarUrl}" alt="${name}">`
                : `<div class="user-profile-avatar user-avatar-placeholder">${initial}</div>`;

            const isChannel = Boolean(u.is_channel || u.chat_type === 'channel');
            const isGroup = Boolean(u.chat_type === 'group');

            // Update modal title
            const titleEl = this._modalEl ? this._modalEl.querySelector('.user-modal-title') : null;
            if (titleEl) {
                if (isChannel) {
                    titleEl.textContent = I18N.t('user.channel_profile') || 'Channel Profile';
                } else if (isGroup) {
                    titleEl.textContent = I18N.t('user.group_profile') || 'Group Profile';
                } else {
                    titleEl.textContent = I18N.t('user.profile_title') || 'User Profile';
                }
            }

            let badgesHtml = '';
            if (isChannel) {
                badgesHtml += '<span class="user-badge channel-badge" title="Channel" style="background:rgba(36,129,204,0.15); color:var(--link-color); border:1px solid rgba(36,129,204,0.3); padding:2px 6px; border-radius:4px; font-size:11px;">📢 Channel</span>';
            } else if (isGroup) {
                badgesHtml += '<span class="user-badge group-badge" title="Group" style="background:rgba(0,200,83,0.15); color:#00c853; border:1px solid rgba(0,200,83,0.3); padding:2px 6px; border-radius:4px; font-size:11px;">👥 Group</span>';
            }
            if (u.emoji_status_document_id && window.EmojiEngine) {
                badgesHtml += window.EmojiEngine.renderStatusBadge(u.emoji_status_document_id);
            }
            if (u.is_bot) badgesHtml += '<span class="user-badge bot-badge" title="Bot">🤖</span>';
            if (u.is_premium) badgesHtml += '<span class="user-badge premium-badge" title="Telegram Premium">⭐</span>';
            if (u.verified) badgesHtml += '<span class="user-badge verified-badge" title="Verified">✅</span>';
            if (u.is_scam) badgesHtml += '<span class="user-badge scam-badge">SCAM</span>';
            if (u.is_fake) badgesHtml += '<span class="user-badge fake-badge">FAKE</span>';

            // Live status
            let statusText = '';
            let statusClass = '';
            if (u.live_status) {
                const s = u.live_status.status;
                if (s === 'online') {
                    statusText = I18N.t('user.live_online') || 'Online';
                    statusClass = 'status-online';
                } else if (s === 'recently') {
                    statusText = I18N.t('user.live_recently') || 'Recently online';
                    statusClass = 'status-recently';
                } else if (s === 'offline') {
                    statusText = I18N.t('user.live_offline') || 'Offline';
                    statusClass = 'status-offline';
                } else {
                    statusText = escapeHTML(String(s || ''));
                }
            } else if (isChannel) {
                statusText = 'Broadcast Channel';
            } else if (isGroup) {
                statusText = 'Group / Supergroup';
            }

            // Phone
            const phoneHtml = u.phone
                ? `<div class="profile-section"><div class="profile-label">📞 ${I18N.t('user.phone') || 'Phone'}</div><div class="profile-value"><a href="tel:${escapeHTML(u.phone)}" style="color:var(--link-color); text-decoration:none;">${escapeHTML(u.phone)}</a></div></div>`
                : '';

            // Bio
            const bioHtml = u.about
                ? `<div class="profile-section"><div class="profile-label">${I18N.t('user.bio') || 'Bio'}</div><div class="profile-value">${escapeHTML(u.about)}</div></div>`
                : '';

            // Personal Channel
            let personalChannelHtml = '';
            if (u.personal_channel_id) {
                const chTitle = escapeHTML(u.personal_channel_title || `Channel ${u.personal_channel_id}`);
                personalChannelHtml = `
                    <div class="profile-section">
                        <div class="profile-label">📢 ${I18N.t('user.personal_channel') || 'Personal Channel'}</div>
                        <div class="profile-value">
                            <span class="user-channel-badge" style="display:inline-flex; align-items:center; gap:6px; background:rgba(36,129,204,0.15); border:1px solid rgba(36,129,204,0.3); border-radius:6px; padding:4px 8px; font-size:12px;">
                                📢 <strong>${chTitle}</strong>
                            </span>
                        </div>
                    </div>
                `;
            }

            // Birthday
            let bdayHtml = '';
            if (u.birthday) {
                const b = u.birthday;
                const bStr = `${b.day || ''}.${b.month || ''}${b.year ? '.' + b.year : ''}`;
                bdayHtml = `<div class="profile-section"><div class="profile-label">🎂 ${I18N.t('user.birthday') || 'Birthday'}</div><div class="profile-value">${escapeHTML(bStr)}</div></div>`;
            }

            // Business
            let bizHtml = '';
            if (u.business) {
                const bz = u.business;
                let bizContent = '';
                if (bz.intro_title || bz.intro_description) {
                    bizContent += `<div><strong>${escapeHTML(bz.intro_title || '')}</strong>: ${escapeHTML(bz.intro_description || '')}</div>`;
                }
                if (bz.address) {
                    bizContent += `<div>📍 ${escapeHTML(bz.address)}</div>`;
                }
                if (bz.work_hours) {
                    bizContent += `<div>🕒 ${escapeHTML(bz.work_hours.timezone_id || 'Work Hours')}</div>`;
                }
                if (bizContent) {
                    bizHtml = `<div class="profile-section"><div class="profile-label">💼 ${I18N.t('user.business') || 'Business'}</div><div class="profile-value">${bizContent}</div></div>`;
                }
            }

            // Profile Photos History Gallery
            let photosHtml = '';
            this._lastProfilePhotoUrls = [];
            if (u.profile_photos && Array.isArray(u.profile_photos) && u.profile_photos.length > 1) {
                this._lastProfilePhotoUrls = u.profile_photos.map(p => `/data/${String(p).replace(/\\/g, '/')}`);
                let thumbItems = '';
                this._lastProfilePhotoUrls.forEach((phUrl, idx) => {
                    const safePhUrl = escapeHTML(phUrl);
                    thumbItems += `
                        <img src="${safePhUrl}" class="profile-thumb" style="width:50px; height:50px; border-radius:8px; object-fit:cover; cursor:pointer; border:2px solid transparent; transition:border-color 0.2s;" 
                             onmouseover="this.style.borderColor='var(--link-color)'" onmouseout="this.style.borderColor='transparent'"
                             onclick="if(window.Lightbox && UserModal._lastProfilePhotoUrls){ window.Lightbox.open(UserModal._lastProfilePhotoUrls, ${Number(idx) || 0}); }" alt="Avatar ${Number(idx) + 1}">
                    `;
                });
                photosHtml = `
                    <div class="profile-section">
                        <div class="profile-label">🖼️ ${I18N.t('user.profile_photos') || 'Historical Avatars'} (${u.profile_photos.length})</div>
                        <div style="display:flex; gap:8px; overflow-x:auto; padding:4px 0;">${thumbItems}</div>
                    </div>
                `;
            }

            // User Active Stories
            let storiesHtml = '';
            if (u.stories && Array.isArray(u.stories) && u.stories.length > 0) {
                let storyItems = '';
                u.stories.forEach(st => {
                    const isVideo = st.media_path && (st.media_path.endsWith('.mp4') || st.media_path.endsWith('.mov'));
                    const stUrl = st.media_path ? escapeHTML(`/data/${String(st.media_path).replace(/\\/g, '/')}`) : '';
                    const dateStr = escapeHTML(st.date ? new Date(st.date).toLocaleDateString() : '');
                    storyItems += `
                        <div class="story-preview-card" style="min-width:85px; max-width:100px; border-radius:8px; overflow:hidden; border:1px solid var(--border-color); background:rgba(0,0,0,0.2); cursor:pointer;"
                             data-story-url="${stUrl}"
                             onclick="if(window.Lightbox && this.dataset.storyUrl){ window.Lightbox.open([this.dataset.storyUrl], 0); }">
                            ${isVideo 
                                ? `<video src="${stUrl}" style="width:100%; height:85px; object-fit:cover;" muted></video>`
                                : `<img src="${stUrl}" style="width:100%; height:85px; object-fit:cover;" alt="Story">`
                            }
                            <div style="padding:4px; font-size:10px; text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                                ${dateStr || 'Story'}
                            </div>
                        </div>
                    `;
                });
                storiesHtml = `
                    <div class="profile-section">
                        <div class="profile-label">✨ ${I18N.t('user.stories') || 'Stories'} (${u.stories.length})</div>
                        <div style="display:flex; gap:8px; overflow-x:auto; padding:4px 0;">${storyItems}</div>
                    </div>
                `;
            }

            // Star Gifts Showcase
            let giftsHtml = '';
            if (u.gifts && Array.isArray(u.gifts) && u.gifts.length > 0) {
                let giftItems = '';
                u.gifts.forEach(g => {
                    const gTitle = escapeHTML(g.title || g.name || 'Star Gift');
                    const stars = g.stars ? `<span>⭐ ${g.stars}</span>` : '';
                    const gMsg = g.message ? `<div style="font-size:11px; opacity:0.8; font-style:italic; margin-top:2px;">"${escapeHTML(g.message)}"</div>` : '';
                    giftItems += `
                        <div class="star-gift-badge" title="${gTitle}">
                            <div class="gift-icon">🎁</div>
                            <div class="gift-meta">
                                <div class="gift-title">${gTitle}</div>
                                <div class="gift-stars">${stars}</div>
                                ${gMsg}
                            </div>
                        </div>
                    `;
                });
                giftsHtml = `
                    <div class="profile-section">
                        <div class="profile-label">🎁 ${I18N.t('user.gifts') || 'Star Gifts'} (${u.gifts.length})</div>
                        <div class="profile-gifts-grid">${giftItems}</div>
                    </div>
                `;
            }

            const updatedStr = u.full_profile_updated_at
                ? `<div class="profile-updated-at">Updated: ${new Date(u.full_profile_updated_at).toLocaleString(I18N.locale())}</div>`
                : '';

            let openChatBtn = '';
            const matchChat = (typeof chatListData !== 'undefined' && chatListData) ? chatListData.find(c => (typeof areChatIdsEqual === 'function') ? areChatIdsEqual(c.chat_id, u.user_id) : String(c.chat_id) === String(u.user_id)) : null;
            if (matchChat) {
                openChatBtn = `
                    <button class="action-btn" style="background:var(--link-color); color:white;" data-cid="${escapeHTML(String(matchChat.chat_id))}" onclick="UserModal.close(); const targetLi = Array.from(document.querySelectorAll('.chat-list-item')).find(el => el.dataset.chatId === this.dataset.cid); if(targetLi) targetLi.click();">
                        💬 ${I18N.t('user.open_chat_archive') || 'Open Chat Archive'}
                    </button>
                `;
            }

            content.innerHTML = `
                <div class="user-profile-header-card">
                    ${avatarHtml}
                    <div class="user-profile-meta">
                        <div class="user-profile-fullname">${name} ${badgesHtml}</div>
                        <div class="user-profile-username">${username} <span class="user-profile-id">#${escapeHTML(String(u.user_id ?? ''))}</span></div>
                        ${statusText ? `<div class="user-profile-status ${statusClass}">● ${statusText}</div>` : ''}
                    </div>
                </div>

                <div class="user-profile-details">
                    ${phoneHtml}
                    ${bioHtml}
                    ${personalChannelHtml}
                    ${bdayHtml}
                    ${bizHtml}
                    ${photosHtml}
                    ${storiesHtml}
                    ${giftsHtml}
                </div>

                <div class="user-modal-actions" style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
                    ${openChatBtn}
                    <button id="btn-fetch-fresh-user" class="action-btn user-fetch-btn" data-uid="${escapeHTML(String(u.user_id ?? ''))}" onclick="UserModal.fetchFresh(this.dataset.uid)">
                        🔄 ${I18N.t('user.fetch_full') || 'Fetch Full Profile'}
                    </button>
                    ${updatedStr}
                </div>
            `;
        },

        async fetchFresh(userId) {
            const btn = document.getElementById('btn-fetch-fresh-user');
            if (btn) {
                btn.disabled = true;
                btn.textContent = `⏳ ${I18N.t('user.fetching') || 'Fetching...'}`;
            }

            try {
                const resp = await fetch('/api/fetch_user_profile', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-TeleBackup-Auth-Token': (window.getCsrfToken ? window.getCsrfToken() : '')
                    },
                    body: JSON.stringify({ user_id: userId })
                });
                const data = await resp.json();
                if (!resp.ok || !data.ok) {
                    alert(data.error || 'Failed to fetch fresh profile from Telegram');
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = `🔄 ${I18N.t('user.fetch_full') || 'Fetch Full Profile'}`;
                    }
                    return;
                }
                const profile = data.profile;
                this.render(profile);

                // Update memory cache for this user
                const uidStr = String(userId);
                if (typeof allUsers !== 'undefined' && allUsers) {
                    allUsers[userId] = Object.assign({}, allUsers[userId] || {}, profile);
                    allUsers[uidStr] = allUsers[userId];
                }
                if (window.AppState && window.AppState.allUsers) {
                    window.AppState.allUsers[userId] = Object.assign({}, window.AppState.allUsers[userId] || {}, profile);
                    window.AppState.allUsers[uidStr] = window.AppState.allUsers[userId];
                }

                // Instantly update on-screen avatars in chats
                const vParam = profile.avatar_mtime || (profile.full_profile_updated_at ? new Date(profile.full_profile_updated_at).getTime() : Date.now());
                const freshAvatarUrl = profile.avatar_path ? `/data/${profile.avatar_path.replace(/\\/g, '/')}?v=${vParam}` : '';

                // Update all message avatars for this sender
                const msgEls = document.querySelectorAll(`.message[data-sender-id="${userId}"]`);
                msgEls.forEach(msgEl => {
                    const avImg = msgEl.querySelector('img.message-avatar');
                    const avPlaceholder = msgEl.querySelector('.message-avatar-placeholder');
                    if (freshAvatarUrl) {
                        if (avImg) {
                            avImg.src = freshAvatarUrl;
                            avImg.style.display = '';
                            if (avPlaceholder) avPlaceholder.style.display = 'none';
                        } else if (avPlaceholder) {
                            const newImg = document.createElement('img');
                            newImg.src = freshAvatarUrl;
                            newImg.className = 'message-avatar';
                            newImg.alt = 'avatar';
                            newImg.loading = 'lazy';
                            newImg.setAttribute('onclick', `if(window.UserModal) UserModal.open(${userId})`);
                            newImg.style.cursor = 'pointer';
                            newImg.title = 'View Profile';
                            avPlaceholder.parentNode.insertBefore(newImg, avPlaceholder);
                            avPlaceholder.style.display = 'none';
                        }
                    } else if (avImg) {
                        avImg.style.display = 'none';
                        if (avPlaceholder) avPlaceholder.style.display = 'flex';
                    }
                });

                // Update chat header / chat list if this is the open chat
                if (freshAvatarUrl) {
                    if (typeof currentChatId !== 'undefined' && (String(currentChatId) === uidStr || String(currentChatId).replace(/^-100/, '').replace(/^-/, '') === uidStr)) {
                        const chatHeaderAvatar = document.querySelector('#chat-header-avatar, .chat-header-avatar');
                        if (chatHeaderAvatar) {
                            if (chatHeaderAvatar.tagName === 'IMG') {
                                chatHeaderAvatar.src = freshAvatarUrl;
                            } else {
                                const cImg = chatHeaderAvatar.querySelector('img');
                                if (cImg) cImg.src = freshAvatarUrl;
                            }
                        }
                    }

                    // Update chat list item if present
                    const chatListItem = document.querySelector(`.chat-list-item[data-chat-id="${userId}"] img.chat-list-avatar`);
                    if (chatListItem) {
                        chatListItem.src = freshAvatarUrl;
                    }
                }
            } catch (err) {
                alert(`Error: ${err.message}`);
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = `🔄 ${I18N.t('user.fetch_full') || 'Fetch Full Profile'}`;
                }
            }
        }
    };

    window.UserModal = UserModal;
    document.addEventListener('DOMContentLoaded', () => {
        UserModal.init();
    });
})(window);
