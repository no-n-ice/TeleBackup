const _dtFormatters = {};
function getDtFormatters(locale) {
    const loc = locale || (typeof I18N !== 'undefined' ? I18N.locale() : 'default');
    if (!_dtFormatters[loc]) {
        _dtFormatters[loc] = {
            date: new Intl.DateTimeFormat(loc),
            time: new Intl.DateTimeFormat(loc, { hour: '2-digit', minute: '2-digit' }),
            full: new Intl.DateTimeFormat(loc, { year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' }),
            fwdDate: new Intl.DateTimeFormat(loc, { day: 'numeric', month: 'short', year: 'numeric' }),
            savedDate: new Intl.DateTimeFormat(loc, { day: 'numeric', month: 'short' })
        };
    }
    return _dtFormatters[loc];
}

function stripMessageIdPrefix(name, msgId) {
    if (!name || !msgId) return name || '';
    const prefix = `${msgId} - `;
    return name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

function createMessageElement(msg) {
    if (msg.service_action) {
        const serviceDiv = document.createElement('div');
        serviceDiv.className = 'service-message';
        serviceDiv.id = 'msg-' + msg.message_id;
        serviceDiv.setAttribute('data-id', msg.message_id);
        serviceDiv.setAttribute('data-msg-id', msg.message_id);
        serviceDiv.dataset.messageId = msg.message_id;
        const dateObj = msg.date ? new Date(msg.date) : null;
        if (dateObj && !isNaN(dateObj.getTime())) {
            const fmt = getDtFormatters(I18N.locale());
            serviceDiv.setAttribute('data-date', fmt.date.format(dateObj));
            serviceDiv.title = fmt.full.format(dateObj);
        }
        const timeStr = (dateObj && !isNaN(dateObj.getTime())) ? getDtFormatters(I18N.locale()).time.format(dateObj) : '';
        const actionHtml = formatServiceMessage(msg, allUsers);
        const timeBadge = timeStr ? `<span class="service-msg-time" style="margin-left: 6px; font-size: 0.85em; opacity: 0.65;">${timeStr}</span>` : '';
        serviceDiv.innerHTML = `${actionHtml}${timeBadge}`;
        return serviceDiv;
    }

    const msgDiv = document.createElement('div');
    msgDiv.className = 'message';
    msgDiv.id = 'msg-' + msg.message_id;
    const mimeFlag = msg.media_mime_type || '';
    const isStickerMsg = (!msg.text) && (mimeFlag === 'application/x-tgsticker' || mimeFlag === 'image/webp' || mimeFlag === 'video/webm');
    if (isStickerMsg) msgDiv.classList.add('sticker-message');

    msgDiv.setAttribute('data-id', msg.message_id);
    msgDiv.setAttribute('data-msg-id', msg.message_id);
    msgDiv.dataset.messageId = msg.message_id;
    const parsedDate = msg.date ? new Date(msg.date) : null;
    if (parsedDate && !isNaN(parsedDate.getTime())) {
        msgDiv.setAttribute('data-date', getDtFormatters(I18N.locale()).date.format(parsedDate));
    }
    if (msg.sender_id) { msgDiv.setAttribute('data-sender-id', msg.sender_id); }
    // is_outgoing hard override: DB value always wins
    const isOut = (msg.is_outgoing === 1) || (myUserId && msg.sender_id == myUserId);
    if (isOut) msgDiv.classList.add('out');
    if (msg.is_post == 1 || msg.is_post === '1' || msg.is_post === true) msgDiv.dataset.isPost = "1";
    if (msg.is_mentioned === 1) msgDiv.dataset.isMentioned = "1";
    if (msg.is_deleted == 1 || msg.is_deleted === '1' || msg.is_deleted === true) msgDiv.classList.add('is-deleted-msg');

    if (msg.invert_media === 1) msgDiv.classList.add('inverted-media');

    const { senderName, senderAvatarPath, senderObj, isOfficialPost, adminRank } = resolveSenderInfo(msg);
    const colorIndex = msg.sender_id ? Math.abs(parseInt(msg.sender_id, 10)) % 7 : 0;

    const activeChatId = window.AppState ? window.AppState.currentChatId : (typeof currentChatId !== 'undefined' ? currentChatId : null);

    let senderAvatarHtml = '';
    if (!msgDiv.classList.contains('out') && !isOfficialPost) {
        const clickAttr = msg.sender_id ? ` onclick="if(window.UserModal) UserModal.open(${Number(msg.sender_id) || 0})" style="cursor:pointer;" title="View Profile"` : '';
        const avatarInitial = escapeHTML((senderName[0] || '?').toUpperCase());
        if (senderAvatarPath && senderAvatarPath !== "None") {
            const vParam = (senderObj && senderObj.avatar_mtime) ? `?v=${senderObj.avatar_mtime}` : '';
            const avatarUrl = encodeURI('/data/' + senderAvatarPath.replace(/\\/g, '/')).replace(/'/g, "%27") + vParam;
            senderAvatarHtml = `<img src="${avatarUrl}" class="message-avatar" alt="avatar" loading="lazy" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';"${clickAttr}>`;
            senderAvatarHtml += `<div class="message-avatar message-avatar-placeholder" style="background: var(--peer-color-${colorIndex}); cursor:pointer; display: none;"${clickAttr}>${avatarInitial}</div>`;
        } else {
            senderAvatarHtml = `<div class="message-avatar message-avatar-placeholder" style="background: var(--peer-color-${colorIndex}); cursor:pointer;"${clickAttr}>${avatarInitial}</div>`;
        }
    }

    // --- Date & Time ---
    const rawDateVal = msg.date;
    const parsedObj = rawDateVal ? new Date(rawDateVal) : new Date();
    const dateObj = isNaN(parsedObj.getTime()) ? new Date() : parsedObj;
    const fmt = getDtFormatters(I18N.locale());
    const fullDateStr = fmt.full.format(dateObj);
    let timeDisplay = fmt.time.format(dateObj);

    let tooltipText = I18N.t('msg.sent', fullDateStr);
    let metaClass = 'message-meta';

    if (msg.edited_date && msg.edited_date !== "None") {
        const mime = msg.media_mime_type || '';
        const isUneditable = (
            mime === 'application/x-tgsticker' ||
            mime === 'image/webp' ||
            mime === 'video/webm' ||
            mime === 'image/gif' ||
            mime === 'audio/ogg' ||
            mime === 'audio/oga'
        );

        if (!isUneditable) {
            const editDateObj = new Date(msg.edited_date);
            const editFullStr = !isNaN(editDateObj.getTime()) ? fmt.full.format(editDateObj) : msg.edited_date;
            tooltipText += `\n${I18N.t('msg.edited', editFullStr)}`;
            const hasEditHistory = msg.edit_history || (msg.edit_history_json && msg.edit_history_json !== '[]' && msg.edit_history_json !== 'null');
            if (hasEditHistory) {
                timeDisplay += ` <span class="edit-history-trigger" onclick="event.stopPropagation(); toggleEditHistory(this);">${I18N.t('msg.edited_badge')}</span>`;
            } else {
                timeDisplay += ` ${I18N.t('msg.edited_badge')}`;
            }
            metaClass += ' is-edited';
        }
    }

    let headerContent = '';
    headerContent += buildForwardHeader(msg);
    headerContent += buildReplyHeader(msg, isStickerMsg);

    const infoItems = buildInfoItems(msg);

    let metaInfoHtml = '';
    if (infoItems.length > 0) {
        metaInfoHtml = infoItems.map(item => `<span class="meta-info-item">${item}</span>`).join('');
    }

    // from_scheduled tooltip extra
    if (msg.from_scheduled === 1) tooltipText += '\nScheduled message';

    const metaHtml = buildMessageMeta({
        msg,
        metaClass,
        infoItems,
        timeDisplay,
        tooltipText
    });

    const hasCaption = Boolean(msg.text && msg.text.trim());
    const isVisualMedia = !hasCaption && !isStickerMsg && Boolean(
        (msg.media_local_path && (
            (msg.media_mime_type && (msg.media_mime_type.startsWith('image/') || msg.media_mime_type.startsWith('video/'))) ||
            msg.is_round_video === 1
        )) ||
        (msg.media_other && msg.media_other._ === 'MessageMediaWebPage' && msg.media_other.webpage && (msg.media_other.webpage.photo || msg.media_other.webpage.photos))
    );

    const { textHtml, mediaHtml } = getMessageParts(msg, false, isVisualMedia ? metaHtml : '');
    const hasMedia = Boolean(mediaHtml && mediaHtml.trim());
    const hasText = Boolean(textHtml && textHtml.trim());

    const mediaContentHtml = hasMedia ? `<div class="message-media-content">${mediaHtml}</div>` : '';
    const textContentHtml = textHtml || (!hasMedia ? `<div class="text">${I18N.t('msg.empty')}</div>` : '');
    const reactionsHtml = createReactionsHtml(msg.reactions, allUsers, window.allCustomEmoji || {});

    let buttonsHtml = '';
    if (msg.reply_markup && msg.reply_markup.rows) {
        buttonsHtml += '<div class="inline-buttons-container">';
        msg.reply_markup.rows.forEach(row => {
            buttonsHtml += '<div class="inline-buttons-row">';
            row.buttons.forEach(button => {
                if (button._ === 'KeyboardButtonUrl') {
                    buttonsHtml += `<a class="inline-button" href="${escapeHTML((window.sanitizeUrl || String)(button.url))}" target="_blank" rel="noopener noreferrer">${escapeHTML(button.text)}</a>`;
                } else {
                    buttonsHtml += `<button class="inline-button" disabled>${escapeHTML(button.text)}</button>`;
                }
            });
            buttonsHtml += '</div>';
        });
        buttonsHtml += '</div>';
    }

    let chatInfoHtml = '';
    if (chatContainer.dataset.mode === 'search') {
        msgDiv.classList.add('search-result');
        if (msg.chat_id) { msgDiv.dataset.chatId = msg.chat_id; }

        const isGlobalSearch = (chatContainer.dataset.searchScope === 'global');
        const isFromOtherChat = !currentChatId || (typeof areChatIdsEqual === 'function' ? !areChatIdsEqual(msg.chat_id, currentChatId) : String(msg.chat_id) !== String(currentChatId));
        const isOut = (msg.is_outgoing === 1) || (myUserId && msg.sender_id == myUserId);

        if (isGlobalSearch && isFromOtherChat && !isOut && msg.chat_name) {
            const chatAvatarUrl = (msg.chat_avatar_path && msg.chat_avatar_path !== "None") ? '/data/' + msg.chat_avatar_path.replace(/\\/g, '/') : '';
            const avatarHtml = chatAvatarUrl ? `<img src="${chatAvatarUrl}" class="chat-source-avatar" alt="chat" loading="lazy">` : `<div class="chat-source-avatar-placeholder">${escapeHTML(msg.chat_name[0] || '?')}</div>`;
            chatInfoHtml = `<div class="chat-source-info">${avatarHtml}<span>${escapeHTML(msg.chat_name)}</span></div>`;
        }
    }

    const senderBadges = buildSenderBadges(senderObj);
    const viaBotHtml = resolveViaBot(msg);
    const rankHtml = adminRank ? `<span class="admin-rank-label">${escapeHTML(adminRank)}</span>` : '';
    const clickAttr = msg.sender_id ? ` onclick="if(window.UserModal) UserModal.open(${msg.sender_id})" style="cursor:pointer;" title="View Profile"` : '';
    const senderNameSpan = senderName ? `<span class="sender-name-text"${clickAttr}>${escapeHTML(senderName)}</span>` : '';
    const senderHtml = (senderName || senderBadges || viaBotHtml)
        ? `<div class="sender" data-peer-color="${colorIndex}">${senderNameSpan}${viaBotHtml ? ' ' + viaBotHtml : ''}${rankHtml}${senderBadges}</div>`
        : '';

    const commentsBarHtml = buildCommentsBar(msg);
    const hasReactions = reactionsHtml && reactionsHtml.trim() !== '';
    if (hasReactions) {
        msgDiv.classList.add('has-reactions');
    }
    let deletedHeaderHtml = '';
    let bodyWrapStart = '';
    let bodyWrapEnd = '';
    const isDeletedMsg = (msg.is_deleted == 1 || msg.is_deleted === '1' || msg.is_deleted === true);
    if (isDeletedMsg) {
        msgDiv.classList.add('is-deleted-msg');
        const isBotSender = (senderObj && (senderObj.is_bot == 1 || senderObj.is_bot === '1' || senderObj.is_bot === true)) || (msg.is_bot == 1);
        if (isBotSender) {
            msgDiv.classList.add('collapsed-deleted');
        }
        deletedHeaderHtml = `
                <div class="deleted-msg-header" onclick="event.stopPropagation(); this.closest('.message').classList.toggle('collapsed-deleted')" title="${I18N.t('msg.toggle_deleted')}">
                    <span class="deleted-badge">🗑 ${I18N.t('messages.deleted')} (<span class="show-text">${I18N.t('messages.show')}</span><span class="hide-text">${I18N.t('messages.hide')}</span>)</span>
                </div>`;
        bodyWrapStart = '<div class="deleted-msg-body">';
        bodyWrapEnd = '</div>';
    }

    const editHistoryHtml = renderEditHistoryPanel(msg);

    const hasBodyContent = Boolean(hasText || !hasMedia || reactionsHtml || (!isVisualMedia && metaHtml));

    // --- HTML ASSEMBLY ---
    msgDiv.innerHTML = `
            ${senderAvatarHtml}
            ${deletedHeaderHtml}
            ${bodyWrapStart}
            ${chatInfoHtml} 
            ${senderHtml}
            ${headerContent} 
            ${msg.invert_media ? '' : mediaContentHtml}
            ${hasBodyContent ? `
            <div class="message-body-wrapper">
                <div class="message-main-content">
                    ${textContentHtml}
                </div>
                ${editHistoryHtml}
                <div class="message-footer">
                    ${reactionsHtml}
                    ${isVisualMedia ? '' : metaHtml}
                </div>
            </div>` : editHistoryHtml}
            ${msg.invert_media ? mediaContentHtml : ''}
            ${commentsBarHtml}
            ${bodyWrapEnd}
        `;




    const currentChatData = (typeof getChatById === 'function' ? getChatById(currentChatId) : chatListData.find(c => c.chat_id == currentChatId)) || {};
    if (currentChatData.linked_chat_id && msg.forward_info && msg.forward_info.saved_from_msg_id) {
        // Highlight root post in discussion groups
        msgDiv.classList.add('discussion-root-post');
    }

    const stickerContainers = msgDiv.querySelectorAll('.sticker-container[data-path]');
    if (stickerContainers.length > 0) {
        stickerContainers.forEach(el => {
            if (typeof queueStickerLoad === 'function') {
                queueStickerLoad(el, el.dataset.path);
            }
        });
    }

    const waveformCanvas = msgDiv.querySelector('.voice-waveform-canvas');
    if (waveformCanvas && window.drawVoiceWaveform) {
        requestAnimationFrame(() => window.drawVoiceWaveform(waveformCanvas, 0));
    }

    return msgDiv;
}

function createAlbumElement(albumBatch) {
    albumBatch.messages.sort((a, b) => a.message_id - b.message_id);
    const firstMsg = albumBatch.messages[0];
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message';
    const allIds = albumBatch.messages.map(m => m.message_id).join(',');
    msgDiv.setAttribute('data-id', firstMsg.message_id);
    msgDiv.setAttribute('data-album-ids', allIds);
    const albumDateObj = firstMsg.date ? new Date(firstMsg.date) : null;
    const albumFmt = getDtFormatters(I18N.locale());
    if (albumDateObj && !isNaN(albumDateObj.getTime())) {
        msgDiv.setAttribute('data-date', albumFmt.date.format(albumDateObj));
    }
    if (firstMsg.chat_id) { msgDiv.setAttribute('data-chat-id', firstMsg.chat_id); }
    if (firstMsg.sender_id) { msgDiv.setAttribute('data-sender-id', firstMsg.sender_id); }
    const isOutAlbum = (firstMsg.is_outgoing === 1) || (myUserId && firstMsg.sender_id == myUserId);
    if (isOutAlbum) msgDiv.classList.add('out');
    if (firstMsg.is_post === 1) msgDiv.setAttribute('data-is-post', "1");
    const { senderName, senderAvatarPath, senderObj, isOfficialPost } = resolveSenderInfo(firstMsg);
    const colorIdx = firstMsg.sender_id ? Math.abs(parseInt(firstMsg.sender_id, 10)) % 7 : 0;

    const activeChatIdAlbum = window.AppState ? window.AppState.currentChatId : (typeof currentChatId !== 'undefined' ? currentChatId : null);

    let senderAvatarHtml = '';
    if (!msgDiv.classList.contains('out') && !isOfficialPost) {
        const initial = escapeHTML((senderName[0] || '?').toUpperCase());
        const clickAttr = firstMsg.sender_id ? ` onclick="if(window.UserModal) UserModal.open(${Number(firstMsg.sender_id) || 0})" style="cursor:pointer;" title="View Profile"` : '';
        if (senderAvatarPath && senderAvatarPath !== "None") {
            const vParam = (senderObj && senderObj.avatar_mtime) ? `?v=${senderObj.avatar_mtime}` : '';
            const avatarUrl = encodeURI('/data/' + senderAvatarPath.replace(/\\/g, '/')).replace(/'/g, "%27") + vParam;
            senderAvatarHtml = `<img src="${avatarUrl}" class="message-avatar" alt="avatar" loading="lazy" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';"${clickAttr}>`;
            senderAvatarHtml += `<div class="message-avatar message-avatar-placeholder" style="background: var(--peer-color-${colorIdx}); cursor:pointer; display: none;"${clickAttr}>${initial}</div>`;
        } else {
            senderAvatarHtml = `<div class="message-avatar message-avatar-placeholder" style="background: var(--peer-color-${colorIdx}); cursor:pointer;"${clickAttr}>${initial}</div>`;
        }
    }

    let headerContent = '';
    headerContent += buildForwardHeader(firstMsg);

    // --- Date for albums ---
    const dateObj = (firstMsg.date ? new Date(firstMsg.date) : new Date());
    const validDate = isNaN(dateObj.getTime()) ? new Date() : dateObj;
    const fullDateStr = albumFmt.full.format(validDate);
    const timeStr = albumFmt.time.format(validDate);

    let albumHtml = '<div class="album-container">';
    albumBatch.messages.forEach(msg => {
        const spoilerClass = msg.is_media_spoiler ? ' media-spoiler' : '';
        const spoilerCheck = `if(this.classList.contains('media-spoiler')) { this.classList.remove('media-spoiler'); } else `;

        if (msg.media_local_path) {
            const relativePath = msg.media_local_path.replace(/^(media_backup[\\/])/, '');
            const mediaPath = '/data/' + relativePath.replace(/\\/g, '/');
            const safeMediaPath = encodeURI(mediaPath).replace(/'/g, "%27");
            const mime = msg.media_mime_type || '';

            if (mime.startsWith('video/') && msg.thumbnail_local_path) {
                const thumbRelPath = msg.thumbnail_local_path.replace(/^(media_backup[\\/])/, '');
                const safeThumbPath = encodeURI('/data/' + thumbRelPath.replace(/\\/g, '/')).replace(/'/g, "%27");
                albumHtml += `<div class="album-item video-thumb-album${spoilerClass}" onclick="${spoilerCheck}{ openPlayer('${safeMediaPath}', '${mime}'); }"><img src="${safeThumbPath}" loading="lazy" alt="thumb"><div class="play-icon-album">▶</div></div>`;
            } else if (mime.startsWith('image/')) {
                albumHtml += `<div class="album-item${spoilerClass}" onclick="${spoilerCheck}{ if(typeof openLightbox==='function') openLightbox('${safeMediaPath}'); else window.open('${safeMediaPath}', '_blank'); }"><img src="${safeMediaPath}" loading="lazy" alt="image"></div>`;
            }

        } else if (msg.media_skipped) {
            let skippedName = stripMessageIdPrefix(msg.media_original_name || I18N.t('msg.file_default') || 'File', msg.message_id);
            const chatId = msg.chat_id || (window.currentChatId || null);
            const dlBtnTitle = (typeof I18N !== 'undefined' && I18N.t('btn.download_media_title')) || 'Download this media file';
            const dlBtn = chatId ? `<button type="button" class="chat-inline-dl-btn" onclick="event.stopPropagation(); if(window.downloadSingleMedia) window.downloadSingleMedia(${chatId}, ${msg.message_id}, this);" title="${escapeHTML(dlBtnTitle)}">⬇️</button>` : '';
            albumHtml += `<div class="album-item album-item-skipped" id="skipped-media-${msg.message_id}" title="${escapeHTML(msg.media_skipped)}">
                                <span class="album-skipped-name">${escapeHTML(skippedName)}</span>
                                <div style="display: flex; align-items: center; gap: 4px;">
                                    <span class="album-skipped-reason">🚫</span>
                                    ${dlBtn}
                                </div>
                             </div>`;
        }
    });
    const metaHtml = buildMessageMeta({
        msg: firstMsg,
        metaClass: 'message-meta',
        infoItems: buildInfoItems(firstMsg),
        timeDisplay: timeStr,
        tooltipText: fullDateStr
    });

    albumHtml += `${metaHtml}</div>`;

    const reactionsHtml = createReactionsHtml(firstMsg.reactions, allUsers, window.allCustomEmoji || {});
    const hasReactions = reactionsHtml && reactionsHtml.trim() !== '';
    if (hasReactions) {
        msgDiv.classList.add('has-reactions');
    }

    const commentsBarHtml = buildCommentsBar(firstMsg);

    const viaBotAlbum = resolveViaBot(firstMsg);
    const senderBadges = buildSenderBadges(senderObj);
    const senderNameSpan = senderName ? `<span class="sender-name-text">${escapeHTML(senderName)}</span>` : '';
    const senderHtml = (senderName || senderBadges || viaBotAlbum)
        ? `<div class="sender" data-peer-color="${colorIdx}">${senderNameSpan}${viaBotAlbum ? ' ' + viaBotAlbum : ''}${senderBadges}</div>`
        : '';

    // --- HTML ASSEMBLY FOR ALBUMS ---
    msgDiv.innerHTML = `
            ${senderAvatarHtml}
            ${senderHtml}
            ${headerContent}
            ${albumHtml}
            <div class="message-footer">
                ${reactionsHtml}
            </div>
            ${commentsBarHtml}
        `;
    return msgDiv;
}

function resolveSenderInfo(msg) {
    let senderName = '';
    let senderAvatarPath = null;
    let senderObj = null;

    const userDict = (typeof allUsers !== 'undefined' && allUsers) ? allUsers : (window.allUsers || {});
    if (msg.sender_id && userDict) {
        senderObj = userDict[msg.sender_id] || userDict[String(msg.sender_id)] || null;
    }

    if (msg.sender_first_name !== undefined && msg.sender_first_name !== null && String(msg.sender_first_name).trim() !== '') {
        senderName = msg.sender_first_name;
        if (msg.sender_last_name && msg.sender_last_name !== '[Недоступен]' && msg.sender_last_name !== '[Unavailable]') senderName += ` ${msg.sender_last_name}`;
        senderAvatarPath = (senderObj && senderObj.avatar_path) ? senderObj.avatar_path : msg.sender_avatar_path;
    } else if (senderObj) {
        senderName = senderObj.first_name || '';
        if (senderObj.last_name && senderObj.last_name !== '[Недоступен]' && senderObj.last_name !== '[Unavailable]') senderName += ` ${senderObj.last_name}`;
        senderAvatarPath = senderObj.avatar_path;
    } else {
        senderAvatarPath = msg.sender_avatar_path;
    }

    if (!senderName || senderName.trim() === '' || senderName === '???') {
        if (msg.post_author_name) {
            senderName = msg.post_author_name;
        } else if (msg.sender_id) {
            senderName = `ID: ${msg.sender_id}`;
        } else {
            senderName = '';
        }
    }

    // We NO LONGER suppress name for channel posts as per user request.
    const isOfficialPost = (msg.is_post === 1 && !msg.forward_info && !msg.post_author_name);

    // Ensure senderName is resolved even for official posts
    const isSenderPlaceholder = !senderName || senderName.startsWith('ID:') || senderName.startsWith('[ID:');
    if (isOfficialPost && isSenderPlaceholder) {
        const chatIdToLookup = msg.chat_id || currentChatId;
        const chatInfo = userDict[chatIdToLookup] || userDict[msg.sender_id] || userDict[String(chatIdToLookup)] || userDict[String(msg.sender_id)];
        if (chatInfo) {
            senderName = chatInfo.first_name || chatInfo.chat_name || '';
            senderAvatarPath = chatInfo.avatar_path;
            senderObj = chatInfo;
        }
    }

    if (!senderName && msg.is_post !== 1) senderName = msg.sender_id ? `[ID: ${msg.sender_id}]` : '';

    let adminRank = null;
    const cid = msg.chat_id || currentChatId;
    const chatData = (typeof getChatById === 'function') ? getChatById(cid) : ((typeof chatListData !== 'undefined' && chatListData) ? chatListData.find(c => String(c.chat_id) === String(cid)) : null);
    if (chatData && chatData.admin_list && msg.sender_id) {
        const adminEntry = chatData.admin_list.find(a => a.user_id === msg.sender_id || String(a.user_id) === String(msg.sender_id));
        if (adminEntry) {
            adminRank = adminEntry.rank || (adminEntry.is_creator ? (I18N.t('rank.owner') || 'owner') : (I18N.t('rank.admin') || 'admin'));
        }
    }

    return { senderName, senderAvatarPath, senderObj, isOfficialPost, adminRank };
}

function buildForwardHeader(msg) {
    if (!msg.forward_info) return '';
    const fwd = msg.forward_info;
    const fromId = fwd.from_id || fwd.chat_id || fwd.sender_id || 
        (fwd.from_id_raw && (fwd.from_id_raw.channel_id || fwd.from_id_raw.user_id || fwd.from_id_raw.chat_id)) ||
        (fwd.saved_from_peer && (fwd.saved_from_peer.channel_id || fwd.saved_from_peer.user_id || fwd.saved_from_peer.chat_id)) ||
        fwd.saved_from_id;

    let resolvedFwd = null;
    if (fromId) {
        const idStr = String(fromId);
        const idAbs = idStr.replace(/^-100/, '').replace(/^-/, '');
        resolvedFwd = allUsers[idStr] ||
            allUsers[idAbs] ||
            allUsers['-100' + idAbs] ||
            allUsers['-' + idAbs] ||
            // Try O(1) lookup first, then fallback to prefix-stripped matching
            (typeof getChatById === 'function' && getChatById(idStr)) ||
            (typeof getChatById === 'function' && getChatById('-100' + idAbs)) ||
            (typeof getChatById === 'function' && getChatById('-' + idAbs)) ||
            (window.chatListData && window.chatListData.find(c => {
                const cidStr = String(c.chat_id).replace(/^-100/, '').replace(/^-/, '');
                return cidStr === idAbs;
            }));
    }

    const isPlaceholder = (n) => !n ||
        n === '[Скрытый отправитель]' ||
        n === '[Hidden sender]' ||
        n === '[Неизвестный отправитель]' ||
        n === '[Unknown sender]' ||
        n.startsWith('Канал (ID:') ||
        n.startsWith('Channel (ID:') ||
        n.startsWith('Пользователь (ID:') ||
        n.startsWith('User (ID:') ||
        n.startsWith('Группа (ID:') ||
        n.startsWith('Group (ID:') ||
        n.startsWith('ID:') ||
        n.startsWith('[ID:');

    // Resolve display name
    let fwdName = '';
    if (resolvedFwd) {
        const fn = resolvedFwd.first_name || resolvedFwd.chat_name || '';
        if (!isPlaceholder(fn)) {
            fwdName = fn;
            if (resolvedFwd.last_name && !resolvedFwd.last_name.includes('[') && !resolvedFwd.last_name.includes('ID:')) {
                fwdName += ` ${resolvedFwd.last_name}`;
            }
        }
    }

    if (isPlaceholder(fwdName)) {
        if (!isPlaceholder(fwd.from_name)) fwdName = fwd.from_name;
        else if (!isPlaceholder(fwd.header_from_name)) fwdName = fwd.header_from_name;
        else if (!isPlaceholder(fwd.saved_from_name)) fwdName = fwd.saved_from_name;
        else if (fwd.post_author) fwdName = fwd.post_author;
    }

    if (isPlaceholder(fwdName)) {
        if (fwd.from_id_raw && fwd.from_id_raw._ === 'PeerChannel') fwdName = I18N.t('msg.channel_id', fwd.from_id_raw.channel_id);
        else if (fwd.from_id_raw && fwd.from_id_raw._ === 'PeerUser') fwdName = I18N.t('msg.user_id', fwd.from_id_raw.user_id);
        else if (!fwdName) fwdName = I18N.t('msg.hidden_sender');
    }

    // Avatar logic for forward origin
    let fwdAvatarHtml = '';
    const avPath = resolvedFwd && (resolvedFwd.avatar_path || resolvedFwd.chat_avatar_path);
    if (avPath && avPath !== 'None') {
        const vParam = resolvedFwd.avatar_mtime ? `?v=${resolvedFwd.avatar_mtime}` : '';
        const fwdAvUrl = encodeURI('/data/' + avPath.replace(/\\/g, '/')).replace(/'/g, "%27") + vParam;
        fwdAvatarHtml = `<img src="${fwdAvUrl}" class="fwd-avatar" onerror="this.style.display='none'">`;
    } else {
        // Clean initial: skip brackets if it starts with them
        let cleanName = fwdName || '?';
        if (cleanName.startsWith('[') && cleanName.length > 1) cleanName = cleanName.substring(1);
        const initial = escapeHTML(cleanName[0].toUpperCase());

        const fwdColorIdx = fromId ? Math.abs(parseInt(fromId, 10)) % 7 : 0;
        fwdAvatarHtml = `<div class="fwd-avatar fwd-avatar-placeholder" style="background: var(--peer-color-${fwdColorIdx})">${initial}</div>`;
    }

    // Original forward date
    let fwdDateStr = '';
    if (fwd.date) {
        const fwdDate = new Date(fwd.date);
        if (!isNaN(fwdDate.getTime())) {
            fwdDateStr = ` · ${getDtFormatters(I18N.locale()).fwdDate.format(fwdDate)}`;
        }
    }
    // Post number
    const postSuffix = fwd.channel_post ? ` <span class="fwd-post-id">#${fwd.channel_post}</span>` : '';
    // Author signature
    const authorSuffix = fwd.post_author ? ` <span class="fwd-author">(${escapeHTML(fwd.post_author)})</span>` : '';
    // Extra tags
    let extras = '';
    if (fwd.saved_out) extras += `<span class="fwd-tag">${I18N.t('msg.outgoing')}</span>`;
    if (fwd.imported) extras += `<span class="fwd-tag">${I18N.t('msg.imported')}</span>`;
    if (fwd.psa_type) extras += `<span class="fwd-tag">PSA: ${escapeHTML(fwd.psa_type)}</span>`;

    // Saved Messages secondary row
    let savedRow = '';
    if (fwd.saved_from_name || fwd.saved_from_id) {
        const savedName = fwd.saved_from_name || `ID: ${fwd.saved_from_id}`;
        let savedDate = '';
        if (fwd.saved_date) {
            const sDate = new Date(fwd.saved_date);
            if (!isNaN(sDate.getTime())) {
                savedDate = ` · ${getDtFormatters(I18N.locale()).savedDate.format(sDate)}`;
            }
        }
        savedRow = `<div class="fwd-saved-row">↺ ${escapeHTML(savedName)}${savedDate}</div>`;
    }

    let channelBadge = '';
    if (msg.is_channel_post || (fwd && (fwd.saved_from_msg_id || fwd.channel_post))) {
        channelBadge = `<div class="channel-post-badge-header">📢 ${I18N.t('msg.channel_post')}</div>`;
    }

    let dateRow = '';
    if (fwdDateStr || savedRow) {
        dateRow = `<div class="fwd-date-row"><span class="fwd-date">${escapeHTML(fwdDateStr)}</span>${savedRow}</div>`;
    }

    return `${channelBadge}<div class="forward">${fwdAvatarHtml}<div class="fwd-content"><div class="fwd-sender-row"><span class="fwd-label">${I18N.t('msg.forwarded_from')}</span> <span class="fwd-name">${escapeHTML(fwdName)}</span>${postSuffix}${authorSuffix}${extras}</div>${dateRow}</div></div>`;
}

function buildReplyHeader(msg, isStickerMsg = false, rootMsgId = null) {
    if (!msg || (!msg.reply_to_msg_id && !msg.reply_to_story_id)) return '';

    const replyId = msg.reply_to_msg_id ? Number(msg.reply_to_msg_id) : null;
    const isStoryReply = !replyId && !!msg.reply_to_story_id;
    const topId = msg.reply_to_top_id ? Number(msg.reply_to_top_id) : null;
    const rootId = rootMsgId ? Number(rootMsgId) : null;

    // Suppress drawing reply box if it points to the root post itself (Telegram automatic thread wrapper)
    if (replyId) {
        if (rootId && replyId === rootId) return '';
        if (topId && replyId === topId) return '';
    }

    let replyName = '';
    if (isStoryReply) {
        replyName = (typeof I18N !== 'undefined' ? I18N.t('msg.story') : 'Story') || 'Story';
    } else if (msg.reply_original_fwd_name) {
        replyName = msg.reply_original_fwd_name;
    } else if (msg.reply_sender_id) {
        const replySender = (typeof allUsers !== 'undefined' && allUsers) ? allUsers[msg.reply_sender_id] : null;
        if (replySender && !replySender.is_chat) {
            replyName = replySender.first_name || `[ID: ${msg.reply_sender_id}]`;
            if (replySender.last_name && replySender.last_name !== '[Недоступен]' && replySender.last_name !== '[Unavailable]') replyName += ` ${replySender.last_name}`;
        } else if (msg.reply_sender_first_name) {
            replyName = msg.reply_sender_first_name;
            if (msg.reply_sender_last_name && msg.reply_sender_last_name !== '[Недоступен]' && msg.reply_sender_last_name !== '[Unavailable]') {
                replyName += ` ${msg.reply_sender_last_name}`;
            }
        } else if (replySender) {
            replyName = replySender.first_name || `[ID: ${msg.reply_sender_id}]`;
        } else {
            replyName = `[ID: ${msg.reply_sender_id}]`;
        }
    } else {
        replyName = I18N.t('msg.reply_default') || '[Message]';
    }

    // DOM fallback: if replied message is in DOM, prefer original sender name if replied message was forwarded
    if (replyId && !msg.reply_original_fwd_name) {
        const repliedEl = document.getElementById('msg-' + replyId);
        if (repliedEl) {
            const fwdNameEl = repliedEl.querySelector('.fwd-name');
            if (fwdNameEl && fwdNameEl.textContent.trim()) {
                replyName = fwdNameEl.textContent.trim();
            }
        }
    }

    let replyContent = '';
    if (msg.quote_text) {
        replyContent = `<div class="reply-quote">${formatText(msg.quote_text, msg.quote_entities)}</div>`;
    } else {
        let snippet = msg.reply_snippet || '...';
        if (isStickerMsg && snippet.length > 28) {
            snippet = snippet.substring(0, 25) + '...';
        } else if (snippet.startsWith('http://') || snippet.startsWith('https://')) {
            if (snippet.length > 45) snippet = snippet.substring(0, 42) + '...';
        } else if (snippet.length > 80) {
            snippet = snippet.substring(0, 77) + '...';
        }
        replyContent = `<div class="snippet">${escapeHTML(snippet)}</div>`;
    }

    let replyExtras = '';
    if (msg.is_forum_topic) replyExtras += `<span class="reply-tag forum-tag">${I18N.t('msg.forum_topic')}</span>`;
    if (msg.reply_to_top_id && msg.reply_to_top_id !== msg.reply_to_msg_id)
        replyExtras += `<span class="reply-tag thread-tag">${I18N.t('msg.thread')}</span>`;
    if (msg.reply_to_peer_id) replyExtras += `<span class="reply-tag cross-chat-tag">${I18N.t('msg.cross_chat')}</span>`;
    const replyMediaHtml = msg.reply_media ? '<span class="reply-media-icon">📎</span>' : '';

    let replyFromHtml = '';
    if (msg.reply_from) {
        const rfName = msg.reply_from.from_name || `ID: ${msg.reply_from.from_id || '?'}`;
        replyFromHtml = `<div class="reply-from-row">↺ ${escapeHTML(rfName)}</div>`;
    }

    let replyColorIdx = 0;
    if (msg.reply_sender_id && Number(msg.reply_sender_id) > 0) {
        replyColorIdx = Math.abs(parseInt(msg.reply_sender_id, 10)) % 7;
    } else if (replyName && replyName !== '[Сообщение]' && replyName !== '[Message]') {
        let hash = 0;
        for (let i = 0; i < replyName.length; i++) {
            hash = (hash * 31 + replyName.charCodeAt(i)) & 0xffffffff;
        }
        replyColorIdx = Math.abs(hash) % 7;
    } else if (msg.reply_sender_id) {
        replyColorIdx = Math.abs(parseInt(msg.reply_sender_id, 10)) % 7;
    }
    let thumbHtml = '';
    const thumbRaw = msg.reply_thumbnail_path || (msg.reply_media_type === 'photo' ? msg.reply_media_path : null);
    if (thumbRaw) {
        const thumbRel = thumbRaw.replace(/^(media_backup[\\/])/, '');
        const thumbUrl = '/data/' + thumbRel.replace(/\\/g, '/');
        const safeThumbUrl = encodeURI(thumbUrl).replace(/'/g, "%27");
        thumbHtml = `<img class="reply-thumb" src="${safeThumbUrl}" alt="" loading="lazy" onerror="this.style.display='none'">`;
    }

    const rawReplyPeer = (msg.reply_to_peer_id && typeof msg.reply_to_peer_id === 'object') ? (msg.reply_to_peer_id.channel_id ? `-100${msg.reply_to_peer_id.channel_id}` : (msg.reply_to_peer_id.chat_id ? `-${msg.reply_to_peer_id.chat_id}` : msg.reply_to_peer_id.user_id)) : msg.reply_to_peer_id; const targetChat = String(rawReplyPeer || msg.chat_id || window.currentChatId || '').replace(/[^0-9-]/g, '');
    const replyNameSpan = replyName ? `<span class="sender-name-text reply-sender-name">${escapeHTML(replyName)}</span>` : '';
    const clickAction = msg.reply_to_msg_id ? `scrollToMessage(${Number(msg.reply_to_msg_id) || 0}, '${targetChat}')` : '';
    const cursorStyle = msg.reply_to_msg_id ? 'pointer' : 'default';
    return `<div class="reply" style="cursor: ${cursorStyle}; border-left-color: var(--peer-color-${replyColorIdx})" onclick="event.stopPropagation(); ${clickAction}"><div class="reply-main-content"><div class="sender reply-sender" data-peer-color="${replyColorIdx}">${replyNameSpan}${replyExtras}${replyMediaHtml}</div>${replyFromHtml}${replyContent}</div>${thumbHtml}</div>`;
}

window.buildForwardHeader = buildForwardHeader;
window.buildReplyHeader = buildReplyHeader;

function resolveViaBot(msg) {
    const botId = msg.via_bot_id || msg.via_business_bot_id;
    if (!botId) return '';
    const isBusiness = Boolean(msg.via_business_bot_id && !msg.via_bot_id);
    const botUser = (typeof allUsers !== 'undefined' && allUsers) ? (allUsers[botId] || allUsers[String(botId)]) : null;
    let botHandle = '';
    if (botUser && botUser.username) {
        botHandle = botUser.username;
    } else if (botUser && botUser.first_name) {
        botHandle = botUser.first_name;
    } else {
        botHandle = `ID: ${botId}`;
    }
    const labelKey = isBusiness ? 'msg.via_business' : 'msg.via';
    const localizedText = (typeof I18N !== 'undefined' && I18N.t) ? I18N.t(labelKey, escapeHTML(botHandle)) : `via @${escapeHTML(botHandle)}`;
    return `<span class="via-bot" onclick="event.stopPropagation(); if(window.UserModal) UserModal.open(${Number(botId) || 0});" style="cursor:pointer;" title="Inline Bot">${localizedText}</span>`;
}
window.resolveViaBot = resolveViaBot;

function buildInfoItems(msg) {
    let infoItems = [];
    if (msg.post_author_name) { infoItems.push(`${I18N.t('msg.author', escapeHTML(msg.post_author_name))}`); }
    if (msg.views_count) { infoItems.push(`👁 ${msg.views_count}`); }
    if (msg.forwards > 0) { infoItems.push(`↪ ${msg.forwards}`); }

    const cid = msg.chat_id || currentChatId;
    const chatData = (typeof getChatById === 'function') ? getChatById(cid) : chatListData.find(c => String(c.chat_id) === String(cid));
    const linkedId = chatData ? normalizeId(chatData.linked_chat_id) : null;

    if (msg.replies && msg.replies.replies > 0 && msg.is_post !== 1) {
        infoItems.push(`💬 ${msg.replies.replies}`);
    }
    if (msg.is_post === 1) { infoItems.push('<span class="msg-badge post-badge">📢 Post</span>'); }
    if (msg.is_legacy === 1) { infoItems.push('<span class="msg-badge legacy-badge">legacy</span>'); }
    if (msg.restriction_reason && msg.restriction_reason.length > 0) {
        infoItems.push(`<span class="msg-badge restricted-badge">${I18N.t('msg.restricted')}</span>`);
    }
    if (msg.saved_peer_id) {
        const sp = msg.saved_peer_id;
        const spLabel = escapeHTML(sp.user_id ? `user:${sp.user_id}` : (sp.channel_id ? `channel:${sp.channel_id}` : JSON.stringify(sp).substring(0, 20)));
        infoItems.push(I18N.t('msg.saved_from', spLabel));
    }
    if (msg.is_forum_topic === 1 && msg.reply_to_top_id) {
        infoItems.push(`<span class="msg-badge topic-badge">💬 Topic #${escapeHTML(String(msg.reply_to_top_id))}</span>`);
    }
    return infoItems;
}

function buildCommentsBar(msg) {
    // Only genuine channel posts can have discussion comments
    if (msg.is_post !== 1) return '';

    // Suppress if message is forwarded channel post or discussion root copy
    if (msg.is_channel_post || (msg.forward_info && (msg.forward_info.saved_from_msg_id || msg.forward_info.channel_post))) return '';

    const cid = msg.chat_id || currentChatId;

    // Suppress if current chat is a linked discussion child chat
    if (window.childChatIds && window.childChatIds.has(String(cid))) return '';

    const chatData = (typeof getChatById === 'function')
        ? getChatById(cid)
        : ((typeof chatListData !== 'undefined' && chatListData) ? chatListData.find(c => String(c.chat_id) === String(cid)) : null);

    // Comments bar only exists for broadcast channels
    if (!chatData || chatData.chat_type !== 'channel') return '';

    // Must have a linked discussion chat ID
    const linkedId = chatData.linked_chat_id ? normalizeId(chatData.linked_chat_id) : null;
    if (!linkedId) return '';

    // Must have comments enabled in replies info
    if (!msg.replies || !msg.replies.replies || msg.replies.replies <= 0) return '';
    if (msg.replies.comments === false) return '';

    const count = msg.replies.replies;
    const label = `${count} ${getCommentWord(count)}`;

    return `
            <div class="comments-bar" data-msg-id="${msg.message_id}" data-linked-id="${linkedId}" onclick="event.stopPropagation(); triggerJumpToComments(this)">
                <div class="comments-left">
                    <span class="comments-icon">💬</span>
                    <span class="comments-label">${label}</span>
                </div>
            </div>
        `;
}

function getCommentWord(n) {
    if (I18N.lang() === 'en') {
        return n === 1 ? I18N.t('msg.comment') : I18N.t('msg.comments');
    }
    const last = n % 10;
    const lastTwo = n % 100;
    if (lastTwo >= 11 && lastTwo <= 19) return 'комментариев';
    if (last === 1) return 'комментарий';
    if (last >= 2 && last <= 4) return 'комментария';
    return 'комментариев';
}

function getMessageParts(msg, isComment = false, mediaMetaHtml = '') {
    const textHtml = msg.text ? `<div class="text">${formatText(msg.text, msg.entities)}</div>` : '';
    let mediaHtml = '';

    if (msg.poll_data) {
        mediaHtml = renderPoll(msg.poll_data);
    } else if (msg.media_local_path) {
        mediaHtml = renderLocalMedia(msg, isComment, mediaMetaHtml);
    } else if (msg.media_skipped) {
        mediaHtml = renderSkippedMedia(msg, isComment);
    } else if (msg.media_other && ['MessageMediaWebPage', 'MessageMediaGeo', 'MessageMediaGeoLive', 'MessageMediaVenue', 'MessageMediaContact', 'MessageMediaDice', 'MessageMediaGame'].includes(msg.media_other._)) {
        mediaHtml = renderOtherMedia(msg.media_other, msg.media_type, isComment, mediaMetaHtml);
    } else if (msg.media_type || msg.media_original_name || msg.thumbnail_local_path) {
        mediaHtml = renderPendingMedia(msg, isComment);
    } else if (msg.media_other) {
        mediaHtml = renderOtherMedia(msg.media_other, msg.media_type, isComment, mediaMetaHtml);
    }

    return { textHtml, mediaHtml };
}

function renderMessageContent(msg, isComment = false, mediaMetaHtml = '') {
    const { textHtml, mediaHtml } = getMessageParts(msg, isComment, mediaMetaHtml);
    // invert_media: show text BEFORE media
    const parts = msg.invert_media ? [textHtml, mediaHtml] : [mediaHtml, textHtml];
    const result = parts.join('');
    return result || `<div class="text">${I18N.t('msg.empty')}</div>`;
}

function renderPendingMedia(msg, isComment = false) {
    let rawName = stripMessageIdPrefix(msg.media_original_name || (msg.media_type ? msg.media_type.toUpperCase() : 'Media'), msg.message_id);
    const mediaClass = isComment ? 'media comment-media-compact' : 'media';
    const chatId = msg.chat_id || (window.currentChatId || null);
    const mType = (msg.media_type || '').toLowerCase();
    let icon = '📎';
    if (mType === 'photo') icon = '🖼️';
    else if (mType === 'video') icon = '🎥';
    else if (mType === 'voice') icon = '🎤';
    else if (mType === 'video_note') icon = '⭕';
    else if (mType === 'sticker') icon = '🎭';
    else if (mType === 'gif') icon = '🎞️';
    else if (mType === 'audio') icon = '🎵';

    const sizeBytes = msg.file_size || (msg.media_other && msg.media_other.size) || 0;
    const sizeStr = sizeBytes > 0 ? (sizeBytes / (1024 * 1024)).toFixed(1) + ' MB' : '';

    const pendingLabel = (typeof I18N !== 'undefined') ? I18N.t('media.not_downloaded') : 'Not downloaded';
    const reasonText = sizeStr ? `${pendingLabel} (${sizeStr})` : pendingLabel;
    const dlBtnTitle = (typeof I18N !== 'undefined' && I18N.t('btn.download_media_title')) || 'Download this media file';
    const dlBtnText = (typeof I18N !== 'undefined') ? (I18N.t('btn.download') || 'Download') : 'Download';
    const dlBtn = chatId ? `<button type="button" class="chat-inline-dl-btn" onclick="event.stopPropagation(); if(window.downloadSingleMedia) window.downloadSingleMedia(${chatId}, ${msg.message_id}, this);" title="${escapeHTML(dlBtnTitle)}">⬇️ ${escapeHTML(dlBtnText)}</button>` : '';

    if (msg.thumbnail_local_path) {
        const relThumb = msg.thumbnail_local_path.replace(/^(media_backup[\\/])/, '');
        const thumbUrl = encodeURI('/data/' + relThumb.replace(/\\/g, '/')).replace(/'/g, "%27");
        return `
            <div class="${mediaClass}">
                <div class="pending-media-thumb-card" id="skipped-media-${msg.message_id}" style="position: relative; max-width: 380px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color); background: rgba(0,0,0,0.25);">
                    <img src="${thumbUrl}" alt="Media thumbnail" style="width: 100%; max-height: 320px; object-fit: cover; display: block; opacity: 0.9; filter: brightness(0.92);" loading="lazy" />
                    <div style="position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,0.85) 45%); padding: 10px 12px; display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                        <div style="min-width: 0;">
                            <div style="font-size: 11px; font-weight: 600; color: #ffb74d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${icon} ${mType ? mType.toUpperCase() : 'MEDIA'} — ${escapeHTML(reasonText)}
                            </div>
                            <div style="font-size: 10px; opacity: 0.8; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHTML(rawName)}</div>
                        </div>
                        ${dlBtn}
                    </div>
                </div>
            </div>
        `;
    }

    return `<div class="${mediaClass}"><div class="download-link pending" id="skipped-media-${msg.message_id}"><div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;"><div><span>${icon}</span> <b>${escapeHTML(rawName)}</b> <span class="skipped-reason" style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3);">${escapeHTML(reasonText)}</span></div>${dlBtn}</div></div></div>`;
}

function renderSkippedMedia(msg, isComment = false) {
    let skippedName = stripMessageIdPrefix(msg.media_original_name || 'File', msg.message_id);
    const mediaClass = isComment ? 'media comment-media-compact' : 'media';
    const chatId = msg.chat_id || (window.currentChatId || null);
    const mType = (msg.media_type || '').toLowerCase();
    let icon = '📎';
    if (mType === 'photo') icon = '🖼️';
    else if (mType === 'video') icon = '🎥';
    else if (mType === 'voice') icon = '🎤';
    else if (mType === 'video_note') icon = '⭕';
    else if (mType === 'sticker') icon = '🎭';
    else if (mType === 'gif') icon = '🎞️';
    else if (mType === 'audio') icon = '🎵';

    const sizeBytes = msg.file_size || (msg.media_other && msg.media_other.size) || 0;
    const sizeStr = sizeBytes > 0 ? (sizeBytes / (1024 * 1024)).toFixed(1) + ' MB' : '';

    const dlBtnTitle = (typeof I18N !== 'undefined' && I18N.t('btn.download_media_title')) || 'Download this media file';
    const dlBtnText = (typeof I18N !== 'undefined') ? (I18N.t('btn.download') || 'Download') : 'Download';
    const dlBtn = chatId ? `<button type="button" class="chat-inline-dl-btn" onclick="event.stopPropagation(); if(window.downloadSingleMedia) window.downloadSingleMedia(${chatId}, ${msg.message_id}, this);" title="${escapeHTML(dlBtnTitle)}">⬇️ ${escapeHTML(dlBtnText)}</button>` : '';

    const rawSkip = msg.media_skipped || '';
    const skipLower = rawSkip.toLowerCase();
    let badgeColor = '#ffb74d';
    let badgeLabel = rawSkip || 'Skipped';
    if (skipLower === 'downloading') {
        badgeColor = 'var(--link-color, #2481cc)';
        badgeLabel = (typeof I18N !== 'undefined') ? I18N.t('media.downloading') : 'Downloading...';
    } else if (skipLower.includes('unavailable') || skipLower.includes('expired')) {
        badgeColor = '#b0bec5';
        badgeLabel = rawSkip || 'Unavailable';
    } else if (skipLower.startsWith('failed:') || skipLower.startsWith('download error:') || skipLower.includes('timeout')) {
        badgeColor = '#ff8a80';
        badgeLabel = rawSkip || 'Download Failed';
    }

    if (msg.thumbnail_local_path) {
        const relThumb = msg.thumbnail_local_path.replace(/^(media_backup[\\/])/, '');
        const thumbUrl = encodeURI('/data/' + relThumb.replace(/\\/g, '/')).replace(/'/g, "%27");

        return `
            <div class="${mediaClass}">
                <div class="pending-media-thumb-card" id="skipped-media-${msg.message_id}" style="position: relative; max-width: 380px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color); background: rgba(0,0,0,0.25);" title="${escapeHTML(rawSkip)}">
                    <img src="${thumbUrl}" alt="Media thumbnail" style="width: 100%; max-height: 320px; object-fit: cover; display: block; opacity: 0.9; filter: brightness(0.92);" loading="lazy" />
                    <div style="position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,0.85) 45%); padding: 10px 12px; display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                        <div style="min-width: 0;">
                            <div style="font-size: 11px; font-weight: 600; color: ${badgeColor}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${icon} ${mType ? mType.toUpperCase() : 'MEDIA'} — ${escapeHTML(badgeLabel)}
                            </div>
                            <div style="font-size: 10px; opacity: 0.8; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHTML(skippedName)} ${sizeStr ? `(${sizeStr})` : ''}</div>
                        </div>
                        ${dlBtn}
                    </div>
                </div>
            </div>
        `;
    }

    let skipBadgeStyle = '';
    let displayedSkipText = rawSkip;
    if (skipLower === 'downloading') {
        skipBadgeStyle = 'color: var(--link-color, #2481cc); font-weight: 500;';
        displayedSkipText = (typeof I18N !== 'undefined') ? I18N.t('media.downloading') : 'Downloading...';
    } else if (skipLower.includes('unavailable') || skipLower.includes('expired')) {
        skipBadgeStyle = 'color: #90a4ae;';
    } else if (skipLower.startsWith('failed:') || skipLower.startsWith('download error:') || skipLower.includes('timeout')) {
        skipBadgeStyle = 'color: #ff5252;';
    }

    return `<div class="${mediaClass}"><div class="download-link skipped" id="skipped-media-${msg.message_id}" title="${escapeHTML(rawSkip)}"><div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;"><div>${escapeHTML(skippedName)}<span class="skipped-reason" style="${skipBadgeStyle}">${escapeHTML(displayedSkipText)}</span></div>${dlBtn}</div></div></div>`;
}

function renderLocalMedia(msg, isComment = false, mediaMetaHtml = '') {
    const relativePath = msg.media_local_path.replace(/^(media_backup[\\/])/, '');
    const mediaPath = '/data/' + relativePath.replace(/\\/g, '/');
    const safeMediaPath = encodeURI(mediaPath).replace(/'/g, "%27");

    const mime = msg.media_mime_type || '';
    let filename = stripMessageIdPrefix(mediaPath.split('/').pop(), msg.message_id);
    let mediaHtml = '';
    const spoilerClass = msg.is_media_spoiler ? ' media-spoiler' : '';
    const spoilerCheck = `if(this.classList.contains('media-spoiler')) { this.classList.remove('media-spoiler'); } else `;

    if (mime === 'application/x-tgsticker') {
        const stickerId = `sticker-${msg.message_id}`;
        mediaHtml = `<div id="${stickerId}" class="sticker-container" data-path="${safeMediaPath}"></div>`;
    } else if (mime === 'video/webm') {
        mediaHtml = `<video class="sticker-container" src="${safeMediaPath}" autoplay loop muted playsinline preload="auto"></video>`;
    } else if (mime === 'image/webp') {
        mediaHtml = `<img src="${safeMediaPath}" class="sticker-image" alt="sticker" loading="lazy">`;
    } else if (msg.is_voice === 1) {
        const msgId = msg.id || msg.message_id;
        const isOut = (msg.is_outgoing === 1) || (typeof myUserId !== 'undefined' && myUserId && msg.sender_id == myUserId) || (window.myUserId && msg.sender_id == window.myUserId);
        const hasTranscription = Boolean(msg.transcription_text && msg.transcription_text.trim());
        const canTranscribe = Boolean(msg.media_local_path || hasTranscription);
        const alignClass = isOut ? 'outgoing' : 'incoming';
        const compactClass = isComment ? ' comment-transcription-compact' : '';

        const toggleBtnHtml = canTranscribe ? `
            <button type="button" class="transcribe-toggle-btn ${alignClass} ${hasTranscription ? 'has-text' : ''}" 
                    id="transcribe-btn-${msg.chat_id}-${msgId}"
                    onclick="event.stopPropagation(); window.handleTranscribeToggle(${msg.chat_id}, ${msgId}, this, event)" 
                    title="${hasTranscription ? 'Toggle transcription (→A)' : 'Transcribe audio (→A)'}">
                →A
            </button>` : '';

        const transcriptBlockHtml = canTranscribe ? `
            <div class="voice-transcription-block ${hasTranscription ? 'collapsed' : 'hidden'}${compactClass}" 
                 id="transcript-${msg.chat_id}-${msgId}">
                <div class="transcription-header">
                    <span class="transcription-icon" title="Transcription">📝</span>
                    <button type="button" class="transcription-copy-btn" onclick="event.stopPropagation(); window.copyTranscriptionText(this)" title="Copy text">📋</button>
                </div>
                <div class="transcription-text">${hasTranscription ? escapeHTML(msg.transcription_text) : ''}</div>
            </div>` : '';

        const durationSec = Number(msg.duration) || 0;
        const displayDuration = durationSec > 0 ? (window.formatVoiceDuration ? window.formatVoiceDuration(durationSec) : `${Math.floor(durationSec/60)}:${(durationSec%60).toString().padStart(2,'0')}`) : '--:--';
        const waveformRaw = msg.waveform_data ? encodeURIComponent(msg.waveform_data) : '';

        mediaHtml = `
            <div class="voice-player-container ${alignClass}${compactClass}">
                <div class="voice-message-player ${alignClass}">
                    <div class="voice-player-top-row">
                        <button type="button" class="voice-play-btn" onclick="toggleVoicePlay(this)" title="Play voice message"><svg class="icon-play" viewBox="0 0 24 24" width="14" height="14" style="margin-left:1.5px; fill:currentColor;"><path d="M8 5v14l11-7z"/></svg></button>
                        <div class="voice-waveform-wrap">
                            <canvas class="voice-waveform-canvas" width="160" height="24" data-waveform="${waveformRaw}" onclick="window.handleWaveformClick(event, this)"></canvas>
                        </div>
                        <audio class="voice-audio-hidden" src="${safeMediaPath}" preload="metadata" onloadedmetadata="const p=this.closest('.voice-message-player'); if(p){ const d=p.querySelector('.voice-duration'); if(d && isFinite(this.duration) && this.duration > 0){ if(window.formatVoiceDuration) d.textContent=window.formatVoiceDuration(this.duration); d.setAttribute('data-total-duration', this.duration); } }"></audio>
                    </div>
                    <div class="voice-player-sub-bar">
                        <span class="voice-duration" data-total-duration="${durationSec}">${displayDuration}</span>
                        ${toggleBtnHtml}
                    </div>
                </div>
                ${transcriptBlockHtml}
            </div>`;
    } else if (msg.is_round_video === 1) {
        let posterAttr = '';
        const coverSrc = msg.video_cover_path || msg.thumbnail_local_path;
        if (coverSrc) {
            const coverRel = coverSrc.replace(/^(media_backup[\\/])/, '');
            posterAttr = `poster="${encodeURI('/data/' + coverRel.replace(/\\/g, '/')).replace(/'/g, '%27')}"`;
        }
        const msgId = msg.id || msg.message_id;
        const isOut = (msg.is_outgoing === 1) || (typeof myUserId !== 'undefined' && myUserId && msg.sender_id == myUserId) || (window.myUserId && msg.sender_id == window.myUserId);
        const hasTranscription = Boolean(msg.transcription_text && msg.transcription_text.trim());
        const canTranscribe = Boolean(msg.media_local_path || hasTranscription);
        const alignClass = isOut ? 'outgoing' : 'incoming';
        const compactClass = isComment ? ' comment-transcription-compact' : '';

        const toggleBtnHtml = canTranscribe ? `
            <button type="button" class="transcribe-toggle-btn ${alignClass} ${hasTranscription ? 'has-text' : ''}" 
                    id="transcribe-btn-${msg.chat_id}-${msgId}"
                    onclick="event.stopPropagation(); window.handleTranscribeToggle(${msg.chat_id}, ${msgId}, this, event)" 
                    title="${hasTranscription ? 'Toggle transcription (→A)' : 'Transcribe audio (→A)'}">
                →A
            </button>` : '';

        const transcriptBlockHtml = canTranscribe ? `
            <div class="voice-transcription-block ${hasTranscription ? 'collapsed' : 'hidden'}${compactClass}" 
                 id="transcript-${msg.chat_id}-${msgId}">
                <div class="transcription-header">
                    <span class="transcription-icon" title="Transcription">📝</span>
                    <button type="button" class="transcription-copy-btn" onclick="event.stopPropagation(); window.copyTranscriptionText(this)" title="Copy text">📋</button>
                </div>
                <div class="transcription-text">${hasTranscription ? escapeHTML(msg.transcription_text) : ''}</div>
            </div>` : '';

        mediaHtml = `
            <div class="round-video-wrapper">
                <video class="round-video" src="${safeMediaPath}" ${posterAttr} controls preload="metadata"></video>
                ${mediaMetaHtml || ''}
                <div class="round-video-controls ${alignClass}">
                    ${toggleBtnHtml}
                </div>
                ${transcriptBlockHtml}
            </div>`;
    } else if (mime.startsWith('video/')) {
        let posterAttr = '';
        const posterSrc = msg.video_cover_path || msg.thumbnail_local_path;
        if (posterSrc) {
            const rel = posterSrc.replace(/^(media_backup[\\/])/, '');
            posterAttr = `poster="${encodeURI('/data/' + rel.replace(/\\/g, '/')).replace(/'/g, '%27')}"`;
        }
        const maxStyle = isComment ? 'max-width:220px; max-height:140px;' : 'max-width:100%; max-height:var(--chat-max-media-height, 68vh);';
        mediaHtml = `
                <div class="video-container${spoilerClass}" style="position:relative; display:inline-flex;" onclick="${spoilerCheck}{}">
                    <video src="${safeMediaPath}" ${posterAttr} controls preload="metadata" style="${maxStyle} border-radius:8px; display:block;" onloadedmetadata="if(window.handleMediaRatio) window.handleMediaRatio(this);"></video>
                    <button onclick="event.stopPropagation(); openPlayer('${safeMediaPath}', '${mime}')" title="Separated window" style="position:absolute; top:5px; right:5px; background:rgba(0,0,0,0.6); color:white; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; z-index:2;">🔲</button>
                    ${mediaMetaHtml || ''}
                </div>`;
    } else if (mime === 'audio/ogg' || mime === 'audio/oga') {
        mediaHtml = `<audio class="voice-player" src="${safeMediaPath}" controls preload="none"></audio>`;
    } else if (mime.startsWith('audio/')) {
        mediaHtml = `<a class="download-link" href="javascript:void(0)" onclick="openPlayer('${safeMediaPath}', '${mime}')" title="${escapeHTML(filename)}">${I18N.t('msg.audio', escapeHTML(filename))}</a>`;
    } else if (mime.startsWith('image/')) {
        mediaHtml = `<div class="media-thumb-container${spoilerClass}" onclick="${spoilerCheck}{ if(typeof openLightbox==='function') openLightbox('${safeMediaPath}'); else window.open('${safeMediaPath}', '_blank'); }"><img src="${safeMediaPath}" alt="image" class="photo" loading="lazy" decoding="async" onload="if(window.handleMediaRatio) window.handleMediaRatio(this);">${mediaMetaHtml || ''}</div>`;
    } else {
        mediaHtml = `<a class="download-link" href="${safeMediaPath}" target="_blank" title="${escapeHTML(filename)}">📁 ${escapeHTML(filename)}</a>`;
    }
    const mediaClass = isComment ? 'media comment-media-compact' : 'media';
    return `<div class="${mediaClass}">${mediaHtml}</div>`;
}

function renderPoll(poll_data) {
    let pollHtml = `<div class="poll">`;
    let pollQuestion = poll_data.question;
    if (typeof pollQuestion === 'object' && pollQuestion !== null && typeof pollQuestion.text !== 'undefined') {
        pollQuestion = pollQuestion.text;
    }
    pollHtml += `<div class="poll-question">${escapeHTML(pollQuestion)}</div>`;
    let totalVotes = poll_data.total_voters || 0;
    if (totalVotes === 0 && poll_data.options) {
        poll_data.options.forEach(opt => { totalVotes += (opt.voters || 0); });
    }
    pollHtml += `<ul class="poll-options">`;
    if (poll_data.options) {
        poll_data.options.forEach(opt => {
            let percent = 0;
            if (totalVotes > 0) percent = Math.round(((opt.voters || 0) / totalVotes) * 100);
            pollHtml += `<li class="poll-option"><div class="poll-option-text">${escapeHTML(opt.text)}</div><div class="poll-option-bar-container"><div class="poll-option-bar-bg"><div class="poll-option-bar" style="width: ${percent}%;"></div></div><div class="poll-option-percent">${percent}%</div></div></li>`;
        });
    }
    pollHtml += `</ul>`;
    let pollFooter = poll_data.is_closed ? I18N.t('poll.closed') : I18N.t('poll.active');
    if (poll_data.is_quiz) pollFooter = I18N.t('poll.quiz');
    if (totalVotes > 0) pollFooter += ` · ${I18N.t('poll.votes', totalVotes)}`;
    pollHtml += `<div class="poll-footer">${pollFooter}</div></div>`;
    return pollHtml;
}

function renderOtherMedia(media_other, media_type, isComment = false, mediaMetaHtml = '') {
    let mediaHtml = '';
    switch (media_other._) {
        case 'MessageMediaWebPage':
            if (media_other.webpage && media_other.webpage.type === 'webpage') {
                const wp = media_other.webpage;
                let mediaPreviewHtml = '';

                // Collect all possible media items from the webpage
                const mediaItems = [];
                if (wp.photo) mediaItems.push({ type: 'photo', item: wp.photo });
                if (wp.photos) wp.photos.forEach(p => mediaItems.push({ type: 'photo', item: p }));
                if (wp.document) mediaItems.push({ type: 'document', item: wp.document });
                if (wp.documents) wp.documents.forEach(d => mediaItems.push({ type: 'document', item: d }));

                if (mediaItems.length > 1) {
                    mediaPreviewHtml = '<div class="album-container" style="margin-top: 8px; max-width: 100%;">';
                    mediaItems.forEach(mi => {
                        const item = mi.item;
                        if (item.local_path) {
                            const relPath = item.local_path.replace(/^(media_backup[\\/])/, '');
                            const safePath = encodeURI('/data/' + relPath.replace(/\\/g, '/')).replace(/'/g, "%27");

                            if (mi.type === 'photo' || (item.mime_type === 'image/gif')) {
                                mediaPreviewHtml += `<div class="album-item" onclick="event.stopPropagation(); if(typeof openLightbox==='function') openLightbox('${safePath}'); else window.open('${safePath}', '_blank');"><img src="${safePath}" loading="lazy"></div>`;
                            } else if (item.mime_type && item.mime_type.startsWith('video/')) {
                                mediaPreviewHtml += `<div class="album-item video-thumb-album" onclick="event.stopPropagation(); if(typeof openLightbox==='function') openLightbox('${safePath}'); else window.open('${safePath}', '_blank');"><video src="${safePath}" muted loop autoplay playsinline style="width:100%;height:100%;object-fit:cover;"></video><div class="play-icon-album">▶</div></div>`;
                            }
                        }
                    });
                    mediaPreviewHtml += '</div>';
                } else if (mediaItems.length === 1) {
                    const mi = mediaItems[0];
                    const item = mi.item;
                    if (item.local_path) {
                        const relPath = item.local_path.replace(/^(media_backup[\\/])/, '');
                        const safePath = encodeURI('/data/' + relPath.replace(/\\/g, '/')).replace(/'/g, "%27");

                        if (mi.type === 'photo' || (item.mime_type === 'image/gif')) {
                            mediaPreviewHtml = `<div class="media-thumb-container web-preview-media-container" style="display:block; width: 100%;"><img src="${safePath}" class="web-preview-image photo" loading="lazy">${mediaMetaHtml || ''}</div>`;
                        } else if (item.mime_type && item.mime_type.startsWith('video/')) {
                            mediaPreviewHtml = `
                                    <div class="video-container web-preview-media-container" style="position:relative; display:block; width: 100%; max-width: 100%;" onclick="event.stopPropagation();">
                                        <video src="${safePath}" autoplay loop muted playsinline preload="auto" class="web-preview-video" onclick="event.stopPropagation(); this.paused ? this.play() : this.pause();"></video>
                                    </div>`;
                        }
                    }
                }
                mediaHtml = `
                        <div class="web-preview" data-preview-url="${escapeHTML((window.sanitizeUrl || String)(wp.url))}" onclick="if(this.dataset.previewUrl && this.dataset.previewUrl !== '#') window.open(this.dataset.previewUrl, '_blank', 'noopener,noreferrer')">
                            <div class="web-preview-site">${escapeHTML(wp.site_name || I18N.t('msg.website') || 'Website')}</div>
                            <div class="web-preview-title">${escapeHTML(wp.title || '')}</div>
                            <div class="web-preview-desc">${formatText(wp.description, null)}</div>
                            <div style="margin-top: 8px; width: 100%;">
                                ${mediaPreviewHtml}
                            </div>
                        </div>`;
            }
            break;
        case 'MessageMediaGeo':
        case 'MessageMediaGeoLive':
            if (media_other.geo) {
                mediaHtml = `
                        <a class="special-media-card" href="https://maps.google.com/?q=${encodeURIComponent(media_other.geo.lat)},${encodeURIComponent(media_other.geo.long)}" target="_blank" rel="noopener noreferrer">
                            <div class="media-icon geo-icon">📍</div>
                            <div class="media-details">
                                <div class="media-title">${I18N.t('msg.geo')}</div>
                                <div class="media-subtitle">${I18N.t('msg.open_map')}</div>
                            </div>
                        </a>`;
            }
            break;
        case 'MessageMediaVenue':
            if (media_other.geo) {
                mediaHtml = `
                        <a class="special-media-card" href="https://maps.google.com/?q=${encodeURIComponent(media_other.geo.lat)},${encodeURIComponent(media_other.geo.long)}" target="_blank" rel="noopener noreferrer">
                            <div class="media-icon geo-icon">🏢</div>
                            <div class="media-details">
                                <div class="media-title">${escapeHTML(media_other.title)}</div>
                                <div class="media-subtitle">${escapeHTML(media_other.address)}</div>
                            </div>
                        </a>`;
            }
            break;
        case 'MessageMediaContact':
            const firstName = media_other.first_name || '';
            const lastName = media_other.last_name || '';
            mediaHtml = `
                    <div class="special-media-card">
                        <div class="media-icon contact-icon">${escapeHTML(firstName.charAt(0) || '👤')}</div>
                        <div class="media-details">
                            <div class="media-title">${escapeHTML(firstName)} ${escapeHTML(lastName)}</div>
                            <div class="media-subtitle">${escapeHTML(media_other.phone_number)}</div>
                        </div>
                    </div>`;
            break;
        case 'MessageMediaDice':
            mediaHtml = `
                    <div class="dice-container">
                        <span class="dice-emoji">${escapeHTML(media_other.emoticon)}</span>
                        <span class="dice-value">${escapeHTML(String(media_other.value ?? ''))}</span>
                    </div>`;
            break;
        case 'MessageMediaGame': {
            const game = (media_other.game) || {};
            mediaHtml = `
                    <div class="special-media-card game-card">
                        <div class="media-icon">🎮</div>
                        <div class="media-details">
                            <div class="media-title">${escapeHTML(game.title || I18N.t('msg.game') || 'Game')}</div>
                            <div class="media-subtitle">${escapeHTML((game.description || '').substring(0, 80))}</div>
                        </div>
                    </div>`;
            break;
        }
        case 'MessageMediaStory': {
            const peer = media_other.peer || {};
            const storyId = media_other.id ? ` #${media_other.id}` : '';
            const storyPeer = peer.user_id ? `user:${peer.user_id}` : (peer.channel_id ? `channel:${peer.channel_id}` : '');
            mediaHtml = `
                    <div class="special-media-card story-card">
                        <div class="media-icon">📖</div>
                        <div class="media-details">
                            <div class="media-title">${I18N.t('msg.story')}${storyId}${storyPeer ? ' (' + escapeHTML(storyPeer) + ')' : ''}</div>
                            <div class="media-subtitle">${I18N.t('msg.no_content')}</div>
                        </div>
                    </div>`;
            break;
        }
        case 'MessageMediaToDo': {
            const items = media_other.todo_list || media_other.items || [];
            const listHtml = items.map(i => {
                const done = i.completed || i.done || false;
                const txt = typeof i === 'string' ? i : (i.text || String(i));
                return `<li class="todo-item${done ? ' todo-done' : ''}">${done ? '☑' : '☐'} ${escapeHTML(txt)}</li>`;
            }).join('');
            mediaHtml = `
                    <div class="todo-card">
                        <div class="media-title">${I18N.t('msg.todo_list')}</div>
                        <ul class="todo-list">${listHtml || `<li>${I18N.t('msg.empty_list')}</li>`}</ul>
                    </div>`;
            break;
        }
        case 'MessageMediaPaidMedia': {
            const stars = media_other.stars || 0;
            const items = media_other.extended_media || [];
            let previewHtml = '';
            if (items.length > 0) {
                previewHtml = '<div class="paid-media-gallery" style="display:flex; gap:6px; margin-top:8px;">';
                items.forEach(it => {
                    if (it.photo) {
                        previewHtml += `<div class="paid-media-pill" style="padding:4px 8px; border-radius:4px; background:rgba(255,255,255,0.1); font-size:12px;">🖼️ Photo</div>`;
                    } else if (it.video) {
                        previewHtml += `<div class="paid-media-pill" style="padding:4px 8px; border-radius:4px; background:rgba(255,255,255,0.1); font-size:12px;">📹 Video</div>`;
                    }
                });
                previewHtml += '</div>';
            }
            mediaHtml = `
                <div class="special-media-card paid-media-card">
                    <div class="media-icon">⭐</div>
                    <div class="media-details">
                        <div class="media-title">${I18N.t('paid_media.title') || 'Paid Content'} · <strong>${stars} ⭐</strong></div>
                        <div class="media-subtitle">${I18N.t('paid_media.unlock_hint') || 'Telegram Stars Paid Media'}</div>
                        ${previewHtml}
                    </div>
                </div>`;
            break;
        }
        case 'MessageMediaInvoice': {
            const title = escapeHTML(media_other.title || 'Invoice');
            const desc = escapeHTML(media_other.description || '');
            const currency = escapeHTML(media_other.currency || '');
            const total = media_other.total_amount ? (media_other.total_amount / 100).toFixed(2) : '';
            mediaHtml = `
                <div class="special-media-card invoice-card">
                    <div class="media-icon">🧾</div>
                    <div class="media-details">
                        <div class="media-title">${title} ${total ? `· <strong>${total} ${currency}</strong>` : ''}</div>
                        <div class="media-subtitle">${desc}</div>
                    </div>
                </div>`;
            break;
        }
        case 'MessageMediaGiveaway': {
            const prize = media_other.stars ? `${media_other.stars} ⭐` : `${media_other.months || 0}m Telegram Premium`;
            const quantity = media_other.quantity || 1;
            mediaHtml = `
                <div class="special-media-card giveaway-card">
                    <div class="media-icon">🎉</div>
                    <div class="media-details">
                        <div class="media-title">Giveaway (${quantity} winners)</div>
                        <div class="media-subtitle">Prize: <strong>${escapeHTML(prize)}</strong></div>
                    </div>
                </div>`;
            break;
        }
    }
    if (mediaHtml) {
        return `<div class="media">${mediaHtml}</div>`;
    }
    return '';
}


function buildMessageMeta({ msg, metaClass, infoItems, timeDisplay, tooltipText }) {
    let metaInfoHtml = '';
    if (infoItems && infoItems.length > 0) {
        metaInfoHtml = infoItems.map(item => `<span class="meta-info-item">${item}</span>`).join('');
    }
    return `
        <div class="${metaClass}" onclick="event.stopPropagation()">
            <div class="meta-right">
                ${metaInfoHtml}
                <span class="date" title="${tooltipText}">${timeDisplay}</span>
            </div>
        </div>
    `;
}

function computeTextDiff(oldText, newText) {
    if (!oldText && !newText) return '';
    if (!oldText) return `<ins class="diff-ins">${escapeHTML(newText)}</ins>`;
    if (!newText) return `<del class="diff-del">${escapeHTML(oldText)}</del>`;

    const tokenize = str => str.match(/\s+|[^\s]+/g) || [];
    const oldTokens = tokenize(oldText);
    const newTokens = tokenize(newText);

    const m = oldTokens.length;
    const n = newTokens.length;
    if (m * n > 250000) {
        return `<del class="diff-del">${escapeHTML(oldText)}</del> <ins class="diff-ins">${escapeHTML(newText)}</ins>`;
    }
    const dp = Array.from({ length: m + 1 }, () => new Int32Array(n + 1));

    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            if (oldTokens[i - 1] === newTokens[j - 1]) {
                dp[i][j] = dp[i - 1][j - 1] + 1;
            } else {
                dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
            }
        }
    }

    let i = m, j = n;
    const diff = [];
    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && oldTokens[i - 1] === newTokens[j - 1]) {
            diff.push({ type: 'same', val: oldTokens[i - 1] });
            i--; j--;
        } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
            diff.push({ type: 'add', val: newTokens[j - 1] });
            j--;
        } else if (i > 0 && (j === 0 || dp[i][j - 1] < dp[i - 1][j])) {
            diff.push({ type: 'del', val: oldTokens[i - 1] });
            i--;
        }
    }
    diff.reverse();

    let html = '';
    let currType = null;
    let currBuf = '';

    const flush = () => {
        if (!currBuf) return;
        if (currType === 'same') {
            html += escapeHTML(currBuf);
        } else if (currType === 'del') {
            html += `<del class="diff-del">${escapeHTML(currBuf)}</del>`;
        } else if (currType === 'add') {
            html += `<ins class="diff-ins">${escapeHTML(currBuf)}</ins>`;
        }
        currBuf = '';
    };

    for (const token of diff) {
        if (token.type !== currType) {
            flush();
            currType = token.type;
        }
        currBuf += token.val;
    }
    flush();
    return html;
}
window.computeTextDiff = computeTextDiff;

const _editHistoryDataMap = new Map();
window.clearEditHistoryData = function() {
    _editHistoryDataMap.clear();
};

function buildEditHistoryDiffHtml(data) {
    const versions = (data.edit_history || []).map(h => ({ ts: h.ts, text: h.old }));
    versions.push({ ts: data.edited_date || data.date, text: data.text || '' });

    let diffsHtml = '';
    for (let idx = 0; idx < versions.length - 1; idx++) {
        const vFrom = versions[idx];
        const vTo = versions[idx + 1];
        const diffBody = computeTextDiff(vFrom.text, vTo.text);
        let dateStrShort = '';
        let fullDateStr = '';
        if (vTo.ts) {
            const d = new Date(vTo.ts);
            const day = String(d.getDate()).padStart(2, '0');
            const month = String(d.getMonth() + 1).padStart(2, '0');
            const year = d.getFullYear();
            const hours = String(d.getHours()).padStart(2, '0');
            const mins = String(d.getMinutes()).padStart(2, '0');
            const secs = String(d.getSeconds()).padStart(2, '0');
            dateStrShort = `${hours}:${mins}`;
            fullDateStr = `${day}.${month}.${year}, ${hours}:${mins}:${secs}`;
        }
        diffsHtml += `
            <div class="edit-history-item">
                <div class="edit-history-meta" title="${fullDateStr}">#${idx + 1} → #${idx + 2} (${dateStrShort})</div>
                <div class="edit-history-diff">${diffBody}</div>
            </div>
        `;
    }
    return diffsHtml;
}

function renderEditHistoryPanel(msg) {
    if (!msg.edit_history || !Array.isArray(msg.edit_history) || msg.edit_history.length === 0) {
        return '';
    }

    const msgId = msg.message_id !== undefined ? msg.message_id : msg.id;
    _editHistoryDataMap.set(String(msgId), {
        edit_history: msg.edit_history,
        edited_date: msg.edited_date,
        date: msg.date,
        text: msg.text
    });

    return `
        <div class="edit-history-content" data-msg-id="${escapeHTML(String(msgId))}" style="display: none;"></div>
    `;
}
window.renderEditHistoryPanel = renderEditHistoryPanel;

function toggleEditHistory(triggerEl) {
    const msgEl = triggerEl.closest('.message');
    if (!msgEl) return;
    const panel = msgEl.querySelector('.edit-history-content');
    if (!panel) return;

    if (!panel.dataset.rendered) {
        const msgId = panel.dataset.msgId;
        const data = _editHistoryDataMap.get(String(msgId));
        if (data) {
            panel.innerHTML = buildEditHistoryDiffHtml(data);
            panel.dataset.rendered = 'true';
        }
    }
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}
window.toggleEditHistory = toggleEditHistory;

function createSponsoredMessageElement(sm) {
    const div = document.createElement('div');
    div.className = 'message sponsored-message in';
    div.style.background = 'var(--message-in-bg, rgba(255,255,255,0.06))';
    div.style.border = '1px solid rgba(36, 129, 204, 0.4)';
    div.style.borderRadius = '12px';
    div.style.padding = '10px 14px';
    div.style.margin = '8px 0';

    const title = sm.title ? `<div style="font-weight:600; font-size:13px; margin-bottom:4px; color:var(--link-color);">${escapeHTML(sm.title)}</div>` : '';
    const text = sm.text ? `<div style="font-size:13px; line-height:1.4; margin-bottom:8px;">${escapeHTML(sm.text)}</div>` : '';

    let btnHtml = '';
    if (sm.url) {
        const btnText = escapeHTML(sm.button_text || 'View');
        btnHtml = `<a href="${escapeHTML((window.sanitizeUrl || String)(sm.url))}" target="_blank" rel="noopener noreferrer" class="inline-button" style="display:inline-block; margin-top:4px; background:var(--link-color); color:white; padding:4px 12px; border-radius:6px; text-decoration:none; font-size:12px;">${btnText} ↗</a>`;
    }

    let extraInfo = '';
    if (sm.sponsor_info || sm.additional_info) {
        extraInfo = `<div style="font-size:10px; opacity:0.6; margin-top:6px;">${escapeHTML(sm.sponsor_info || sm.additional_info || '')}</div>`;
    }

    div.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span style="font-size:11px; font-weight:600; color:var(--link-color); background:rgba(36,129,204,0.15); padding:2px 6px; border-radius:4px;">📢 Sponsored</span>
            <span style="font-size:10px; opacity:0.5;">Telegram Ad</span>
        </div>
        ${title}
        ${text}
        ${btnHtml}
        ${extraInfo}
    `;
    return div;
}
window.createSponsoredMessageElement = createSponsoredMessageElement;

window.updateLiveMessageReactions = function(liveData) {
    if (!liveData || !liveData.message_id) return;
    const msgEl = document.querySelector(`.message[data-id="${liveData.message_id}"], .message[data-msg-id="${liveData.message_id}"]`);
    if (!msgEl) return;

    const uMap = (typeof allUsers !== 'undefined') ? allUsers : {};
    const ceMap = (window.allCustomEmoji || {});
    const rxHtml = createReactionsHtml(liveData.reactions, uMap, ceMap);

    let container = msgEl.querySelector('.reactions-container');
    if (rxHtml && rxHtml.trim()) {
        msgEl.classList.add('has-reactions');
        if (container) {
            container.outerHTML = rxHtml;
        } else {
            let footer = msgEl.querySelector('.message-footer');
            if (!footer) {
                footer = document.createElement('div');
                footer.className = 'message-footer';
                const wrapper = msgEl.querySelector('.message-body-wrapper') || msgEl;
                wrapper.appendChild(footer);
            }
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = rxHtml;
            const newEl = tempDiv.firstElementChild;
            const metaInFooter = footer.querySelector('.message-meta');
            if (metaInFooter) {
                footer.insertBefore(newEl, metaInFooter);
            } else {
                footer.appendChild(newEl);
            }
        }
    } else {
        msgEl.classList.remove('has-reactions');
        if (container) container.remove();
    }
};

window.handleMediaRatio = function (mediaEl) {
    if (!mediaEl) return;
    const w = mediaEl.naturalWidth || mediaEl.videoWidth || 0;
    const h = mediaEl.naturalHeight || mediaEl.videoHeight || 0;
    if (!w || !h) return;
    const ratio = w / h;
    const container = mediaEl.closest('.media-thumb-container, .video-container');
    if (!container) return;

    // Check untypical ratio: tall (ratio < 0.45, e.g. 20:1 height) or wide (ratio > 2.4)
    if (ratio < 0.45 || ratio > 2.4) {
        container.classList.add('untypical-ratio');
        if (ratio < 0.45) {
            container.classList.add('ratio-tall');
        } else {
            container.classList.add('ratio-wide');
        }
        if (!container.querySelector('.media-blur-backdrop')) {
            const backdrop = document.createElement('div');
            backdrop.className = 'media-blur-backdrop';
            const src = mediaEl.src || mediaEl.poster || '';
            if (src) {
                backdrop.style.backgroundImage = `url("${src}")`;
            }
            container.prepend(backdrop);
        }
    }
};

window.createLowerCutoffElement = function (syncBounds) {
    const div = document.createElement('div');
    div.className = 'chat-cutoff-divider lower-cutoff';
    div.setAttribute('data-id', 'cutoff-lower');

    const oldestDate = (syncBounds && syncBounds.oldest_cached_date) 
        ? new Date(syncBounds.oldest_cached_date).toLocaleDateString((typeof I18N !== 'undefined') ? I18N.locale() : 'en-US') 
        : '';
    const minId = (syncBounds && (syncBounds.min_cached_id || syncBounds.min_downloaded_id)) || '1';
    const dateText = oldestDate ? ` (around ${oldestDate})` : '';

    const titleText = (typeof I18N !== 'undefined') ? I18N.t('cutoff.lower_title') : 'Archive Incomplete / Halted Here';
    let descText = (typeof I18N !== 'undefined') 
        ? I18N.t('cutoff.lower_desc', minId, dateText)
        : `History older than Message ID #${minId}${dateText} was not downloaded. Older messages exist on Telegram servers.`;
    const safeEscape = (typeof escapeHTML === 'function') ? escapeHTML : (s) => String(s);
    const escapedDesc = safeEscape(descText).replace('#' + minId, `<b>#${minId}</b>`);
    const btnText = (typeof I18N !== 'undefined') ? I18N.t('cutoff.lower_btn') : 'Continue Download (Older History First)';

    div.innerHTML = `
        <div class="cutoff-card">
            <div class="cutoff-card-header">
                <span class="cutoff-icon">⚠️</span>
                <span class="cutoff-title">${safeEscape(titleText)}</span>
            </div>
            <div class="cutoff-card-body">
                <p class="cutoff-desc">
                    ${escapedDesc}
                </p>
                <button type="button" class="cutoff-action-btn" onclick="if(window.openResumeModal) window.openResumeModal(window.currentChatId, 'older_first');">
                    <span>⏳</span> <span>${safeEscape(btnText)}</span>
                </button>
            </div>
        </div>
    `;
    return div;
};

// --- Speech-to-Text / Voice & Video Note Transcription Engine ---
window._whisperStatusCache = null;
window._pendingWhisperTranscription = null;

window.getWhisperStatus = async function(forceRefresh = false) {
    if (!forceRefresh && window._whisperStatusCache) {
        return window._whisperStatusCache;
    }
    try {
        const res = await fetch('/api/transcribe/status');
        const data = await res.json();
        window._whisperStatusCache = data;
        return data;
    } catch (e) {
        console.error('Failed to get whisper status:', e);
        return { available: false, has_cuda: false, config: {} };
    }
};

window.openWhisperModal = async function(pendingAction = null) {
    if (pendingAction) {
        window._pendingWhisperTranscription = pendingAction;
    }
    const modal = document.getElementById('whisper-setup-modal');
    if (!modal) return;

    modal.style.display = 'flex';
    const status = await window.getWhisperStatus(true);

    const warnBanner = document.getElementById('whisper-not-installed-banner');
    if (warnBanner) {
        warnBanner.style.display = status.available ? 'none' : 'block';
    }

    const detectedEl = document.getElementById('whisper-device-detected');
    if (detectedEl) {
        if (!status.available) {
            detectedEl.innerText = 'faster-whisper is not installed on system';
            detectedEl.style.color = '#ff8a80';
        } else if (status.has_cuda) {
            detectedEl.innerText = `CUDA GPU Detected: ${status.device_name || 'NVIDIA GPU'} (Acceleration supported)`;
            detectedEl.style.color = '#48c78e';
        } else {
            detectedEl.innerText = 'CPU Mode: int8 quantization (Fast, low memory, no GPU required)';
            detectedEl.style.color = 'var(--text-color-light)';
        }
    }

    const cfg = (status && status.config) || {};
    const modelSelect = document.getElementById('whisper-model-select');
    if (modelSelect && cfg.model) {
        modelSelect.value = cfg.model;
    }
    const deviceSelect = document.getElementById('whisper-device-select');
    if (deviceSelect && cfg.device) {
        deviceSelect.value = cfg.device;
    }
    const dlRoot = document.getElementById('whisper-download-root');
    if (dlRoot && typeof cfg.download_root !== 'undefined') {
        dlRoot.value = cfg.download_root;
    }
};

window.closeWhisperModal = function() {
    const modal = document.getElementById('whisper-setup-modal');
    if (modal) modal.style.display = 'none';
};

window.saveWhisperSettingsAndProceed = async function() {
    const modelSelect = document.getElementById('whisper-model-select');
    const deviceSelect = document.getElementById('whisper-device-select');
    const dlRoot = document.getElementById('whisper-download-root');
    const saveBtn = document.getElementById('whisper-save-btn');

    const payload = {
        model: modelSelect ? modelSelect.value : 'base',
        device: deviceSelect ? deviceSelect.value : 'auto',
        download_root: dlRoot ? dlRoot.value.trim() : ''
    };

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = 'Saving...';
    }

    try {
        const csrf = typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '';
        const res = await fetch('/api/transcribe/settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-TeleBackup-Auth-Token': csrf
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'ok') {
            if (window._whisperStatusCache) {
                window._whisperStatusCache.config = data.config;
                window._whisperStatusCache.config.configured = true;
            }
            window.closeWhisperModal();
            const pending = window._pendingWhisperTranscription;
            window._pendingWhisperTranscription = null;
            if (pending && pending.chatId && pending.msgId && pending.btnEl) {
                window.requestTranscription(pending.chatId, pending.msgId, pending.btnEl);
            }
        } else {
            alert('Could not save settings: ' + (data.message || 'Unknown error'));
        }
    } catch (e) {
        console.error('Failed to save whisper settings:', e);
        alert('Network error while saving settings');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<span>💾</span> Save & Transcribe';
        }
    }
};

window.copyTranscriptionText = function(btnEl) {
    if (!btnEl) return;
    const block = btnEl.closest('.voice-transcription-block');
    if (!block) return;
    const textEl = block.querySelector('.transcription-text');
    if (!textEl) return;
    const text = textEl.innerText || textEl.textContent || '';
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            const orig = btnEl.innerText;
            btnEl.innerText = '✓';
            setTimeout(() => { btnEl.innerText = orig; }, 1500);
        });
    } else {
        alert('Copied: ' + text);
    }
};

window.handleTranscribeToggle = async function(chatId, msgId, btnEl, event) {
    if (event) event.stopPropagation();
    if (!btnEl) return;

    const transcriptEl = document.getElementById(`transcript-${chatId}-${msgId}`);
    const textEl = transcriptEl ? transcriptEl.querySelector('.transcription-text') : null;
    const hasExistingText = textEl && (textEl.textContent || textEl.innerText || '').trim().length > 0;

    if (hasExistingText) {
        if (transcriptEl.classList.contains('collapsed') || transcriptEl.classList.contains('hidden')) {
            transcriptEl.classList.remove('collapsed', 'hidden');
        } else {
            transcriptEl.classList.add('collapsed');
        }
        return;
    }

    // Not yet transcribed — check status and start or show modal
    const status = await window.getWhisperStatus();
    if (!status.available || !status.config || !status.config.configured) {
        window.openWhisperModal({ chatId, msgId, btnEl });
        return;
    }

    window.requestTranscription(chatId, msgId, btnEl);
};

window.requestTranscription = async function(chatId, msgId, btnEl) {
    if (!btnEl) return;
    const transcriptEl = document.getElementById(`transcript-${chatId}-${msgId}`);

    btnEl.disabled = true;
    btnEl.classList.add('transcribing');
    btnEl.innerText = '⏳';
    btnEl.title = 'Transcribing audio...';

    try {
        const csrf = typeof window.getCsrfToken === 'function' ? window.getCsrfToken() : '';
        const res = await fetch(`/api/messages/${chatId}/${msgId}/transcribe`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-TeleBackup-Auth-Token': csrf
            },
            body: JSON.stringify({ auth_csrf_token: csrf })
        });
        const data = await res.json();
        if (data.status === 'ok' && data.text) {
            const safeText = (typeof escapeHTML === 'function') ? escapeHTML(data.text) : data.text;
            if (transcriptEl) {
                const textContainer = transcriptEl.querySelector('.transcription-text');
                if (textContainer) {
                    textContainer.innerHTML = safeText;
                } else {
                    transcriptEl.innerHTML = `
                        <div class="transcription-header">
                            <span class="transcription-icon" title="Transcription">📝</span>
                            <button type="button" class="transcription-copy-btn" onclick="event.stopPropagation(); window.copyTranscriptionText(this)" title="Copy text">📋</button>
                        </div>
                        <div class="transcription-text">${safeText}</div>`;
                }
                transcriptEl.classList.remove('hidden', 'collapsed');
            }
            btnEl.classList.remove('transcribing');
            btnEl.classList.add('has-text');
            btnEl.disabled = false;
            btnEl.innerText = '→A';
            btnEl.title = 'Toggle transcription (→A)';
        } else {
            btnEl.classList.remove('transcribing');
            btnEl.disabled = false;
            btnEl.innerText = '→A';
            btnEl.title = 'Transcribe failed. Click to retry';
            if (res.status === 503) {
                window.openWhisperModal({ chatId, msgId, btnEl });
            } else {
                alert('Transcription: ' + (data.message || 'No text detected'));
            }
        }
    } catch (e) {
        btnEl.classList.remove('transcribing');
        btnEl.disabled = false;
        btnEl.innerText = '→A';
        btnEl.title = 'Transcribe failed. Click to retry';
        console.error('Transcription error:', e);
    }
};


// --- VOICE WAVEFORM ENGINE & HELPERS ---
function formatVoiceDuration(sec) {
    if (!sec || isNaN(sec) || sec <= 0 || !isFinite(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
}
window.formatVoiceDuration = formatVoiceDuration;

function drawVoiceWaveform(canvas, progressRatio) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = canvas.width || 160;
    const h = canvas.height || 24;
    ctx.clearRect(0, 0, w, h);

    let waveform = [];
    const rawData = canvas.dataset.waveform;
    if (rawData) {
        try {
            waveform = JSON.parse(decodeURIComponent(rawData));
        } catch (e) {
            try { waveform = JSON.parse(rawData); } catch (_) {}
        }
    }

    if (!Array.isArray(waveform) || waveform.length === 0) {
        const count = 36;
        waveform = [];
        for (let i = 0; i < count; i++) {
            waveform.push(Math.round(6 + Math.sin(i * 0.45) * 8 + (i % 4) * 3));
        }
    }

    const numBars = Math.min(waveform.length, 64);
    const barWidth = 2.5;
    const gap = 1.5;
    const totalBarsWidth = numBars * (barWidth + gap);
    const scale = totalBarsWidth > w ? (w / totalBarsWidth) : 1;
    const effBarWidth = Math.max(1.5, barWidth * scale);
    const effGap = Math.max(1, gap * scale);
    const step = effBarWidth + effGap;

    const isOut = canvas.closest('.message') ? canvas.closest('.message').classList.contains('out') : false;
    const computedLink = (typeof getComputedStyle === 'function' ? getComputedStyle(canvas).getPropertyValue('--link-color').trim() : '') || '#3390ec';
    const activeColor = isOut ? '#ffffff' : computedLink;
    const inactiveColor = isOut ? 'rgba(255, 255, 255, 0.45)' : 'rgba(127, 127, 127, 0.35)';

    for (let i = 0; i < numBars; i++) {
        const val = waveform[i] || 0;
        const normVal = Math.max(2, Math.min(31, val));
        const barHeight = Math.max(4, (normVal / 31) * (h - 4));
        const x = i * step;
        const y = (h - barHeight) / 2;

        const isPlayed = ((i + 0.5) / numBars) <= (progressRatio || 0);
        ctx.fillStyle = isPlayed ? activeColor : inactiveColor;

        ctx.beginPath();
        if (ctx.roundRect) {
            ctx.roundRect(x, y, effBarWidth, barHeight, effBarWidth / 2);
        } else {
            ctx.rect(x, y, effBarWidth, barHeight);
        }
        ctx.fill();
    }
}
window.drawVoiceWaveform = drawVoiceWaveform;

window.handleWaveformClick = function(event, canvas) {
    event.stopPropagation();
    const rect = canvas.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, clickX / rect.width));
    const container = canvas.closest('.voice-message-player');
    if (!container) return;
    const audio = container.querySelector('.voice-audio-hidden');
    if (!audio) return;
    const durSpan = container.querySelector('.voice-duration');
    const durAttr = parseFloat(durSpan?.getAttribute('data-total-duration'));
    const total = (audio.duration && isFinite(audio.duration) && audio.duration > 0)
        ? audio.duration
        : (durAttr > 0 ? durAttr : 0);

    if (total > 0) {
        audio.currentTime = ratio * total;
    }
    drawVoiceWaveform(canvas, ratio);
    if (durSpan && total > 0) {
        durSpan.textContent = formatVoiceDuration(audio.currentTime);
    }
};
