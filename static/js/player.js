// player.js — Floating draggable media player modal
(function () {
    let playerModal, modalVideo, modalAudio;
    let isDragging = false;
    let offset = { x: 0, y: 0 };

    function initPlayer() {
        playerModal = document.getElementById('player-modal');
        modalVideo = document.getElementById('modal-video');
        modalAudio = document.getElementById('modal-audio');

        if (!playerModal) return;

        const modalHeader = playerModal.querySelector('.modal-header');
        if (modalHeader) {
            modalHeader.addEventListener('mousedown', (e) => {
                if (e.target.classList.contains('modal-close')) return;
                if (playerModal.style.transform !== 'none') {
                    const rect = playerModal.getBoundingClientRect();
                    playerModal.style.transform = 'none';
                    playerModal.style.left = rect.left + 'px';
                    playerModal.style.top = rect.top + 'px';
                }
                isDragging = true;
                offset.x = e.clientX - playerModal.getBoundingClientRect().left;
                offset.y = e.clientY - playerModal.getBoundingClientRect().top;
                modalHeader.style.cursor = 'grabbing';
            });
        }

        document.addEventListener('mouseup', () => {
            isDragging = false;
            if (modalHeader) modalHeader.style.cursor = 'move';
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            e.preventDefault();
            let newX = e.clientX - offset.x;
            let newY = e.clientY - offset.y;
            const maxX = window.innerWidth - playerModal.offsetWidth;
            const maxY = window.innerHeight - playerModal.offsetHeight;

            if (newX < 0) newX = 0;
            if (newY < 0) newY = 0;
            if (newX > maxX) newX = maxX;
            if (newY > maxY) newY = maxY;

            playerModal.style.left = newX + 'px';
            playerModal.style.top = newY + 'px';
        });
    }

    window.openPlayer = function (filePath, mimeType) {
        if (!playerModal) initPlayer();
        if (!playerModal) return;

        modalVideo.style.display = 'none';
        modalAudio.style.display = 'none';

        if (mimeType.startsWith('video/')) {
            modalVideo.src = filePath;
            modalVideo.style.display = 'block';
            modalVideo.play().catch(() => {});
        } else if (mimeType.startsWith('audio/')) {
            modalAudio.src = filePath;
            modalAudio.style.display = 'block';
            modalAudio.play().catch(() => {});
        }
        playerModal.style.display = 'block';
    };

    window.closePlayer = function () {
        if (!playerModal) return;
        playerModal.style.display = 'none';
        if (modalVideo) {
            modalVideo.pause();
            modalVideo.src = '';
        }
        if (modalAudio) {
            modalAudio.pause();
            modalAudio.src = '';
        }
    };

    document.addEventListener('DOMContentLoaded', initPlayer);
})();
