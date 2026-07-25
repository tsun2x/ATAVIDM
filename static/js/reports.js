/**
 * TAVIDM - Reports Page (real PDF/Excel generation)
 */

(function () {
    "use strict";

    const historyBody = document.getElementById("historyBody");

    function collectFilters() {
        return {
            title: document.getElementById("reportName")?.value.trim() || null,
            date_from: document.getElementById("dateFrom")?.value || null,
            date_to: document.getElementById("dateTo")?.value || null,
            violation_type: document.getElementById("reportViolationType")?.value || null,
            video_id: document.getElementById("reportVideo")?.value || null,
            status: document.getElementById("reportStatus")?.value || null,
        };
    }

    function formatBadge(type) {
        return type === "PDF"
            ? '<span class="badge bg-danger-subtle text-danger">PDF</span>'
            : '<span class="badge bg-success-subtle text-success">Excel</span>';
    }

    function renderHistory(reports) {
        if (!historyBody) return;
        if (!reports.length) {
            historyBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">No reports generated yet.</td></tr>';
            return;
        }
        historyBody.innerHTML = reports.map(function (r) {
            return (
                "<tr>" +
                "<td>" + r.name + "</td>" +
                '<td class="small">' + r.date + "</td>" +
                '<td class="small text-muted">' + r.filters + "</td>" +
                "<td>" + formatBadge(r.type) + "</td>" +
                '<td class="small">' + r.generated_by + "</td>" +
                '<td><a class="btn btn-sm btn-outline-danger" href="' + r.download_url + '" title="Download"><i class="bi bi-download"></i></a></td>' +
                "</tr>"
            );
        }).join("");
    }

    function refreshHistory() {
        return fetch("/api/reports")
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) renderHistory(payload.reports);
            });
    }

    function generateReport(format) {
        const body = collectFilters();
        body.format = format;
        showToast("Generating " + format.toUpperCase(), "Building the report from the violations database…", "info");

        fetch("/api/reports", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    showToast("Report Ready", payload.report.name + " (" + payload.report.type + ") is ready.", "success");
                    refreshHistory();
                    window.location.href = payload.report.download_url;
                } else {
                    showToast("Error", payload.error || "Report generation failed.", "danger");
                }
            })
            .catch(function () {
                showToast("Error", "Report generation failed. Please try again.", "danger");
            });
    }

    document.getElementById("btnGeneratePdf")?.addEventListener("click", function () {
        generateReport("pdf");
    });

    document.getElementById("btnGenerateExcel")?.addEventListener("click", function () {
        generateReport("excel");
    });

    document.getElementById("btnRefreshHistory")?.addEventListener("click", function () {
        refreshHistory().then(function () {
            showToast("Refreshed", "Report history updated.", "info");
        });
    });
})();
