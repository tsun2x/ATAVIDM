/**
 * TAVIDM - Main JavaScript
 * Shared utilities and sidebar navigation
 */

(function () {
    "use strict";

    // Sidebar toggle (mobile)
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebarToggle");
    const sidebarOverlay = document.getElementById("sidebarOverlay");

    function toggleSidebar() {
        sidebar.classList.toggle("show");
        sidebarOverlay.classList.toggle("show");
    }

    function closeSidebar() {
        sidebar.classList.remove("show");
        sidebarOverlay.classList.remove("show");
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener("click", toggleSidebar);
    }

    if (sidebarOverlay) {
        sidebarOverlay.addEventListener("click", closeSidebar);
    }

    // Close sidebar on resize to desktop
    window.addEventListener("resize", function () {
        if (window.innerWidth >= 992) {
            closeSidebar();
        }
    });

    // Global search (demo toast)
    const globalSearch = document.getElementById("globalSearch");
    if (globalSearch) {
        globalSearch.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && this.value.trim()) {
                showToast("Search", 'Demo search for: "' + this.value.trim() + '" — no backend connected.', "info");
            }
        });
    }

    /**
     * Show Bootstrap toast notification
     * @param {string} title
     * @param {string} message
     * @param {string} type - success | danger | warning | info
     */
    window.showToast = function (title, message, type) {
        type = type || "info";
        const container = document.getElementById("toastContainer");
        if (!container) return;

        const toastId = "toast-" + Date.now();
        const bgClass = {
            success: "text-bg-success",
            danger: "text-bg-danger",
            warning: "text-bg-warning",
            info: "text-bg-primary",
        }[type] || "text-bg-primary";

        const html =
            '<div id="' + toastId + '" class="toast ' + bgClass + '" role="alert" aria-live="assertive" aria-atomic="true">' +
            '<div class="toast-header">' +
            "<strong class=\"me-auto\">" + title + "</strong>" +
            '<button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>' +
            "</div>" +
            '<div class="toast-body">' + message + "</div>" +
            "</div>";

        container.insertAdjacentHTML("beforeend", html);
        const toastEl = document.getElementById(toastId);
        const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
        toast.show();
        toastEl.addEventListener("hidden.bs.toast", function () {
            toastEl.remove();
        });
    };

    // Format timestamp helper
    window.formatTimestamp = function () {
        const now = new Date();
        return now.toLocaleString("en-PH", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
        });
    };
})();
