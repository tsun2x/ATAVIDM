/**
 * TAVIDM - Settings Page
 */

(function () {
    "use strict";

    const sliders = [
        { input: "confidenceThreshold", display: "confidenceValue", suffix: "%" },
        { input: "speedThreshold", display: "speedValue", suffix: " km/h" },
        { input: "redLightSensitivity", display: "redLightValue", suffix: "%" },
    ];

    sliders.forEach(function (s) {
        const input = document.getElementById(s.input);
        const display = document.getElementById(s.display);
        if (!input || !display) return;

        input.addEventListener("input", function () {
            display.textContent = this.value + s.suffix;
        });
    });

    document.getElementById("btnSaveSettings")?.addEventListener("click", function () {
        showToast("Settings Saved", "System preferences have been saved (demo — no backend).", "success");
    });

    document.getElementById("btnAddCamera")?.addEventListener("click", function () {
        showToast("Add Camera", "Camera registration form would open here (demo).", "info");
    });

    document.getElementById("btnAddUser")?.addEventListener("click", function () {
        showToast("Add User", "User creation form would open here (demo).", "info");
    });

    document.querySelectorAll(".btn-edit-camera").forEach(function (btn) {
        btn.addEventListener("click", function () {
            showToast("Edit Camera", "Editing camera " + this.dataset.cameraId + " (demo).", "info");
        });
    });

    document.querySelectorAll(".btn-edit-user").forEach(function (btn) {
        btn.addEventListener("click", function () {
            showToast("Edit User", "Editing user #" + this.dataset.userId + " (demo).", "info");
        });
    });
})();
