(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    if (root) root.TAVIDMProcessingJobs = api;
})(typeof window !== "undefined" ? window : null, function () {
    "use strict";

    function createController(options) {
        const fetchFn = options.fetchFn;
        const setTimeoutFn = options.setTimeoutFn || setTimeout;
        const clearTimeoutFn = options.clearTimeoutFn || clearTimeout;
        const onState = options.onState || function () {};
        const endpoint = options.endpoint || "/api/processing-jobs";
        let started = false;
        let stopped = false;
        let timer = null;
        let snapshot = { success: true, active: [], recent: [], poll_after_ms: 10000 };
        let retryMs = 5000;
        let requestVersion = 0;

        function schedule(delay) {
            if (stopped) return;
            if (timer !== null) clearTimeoutFn(timer);
            timer = setTimeoutFn(refresh, delay);
        }

        async function refresh() {
            if (stopped) return;
            timer = null;
            const version = ++requestVersion;
            try {
                const response = await fetchFn(endpoint, { credentials: "same-origin", headers: { Accept: "application/json" } });
                if (!response.ok) throw new Error("Processing status request failed.");
                const next = await response.json();
                if (!next || next.success !== true) throw new Error("Invalid processing status response.");
                if (version !== requestVersion || stopped) return;
                snapshot = next;
                retryMs = 5000;
                onState(snapshot, { stale: false, error: null });
                schedule(Math.max(1000, Number(next.poll_after_ms) || 10000));
            } catch (error) {
                if (version !== requestVersion || stopped) return;
                onState(snapshot, { stale: true, error: error instanceof Error ? error.message : String(error) });
                schedule(retryMs);
                retryMs = Math.min(30000, retryMs * 2);
            }
        }

        return {
            start: function () {
                if (started) return;
                started = true;
                stopped = false;
                refresh();
            },
            stop: function () {
                stopped = true;
                if (timer !== null) clearTimeoutFn(timer);
                timer = null;
            },
            refresh: refresh,
            getSnapshot: function () { return snapshot; },
        };
    }

    function textNode(tag, className, value) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.textContent = value == null ? "" : String(value);
        return node;
    }

    function renderList(container, items, emptyText) {
        container.replaceChildren();
        if (!items.length) {
            container.appendChild(textNode("p", "text-muted small px-3 py-2 mb-0", emptyText));
            return;
        }
        items.forEach(function (job) {
            const link = document.createElement("a");
            link.className = "processing-job-item";
            link.href = job.live_monitor_url;
            const heading = textNode("span", "processing-job-name", job.filename);
            const status = textNode("span", "processing-job-status", job.status);
            const detail = textNode(
                "span",
                "processing-job-detail",
                job.status === "running" || job.status === "queued"
                    ? (job.stage || job.status) + " · " + Math.round(Number(job.progress_percent) || 0) + "%"
                    : (job.is_current_result ? "Current result" : job.is_superseded ? "Superseded" : job.status)
            );
            link.append(heading, status, detail);
            container.appendChild(link);
        });
    }

    function parseLiveMonitorTarget(search) {
        const params = new URLSearchParams(search || "");
        const videoId = Number(params.get("video_id"));
        const runId = Number(params.get("run_id"));
        const tab = params.get("jobs") === "completed" ? "completed" : "active";
        return {
            videoId: Number.isInteger(videoId) && videoId > 0 ? videoId : null,
            runId: Number.isInteger(runId) && runId > 0 ? runId : null,
            tab: tab,
        };
    }

    function describeJobState(job) {
        if (job.status === "completed" && job.is_current_result) return "Current result";
        if (job.status === "completed" && job.is_superseded) return "Superseded";
        return String(job.status || "unknown").replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
    }

    function bootBrowser() {
        const toggle = document.getElementById("processingJobsToggle");
        const activeList = document.getElementById("globalActiveJobs");
        const recentList = document.getElementById("globalRecentJobs");
        const count = document.getElementById("processingJobsCount");
        const connection = document.getElementById("processingJobsConnection");
        if (!toggle || !activeList || !recentList || !count || !connection) return null;
        const controller = createController({
            fetchFn: window.fetch.bind(window),
            onState: function (snapshot, meta) {
                const active = Array.isArray(snapshot.active) ? snapshot.active : [];
                const recent = Array.isArray(snapshot.recent) ? snapshot.recent : [];
                count.textContent = String(active.length);
                count.classList.toggle("d-none", active.length === 0);
                toggle.setAttribute("aria-label", active.length + " active processing job" + (active.length === 1 ? "" : "s"));
                toggle.classList.toggle("has-active-jobs", active.length > 0);
                connection.textContent = meta.stale ? "Reconnecting… showing last update" : "Updated from processing history";
                connection.classList.toggle("text-danger", meta.stale);
                renderList(activeList, active, "No active jobs.");
                renderList(recentList, recent, "No completed jobs yet.");
                document.dispatchEvent(new CustomEvent("tavidm:processing-jobs-updated", { detail: { snapshot: snapshot, stale: meta.stale } }));
            },
        });
        controller.start();
        return controller;
    }

    const api = {
        createController: createController,
        bootBrowser: bootBrowser,
        parseLiveMonitorTarget: parseLiveMonitorTarget,
        describeJobState: describeJobState,
        controller: null,
    };
    if (typeof document !== "undefined") {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", function () { api.controller = bootBrowser(); }, { once: true });
        } else {
            api.controller = bootBrowser();
        }
    }
    return api;
});
