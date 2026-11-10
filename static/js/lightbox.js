// lightbox.js — High-performance image and video viewer with gallery navigation
(function () {
    let overlay, wrapper, prevBtn, nextBtn, closeBtn, counterEl, zoomBtn, downloadBtn, captionEl;
    let galleryItems = [];
    let currentIndex = 0;
    let isZoomed = false;

    function initLightbox() {
        overlay = document.getElementById('lightbox-overlay');
        if (!overlay) return;

        wrapper = overlay.querySelector('.lightbox-content-wrapper');
        prevBtn = overlay.querySelector('.lightbox-prev');
        nextBtn = overlay.querySelector('.lightbox-next');
        closeBtn = overlay.querySelector('.lightbox-close');
        counterEl = overlay.querySelector('.lightbox-counter');
        zoomBtn = overlay.querySelector('.lightbox-zoom-btn');
        downloadBtn = overlay.querySelector('.lightbox-download-btn');
        captionEl = overlay.querySelector('.lightbox-caption');

        if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
        if (prevBtn) prevBtn.addEventListener('click', () => navigate(-1));
        if (nextBtn) nextBtn.addEventListener('click', () => navigate(1));

        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay || e.target === wrapper) {
                    closeLightbox();
                }
            });

            let hideControlsTimer = null;
            overlay.addEventListener('mousemove', () => {
                overlay.classList.add('controls-visible');
                clearTimeout(hideControlsTimer);
                hideControlsTimer = setTimeout(() => {
                    const tb = overlay.querySelector('.lightbox-toolbar');
                    if (tb && !tb.matches(':hover')) {
                        overlay.classList.remove('controls-visible');
                    }
                }, 2200);
            });
            overlay.addEventListener('mouseleave', () => {
                overlay.classList.remove('controls-visible');
            });
        }

        if (zoomBtn) {
            zoomBtn.addEventListener('click', toggleZoom);
        }

        document.addEventListener('keydown', (e) => {
            if (!overlay || !overlay.classList.contains('active')) return;
            if (e.key === 'Escape') closeLightbox();
            else if (e.key === 'ArrowLeft') navigate(-1);
            else if (e.key === 'ArrowRight') navigate(1);
        });
    }

    function collectCurrentChatMedia() {
        const items = [];
        const mediaNodes = document.querySelectorAll('#chat-container .photo, #chat-container .album-item img, #chat-container .album-item video, #chat-container .web-preview-image');
        mediaNodes.forEach(node => {
            const src = node.getAttribute('src');
            if (!src || src.includes('avatar') || src.includes('custom_emoji')) return;
            const isVideo = node.tagName.toLowerCase() === 'video' || src.endsWith('.mp4') || src.endsWith('.webm');
            const msgEl = node.closest('.message');
            const dateStr = msgEl ? (msgEl.querySelector('.message-time')?.textContent || '') : '';
            const senderStr = msgEl ? (msgEl.querySelector('.message-author')?.textContent || '') : '';
            items.push({
                src: src,
                isVideo: isVideo,
                caption: senderStr ? `${senderStr} · ${dateStr}` : dateStr
            });
        });
        return items;
    }

    function openLightbox(srcOrItem, index = -1) {
        if (!overlay) initLightbox();
        if (!overlay) return;

        if (Array.isArray(srcOrItem)) {
            galleryItems = srcOrItem;
            currentIndex = Math.max(0, index);
        } else {
            const targetSrc = typeof srcOrItem === 'string' ? srcOrItem : srcOrItem.src;
            galleryItems = collectCurrentChatMedia();
            currentIndex = galleryItems.findIndex(i => i.src === targetSrc);
            if (currentIndex === -1) {
                const isVideo = targetSrc.endsWith('.mp4') || targetSrc.endsWith('.webm');
                galleryItems.push({ src: targetSrc, isVideo: isVideo, caption: '' });
                currentIndex = galleryItems.length - 1;
            }
        }

        renderCurrentItem();
        overlay.style.display = 'flex';
        overlay.classList.add('controls-visible');
        requestAnimationFrame(() => overlay.classList.add('active'));
    }

    function renderCurrentItem() {
        if (!galleryItems || galleryItems.length === 0) return;
        if (currentIndex < 0) currentIndex = 0;
        if (currentIndex >= galleryItems.length) currentIndex = galleryItems.length - 1;

        const item = galleryItems[currentIndex];
        isZoomed = false;

        // Clear existing media
        const oldMedia = wrapper.querySelector('.lightbox-media');
        if (oldMedia) {
            if (oldMedia.tagName.toLowerCase() === 'video') oldMedia.pause();
            oldMedia.remove();
        }

        let mediaEl;
        if (item.isVideo) {
            mediaEl = document.createElement('video');
            mediaEl.src = item.src;
            mediaEl.controls = true;
            mediaEl.autoplay = true;
            mediaEl.className = 'lightbox-media';
        } else {
            mediaEl = document.createElement('img');
            mediaEl.src = item.src;
            mediaEl.className = 'lightbox-media';
            mediaEl.addEventListener('click', toggleZoom);
        }

        wrapper.insertBefore(mediaEl, captionEl);

        if (counterEl) {
            counterEl.textContent = `${currentIndex + 1} / ${galleryItems.length}`;
        }
        if (captionEl) {
            captionEl.textContent = item.caption || '';
        }
        if (downloadBtn) {
            downloadBtn.onclick = () => {
                const a = document.createElement('a');
                a.href = item.src;
                a.download = item.src.split('/').pop() || 'media';
                document.body.appendChild(a);
                a.click();
                a.remove();
            };
        }

        if (prevBtn) prevBtn.style.display = galleryItems.length > 1 ? 'flex' : 'none';
        if (nextBtn) nextBtn.style.display = galleryItems.length > 1 ? 'flex' : 'none';
    }

    function navigate(direction) {
        if (galleryItems.length <= 1) return;
        currentIndex = (currentIndex + direction + galleryItems.length) % galleryItems.length;
        renderCurrentItem();
    }

    function toggleZoom() {
        const media = wrapper.querySelector('.lightbox-media');
        if (!media || media.tagName.toLowerCase() === 'video') return;
        isZoomed = !isZoomed;
        if (isZoomed) media.classList.add('zoomed');
        else media.classList.remove('zoomed');
    }

    function closeLightbox() {
        if (!overlay) return;
        overlay.classList.remove('active');
        const media = wrapper.querySelector('.lightbox-media');
        if (media && media.tagName.toLowerCase() === 'video') media.pause();
        setTimeout(() => {
            overlay.style.display = 'none';
            if (media) media.remove();
        }, 200);
    }

    window.openLightbox = openLightbox;
    window.closeLightbox = closeLightbox;

    document.addEventListener('DOMContentLoaded', initLightbox);
})();
