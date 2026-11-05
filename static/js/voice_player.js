// static/js/voice_player.js — Voice Message Waveform Player & Inline Discussion Comments Thread

    // Global helper to close inline comments thread and restore comments bar

    window.closeInlineComments = function(el) {

        const thread = el ? el.closest('.inline-comments-thread') : null;

        if (!thread) return;

        thread.style.display = 'none';

        const msgBubble = thread.closest('.message') || thread.closest('.album-container') || thread.parentElement;

        if (msgBubble) {

            const commentsBar = msgBubble.querySelector('.comments-bar');

            if (commentsBar) {

                commentsBar.classList.remove('active');

                commentsBar.style.display = '';

            }

        }

    };



    // Global helper for inline comments toggle

    window.triggerJumpToComments = async (el) => {

        const msgId = el.dataset.msgId;

        const linkedId = el.dataset.linkedId;

        if (!linkedId) {

            showNotification('Linked discussion chat is not available.', 'error');

            return;

        }



        const msgBubble = el.closest('.message') || el.closest('.album-container') || el.parentElement;

        if (!msgBubble) return;



        let threadContainer = msgBubble.querySelector('.inline-comments-thread');

        if (threadContainer) {

            if (threadContainer.style.display === 'none') {

                threadContainer.style.display = 'block';

                el.classList.add('active');

                el.style.display = 'none';

            } else {

                threadContainer.style.display = 'none';

                el.classList.remove('active');

                el.style.display = '';

            }

            return;

        }



        threadContainer = document.createElement('div');

        threadContainer.className = 'inline-comments-thread';

        threadContainer.innerHTML = '<div class="inline-comments-loading">⌛ Loading comments...</div>';

        msgBubble.appendChild(threadContainer);

        el.classList.add('active');

        el.style.display = 'none';



        try {

            const rootRes = await fetch(`/api/find_comment_root?discussion_chat_id=${linkedId}&channel_msg_id=${msgId}`);

            if (rootRes.ok) {

                const rootData = await rootRes.json();

                const discMsgId = rootData.discussion_msg_id || rootData.message_id;

                if (discMsgId) {

                    const commRes = await fetch(`/api/comments?discussion_chat_id=${linkedId}&root_msg_id=${discMsgId}`);

                    if (commRes.ok) {

                        const commData = await commRes.json();

                        if (commData.users && typeof allUsers === 'object') {

                            Object.assign(allUsers, commData.users);

                        }

                        if (commData.custom_emoji && typeof allCustomEmoji === 'object') {

                            Object.assign(allCustomEmoji, commData.custom_emoji);

                            if (window.EmojiEngine) window.EmojiEngine.registerMap(commData.custom_emoji);

                        }

                        renderInlineCommentsList(threadContainer, commData.messages || [], linkedId, discMsgId);

                        return;

                    }

                }

            }

        } catch (e) {

            console.error('Error loading inline comments:', e);

        }



        threadContainer.innerHTML = `

            <div class="inline-comments-header" onclick="event.stopPropagation(); closeInlineComments(this);" title="Close comments">

                <span>💬 0 comments</span>

                <span class="inline-comments-close" onclick="event.stopPropagation(); closeInlineComments(this);">✕</span>

            </div>

            <div class="inline-comments-empty">No comments found in local backup.</div>

        `;

    };



    function renderInlineCommentsList(container, messages, linkedId, rootMsgId = null) {

        if (!messages || messages.length === 0) {

            container.innerHTML = `

                <div class="inline-comments-header" onclick="event.stopPropagation(); closeInlineComments(this);" title="Close comments">

                    <span>💬 0 ${typeof getCommentWord === 'function' ? getCommentWord(0) : 'comments'}</span>

                    <span class="inline-comments-close" onclick="event.stopPropagation(); closeInlineComments(this);">✕</span>

                </div>

                <div class="inline-comments-empty">No comments found in local backup.</div>

            `;

            return;

        }



        let html = `

            <div class="inline-comments-header" onclick="event.stopPropagation(); closeInlineComments(this);" title="Close comments">

                <span>💬 ${messages.length} ${typeof getCommentWord === 'function' ? getCommentWord(messages.length) : 'comments'}</span>

                <span class="inline-comments-close" onclick="event.stopPropagation(); closeInlineComments(this);">✕</span>

            </div>

            <div class="inline-comments-list">

        `;



        messages.forEach(msg => {

            const sender = (allUsers && allUsers[String(msg.sender_id)]) || {};

            const senderName = sender.first_name ? `${sender.first_name} ${sender.last_name || ''}`.trim() : (msg.post_author_name || `User ${msg.sender_id || '?'}`);

            const vParam = (sender.avatar_mtime) ? `?v=${sender.avatar_mtime}` : '';

            const avatarUrl = (sender.avatar_path && sender.avatar_path !== 'None')

                ? encodeURI('/data/' + sender.avatar_path.replace(/\\/g, '/')).replace(/'/g, "%27") + vParam

                : '';

            const avatarHtml = avatarUrl

                ? `<img src="${avatarUrl}" class="inline-comment-avatar" loading="lazy" onerror="this.style.display='none'">`

                : `<div class="inline-comment-avatar-placeholder">${escapeHTML((senderName[0] || '?').toUpperCase())}</div>`;



            const timeStr = msg.date ? new Date(msg.date).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '';

            const fwdHeaderHtml = (typeof buildForwardHeader === 'function') ? buildForwardHeader(msg) : '';

            const replyHeaderHtml = (typeof buildReplyHeader === 'function') ? buildReplyHeader(msg, false, rootMsgId) : '';

            const bodyContent = (typeof renderMessageContent === 'function')

                ? renderMessageContent(msg, true)

                : (msg.text ? (typeof formatText === 'function' ? formatText(msg.text, msg.entities) : escapeHTML(msg.text)) : (msg.media_type ? `<em>[${msg.media_type}]</em>` : ''));



            html += `

                <div class="inline-comment-item">

                    ${avatarHtml}

                    <div class="inline-comment-body">

                        <div class="inline-comment-meta">

                            <span class="inline-comment-author">${escapeHTML(senderName)}</span>

                            <span class="inline-comment-time">${timeStr}</span>

                        </div>

                        ${fwdHeaderHtml}

                        ${replyHeaderHtml}

                        <div class="inline-comment-text">${bodyContent}</div>

                    </div>

                </div>

            `;

        });



        html += `</div>`;

        container.innerHTML = html;

        if (typeof renderCustomEmojisInContainer === 'function') {

            renderCustomEmojisInContainer(container, allCustomEmoji);

        }

    }


window.renderInlineCommentsList = renderInlineCommentsList;

    // Voice message toggle play helper with waveform & sub-bar support

    const _voicePlaySvg = '<svg class="icon-play" viewBox="0 0 24 24" width="14" height="14" style="margin-left:1.5px; fill:currentColor;"><path d="M8 5v14l11-7z"/></svg>';

    const _voicePauseSvg = '<svg class="icon-pause" viewBox="0 0 24 24" width="14" height="14" style="fill:currentColor;"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';



    window.toggleVoicePlay = (btn) => {

        const container = btn.closest('.voice-message-player');

        if (!container) return;

        const audio = container.querySelector('.voice-audio-hidden');

        const canvas = container.querySelector('.voice-waveform-canvas');

        const durSpan = container.querySelector('.voice-duration');

        if (!audio) return;



        if (audio.paused) {

            // Pause all other voice players first

            document.querySelectorAll('.voice-audio-hidden').forEach(a => {

                if (a !== audio && !a.paused) {

                    a.pause();

                    const parent = a.closest('.voice-message-player');

                    if (parent) {

                        const b = parent.querySelector('.voice-play-btn');

                        if (b) b.innerHTML = _voicePlaySvg;

                        const c = parent.querySelector('.voice-waveform-canvas');

                        if (c && window.drawVoiceWaveform) window.drawVoiceWaveform(c, 0);

                        const d = parent.querySelector('.voice-duration');

                        const totalD = (a.duration && isFinite(a.duration) && a.duration > 0)

                            ? a.duration

                            : (parseFloat(d?.getAttribute('data-total-duration')) || 0);

                        if (d && totalD > 0 && window.formatVoiceDuration) d.textContent = window.formatVoiceDuration(totalD);

                    }

                }

            });



            audio.play().then(() => {

                btn.innerHTML = _voicePauseSvg;

            }).catch(() => {

                btn.innerHTML = _voicePlaySvg;

            });



            audio.ontimeupdate = () => {

                const total = (audio.duration && !isNaN(audio.duration) && isFinite(audio.duration) && audio.duration > 0)

                    ? audio.duration

                    : (parseFloat(durSpan?.getAttribute('data-total-duration')) || 0);

                if (total > 0) {

                    const ratio = Math.min(1, Math.max(0, audio.currentTime / total));

                    if (canvas && window.drawVoiceWaveform) window.drawVoiceWaveform(canvas, ratio);

                }

                if (durSpan && window.formatVoiceDuration) {

                    durSpan.textContent = window.formatVoiceDuration(audio.currentTime);

                }

            };



            audio.onended = () => {

                btn.innerHTML = _voicePlaySvg;

                if (canvas && window.drawVoiceWaveform) window.drawVoiceWaveform(canvas, 0);

                const total = (audio.duration && isFinite(audio.duration) && audio.duration > 0)

                    ? audio.duration

                    : (parseFloat(durSpan?.getAttribute('data-total-duration')) || 0);

                if (durSpan && total > 0 && window.formatVoiceDuration) durSpan.textContent = window.formatVoiceDuration(total);

            };

        } else {

            audio.pause();

            btn.innerHTML = _voicePlaySvg;

            const total = (audio.duration && isFinite(audio.duration) && audio.duration > 0)

                ? audio.duration

                : (parseFloat(durSpan?.getAttribute('data-total-duration')) || 0);

            if (durSpan && total > 0 && window.formatVoiceDuration) durSpan.textContent = window.formatVoiceDuration(total);

        }

    };

