/**
 * TAVIDM - Analytics Charts
 */

(function () {
    "use strict";

    const data = window.TAVIDM_ANALYTICS;
    if (!data || typeof Chart === "undefined") return;

    const accent = "#e63946";
    const colors = ["#e63946", "#f59e0b", "#f97316", "#22c55e", "#3b82f6", "#8b5cf6", "#f43f5e", "#7f1d1d"];
    const defaults = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } };

    const dailyCtx = document.getElementById("dailyChart");
    if (dailyCtx) {
        new Chart(dailyCtx, {
            type: "line",
            data: {
                labels: data.daily_labels,
                datasets: [{ label: "Violations", data: data.daily_values, borderColor: accent, backgroundColor: "rgba(230,57,70,0.1)", fill: true, tension: 0.4 }],
            },
            options: { ...defaults, scales: { y: { beginAtZero: true }, x: { grid: { display: false } } } },
        });
    }

    const monthlyCtx = document.getElementById("monthlyChart");
    if (monthlyCtx) {
        new Chart(monthlyCtx, {
            type: "bar",
            data: {
                labels: data.monthly_labels,
                datasets: [{ label: "Violations", data: data.monthly_values, backgroundColor: accent, borderRadius: 6 }],
            },
            options: { ...defaults, scales: { y: { beginAtZero: true }, x: { grid: { display: false } } } },
        });
    }

    const breakdownCtx = document.getElementById("breakdownChart");
    if (breakdownCtx) {
        new Chart(breakdownCtx, {
            type: "doughnut",
            data: { labels: data.breakdown_labels, datasets: [{ data: data.breakdown_values, backgroundColor: colors, borderWidth: 2, borderColor: "#fff" }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10 } } } } },
        });
    }

    const conditionCtx = document.getElementById("conditionChart");
    if (conditionCtx) {
        new Chart(conditionCtx, {
            type: "bar",
            data: {
                labels: data.condition_labels,
                datasets: [{ label: "Violations", data: data.condition_values, backgroundColor: ["#fbbf24", "#e63946", "#1e3a5f"], borderRadius: 6 }],
            },
            options: { ...defaults, scales: { y: { beginAtZero: true }, x: { grid: { display: false } } } },
        });
    }

    const confidenceCtx = document.getElementById("confidenceChart");
    if (confidenceCtx) {
        new Chart(confidenceCtx, {
            type: "doughnut",
            data: {
                labels: data.confidence_labels,
                datasets: [{ data: data.confidence_values, backgroundColor: ["#16a34a", "#eab308", "#dc2626"], borderWidth: 0 }],
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } },
        });
    }
})();
