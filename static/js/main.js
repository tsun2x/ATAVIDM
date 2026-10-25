/**
 * TAVIDM - Main JavaScript
 * Shared utilities and sidebar navigation
 */

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function toastToneClass(type) {
    return {
        success: "text-bg-success",
        danger: "text-bg-danger",
        warning: "text-bg-warning",
        info: "text-bg-info",
    }[type] || "text-bg-info";
}

if (typeof window !== "undefined") {
    window.TAVIDMMainUI = { escapeHtml: escapeHtml, toastToneClass: toastToneClass };
}
if (typeof module !== "undefined" && module.exports) {
    module.exports = { escapeHtml: escapeHtml, toastToneClass: toastToneClass };
}

(function () {
    "use strict";

    // Sidebar toggle (mobile)
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebarToggle");
    const sidebarOverlay = document.getElementById("sidebarOverlay");

    function toggleSidebar() {
        const expanded = !sidebar.classList.contains("show");
        sidebar.classList.toggle("show", expanded);
        sidebarOverlay.classList.toggle("show", expanded);
        sidebarToggle?.setAttribute("aria-expanded", String(expanded));
    }

    function closeSidebar() {
        sidebar.classList.remove("show");
        sidebarOverlay.classList.remove("show");
        sidebarToggle?.setAttribute("aria-expanded", "false");
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener("click", toggleSidebar);
    }

    if (sidebarOverlay) {
        sidebarOverlay.addEventListener("click", closeSidebar);
    }

    document.addEventListener?.("keydown", function (e) {
        if (e.key === "Escape" && sidebar?.classList.contains("show")) {
            closeSidebar();
            sidebarToggle?.focus();
        }
    });

    // Close sidebar on resize to desktop
    window.addEventListener("resize", function () {
        if (window.innerWidth >= 992) {
            closeSidebar();
        }
    });

    // Global search: jump to the violations page with the query applied.
    const globalSearch = document.getElementById("globalSearch");
    if (globalSearch) {
        globalSearch.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && this.value.trim()) {
                window.location.href = "/violations?q=" + encodeURIComponent(this.value.trim());
            }
        });
    }

    // Refresh account-scoped sidebar counts when a background tab becomes active.
    const navCountLinks = Array.from(document.querySelectorAll("[data-nav-count-key]")).filter(function (link) {
        return !!link.dataset.navCountKey;
    });
    if (navCountLinks.length) {
        const badgeLabels = {
            review_queue: ["pending review item", "pending review items"],
            motorcycle_detail_review: ["pending motorcycle detail candidate", "pending motorcycle detail candidates"],
            pickup_detail_review: ["pending pickup review lead", "pending pickup review leads"],
        };
        let navCountRequest = null;
        let lastNavCountRefresh = 0;
        let navWasInactive = false;
        const refreshNavCounts = function (force) {
            const now = Date.now();
            if (document.visibilityState === "hidden" || navCountRequest || (!force && now - lastNavCountRefresh < 3000)) {
                return navCountRequest || Promise.resolve();
            }
            lastNavCountRefresh = now;
            navCountRequest = fetch("/api/navigation-counts", {
                credentials: "same-origin",
                cache: "no-store",
                headers: { "Accept": "application/json" },
            }).then(function (response) {
                if (!response.ok) return null;
                return response.json();
            }).then(function (payload) {
                if (!payload || payload.success !== true || !payload.counts) return;
                navCountLinks.forEach(function (link) {
                    const key = link.dataset.navCountKey;
                    const count = Math.max(0, Number(payload.counts[key]) || 0);
                    const labels = badgeLabels[key] || ["pending item", "pending items"];
                    const label = labels[count === 1 ? 0 : 1];
                    let badge = link.querySelector(".nav-badge-count");
                    if (!badge && count > 0) {
                        badge = document.createElement("span");
                        badge.className = "nav-badge nav-badge-count";
                        badge.setAttribute("aria-hidden", "true");
                        link.appendChild(badge);
                    }
                    if (badge) {
                        badge.textContent = String(count);
                        badge.hidden = count === 0;
                    }
                    if (count > 0) {
                        const title = link.querySelector("span:not(.nav-badge)")?.textContent.trim() || "Notifications";
                        link.setAttribute("aria-label", title + ", " + count + " " + label);
                    } else {
                        link.removeAttribute("aria-label");
                    }
                });
            }).catch(function () {
                // Keep the server-rendered counts when a refresh is unavailable.
            }).finally(function () {
                navCountRequest = null;
            });
            return navCountRequest;
        };
        window.TAVIDMRefreshNavCounts = refreshNavCounts;
        window.addEventListener("blur", function () { navWasInactive = true; });
        window.addEventListener("focus", function () {
            if (navWasInactive) {
                navWasInactive = false;
                refreshNavCounts(false);
            }
        });
        window.addEventListener("pageshow", function (event) {
            if (event.persisted) refreshNavCounts(false);
        });
        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "hidden") {
                navWasInactive = true;
            } else if (navWasInactive) {
                navWasInactive = false;
                refreshNavCounts(false);
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
        const bgClass = toastToneClass(type);
        const safeTitle = escapeHtml(title);
        const safeMessage = escapeHtml(message);

        const html =
            '<div id="' + toastId + '" class="toast ' + bgClass + '" role="alert" aria-live="assertive" aria-atomic="true">' +
            '<div class="toast-header">' +
            "<strong class=\"me-auto\">" + safeTitle + "</strong>" +
            '<button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>' +
            "</div>" +
            '<div class="toast-body">' + safeMessage + "</div>" +
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
