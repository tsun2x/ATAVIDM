/**
 * TAVIDM - Analytics Charts
 */

(function () {
    "use strict";

    const data = window.TAVIDM_ANALYTICS;
    if (!data || typeof Chart === "undefined") return;

    const colors = ["#ef4444", "#f97316", "#eab308", "#8b5cf6", "#06b6d4", "#2563eb", "#16a34a"];

    const defaults = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
    };

    const dailyCtx = document.getElementById("dailyChart");
    if (dailyCtx) {
        new Chart(dailyCtx, {
            type: "bar",
            data: {
                labels: data.daily_labels,
                datasets: [{
                    label: "Violations",
                    data: data.daily_values,
                    backgroundColor: "#2563eb",
                    borderRadius: 6,
                }],
            },
            options: {
                ...defaults,
                scales: {
                    y: { beginAtZero: true, grid: { color: "rgba(226,232,240,0.8)" } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    const monthlyCtx = document.getElementById("monthlyChart");
    if (monthlyCtx) {
        new Chart(monthlyCtx, {
            type: "line",
            data: {
                labels: data.monthly_labels,
                datasets: [{
                    label: "Violations",
                    data: data.monthly_values,
                    borderColor: "#7c3aed",
                    backgroundColor: "rgba(124, 58, 237, 0.1)",
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: "#7c3aed",
                }],
            },
            options: {
                ...defaults,
                scales: {
                    y: { beginAtZero: true, grid: { color: "rgba(226,232,240,0.8)" } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    const breakdownCtx = document.getElementById("breakdownChart");
    if (breakdownCtx) {
        new Chart(breakdownCtx, {
            type: "doughnut",
            data: {
                labels: data.breakdown_labels,
                datasets: [{
                    data: data.breakdown_values,
                    backgroundColor: colors,
                    borderWidth: 2,
                    borderColor: "#fff",
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: { padding: 16, usePointStyle: true, pointStyle: "circle" },
                    },
                },
            },
        });
    }
})();
