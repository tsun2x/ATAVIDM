/* Bounded, passive reattachment after a camera worker is replaced. */
(function (global) {
    "use strict";

    function create(maxAttempts) {
        var pending = false;
        var attempts = 0;
        var attachmentRequested = false;
        maxAttempts = Math.max(1, Number(maxAttempts) || 24);

        return {
            begin: function () {
                pending = true;
                attempts = 0;
                attachmentRequested = false;
            },
            reset: function () {
                pending = false;
                attempts = 0;
                attachmentRequested = false;
            },
            observe: function (status, running, requestAttachment) {
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
                if (status === "live" && running) {
                    if (!attachmentRequested) {
                        attachmentRequested = true;
                        if (typeof requestAttachment === "function") requestAttachment();
                        return "attaching";
                    }
                } else if (status !== "starting" && status !== "reconnecting" && status !== "stopping") {
                    attachmentRequested = false;
                }
                attempts += 1;
                if (attempts >= maxAttempts) {
                    pending = false;
                    attachmentRequested = false;
                    return "timeout";
                }
                return "waiting";
            },
            imageLoaded: function () {
                if (!pending || !attachmentRequested) return false;
                pending = false;
                attempts = 0;
                attachmentRequested = false;
                return true;
            },
            imageFailed: function () {
                if (!pending || !attachmentRequested) return "idle";
                attachmentRequested = false;
                attempts += 1;
                if (attempts >= maxAttempts) {
                    pending = false;
                    return "timeout";
                }
                return "retry";
            },
            isPending: function () { return pending; },
        };
    }

    global.TAVIDMCameraReconnect = { create: create };
})(typeof window !== "undefined" ? window : global);
