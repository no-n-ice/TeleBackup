/**
 * emoji_engine.js — Custom Emoji and Emoji Status rendering engine.
 */
(function (window) {
    'use strict';

    const EmojiEngine = {
        _cache: {},

        /**
         * Register or update emoji metadata map.
         * @param {Object} map docId -> {file_path, mime_type, alt_emoji}
         */
        registerMap(map) {
            if (!map || typeof map !== 'object') return;
            Object.assign(this._cache, map);
        },

        /**
         * Retrieve cached info for a custom emoji document ID.
         * @param {string|number} docId
         * @returns {Object|null}
         */
        resolve(docId) {
            if (!docId) return null;
            return this._cache[String(docId)] || null;
        },

        /**
         * Returns HTML markup for a single custom emoji.
         * @param {string|number} docId
         * @param {string} [alt='⭐']
         * @param {string} [extraClass='']
         * @returns {string} HTML string
         */
        renderEmojiHtml(docId, alt = '⭐', extraClass = '') {
            if (!docId) return '';
            const idStr = String(docId);
            const info = this.resolve(idStr);
            const altEscaped = typeof escapeHTML === 'function' ? escapeHTML(alt) : alt;
            const cls = extraClass ? ` ${extraClass}` : '';

            if (info && info.file_path) {
                const safePath = '/data/' + info.file_path.replace(/\\/g, '/');
                const mime = info.mime_type || '';
                const altText = typeof escapeHTML === 'function' ? escapeHTML(info.alt_emoji || alt) : (info.alt_emoji || alt);

                if (mime.includes('webm') || safePath.endsWith('.webm')) {
                    return `<video class="custom-emoji-media${cls}" src="${safePath}" autoplay loop muted playsinline alt="${altText}" title="${altText}"></video>`;
                } else if (mime.includes('tgsticker') || safePath.endsWith('.tgs')) {
                    const uid = `ce-${idStr}-${Math.random().toString(36).substr(2, 6)}`;
                    setTimeout(() => {
                        if (typeof loadSticker === 'function') {
                            loadSticker(uid, safePath);
                        }
                    }, 0);
                    return `<span id="${uid}" class="custom-emoji-tgs${cls}" title="${altText}"></span>`;
                } else {
                    return `<img class="custom-emoji-media${cls}" src="${safePath}" alt="${altText}" title="${altText}" loading="lazy">`;
                }
            }

            // Fallback if local file not downloaded yet
            return `<span class="custom-emoji-fallback${cls}" data-id="${idStr}" title="Custom Emoji #${idStr}">${altEscaped}</span>`;
        },

        /**
         * Returns HTML markup for an emoji status badge (e.g. next to usernames or in headers).
         * @param {string|number} docId
         * @param {string} [alt='✨']
         * @returns {string}
         */
        renderStatusBadge(docId, alt = '✨') {
            if (!docId) return '';
            const emojiHtml = this.renderEmojiHtml(docId, alt, 'status-emoji-icon');
            return `<span class="emoji-status-badge" data-doc-id="${docId}" title="Telegram Emoji Status">${emojiHtml}</span>`;
        },

        /**
         * Scans a DOM container and hydrates any unfilled custom emoji spans.
         * @param {HTMLElement} container
         */
        hydrateContainer(container) {
            if (!container) return;
            const fallbackSpans = container.querySelectorAll('.custom-emoji[data-id], .custom-emoji-fallback[data-id]');
            fallbackSpans.forEach(span => {
                const docId = span.getAttribute('data-id');
                const info = this.resolve(docId);
                if (info && info.file_path && !span.querySelector('img, video, .custom-emoji-tgs')) {
                    const html = this.renderEmojiHtml(docId, span.textContent || '❓');
                    span.outerHTML = html;
                }
            });
        }
    };

    window.EmojiEngine = EmojiEngine;
})(window);
