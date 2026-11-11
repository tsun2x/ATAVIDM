/* DOM-facing adapter for reattaching the live monitor to a replacement camera worker. */
(function (global) {
    "use strict";

    function create(controller, options) {
        var pendingImageSrc = null;
        options = options || {};

        function setStatus(message) {
            if (typeof options.setStatus === "function") options.setStatus(message);
        }

        function observe(camera, status, running) {
            if (!camera) return "idle";
            var result = controller.observe(status, running, function () {
                pendingImageSrc = camera.stream_url + "?reconnect=" + Date.now();
                if (typeof options.showImage === "function") options.showImage(pendingImageSrc);
                setStatus("Updated camera worker is live; attaching to its stream…");
            });
            if (result === "timeout") {
                setStatus("Camera restart is taking too long. Select the camera again to retry.");
            } else if (result === "stopped") {
                setStatus("Camera stream was stopped during restart. Select the camera again to start it.");
            } else if (result === "error") {
                setStatus("Camera restart failed. Check the stream status before retrying.");
            }
            return result;
        }

        return {
            begin: function (camera, status, running) {
                controller.begin();
                return observe(camera, status, running);
            },
            observe: observe,
            imageLoaded: function (src) {
                if (!pendingImageSrc || src !== pendingImageSrc) return false;
                pendingImageSrc = null;
                if (!controller.imageLoaded()) return false;
                setStatus("Reconnected to the updated camera stream.");
                return true;
            },
            imageFailed: function (src) {
                if (!pendingImageSrc || src !== pendingImageSrc) return "idle";
                pendingImageSrc = null;
                var result = controller.imageFailed();
                if (result === "retry") setStatus("The new stream did not attach yet; retrying while the camera is live…");
                if (result === "timeout") setStatus("Could not attach to the restarted camera stream. Select the camera again to retry.");
                return result;
            },
            reset: function () {
                pendingImageSrc = null;
                controller.reset();
            },
        };
    }

    global.TAVIDMLiveCameraReconnectUI = { create: create };
})(typeof window !== "undefined" ? window : global);
