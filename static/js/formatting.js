function formatText(text, entities) {
        if (!text) return ''; if (!entities || !entities.length) return escapeHTML(text).replace(/\r?\n/g, '<br>');
        const tags = [];
        entities.forEach(e => {
            let startTag = '', endTag = '';
            let customContent = null;
            switch (e._) {
                case 'MessageEntityBold': startTag = '<strong>'; endTag = '</strong>'; break;
                case 'MessageEntityItalic': startTag = '<em>'; endTag = '</em>'; break;
                case 'MessageEntityCode': startTag = '<code>'; endTag = '</code>'; break;
                case 'MessageEntityPre': {
                    const safeLang = escapeHTML(String(e.language || '').replace(/[^A-Za-z0-9_+#.-]/g, ''));
                    startTag = `<pre class="lang-${safeLang}">`;
                    endTag = '</pre>';
                    break;
                }
                case 'MessageEntityStrike': startTag = '<s>'; endTag = '</s>'; break;
                case 'MessageEntityUnderline': startTag = '<u>'; endTag = '</u>'; break;
                case 'MessageEntitySpoiler': startTag = '<span class="spoiler">'; endTag = '</span>'; break;
                case 'MessageEntityBlockquote':
                    if (e.collapsed) {
                        startTag = `<details class="collapsed-blockquote"><summary>${I18N.t('msg.show_quote')}</summary><blockquote>`;
                        endTag = '</blockquote></details>';
                    } else {
                        startTag = '<blockquote>';
                        endTag = '</blockquote>';
                    }
                    break;
                case 'MessageEntityTextUrl': startTag = `<a href="${escapeHTML(sanitizeUrl(e.url))}" target="_blank" rel="noopener noreferrer">`; endTag = '</a>'; break;
                case 'MessageEntityUrl': {
                    const url = text.substring(e.offset, e.offset + e.length);
                    let displayUrl = url;
                    if (displayUrl.length > 45) {
                        displayUrl = displayUrl.substring(0, 42) + '...';
                    }
                    startTag = `<a href="${escapeHTML(sanitizeUrl(url))}" title="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">`;
                    endTag = '</a>';
                    if (displayUrl !== url) {
                        customContent = escapeHTML(displayUrl);
                    }
                    break;
                }
                case 'MessageEntityHashtag': startTag = '<span class="mention hashtag">'; endTag = '</span>'; break;
                case 'MessageEntityCashtag': startTag = '<span class="mention cashtag">'; endTag = '</span>'; break;
                case 'MessageEntityBotCommand': startTag = '<span class="mention bot-command">'; endTag = '</span>'; break;
                case 'MessageEntityMention': startTag = '<span class="mention">'; endTag = '</span>'; break;
                case 'MessageEntityMentionName': startTag = `<span class="mention" data-user-id="${escapeHTML(String(e.user_id ?? ''))}">`; endTag = '</span>'; break;
                case 'MessageEntityCustomEmoji': startTag = `<span class="custom-emoji" data-id="${escapeHTML(String(e.document_id ?? ''))}">`; endTag = '</span>'; break;
                case 'MessageEntityBankCard': startTag = '<span class="bank-card">'; endTag = '</span>'; break;
                case 'MessageEntityPhone': startTag = '<span class="phone-link">'; endTag = '</span>'; break;
                case 'MessageEntityEmail': startTag = '<span class="email-link">'; endTag = '</span>'; break;
                default: return;
            }
            tags.push({ pos: e.offset, tag: startTag, type: 'start', skipTo: customContent ? (e.offset + e.length) : null, content: customContent });
            tags.push({ pos: e.offset + e.length, tag: endTag, type: 'end' });
        });
        tags.sort((a, b) => { if (a.pos !== b.pos) return a.pos - b.pos; if (a.type === 'end' && b.type === 'start') return -1; if (a.type === 'start' && b.type === 'end') return 1; return 0; });
        let html = ''; let lastIndex = 0; const preStack = [];
        tags.forEach(tag => {
            if (tag.pos > lastIndex) {
                let segment = text.substring(lastIndex, tag.pos);
                let processedSegment = escapeHTML(segment);
                if (preStack.length === 0) processedSegment = processedSegment.replace(/\r?\n/g, '<br>');
                html += processedSegment;
            }
            if (tag.tag.startsWith('<pre')) preStack.push(true);
            else if (tag.tag.startsWith('</pre')) preStack.pop();

            html += tag.tag;
            if (tag.content) {
                html += tag.content;
            }
            if (tag.skipTo && tag.skipTo > lastIndex) {
                lastIndex = tag.skipTo;
            } else if (tag.pos > lastIndex) {
                lastIndex = tag.pos;
            }
        });
        if (lastIndex < text.length) {
            let segment = text.substring(lastIndex);
            let processedSegment = escapeHTML(segment);
            if (preStack.length === 0) processedSegment = processedSegment.replace(/\r?\n/g, '<br>');
            html += processedSegment;
        }
        return html;
    }

function escapeHTML(str) {
    if (typeof str !== 'string') {
        if (!str) return '';
        if (typeof str === 'object') return '[Object]';
        str = String(str);
    }
    return str.replace(/[&<>"']/g, match => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    }[match]));
}
window.escapeHTML = escapeHTML;

function sanitizeUrl(rawUrl) {
    if (typeof rawUrl !== 'string') return '#';
    const trimmed = rawUrl.trim();
    if (!trimmed) return '#';
    const schemeMatch = trimmed.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):/);
    if (schemeMatch) {
        const scheme = schemeMatch[1].toLowerCase();
        if (scheme === 'http' || scheme === 'https' || scheme === 'mailto' || scheme === 'tg') {
            return trimmed;
        }
        return '#';
    }
    if (trimmed.startsWith('//')) return '#';
    if (/^[a-z0-9.-]+\.[a-z]{2,}([/?#]|$)/i.test(trimmed)) {
        return 'https://' + trimmed;
    }
    return '#';
}
window.sanitizeUrl = sanitizeUrl;

function normalizeId(id) {
        if (!id) return id;
        let n = Number(id);
        if (!isNaN(n) && n > 0 && n < 1000000000000) {
            return -1000000000000 - n;
        }
        return n;
    }

function createReactionsHtml(reactions, users, customEmojiMap = {}) {
        if (!reactions) return '';
        if (typeof reactions === 'string') {
            try { reactions = JSON.parse(reactions); } catch(e) { return ''; }
        }
        if (Array.isArray(reactions)) {
            reactions = {
                results: reactions.map(r => ({
                    reaction: (r.document_id || (r.reaction && r.reaction.document_id))
                        ? { _: "ReactionCustomEmoji", document_id: r.document_id || (r.reaction && r.reaction.document_id) }
                        : { _: "ReactionEmoji", emoticon: r.emoticon || r.emoji || (r.reaction && (r.reaction.emoticon || r.reaction.emoji)) || '❤️‍🔥' },
                    count: r.count || 1
                })),
                recent_reactions: []
            };
        }
        if (!reactions || (!reactions.results && !reactions.recent_reactions) ||
            (reactions.results && reactions.results.length === 0 && reactions.recent_reactions && reactions.recent_reactions.length === 0)) {
            return '';
        }
        let html = '<div class="reactions-container">';
        const reactorsMap = new Map();
        
        function buildEmojiMarkup(rxnObj) {
            if (!rxnObj) return '';
            if (typeof rxnObj === 'string') return `<span class="reaction-emoji">${escapeHTML(rxnObj)}</span>`;
            if (rxnObj.emoticon) return `<span class="reaction-emoji">${escapeHTML(rxnObj.emoticon)}</span>`;
            if (rxnObj.emoji) return `<span class="reaction-emoji">${escapeHTML(rxnObj.emoji)}</span>`;
            const docId = rxnObj.document_id ? String(rxnObj.document_id) : (rxnObj.doc_id ? String(rxnObj.doc_id) : null);
            if (docId) {
                const info = customEmojiMap[docId];
                if (info && info.file_path) {
                    const safePath = escapeHTML('/data/' + String(info.file_path).replace(/\\/g, '/'));
                    const mime = info.mime_type || '';
                    const alt = info.alt_emoji || '❤️‍🔥';
                    if (mime.includes('webm') || safePath.endsWith('.webm')) {
                        return `<video class="reaction-emoji custom-emoji-media" src="${safePath}" autoplay loop muted playsinline alt="${escapeHTML(alt)}"></video>`;
                    } else {
                        return `<img class="reaction-emoji custom-emoji-media" src="${safePath}" alt="${escapeHTML(alt)}" loading="lazy">`;
                    }
                }
            }
            return `<span class="reaction-emoji">❤️‍🔥</span>`;
        }

        if (reactions.hasOwnProperty('recent_reactions') && Array.isArray(reactions.recent_reactions) && reactions.recent_reactions.length > 0) {
            reactions.recent_reactions.forEach(reactor => {
                let emojiMarkup = buildEmojiMarkup(reactor.reaction);
                let peerId = null;
                if (reactor.peer_id) {
                    if (reactor.peer_id.user_id) peerId = reactor.peer_id.user_id;
                    else if (reactor.peer_id.channel_id) peerId = reactor.peer_id.channel_id;
                    else if (typeof reactor.peer_id === 'number') peerId = reactor.peer_id;
                }
                if (emojiMarkup && peerId) {
                    const user = users ? (users[peerId] || users[String(peerId)]) : null;
                    const rawName = user ? (user.first_name ? `${user.first_name} ${user.last_name || ''}`.trim() : (user.name || `ID: ${peerId}`)) : `ID: ${peerId}`;
                    const name = escapeHTML(rawName);
                    if (!reactorsMap.has(emojiMarkup)) reactorsMap.set(emojiMarkup, []);
                    if (!reactorsMap.get(emojiMarkup).includes(name)) {
                        reactorsMap.get(emojiMarkup).push(name);
                    }
                }
            });
        }
        if (reactions.hasOwnProperty('results') && Array.isArray(reactions.results) && reactions.results.length > 0) {
            reactions.results.forEach(item => {
                let emojiMarkup = buildEmojiMarkup(item.reaction);
                if (emojiMarkup) {
                    let tooltipHtml = '';
                    const names = reactorsMap.get(emojiMarkup);
                    if (names && names.length > 0) {
                        tooltipHtml = `<span class="reaction-tooltip"><ul><li>${names.join('</li><li>')}</li></ul></span>`;
                    }
                    const safeCount = Number(item.count) || 0;
                    html += `<div class="reaction-pill">${emojiMarkup}<span class="reaction-count">${safeCount}</span>${tooltipHtml}</div>`;
                }
            });
        }
        else if (reactorsMap.size > 0) {
            reactorsMap.forEach((names, emojiMarkup) => {
                let tooltipHtml = '';
                if (names && names.length > 0) {
                    tooltipHtml = `<span class="reaction-tooltip"><ul><li>${names.join('</li><li>')}</li></ul></span>`;
                }
                html += `<div class="reaction-pill">${emojiMarkup}${tooltipHtml}</div>`;
            });
        }
        html += '</div>';
        return html;
    }

function renderCustomEmojisInContainer(container, customEmojiMap) {
    if (!container) return;
    const map = customEmojiMap || window.allCustomEmoji || (window.EmojiEngine ? window.EmojiEngine._cache : {}) || {};
    const emojiSpans = container.querySelectorAll('.custom-emoji[data-id], .custom-emoji-fallback[data-id]');
    emojiSpans.forEach(span => {
        if (span.querySelector('img, video, .custom-emoji-tgs')) return;
        const docId = span.getAttribute('data-id');
        const info = map[docId] || (window.allCustomEmoji && window.allCustomEmoji[docId]) || (window.EmojiEngine && window.EmojiEngine.resolve(docId));
        if (info && info.file_path) {
            const rawPath = '/data/' + String(info.file_path).replace(/\\/g, '/');
            const attrPath = escapeHTML(rawPath);
            const mime = info.mime_type || '';
            const alt = info.alt_emoji || span.textContent || '❓';
            
            if (mime.includes('webm') || rawPath.endsWith('.webm')) {
                span.innerHTML = `<video class="custom-emoji-media" src="${attrPath}" autoplay loop muted playsinline alt="${escapeHTML(alt)}" title="${escapeHTML(alt)}"></video>`;
            } else if (mime.includes('tgsticker') || rawPath.endsWith('.tgs')) {
                const cleanDocId = String(docId || '').replace(/[^A-Za-z0-9_-]/g, '');
                const uniqueId = `ce-${cleanDocId}-${Math.random().toString(36).substring(2, 7)}`;
                span.innerHTML = `<span id="${uniqueId}" class="custom-emoji-tgs" data-path="${attrPath}" title="${escapeHTML(alt)}"></span>`;
                const tgsEl = span.firstElementChild;
                if (typeof queueStickerLoad === 'function') {
                    queueStickerLoad(tgsEl, rawPath);
                } else if (typeof loadSticker === 'function') {
                    loadSticker(tgsEl, rawPath);
                }
            } else {
                span.innerHTML = `<img class="custom-emoji-media" src="${attrPath}" alt="${escapeHTML(alt)}" title="${escapeHTML(alt)}" loading="lazy">`;
            }
        }
    });
}

function formatServiceMessage(msg, users) {
    const action = msg.service_action;
    const uMap = users || (typeof allUsers !== 'undefined' ? allUsers : {});
    let actorName = `<b>${I18N.t('msg.someone')}</b>`;
    const sId = msg.sender_id;

    if (msg.sender_first_name) {
        let name = msg.sender_first_name;
        if (msg.sender_last_name && msg.sender_last_name !== '[Недоступен]') name += ` ${msg.sender_last_name}`;
        actorName = `<b>${escapeHTML(name)}</b>`;
    } else if (sId && (uMap[sId] || uMap[String(sId)])) {
        const user = uMap[sId] || uMap[String(sId)];
        let name = user.first_name || '';
        if (user.last_name && user.last_name !== '[Недоступен]') name += ` ${user.last_name}`;
        actorName = `<b>${escapeHTML(name.trim() || 'user')}</b>`;
    } else if (sId && (parseInt(sId, 10) < 0 || String(sId).startsWith('-'))) {
        const cData = (window.chatListData || []).find(c => String(c.chat_id) === String(sId));
        const chatName = cData ? cData.chat_name : (msg.chat_name || window.currentChatData?.chat_name);
        if (chatName) {
            actorName = `<b>${escapeHTML(chatName)}</b>`;
        }
    } else if (msg.chat_name || (window.currentChatData && window.currentChatData.chat_name)) {
        const chatName = msg.chat_name || window.currentChatData.chat_name;
        actorName = `<b>${escapeHTML(chatName)}</b>`;
    }

    if (!action) return `[${I18N.t('msg.empty')}]`;
    switch (action._) {
        case 'MessageActionChannelCreate':
            return I18N.t('service.channel_created', escapeHTML(action.title || msg.chat_name || 'Channel'));

        case 'MessageActionChatCreate':
            return I18N.t('service.chat_created', actorName, escapeHTML(action.title || 'Group'));

        case 'MessageActionChatDeletePhoto':
            return I18N.t('service.photo_deleted', actorName);

        case 'MessageActionChatEditPhoto': 
            return I18N.t('service.photo_changed', actorName);

        case 'MessageActionChatEditTitle': 
            return I18N.t('service.title_changed', actorName, escapeHTML(action.title || ''));

        case 'MessageActionPinMessage': {
            const pinMsgId = Number(msg.reply_to_msg_id || action.message_id);
            const link = Number.isFinite(pinMsgId) && pinMsgId > 0
                ? `<a href="javascript:void(0)" onclick="scrollToMessage(${pinMsgId})">${I18N.t('service.go_to')}</a>`
                : '';
            return link ? I18N.t('service.pinned', actorName, link) : (I18N.t('service.pinned_short', actorName) || `📌 ${actorName} pinned a message.`);
        }

        case 'MessageActionChatJoinedByLink': 
            return I18N.t('service.joined_link', actorName);

        case 'MessageActionChatAddUser': {
            const uList = action.users || [];
            if (uList.length === 1 && String(uList[0]) === String(msg.sender_id)) {
                return I18N.t('service.joined_chat', actorName);
            }
            let addedUserName = I18N.t('msg.someone_acc');
            if (uList.length > 0) {
                const names = uList.map(uid => {
                    const u = uMap[uid] || uMap[String(uid)];
                    if (u) {
                        let n = u.first_name || '';
                        if (u.last_name && u.last_name !== '[Недоступен]') n += ` ${u.last_name}`;
                        return `<b>${escapeHTML(n.trim() || 'user')}</b>`;
                    }
                    return `<b>user #${escapeHTML(String(uid))}</b>`;
                });
                addedUserName = names.join(', ');
            }
            return I18N.t('service.added', actorName, addedUserName);
        }

        case 'MessageActionChatDeleteUser': {
            let deletedUserName = I18N.t('msg.someone_acc');
            const delUid = action.user_id;
            if (delUid) {
                const delUser = uMap[delUid] || uMap[String(delUid)];
                if (delUser) {
                    let n = delUser.first_name || '';
                    if (delUser.last_name && delUser.last_name !== '[Недоступен]') n += ` ${delUser.last_name}`;
                    deletedUserName = `<b>${escapeHTML(n.trim() || 'user')}</b>`;
                } else {
                    deletedUserName = `<b>user #${escapeHTML(String(delUid))}</b>`;
                }
            }
            if (String(msg.sender_id) === String(delUid)) {
                return I18N.t('service.left', actorName);
            }
            return I18N.t('service.removed', actorName, deletedUserName);
        }

        case 'MessageActionChatMigrateTo':
            return `
                <div style="font-size: 14px; padding: 10px; text-align: center;">
                    ${I18N.t('service.migrate_end', actorName)}
                </div>`;

        case 'MessageActionChannelMigrateFrom':
            return `
                <div style="font-size: 14px; padding: 10px; text-align: center;">
                    ${I18N.t('service.migrate_start')}
                </div>`;

        case 'MessageActionPhoneCall': {
            const isOut = Boolean(msg.is_outgoing);
            const icon = isOut ? '📞 ↗️' : '📞 ↙️';
            const duration = action.duration ? ` (${Math.floor(action.duration / 60)}:${('0' + (action.duration % 60)).slice(-2)})` : '';
            const reason = (action.reason && (action.reason._ === 'PhoneCallDiscardReasonMissed' || action.reason._ === 'PhoneCallDiscardReasonBusy')) ? ` [${I18N.t('call.missed')}]` : '';
            const label = isOut ? I18N.t('call.outgoing') : I18N.t('call.incoming');
            return `<div class="service-call-card ${isOut ? 'call-out' : 'call-in'}">
                <span class="call-icon">${icon}</span>
                <span class="call-text">${label}${duration}${reason}</span>
            </div>`;
        }

        case 'MessageActionGroupCall': {
            const duration = action.duration ? ` (${Math.floor(action.duration / 60)}:${('0' + (action.duration % 60)).slice(-2)})` : '';
            return `<div class="service-call-card group-call-card">
                <span class="call-icon">🎙️</span>
                <span class="call-text">${I18N.t('call.group_call')}${duration}</span>
            </div>`;
        }

        case 'MessageActionGiftStars': {
            const stars = escapeHTML(String(action.stars || (action.action_data && action.action_data.stars) || ''));
            return `<div class="service-stars-card">
                <span class="stars-icon">⭐</span>
                <span class="stars-text">${I18N.t('service.gift_stars', actorName, stars)}</span>
            </div>`;
        }

        case 'MessageActionStarGift': {
            const gift = action.action_data || action.gift || {};
            const giftTitle = gift.title || 'Star Gift';
            const stars = gift.stars ? ` (${escapeHTML(String(gift.stars))} ⭐)` : '';
            return `<div class="service-stars-card star-gift-card">
                <span class="stars-icon">🎁</span>
                <span class="stars-text">${actorName} sent <b>${escapeHTML(giftTitle)}</b>${stars}</span>
            </div>`;
        }

        case 'MessageActionGiveawayLaunch': {
            const prize = action.stars_prize ? `${escapeHTML(String(action.stars_prize))} ⭐` : 'Telegram Premium';
            return `<div class="service-giveaway-card">🎉 ${I18N.t('service.giveaway_launch', actorName, prize)}</div>`;
        }

        case 'MessageActionGiveawayResults': {
            const winners = escapeHTML(String(action.winners_count || 1));
            return `<div class="service-giveaway-card">🏆 ${I18N.t('service.giveaway_results', winners)}</div>`;
        }

        case 'MessageActionCustomAction':
            return action.message ? `ℹ️ ${escapeHTML(action.message)}` : `[Custom action]`;

        case 'MessageActionBotAllowed':
            return `🤖 ${actorName} allowed app/bot ${escapeHTML(action.domain || '')}`;

        default: 
            if (action && action._) {
                const cleanType = escapeHTML(String(action._).replace('MessageAction', '').replace(/([A-Z])/g, ' $1').trim());
                return I18N.t('service.unknown', cleanType);
            }
            return `[Service message]`;
    }
}

const _stickerDataCache = new Map();
let _stickerObserver = null;
if (typeof IntersectionObserver !== 'undefined') {
    _stickerObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            const anim = entry.target._lottieAnim;
            if (anim) {
                if (entry.isIntersecting) {
                    anim.play();
                } else {
                    anim.pause();
                }
            }
        });
    }, { rootMargin: '150px' });
}

let _stickerLoadObserver = null;
if (typeof IntersectionObserver !== 'undefined') {
    _stickerLoadObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                observer.unobserve(entry.target);
                const el = entry.target;
                const path = el.dataset.path;
                if (el.id && path && !el._lottieAnim) {
                    loadSticker(el.id, path);
                }
            }
        });
    }, { rootMargin: '300px' });
}

function queueStickerLoad(elementOrId, path) {
    const container = (elementOrId && typeof elementOrId === 'object' && elementOrId.nodeType)
        ? elementOrId
        : (typeof elementOrId === 'string' ? document.getElementById(elementOrId) : null);

    const safePath = path || (container ? container.dataset.path : null);
    if (container && safePath && !container.dataset.path) {
        container.dataset.path = safePath;
    }

    if (typeof IntersectionObserver === 'undefined' || !_stickerLoadObserver) {
        setTimeout(() => {
            const target = container || elementOrId;
            if (target) loadSticker(target, safePath);
        }, 0);
        return;
    }

    if (container) {
        _stickerLoadObserver.observe(container);
    } else if (typeof elementOrId === 'string') {
        requestAnimationFrame(() => {
            const el = document.getElementById(elementOrId);
            if (el) {
                if (safePath && !el.dataset.path) el.dataset.path = safePath;
                _stickerLoadObserver.observe(el);
            } else {
                loadSticker(elementOrId, safePath);
            }
        });
    }
}
window.queueStickerLoad = queueStickerLoad;

async function loadSticker(elementOrId, path) {
        const container = (elementOrId && typeof elementOrId === 'object' && elementOrId.nodeType)
            ? elementOrId
            : (typeof elementOrId === 'string' ? document.getElementById(elementOrId) : null);
        if (!container) return;
        if (container._lottieAnim) return;

        try {
            let jsonString = _stickerDataCache.get(path);
            if (!jsonString) {
                const response = await fetch(path);
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                const compressedData = await response.arrayBuffer();
                const decompressedData = pako.inflate(new Uint8Array(compressedData));
                jsonString = new TextDecoder("utf-8").decode(decompressedData);
                if (_stickerDataCache.size > 200) {
                    const firstKey = _stickerDataCache.keys().next().value;
                    _stickerDataCache.delete(firstKey);
                }
                _stickerDataCache.set(path, jsonString);
            }

            const animationData = JSON.parse(jsonString);

            const anim = lottie.loadAnimation({
                container: container,
                renderer: 'canvas',
                loop: true,
                autoplay: true,
                animationData: animationData
            });

            container._lottieAnim = anim;
            if (_stickerObserver) _stickerObserver.observe(container);
        } catch (e) {
            console.error('Lottie sticker load error:', e, path);
            if (container) container.innerHTML = (typeof I18N !== 'undefined') ? I18N.t('msg.sticker_error') : '⚠️ [Sticker Error]';
        }
    }

function highlightAndScrollTo(element) {
        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            const targetTop = element.offsetTop - (chatContainer.clientHeight / 2) + (element.clientHeight / 2);
            chatContainer.scrollTo({ top: targetTop, behavior: 'smooth' });
        } else {
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        element.classList.add('is-highlighted');
        setTimeout(() => {
            element.classList.remove('is-highlighted');
            element.style.transition = 'background-color 0.5s ease';
        }, 2000);
    }

function buildSenderBadges(senderObj) {
        if (!senderObj) return '';
        let b = '';
        if (senderObj.emoji_status_document_id && window.EmojiEngine) {
            b += window.EmojiEngine.renderStatusBadge(senderObj.emoji_status_document_id);
        }
        if (senderObj.is_bot) b += `<span class="user-badge bot-badge" title="${I18N.t('badge.bot')}">🤖</span>`;
        if (senderObj.is_premium) b += `<span class="user-badge premium-badge" title="${I18N.t('badge.premium')}">⭐</span>`;
        if (senderObj.is_restricted) b += `<span class="user-badge restricted-badge" title="${I18N.t('badge.restricted')}">🚫</span>`;
        if (senderObj.verified) b += `<span class="user-badge verified-badge" title="${I18N.t('badge.verified')}">✅</span>`;
        if (senderObj.is_scam) b += '<span class="user-badge scam-badge" title="SCAM">⚠ SCAM</span>';
        if (senderObj.is_fake) b += '<span class="user-badge fake-badge" title="FAKE">⚠ FAKE</span>';
        return b;
    }

function createDateSeparator(dateStr, isPrepended = false) {
        const date = new Date(dateStr);
        const el = document.createElement('div');
        el.className = 'date-separator';
        el.setAttribute('data-date', date.toLocaleDateString(I18N.locale()));
        el.textContent = date.toLocaleDateString(I18N.locale(), { year: 'numeric', month: 'long', day: 'numeric' });
        return el;
    }
