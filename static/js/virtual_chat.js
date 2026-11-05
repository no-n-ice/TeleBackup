// static/js/virtual_chat.js — Virtual DOM Windowing Engine for Smooth Large-Chat Scrolling

    // --- VIRTUAL DOM WINDOWING ENGINE (Rendered Messages +- 15) ---

    const VirtualChat = {

        items: [], // Array of { id, el, height, isDate }

        buffer: 15,

        topSpacer: null,

        bottomSpacer: null,

        rafId: null,

        isSuspended: false,



        init() {

            if (!chatContainer) return;

            if (!this.topSpacer) {

                this.topSpacer = document.createElement('div');

                this.topSpacer.id = 'chat-virtual-top-spacer';

                this.topSpacer.className = 'virtual-spacer';

                this.topSpacer.style.cssText = 'height: 0px; flex-shrink: 0; width: 100%; pointer-events: none; margin: 0; padding: 0;';

            }

            if (!this.bottomSpacer) {

                this.bottomSpacer = document.createElement('div');

                this.bottomSpacer.id = 'chat-virtual-bottom-spacer';

                this.bottomSpacer.className = 'virtual-spacer';

                this.bottomSpacer.style.cssText = 'height: 0px; flex-shrink: 0; width: 100%; pointer-events: none; margin: 0; padding: 0;';

            }

            if (!chatContainer.contains(this.topSpacer)) {

                chatContainer.prepend(this.topSpacer);

            }

            if (!chatContainer.contains(this.bottomSpacer)) {

                chatContainer.appendChild(this.bottomSpacer);

            }

        },



        reset() {

            this.items = [];

            this.isSuspended = false;

            this.init();

            if (this.topSpacer) this.topSpacer.style.height = '0px';

            if (this.bottomSpacer) this.bottomSpacer.style.height = '0px';

        },



        registerElements(elements, isPrepend = false) {

            this.init();

            const existingIds = new Set();

            this.items.forEach(it => { if (it.id) existingIds.add(String(it.id)); });



            const newItems = [];

            elements.forEach(el => {

                if (!el || el === this.topSpacer || el === this.bottomSpacer) return;

                const id = el.dataset ? (el.dataset.id || el.dataset.msgId) : null;

                const isDate = el.classList ? el.classList.contains('date-separator') : false;

                const isCutoff = el.classList ? el.classList.contains('chat-cutoff-divider') : false;

                const strId = id ? String(id) : null;

                if (strId && !isDate && !isCutoff && existingIds.has(strId)) {

                    return; // Skip registering duplicate element

                }

                if (strId) existingIds.add(strId);

                newItems.push({

                    id: strId || ('el-' + Math.random().toString(36).substring(2, 8)),

                    el: el,

                    height: el.offsetHeight || (isDate ? 32 : (isCutoff ? 140 : 65)),

                    isDate: isDate,

                    isCutoff: isCutoff

                });

            });



            if (isPrepend) {

                this.items = newItems.concat(this.items);

            } else {

                this.items = this.items.concat(newItems);

            }



            // Fast measurement pass - avoid immediate DOM unmount during prepend to prevent scroll jitter

            if (!this.isSuspended && !isPrepend) {

                requestAnimationFrame(() => {

                    newItems.forEach(item => {

                        if (item.el.isConnected && item.el.offsetHeight > 0) {

                            item.height = item.el.offsetHeight;

                        }

                    });

                    if (!this.isSuspended) this.update();

                });

            } else {

                requestAnimationFrame(() => {

                    newItems.forEach(item => {

                        if (item.el.isConnected && item.el.offsetHeight > 0) {

                            item.height = item.el.offsetHeight;

                        }

                    });

                });

            }

        },



        update() {

            if (!chatContainer || this.items.length <= (this.buffer * 2)) return;

            if (chatContainer.dataset.mode === 'search') return;

            if (this.isSuspended) return;



            const cTop = chatContainer.scrollTop;

            const cHeight = chatContainer.clientHeight;

            const cBottom = cTop + cHeight;



            let accumulatedTop = 0;

            let firstVisibleIdx = -1;

            let lastVisibleIdx = -1;



            for (let i = 0; i < this.items.length; i++) {

                const item = this.items[i];

                // Only read offsetHeight for connected elements that haven't been measured yet

                // (height === 0 or default estimate). Skip expensive layout reads for already-measured items.

                if (item.el.isConnected && (

                    item.height === 0 || item.height === 65 || item.height === 32 || item.height === 140 ||

                    item._needsMeasure || (item.el.classList && item.el.classList.contains('message') && item.el.querySelector('details[open]'))

                )) {

                    const h = item.el.offsetHeight;

                    if (h > 0) {

                        item.height = h;

                        item._needsMeasure = false;

                    }

                }

                const itemTop = accumulatedTop;

                const itemBottom = itemTop + item.height;



                if (firstVisibleIdx === -1 && itemBottom >= cTop) {

                    firstVisibleIdx = i;

                }

                if (itemTop <= cBottom) {

                    lastVisibleIdx = i;

                }

                accumulatedTop += item.height;

            }



            if (firstVisibleIdx === -1) firstVisibleIdx = 0;

            if (lastVisibleIdx === -1) lastVisibleIdx = this.items.length - 1;



            const startIdx = Math.max(0, firstVisibleIdx - this.buffer);

            const endIdx = Math.min(this.items.length - 1, lastVisibleIdx + this.buffer);



            let topSpacerHeight = 0;

            for (let i = 0; i < startIdx; i++) {

                topSpacerHeight += this.items[i].height;

            }



            let bottomSpacerHeight = 0;

            for (let i = endIdx + 1; i < this.items.length; i++) {

                bottomSpacerHeight += this.items[i].height;

            }



            this.topSpacer.style.height = `${topSpacerHeight}px`;

            this.bottomSpacer.style.height = `${bottomSpacerHeight}px`;



            // Insert mounted items in correct order between topSpacer and bottomSpacer

            let refNode = this.topSpacer.nextSibling;

            const newlyMounted = [];

            for (let i = startIdx; i <= endIdx; i++) {

                const item = this.items[i];

                if (item.el.parentNode !== chatContainer) {

                    chatContainer.insertBefore(item.el, refNode);

                    if (item.el.classList && item.el.classList.contains('message')) {

                        newlyMounted.push(item.el);

                        if (item.el.querySelector('details[open]')) {

                            item._needsMeasure = true;

                        }

                    }

                }

                refNode = item.el.nextSibling;

            }

            if (newlyMounted.length > 0 && typeof adjustMessageFooterWidths === 'function') {

                adjustMessageFooterWidths(chatContainer, newlyMounted);

            }





            // Unmount items outside [startIdx, endIdx]

            for (let i = 0; i < startIdx; i++) {

                const item = this.items[i];

                if (item.el.isConnected) {

                    item.height = item.el.offsetHeight || item.height;

                    item.el.remove();

                }

            }

            for (let i = endIdx + 1; i < this.items.length; i++) {

                const item = this.items[i];

                if (item.el.isConnected) {

                    item.height = item.el.offsetHeight || item.height;

                    item.el.remove();

                }

            }

        },



        scheduleUpdate() {

            if (this.isSuspended) return;

            if (this.rafId) return;

            this.rafId = requestAnimationFrame(() => {

                this.rafId = null;

                this.update();

            });

        },



        findAndMount(msgId) {

            const idx = this.items.findIndex(it => String(it.id) === String(msgId) || (it.el && it.el.dataset && it.el.dataset.id === String(msgId)));

            if (idx === -1) return null;

            const targetItem = this.items[idx];

            let topBefore = 0;

            for (let i = 0; i < idx; i++) {

                topBefore += this.items[i].height;

            }

            chatContainer.scrollTop = Math.max(0, topBefore - (chatContainer.clientHeight / 2) + (targetItem.height / 2));

            this.update();

            return targetItem.el;

        }

    };

    window.VirtualChat = VirtualChat;


