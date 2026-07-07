/**
 * TAVIDM - Video Upload + Zone Annotation Wizard
 */

(function () {
    "use strict";

    const dropzone = document.getElementById("uploadDropzone");
    const fileInput = document.getElementById("videoFileInput");
    const btnBrowse = document.getElementById("btnBrowseVideo");
    const selectedWrap = document.getElementById("uploadSelected");
    const selectedName = document.getElementById("selectedFileName");
    const btnUpload = document.getElementById("btnUploadVideo");
    const btnCancel = document.getElementById("btnCancelUpload");
    const progressWrap = document.getElementById("uploadProgressWrap");
    const progressBar = document.getElementById("uploadProgressBar");
    const uploadCondition = document.getElementById("uploadCondition");
    const videoList = document.getElementById("videoList");

    const wizardModalEl = document.getElementById("annotationWizardModal");
    const wizardStepChoose = document.getElementById("wizardStepChoose");
    const wizardStepEditor = document.getElementById("wizardStepEditor");
    const wizardStepTemplatePrompt = document.getElementById("wizardStepTemplatePrompt");
    const wizardTemplateSelect = document.getElementById("wizardTemplateSelect");
    const zoneTypeTabs = document.getElementById("zoneTypeTabs");
    const zoneEditorCanvas = document.getElementById("zoneEditorCanvas");

    if (!dropzone || !fileInput) return;

    const zoneTypes = window.TAVIDM_ZONE_TYPES || [];
    const maxMb = parseInt(dropzone.dataset.maxMb || "500", 10);
    const maxBytes = maxMb * 1024 * 1024;

    let selectedFile = null;
    let activeXhr = null;
    let wizardModal = null;
    let zoneEditor = null;
    let wizardState = {
        video: null,
        frameUrl: null,
        templates: [],
        mode: "new",
        sourceTemplateId: null,
        pendingZones: null,
    };

    if (wizardModalEl && window.bootstrap) {
        wizardModal = new bootstrap.Modal(wizardModalEl);
    }

    function resetSelection() {
        selectedFile = null;
        fileInput.value = "";
        selectedWrap.classList.add("d-none");
        dropzone.classList.remove("d-none");
        progressWrap.classList.add("d-none");
        progressBar.style.width = "0%";
        btnUpload.disabled = false;
        btnCancel.disabled = true;
        btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
    }

    function validateClient(file) {
        if (!file) return "No file selected.";
        const name = file.name.toLowerCase();
        if (!name.endsWith(".mp4")) return "Only MP4 video files are allowed.";
        if (file.size > maxBytes) return "File exceeds maximum size of " + maxMb + " MB.";
        return null;
    }

    function selectFile(file) {
        const error = validateClient(file);
        if (error) {
            showToast("Upload Error", error, "danger");
            return;
        }
        selectedFile = file;
        selectedName.textContent = file.name + " (" + formatBytes(file.size) + ")";
        dropzone.classList.add("d-none");
        selectedWrap.classList.remove("d-none");
    }

    function formatBytes(bytes) {
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function showWizardStep(step) {
        wizardStepChoose.classList.toggle("d-none", step !== "choose");
        wizardStepEditor.classList.toggle("d-none", step !== "editor");
        wizardStepTemplatePrompt.classList.toggle("d-none", step !== "template");
    }

    function populateTemplateSelect(templates) {
        if (!wizardTemplateSelect) return;
        wizardTemplateSelect.innerHTML = '<option value="">— Select template —</option>';
        templates.forEach(function (t) {
            const opt = document.createElement("option");
            opt.value = t.id;
            opt.textContent = t.template_name + " (used " + (t.usage_count || 0) + "×)";
            wizardTemplateSelect.appendChild(opt);
        });
    }

    function buildZoneTabs() {
        if (!zoneTypeTabs) return;
        zoneTypeTabs.innerHTML = "";
        zoneTypes.forEach(function (zt, idx) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn btn-outline-secondary" + (idx === 0 ? " active" : "");
            btn.dataset.zoneKey = zt.key;
            btn.innerHTML = '<span class="zone-dot" style="background:' + zt.color + '"></span> ' + zt.label;
            btn.addEventListener("click", function () {
                zoneTypeTabs.querySelectorAll(".btn").forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                if (zoneEditor) zoneEditor.setActiveZone(zt.key);
            });
            zoneTypeTabs.appendChild(btn);
        });
    }

    function destroyEditor() {
        if (zoneEditor) {
            zoneEditor.destroy();
            zoneEditor = null;
        }
    }

    function initEditor(zones) {
        destroyEditor();
        if (!zoneEditorCanvas || !wizardState.frameUrl) return;
        zoneEditor = window.TAVIDMZoneEditor.create(zoneEditorCanvas, {
            imageUrl: wizardState.frameUrl + "?t=" + Date.now(),
            zoneTypes: zoneTypes,
            zones: zones,
            activeZone: zoneTypes[0] && zoneTypes[0].key,
        });
        buildZoneTabs();
    }

    function getInitialZones() {
        if (wizardState.mode === "template" && wizardState.sourceTemplateId) {
            const tpl = wizardState.templates.find(function (t) {
                return String(t.id) === String(wizardState.sourceTemplateId);
            });
            if (tpl) return window.TAVIDMZoneEditor.parseZones(tpl.zones_json, zoneTypes);
        }
        return window.TAVIDMZoneEditor.emptyZones(zoneTypes);
    }

    function openAnnotationWizard(payload) {
        wizardState.video = payload.video;
        wizardState.frameUrl = payload.frame_url;
        wizardState.templates = payload.templates || [];
        wizardState.mode = wizardState.templates.length ? "choose" : "new";
        wizardState.sourceTemplateId = null;

        populateTemplateSelect(wizardState.templates);
        document.getElementById("optionUseTemplate")?.classList.toggle("d-none", !wizardState.templates.length);
        document.getElementById("optionNewAnnotation")?.classList.add("active");
        document.getElementById("optionUseTemplate")?.classList.remove("active");
        wizardTemplateSelect.value = "";

        document.getElementById("templateNameFields")?.classList.add("d-none");
        document.getElementById("btnUpdateTemplate")?.classList.add("d-none");

        if (!payload.frame_ready) {
            showToast("Frame Warning", payload.frame_error || "Reference frame unavailable.", "warning");
        }

        showWizardStep("choose");
        wizardModal?.show();
    }

    function appendVideoToList(video) {
        if (!videoList) return;
        const btn = document.createElement("button");
        btn.className = "camera-item";
        btn.dataset.videoId = video.id;
        btn.dataset.videoName = video.name;
        btn.dataset.videoLocation = video.location;
        btn.dataset.videoCondition = video.condition;
        btn.dataset.videoDuration = video.duration;
        btn.dataset.videoThumbnail = video.thumbnail;
        btn.dataset.videoProcessed = video.processed ? "true" : "false";
        const statusLabel = video.has_annotation ? "Annotated" : "Needs zones";
        btn.innerHTML =
            '<div class="camera-thumb"><img src="/static/images/camera_thumb.svg" alt="">' +
            '<span class="cam-status-dot ' + (video.has_annotation ? "online" : "offline") + '"></span></div>' +
            '<div class="camera-info"><span class="camera-name">' + video.filename +
            ' <span class="upload-badge">NEW</span></span>' +
            '<span class="camera-location">' + video.condition + " · " + statusLabel + "</span></div>" +
            '<i class="bi bi-chevron-right"></i>';
        videoList.insertBefore(btn, videoList.firstChild);
        videoList.querySelectorAll(".camera-item").forEach(function (el) { el.classList.remove("active"); });
        btn.classList.add("active");
        btn.click();
    }

    function saveAnnotation(saveMode, extra) {
        const zones = wizardState.pendingZones || (zoneEditor ? zoneEditor.getZones() : {});
        if (!window.TAVIDMZoneEditor.zonesComplete(zones, zoneTypes)) {
            showToast("Incomplete Zones", "Each zone needs at least 3 points.", "warning");
            return;
        }

        const body = Object.assign({
            zones: zones,
            save_mode: saveMode,
            source_template_id: wizardState.sourceTemplateId,
        }, extra || {});

        const dbId = wizardState.video.db_id;
        fetch("/api/videos/" + dbId + "/annotation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (!payload.success) {
                    showToast("Save Error", payload.error || "Could not save annotation.", "danger");
                    return;
                }
                showToast("Annotation Saved", payload.message, "success");
                appendVideoToList(payload.video);
                wizardModal?.hide();
                resetSelection();
                destroyEditor();
            })
            .catch(function () {
                showToast("Save Error", "Network error while saving annotation.", "danger");
            });
    }

    document.getElementById("optionNewAnnotation")?.addEventListener("click", function () {
        wizardState.mode = "new";
        wizardState.sourceTemplateId = null;
        document.getElementById("optionNewAnnotation").classList.add("active");
        document.getElementById("optionUseTemplate")?.classList.remove("active");
    });

    document.getElementById("optionUseTemplate")?.addEventListener("click", function () {
        wizardState.mode = "template";
        document.getElementById("optionUseTemplate").classList.add("active");
        document.getElementById("optionNewAnnotation").classList.remove("active");
    });

    document.getElementById("btnWizardContinue")?.addEventListener("click", function () {
        if (wizardState.mode === "template") {
            wizardState.sourceTemplateId = wizardTemplateSelect.value;
            if (!wizardState.sourceTemplateId) {
                showToast("Select Template", "Choose a zone template to continue.", "warning");
                return;
            }
        } else {
            wizardState.sourceTemplateId = null;
        }
        initEditor(getInitialZones());
        showWizardStep("editor");
    });

    document.getElementById("btnWizardBack")?.addEventListener("click", function () {
        showWizardStep("choose");
    });

    document.getElementById("btnResetZone")?.addEventListener("click", function () {
        if (zoneEditor) zoneEditor.resetActiveZone();
    });

    document.getElementById("btnWizardSaveAnnotation")?.addEventListener("click", function () {
        if (!zoneEditor) return;
        wizardState.pendingZones = zoneEditor.getZones();
        if (!window.TAVIDMZoneEditor.zonesComplete(wizardState.pendingZones, zoneTypes)) {
            showToast("Incomplete Zones", "Each zone needs at least 3 points.", "warning");
            return;
        }
        const updateBtn = document.getElementById("btnUpdateTemplate");
        if (wizardState.sourceTemplateId) {
            updateBtn?.classList.remove("d-none");
        } else {
            updateBtn?.classList.add("d-none");
        }
        document.getElementById("templateNameFields")?.classList.add("d-none");
        showWizardStep("template");
    });

    document.getElementById("btnSaveVideoOnly")?.addEventListener("click", function () {
        saveAnnotation("video_only");
    });

    document.getElementById("btnSaveAsTemplate")?.addEventListener("click", function () {
        document.getElementById("templateNameFields")?.classList.remove("d-none");
    });

    document.getElementById("btnUpdateTemplate")?.addEventListener("click", function () {
        saveAnnotation("update_template", { template_id: wizardState.sourceTemplateId });
    });

    document.getElementById("btnConfirmTemplateSave")?.addEventListener("click", function () {
        const name = document.getElementById("wizardTemplateName")?.value.trim();
        if (!name) {
            showToast("Template Name", "Enter a template name.", "warning");
            return;
        }
        saveAnnotation("new_template", {
            template_name: name,
            template_description: document.getElementById("wizardTemplateDescription")?.value.trim(),
        });
    });

    wizardModalEl?.addEventListener("hidden.bs.modal", function () {
        destroyEditor();
    });

    function uploadFile() {
        if (!selectedFile || activeXhr) return;

        const formData = new FormData();
        formData.append("video", selectedFile);
        formData.append("condition", uploadCondition?.value || "peak");

        const xhr = new XMLHttpRequest();
        activeXhr = xhr;

        btnUpload.disabled = true;
        btnCancel.disabled = false;
        btnUpload.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Uploading...';
        progressWrap.classList.remove("d-none");

        xhr.upload.addEventListener("progress", function (e) {
            if (e.lengthComputable) {
                progressBar.style.width = Math.round((e.loaded / e.total) * 100) + "%";
            }
        });

        xhr.addEventListener("load", function () {
            activeXhr = null;
            btnCancel.disabled = true;
            let payload;
            try { payload = JSON.parse(xhr.responseText); } catch (err) {
                showToast("Upload Error", "Invalid server response.", "danger");
                btnUpload.disabled = false;
                btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
                return;
            }
            if (xhr.status >= 200 && xhr.status < 300 && payload.success) {
                showToast("Upload Complete", payload.message, "success");
                selectedWrap.classList.add("d-none");
                dropzone.classList.remove("d-none");
                progressWrap.classList.add("d-none");
                progressBar.style.width = "0%";
                btnUpload.disabled = false;
                btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
                selectedFile = null;
                fileInput.value = "";
                if (payload.requires_annotation) {
                    openAnnotationWizard(payload);
                } else {
                    appendVideoToList(payload.video);
                }
            } else {
                showToast("Upload Error", payload.error || "Upload failed.", "danger");
                btnUpload.disabled = false;
                btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
            }
        });

        xhr.addEventListener("error", function () {
            activeXhr = null;
            showToast("Upload Error", "Network error during upload.", "danger");
            btnUpload.disabled = false;
            btnCancel.disabled = true;
            btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
        });

        xhr.addEventListener("abort", function () {
            activeXhr = null;
            showToast("Upload Cancelled", "Upload was cancelled.", "info");
            btnUpload.disabled = false;
            btnCancel.disabled = true;
            btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
            progressWrap.classList.add("d-none");
            progressBar.style.width = "0%";
        });

        xhr.open("POST", "/api/upload-video");
        xhr.send(formData);
    }

    dropzone.addEventListener("click", function (e) {
        if (e.target.closest("#btnBrowseVideo") || e.target === dropzone) fileInput.click();
    });

    btnBrowse?.addEventListener("click", function (e) {
        e.stopPropagation();
        fileInput.click();
    });

    fileInput.addEventListener("change", function () {
        if (fileInput.files?.[0]) selectFile(fileInput.files[0]);
    });

    ["dragenter", "dragover"].forEach(function (evt) {
        dropzone.addEventListener(evt, function (e) {
            e.preventDefault();
            dropzone.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(function (evt) {
        dropzone.addEventListener(evt, function (e) {
            e.preventDefault();
            dropzone.classList.remove("dragover");
        });
    });

    dropzone.addEventListener("drop", function (e) {
        const file = e.dataTransfer?.files?.[0];
        if (file) selectFile(file);
    });

    btnUpload?.addEventListener("click", uploadFile);

    btnCancel?.addEventListener("click", function () {
        if (activeXhr) activeXhr.abort();
        else resetSelection();
    });

    window.TAVIDM_upload = { resetSelection: resetSelection, openAnnotationWizard: openAnnotationWizard };
})();
