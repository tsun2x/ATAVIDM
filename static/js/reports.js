/**
 * TAVIDM - Reports Page
 */

(function () {
    "use strict";

    const dateFrom = document.getElementById("dateFrom");
    const dateTo = document.getElementById("dateTo");
    const previewRange = document.getElementById("previewRange");
    const previewFormat = document.getElementById("previewFormat");
    const previewSize = document.getElementById("previewSize");

    function formatDateRange() {
        if (!dateFrom || !dateTo || !previewRange) return;
        const from = new Date(dateFrom.value);
        const to = new Date(dateTo.value);
        const opts = { month: "short", day: "numeric", year: "numeric" };
        previewRange.textContent = from.toLocaleDateString("en-US", opts) + " \u2013 " + to.toLocaleDateString("en-US", opts);
    }

    [dateFrom, dateTo].forEach(function (el) {
        el?.addEventListener("change", formatDateRange);
    });

    formatDateRange();

    function generateReport(format) {
        const name = document.getElementById("reportName")?.value || "Report";
        previewFormat.textContent = format;
        previewSize.textContent = format === "PDF" ? "2.1 MB" : "1.4 MB";

        showToast(
            "Generating " + format,
            '"' + name + '" is being generated. This is a demo — no file will be created.',
            format === "PDF" ? "danger" : "success"
        );

        setTimeout(function () {
            addToHistory(name, format);
            showToast("Report Ready", name + " (" + format + ") is ready for download (demo).", "success");
        }, 2000);
    }

    function addToHistory(name, type) {
        const tbody = document.getElementById("historyBody");
        if (!tbody) return;

        const id = "RPT-" + String(Math.floor(Math.random() * 900) + 100);
        const today = new Date().toISOString().split("T")[0];
        const size = type === "PDF" ? "2.1 MB" : "1.4 MB";
        const badge = type === "PDF"
            ? '<span class="badge bg-danger-subtle text-danger"><i class="bi bi-file-earmark-pdf me-1"></i>PDF</span>'
            : '<span class="badge bg-success-subtle text-success"><i class="bi bi-file-earmark-excel me-1"></i>Excel</span>';

        const row = document.createElement("tr");
        row.innerHTML =
            "<td><code>" + id + "</code></td>" +
            "<td>" + name + "</td>" +
            "<td>" + badge + "</td>" +
            "<td>" + today + "</td>" +
            "<td>" + size + "</td>" +
            '<td><span class="badge bg-success-subtle text-success">Ready</span></td>' +
            '<td><button class="btn btn-sm btn-outline-primary btn-download-report" data-report-id="' + id + '" data-report-name="' + name + '"><i class="bi bi-download"></i></button></td>';

        tbody.insertBefore(row, tbody.firstChild);
    }

    document.getElementById("btnGeneratePdf")?.addEventListener("click", function () {
        generateReport("PDF");
    });

    document.getElementById("btnGenerateExcel")?.addEventListener("click", function () {
        generateReport("Excel");
    });

    document.getElementById("btnRefreshHistory")?.addEventListener("click", function () {
        showToast("Refreshed", "Download history has been refreshed.", "info");
    });

    document.getElementById("historyTable")?.addEventListener("click", function (e) {
        const btn = e.target.closest(".btn-download-report");
        if (!btn || btn.disabled) return;
        showToast("Downloading", "Downloading " + btn.dataset.reportName + " (demo).", "success");
    });
})();
