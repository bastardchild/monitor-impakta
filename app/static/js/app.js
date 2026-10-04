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
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointHoverBackgroundColor: '#38bdf8',
                    pointHoverBorderColor: '#0f172a',
                    pointHoverBorderWidth: 2,
                    tension: 0.35,
                    fill: true,
                    yAxisID: 'y',
                },
                {
                    label: 'Pengikut (Followers)',
                    data: data.followers,
                    borderColor: '#34d399', // Emerald 400
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    borderDash: [4, 4],
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointHoverBackgroundColor: '#34d399',
                    pointHoverBorderColor: '#0f172a',
                    pointHoverBorderWidth: 2,
                    tension: 0.35,
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
