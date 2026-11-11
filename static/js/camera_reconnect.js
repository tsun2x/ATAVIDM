/* Bounded, passive reattachment after a camera worker is replaced. */
(function (global) {
    "use strict";

    function create(maxAttempts) {
        var pending = false;
        var attempts = 0;
        maxAttempts = Math.max(1, Number(maxAttempts) || 24);

        return {
            begin: function () {
                pending = true;
                attempts = 0;
            },
            reset: function () {
                pending = false;
                attempts = 0;
            },
            observe: function (status, running) {
                if (status === "stopping" && !pending) {
                    pending = true;
                    attempts = 0;
                }
                if (!pending) return "idle";
                if (status === "error") {
                    pending = false;
                    return "error";
                }
                if (status === "stopped") {
                    pending = false;
                    attempts = 0;
                    return "stopped";
                }
                if (status === "running" && running) {
                    pending = false;
                    attempts = 0;
                    return "reconnect";
                }
                attempts += 1;
                if (attempts >= maxAttempts) {
                    pending = false;
                    return "timeout";
                }
                return "waiting";
            },
            isPending: function () { return pending; },
        };
    }

    global.TAVIDMCameraReconnect = { create: create };
})(typeof window !== "undefined" ? window : global);
