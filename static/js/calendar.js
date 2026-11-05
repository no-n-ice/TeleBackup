// calendar.js — Activity calendar heatmap navigator
(function () {
    async function loadDailyHeatmap(chatId) {
        const container = document.getElementById('calendar-heatmap-container');
        if (!container || !chatId) return;

        container.innerHTML = '<div style="font-size:12px;opacity:0.7;padding:8px;">Loading activity...</div>';
        try {
            const res = await fetch(`/api/stats/daily_heatmap?chat_id=${chatId}`);
            if (!res.ok) throw new Error('Failed to load activity');
            const data = await res.json();
            renderHeatmap(container, data);
        } catch (e) {
            container.innerHTML = `<div style="font-size:12px;color:red;padding:8px;">${(window.escapeHTML || String)(e.message)}</div>`;
        }
    }

    function renderHeatmap(container, data) {
        const days = Object.keys(data).sort();
        if (days.length === 0) {
            container.innerHTML = '<div style="font-size:12px;opacity:0.6;padding:8px;">No message history</div>';
            return;
        }

        const maxCount = Math.max(...Object.values(data));
        container.innerHTML = '<div class="heatmap-grid"></div>';
        const grid = container.querySelector('.heatmap-grid');

        days.forEach(day => {
            const count = data[day];
            const cell = document.createElement('div');
            cell.className = 'heatmap-cell';
            const intensity = maxCount > 0 ? Math.min(1, count / maxCount) : 0;
            
            if (intensity > 0.75) cell.style.background = 'var(--accent-color)';
            else if (intensity > 0.4) cell.style.background = 'rgba(39, 174, 96, 0.7)';
            else if (intensity > 0.1) cell.style.background = 'rgba(39, 174, 96, 0.4)';
            else cell.style.background = 'rgba(39, 174, 96, 0.2)';

            cell.title = `${day}: ${count} messages`;
            cell.addEventListener('click', () => {
                if (typeof searchByDate === 'function') {
                    searchByDate(day);
                }
            });
            grid.appendChild(cell);
        });
    }

    window.loadDailyHeatmap = loadDailyHeatmap;
})();
