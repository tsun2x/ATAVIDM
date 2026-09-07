/**
 * Accessible green/red sliding violation switches for TAVIDM.
 */
(function (global) {
    "use strict";

    function slugify(name) {
        return String(name).replace(/[^a-z0-9]+/gi, "_").toLowerCase();
    }

    /**
     * Build one switch row. Returns HTML string.
     * @param {object} opts
     */
    function renderViolationSwitch(opts) {
        const name = opts.name || "";
        const id = opts.id || ("vsw-" + slugify(name));
        const checked = !!opts.checked;
        const disabled = !!opts.disabled;
        const reason = opts.reason || "";
        const badge = opts.badgeHtml || "";
        const inputClass = opts.inputClass || "violation-switch-input";
        const stateText = checked ? "Active" : "Inactive";
        const stateClass = checked ? "is-active" : "is-inactive";
        return (
            '<div class="tavidm-switch-row ' + (disabled ? "is-disabled" : "") + '">' +
            '<div class="tavidm-switch-labels flex-grow-1">' +
            '<label class="tavidm-switch-title" for="' + id + '">' + name + badge + "</label>" +
            (reason ? '<div class="tavidm-switch-reason text-muted small">' + reason + "</div>" : "") +
            "</div>" +
            '<div class="tavidm-switch-control">' +
            '<span class="tavidm-switch-state ' + stateClass + '" data-state-for="' + id + '" aria-hidden="true">' +
            stateText + "</span>" +
            '<label class="tavidm-switch ' + (checked ? "switch-on" : "switch-off") + (disabled ? " switch-disabled" : "") + '" for="' + id + '">' +
            '<input type="checkbox" role="switch" class="' + inputClass + '"' +
            ' id="' + id + '" value="' + name.replace(/"/g, "&quot;") + '"' +
            (checked ? " checked" : "") +
            (disabled ? " disabled" : "") +
            ' aria-checked="' + (checked ? "true" : "false") + '"' +
            (reason ? ' aria-describedby="' + id + '-reason"' : "") +
            ">" +
            '<span class="tavidm-switch-track" aria-hidden="true"><span class="tavidm-switch-thumb"></span></span>' +
            "</label>" +
            (reason ? '<span id="' + id + '-reason" class="visually-hidden">' + reason + "</span>" : "") +
            "</div></div>"
        );
    }

    function isMixedSwitch(input) {
        if (!input) return false;
        const ds = input.dataset || {};
        // Explicit on/off after user toggle wins over a stale aria-checked.
        if (ds.enabledState === "on" || ds.enabledState === "off") {
            return false;
        }
        if (input.indeterminate) return true;
        if (ds.enabledState === "mixed") return true;
        if (input.getAttribute && input.getAttribute("aria-checked") === "mixed") {
            return true;
        }
        return false;
    }

    function syncSwitchState(input) {
        if (!input) return;
        const mixed = isMixedSwitch(input);
        const checked = !!input.checked;
        const wrap = input.closest(".tavidm-switch");
        const state = document.querySelector('[data-state-for="' + input.id + '"]');
        if (wrap) {
            wrap.classList.toggle("switch-on", !mixed && checked);
            wrap.classList.toggle("switch-off", !mixed && !checked);
            wrap.classList.toggle("switch-mixed", mixed);
        }
        if (state) {
            if (mixed) {
                state.textContent = "Mixed";
                state.classList.add("is-mixed");
                state.classList.remove("is-active", "is-inactive");
            } else {
                state.textContent = checked ? "Active" : "Inactive";
                state.classList.toggle("is-active", checked);
                state.classList.toggle("is-inactive", !checked);
                state.classList.remove("is-mixed");
            }
        }
        input.setAttribute(
            "aria-checked",
            mixed ? "mixed" : checked ? "true" : "false"
        );
    }

    function bindSwitchRoot(root) {
        const el = root || document;
        el.addEventListener("change", function (e) {
            if (e.target && e.target.matches("input[role='switch']")) {
                syncSwitchState(e.target);
            }
        });
    }

    function collectChecked(selector) {
        return Array.from(document.querySelectorAll(selector + ":checked")).map(function (el) {
            return el.value;
        });
    }

    global.TavidmViolationSwitch = {
        render: renderViolationSwitch,
        sync: syncSwitchState,
        bind: bindSwitchRoot,
        collectChecked: collectChecked,
    };

    document.addEventListener("DOMContentLoaded", function () {
        bindSwitchRoot(document);
        document.querySelectorAll("input[role='switch']").forEach(syncSwitchState);
    });
})(window);
