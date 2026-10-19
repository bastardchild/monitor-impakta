// Chart.js helper with sleek dark theme and instance management
window.kpiCharts = window.kpiCharts || {};

function initKpiChart(canvasId, payloadId) {
    const el = document.getElementById(payloadId);
    if (!el) return;
    try {
        const data = JSON.parse(el.textContent);
        if (data && data.labels && data.labels.length > 0) {
            renderTrendChart(canvasId, data);
        }
    } catch (e) {
        console.error('Failed to initialize chart from payload:', e);
    }
}

function renderTrendChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    if (window.kpiCharts[canvasId]) {
        window.kpiCharts[canvasId].destroy();
    }

    const textColor = '#94a3b8';
    const gridColor = 'rgba(255, 255, 255, 0.04)';

    const isYouTube = data.platform === 'youtube';
    const secondLabel = isYouTube ? 'Waktu Tonton (Jam)' : 'Pengikut (Followers)';
    const secondData = isYouTube && data.watch_time_hours ? data.watch_time_hours : data.followers;
    const secondColor = isYouTube ? '#10b981' : '#34d399';

    window.kpiCharts[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: 'Tayangan (Views)',
                    data: data.views,
                    borderColor: '#38bdf8', // Cyan 400
                    backgroundColor: 'rgba(56, 189, 248, 0.08)',
                    borderWidth: 2,
                    pointRadius: (data.labels && data.labels.length <= 3) ? 4 : 0,
                    pointHoverRadius: 4,
                    pointHoverBackgroundColor: '#38bdf8',
                    pointHoverBorderColor: '#0f172a',
                    pointHoverBorderWidth: 2,
                    tension: 0.35,
                    fill: true,
                    yAxisID: 'y',
                },
                {
                    label: secondLabel,
                    data: secondData,
                    borderColor: secondColor,
                    backgroundColor: isYouTube ? 'rgba(16, 185, 129, 0.05)' : 'transparent',
                    borderWidth: 2,
                    borderDash: isYouTube ? [] : [4, 4],
                    pointRadius: (data.labels && data.labels.length <= 3) ? 4 : 0,
                    pointHoverRadius: 4,
                    pointHoverBackgroundColor: secondColor,
                    pointHoverBorderColor: '#0f172a',
                    pointHoverBorderWidth: 2,
                    tension: 0.35,
                    fill: isYouTube,
                    yAxisID: 'y1',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: {
                        color: textColor,
                        boxWidth: 10,
                        boxHeight: 10,
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 16,
                        font: {
                            family: "'Plus Jakarta Sans', sans-serif",
                            size: 11,
                            weight: 500
                        }
                    }
                },
                tooltip: {
                    backgroundColor: '#0f172a',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: '#1e293b',
                    borderWidth: 1,
                    padding: 12,
                    boxPadding: 6,
                    usePointStyle: true,
                    titleFont: {
                        family: "'Plus Jakarta Sans', sans-serif",
                        size: 12,
                        weight: 600
                    },
                    bodyFont: {
                        family: "'JetBrains Mono', monospace",
                        size: 11
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#64748b',
                        font: {
                            family: "'JetBrains Mono', monospace",
                            size: 10
                        },
                        maxRotation: 0,
                        autoSkip: true,
                        maxTicksLimit: 8
                    },
                    grid: {
                        color: gridColor,
                        drawBorder: false
                    },
                    border: {
                        display: false
                    }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    ticks: {
                        color: '#64748b',
                        font: {
                            family: "'JetBrains Mono', monospace",
                            size: 10
                        }
                    },
                    grid: {
                        color: gridColor,
                        drawBorder: false
                    },
                    border: {
                        display: false
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    ticks: {
                        color: '#64748b',
                        font: {
                            family: "'JetBrains Mono', monospace",
                            size: 10
                        }
                    },
                    grid: {
                        drawOnChartArea: false
                    },
                    border: {
                        display: false
                    }
                }
            }
        }
    });
}

// Automatically re-initialize Alpine.js components on HTMX content swaps
document.addEventListener('htmx:afterSwap', function (e) {
    if (window.Alpine && e.detail && e.detail.target) {
        window.Alpine.initTree(e.detail.target);
    }
});

// Global Sync / Reload Loading Modal Controller
(function () {
    function initSyncModal() {
        const overlay = document.getElementById('sync-modal-overlay');
        if (!overlay) return;

        const titleEl = document.getElementById('sync-modal-title');
        const subtitleEl = document.getElementById('sync-modal-subtitle');

        const defaultTitle = 'Menyinkronkan Data...';
        const defaultSubtitle = 'Sistem sedang mengambil & memperbarui data terbaru. Mohon tunggu sebentar.';

        let activeRequestsCount = 0;

        document.body.addEventListener('htmx:beforeRequest', function (e) {
            const detail = e.detail || {};
            const path = (detail.pathInfo && detail.pathInfo.requestPath) || '';
            const elt = detail.elt;

            const isSync = path.includes('/sync') || 
                           (elt && (elt.matches('[data-sync-button]') || elt.closest('[data-sync-button]')));

            const isFilterRange = path.includes('/partials/kpi-cards') || 
                                  path.includes('/partials/trend-chart') || 
                                  path.includes('/partials/top-videos') || 
                                  path.includes('/partials/top-posts') ||
                                  path.includes('/partials/sentiment-insight') ||
                                  path.includes('/partials/unmer') ||
                                  path.includes('/partials/external-media');

            if (isSync || isFilterRange) {
                activeRequestsCount++;

                if (isSync) {
                    const btn = elt ? (elt.matches('[data-sync-button]') ? elt : elt.closest('[data-sync-button]')) : null;
                    if (titleEl) {
                        titleEl.textContent = (btn && btn.dataset.syncTitle) || defaultTitle;
                    }
                    if (subtitleEl) {
                        subtitleEl.textContent = (btn && btn.dataset.syncSubtitle) || defaultSubtitle;
                    }
                } else if (isFilterRange && activeRequestsCount === 1) {
                    if (titleEl) {
                        titleEl.textContent = 'Memuat Data Periode...';
                    }
                    if (subtitleEl) {
                        subtitleEl.textContent = 'Sedang memperbarui metrik dan grafik untuk periode terpilih...';
                    }
                }

                document.body.classList.add('sync-in-progress');
                overlay.classList.add('active');
            }
        });

        function handleRequestEnd() {
            if (activeRequestsCount > 0) {
                activeRequestsCount--;
            }
            if (activeRequestsCount <= 0) {
                activeRequestsCount = 0;
                document.body.classList.remove('sync-in-progress');
                overlay.classList.remove('active');
                if (titleEl) titleEl.textContent = defaultTitle;
                if (subtitleEl) subtitleEl.textContent = defaultSubtitle;
            }
        }

        document.body.addEventListener('htmx:afterRequest', handleRequestEnd);
        document.body.addEventListener('htmx:responseError', handleRequestEnd);
        document.body.addEventListener('htmx:sendError', handleRequestEnd);
        document.body.addEventListener('htmx:timeout', handleRequestEnd);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSyncModal);
    } else {
        initSyncModal();
    }
})();



