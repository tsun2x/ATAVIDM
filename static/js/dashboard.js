/**
 * TAVIDM - Dashboard Charts
 */

(function () {
    "use strict";

    const data = window.TAVIDM_CHART_DATA;
    if (!data || typeof Chart === "undefined") return;

    const chartDefaults = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
        },
    };

    // Hourly violations line chart
    const hourlyCtx = document.getElementById("hourlyChart");
    if (hourlyCtx) {
        new Chart(hourlyCtx, {
            type: "line",
            data: {
                labels: data.hourly_labels,
                datasets: [{
                    label: "Violations",
                    data: data.hourly_values,
                    borderColor: "#2563eb",
                    backgroundColor: "rgba(37, 99, 235, 0.1)",
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: "#2563eb",
                    pointRadius: 4,
                    pointHoverRadius: 6,
                }],
            },
            options: {
                ...chartDefaults,
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: "rgba(226, 232, 240, 0.8)" },
                        ticks: { stepSize: 5 },
                    },
                    x: {
                        grid: { display: false },
                    },
                },
            },
        });
    }

    // Weekly bar chart
    const weeklyCtx = document.getElementById("weeklyChart");
    if (weeklyCtx) {
        new Chart(weeklyCtx, {
            type: "bar",
            data: {
                labels: data.weekly_labels,
                datasets: [{
                    label: "Violations",
                    data: data.weekly_values,
                    backgroundColor: [
                        "#2563eb", "#3b82f6", "#60a5fa", "#2563eb",
                        "#1d4ed8", "#93c5fd", "#bfdbfe",
                    ],
                    borderRadius: 6,
                    borderSkipped: false,
                }],
            },
            options: {
                ...chartDefaults,
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: "rgba(226, 232, 240, 0.8)" },
                    },
                    x: {
                        grid: { display: false },
                    },
                },
            },
        });
    }
})();
