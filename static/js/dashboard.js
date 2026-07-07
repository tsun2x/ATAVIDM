/**
 * TAVIDM - Dashboard Charts
 */

(function () {
    "use strict";

    const data = window.TAVIDM_CHART_DATA;
    if (!data || typeof Chart === "undefined") return;

    const accent = "#e63946";
    const chartDefaults = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
    };

    const hourlyCtx = document.getElementById("hourlyChart");
    if (hourlyCtx) {
        new Chart(hourlyCtx, {
            type: "bar",
            data: {
                labels: data.hourly_labels,
                datasets: [{
                    label: "Violations",
                    data: data.hourly_values,
                    backgroundColor: accent,
                    borderRadius: 6,
                    borderSkipped: false,
                }],
            },
            options: {
                ...chartDefaults,
                scales: {
                    y: { beginAtZero: true, grid: { color: "rgba(226, 232, 240, 0.8)" }, ticks: { stepSize: 2 } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    const distCtx = document.getElementById("distributionChart");
    if (distCtx) {
        new Chart(distCtx, {
            type: "doughnut",
            data: {
                labels: data.distribution_labels,
                datasets: [{
                    data: data.distribution_values,
                    backgroundColor: ["#e63946", "#f59e0b", "#f97316", "#22c55e", "#3b82f6"],
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } },
                },
            },
        });
    }
})();
