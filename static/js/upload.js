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
    const uploadRecordedAt = document.getElementById("uploadRecordedAt");
    const videoList = document.getElementById("videoList");

    const wizardModalEl = document.getElementById("annotationWizardModal");
    const wizardStepChoose = document.getElementById("wizardStepChoose");
    const wizardStepEditor = document.getElementById("wizardStepEditor");
    const wizardStepTemplatePrompt = document.getElementById("wizardStepTemplatePrompt");
    const wizardTemplateSelect = document.getElementById("wizardTemplateSelect");
    const zoneTypeTabs = document.getElementById("zoneTypeTabs");
    const sceneKindTabs = document.getElementById("sceneKindTabs");
    const zoneEditorCanvas = document.getElementById("zoneEditorCanvas");
    const sceneLaneSelect = document.getElementById("sceneLaneSelect");
    const sceneProhibitedFrom = document.getElementById("sceneProhibitedFrom");
    const sceneSelectedLabel = document.getElementById("sceneSelectedLabel");

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
        pendingScene: null,
        existingScene: null,
        startAtEditor: false,
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

    function currentTypeOptions(kind) {
        if (kind === "zones") {
            return (zoneTypes || []).filter(function (zt) { return zt.key !== "active_lane"; });
        }
        const kinds = (window.TAVIDMZoneEditor && window.TAVIDMZoneEditor.SCENE_KINDS) || [];
        const def = kinds.find(function (k) { return k.key === kind; });
        return (def && def.types) || [];
    }

    function refreshSceneFields() {
        if (!zoneEditor) return;
        const kind = zoneEditor.objectKind;
        const selected = zoneEditor.selectedObject && zoneEditor.selectedObject();
        if (sceneSelectedLabel) {
            sceneSelectedLabel.textContent = selected
                ? ("Selected: " + selected.id + " (" + selected.type + ")")
                : "No object selected";
        }
        if (sceneLaneSelect) {
            const needsLane = kind === "flow_arrows" || kind === "threshold_lines" || kind === "signs" || kind === "markings";
            sceneLaneSelect.classList.toggle("d-none", !needsLane);
            sceneLaneSelect.innerHTML = '<option value="">— Apply to lane —</option>';
            (zoneEditor.scene.objects.lanes || []).forEach(function (lane) {
                const opt = document.createElement("option");
                opt.value = lane.id;
                opt.textContent = lane.id;
                sceneLaneSelect.appendChild(opt);
            });
            if (selected && selected.lane_ids && selected.lane_ids[0]) {
                sceneLaneSelect.value = selected.lane_ids[0];
            }
        }
        if (sceneProhibitedFrom) {
            const showPf = kind === "markings";
            sceneProhibitedFrom.classList.toggle("d-none", !showPf);
            if (selected && selected.prohibited_from) sceneProhibitedFrom.value = selected.prohibited_from;
        }
    }

    function buildKindTabs() {
        if (!sceneKindTabs || !window.TAVIDMZoneEditor) return;
        sceneKindTabs.innerHTML = "";
        const kinds = window.TAVIDMZoneEditor.SCENE_KINDS || [];
        const activeKind = zoneEditor ? zoneEditor.objectKind : "zones";
        kinds.forEach(function (kind) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn btn-outline-secondary" + (kind.key === activeKind ? " active" : "");
            btn.dataset.kind = kind.key;
            btn.textContent = kind.label;
            btn.addEventListener("click", function () {
                sceneKindTabs.querySelectorAll(".btn").forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                if (zoneEditor) zoneEditor.setObjectKind(kind.key);
                buildTypeTabs();
                refreshSceneFields();
            });
            sceneKindTabs.appendChild(btn);
        });
    }

    function buildTypeTabs() {
        if (!zoneTypeTabs) return;
        zoneTypeTabs.innerHTML = "";
        const kind = zoneEditor ? zoneEditor.objectKind : "zones";
        const types = currentTypeOptions(kind);
        const activeType = zoneEditor ? zoneEditor.objectType : (types[0] && types[0].key);
        types.forEach(function (zt, idx) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn btn-outline-secondary" + (zt.key === activeType || (!activeType && idx === 0) ? " active" : "");
            btn.dataset.zoneKey = zt.key;
            btn.innerHTML = '<span class="zone-dot" style="background:' + zt.color + '"></span> ' + zt.label;
            btn.addEventListener("click", function () {
                zoneTypeTabs.querySelectorAll(".btn").forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                if (zoneEditor) {
                    if (kind === "zones") zoneEditor.setActiveZone(zt.key);
                    else zoneEditor.setObjectKind(kind, zt.key);
                }
                refreshSceneFields();
            });
            zoneTypeTabs.appendChild(btn);
        });
    }

    function buildZoneTabs() {
        buildKindTabs();
        buildTypeTabs();
        refreshSceneFields();
    }

    function destroyEditor() {
        if (zoneEditor) {
            zoneEditor.destroy();
            zoneEditor = null;
        }
    }

    function initEditor(sceneDoc) {
        destroyEditor();
        if (!zoneEditorCanvas || !wizardState.frameUrl) return;
        zoneEditor = window.TAVIDMZoneEditor.create(zoneEditorCanvas, {
            imageUrl: wizardState.frameUrl + "?t=" + Date.now(),
            zoneTypes: zoneTypes,
            sceneDocument: sceneDoc || window.TAVIDMZoneEditor.emptySceneV2(),
            activeZone: zoneTypes[0] && zoneTypes[0].key,
            onChange: refreshSceneFields,
        });
        buildZoneTabs();
    }

    function getInitialScene() {
        if (wizardState.existingScene) {
            return window.TAVIDMZoneEditor.parseSceneDocument(wizardState.existingScene, zoneTypes);
        }
        if (wizardState.mode === "template" && wizardState.sourceTemplateId) {
            const tpl = wizardState.templates.find(function (t) {
                return String(t.id) === String(wizardState.sourceTemplateId);
            });
            if (tpl) return window.TAVIDMZoneEditor.parseSceneDocument(tpl.zones_json, zoneTypes);
        }
        return window.TAVIDMZoneEditor.emptySceneV2();
    }

    function openAnnotationWizard(payload) {
        wizardState.video = payload.video;
        wizardState.frameUrl = payload.frame_url;
        wizardState.templates = payload.templates || [];
        wizardState.mode = wizardState.templates.length ? "choose" : "new";
        wizardState.sourceTemplateId = null;
        wizardState.existingScene = payload.existing_scene || null;
        wizardState.startAtEditor = !!payload.start_at_editor;

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
        if (wizardState.startAtEditor) {
            initEditor(getInitialScene());
            showWizardStep("editor");
        }
    }

    function appendVideoToList(video) {
        if (!videoList) return;
        const empty = videoList.querySelector(".text-center.text-muted");
        if (empty) empty.remove();

        const row = document.createElement("div");
        row.className = "camera-item-row d-flex align-items-stretch";
        const ready = !!(video.has_annotation && (video.status === "ready" || video.status === "processed"));
        const check = document.createElement("label");
        check.className = "bulk-check-wrap px-2 d-flex align-items-center mb-0";
        check.innerHTML =
            '<input type="checkbox" class="form-check-input bulk-video-check" value="' + video.db_id + '"' +
            ' data-ready="' + (ready ? "1" : "0") + '"' +
            (ready ? "" : " disabled") +
            ' aria-label="Select ' + (video.filename || "video") + '">';

        const btn = document.createElement("button");
        btn.className = "camera-item flex-grow-1";
        btn.dataset.video = JSON.stringify(video);
        const statusLabel = video.status || (video.has_annotation ? "ready" : "annotating");
        btn.innerHTML =
            '<div class="camera-thumb"><img src="/static/images/camera_thumb.svg" alt="">' +
            '<span class="cam-status-dot ' + (video.processed ? "online" : "offline") + '"></span></div>' +
            '<div class="camera-info"><span class="camera-name">' + video.filename +
            ' <span class="upload-badge">NEW</span></span>' +
            '<span class="camera-location video-status-label" data-db-id="' + video.db_id + '">' +
            (video.condition || "peak") + " · " + statusLabel + "</span></div>" +
            '<i class="bi bi-chevron-right"></i>';

        row.appendChild(check);
        row.appendChild(btn);
        videoList.insertBefore(row, videoList.firstChild);
        videoList.querySelectorAll(".camera-item").forEach(function (el) { el.classList.remove("active"); });
        btn.classList.add("active");
        btn.click();
    }

    function setUploadStage(label) {
        const statusEl = document.getElementById("uploadStageStatus");
        if (statusEl) statusEl.textContent = label;
        else if (progressWrap) {
            let stage = document.getElementById("uploadStageInline");
            if (!stage) {
                stage = document.createElement("div");
                stage.id = "uploadStageInline";
                stage.className = "small text-muted mb-2";
                progressWrap.parentNode?.insertBefore(stage, progressWrap);
            }
            stage.textContent = label;
        }
    }

    function saveAnnotation(saveMode, extra) {
        const scene = wizardState.pendingScene
            || (zoneEditor ? zoneEditor.getSceneDocument({ completeOnly: true }) : window.TAVIDMZoneEditor.emptySceneV2());
        if (!window.TAVIDMZoneEditor.sceneSaveReady(scene, zoneTypes)) {
            showToast("Incomplete Zones", "Draw at least one zone or lane; each drawn polygon needs at least 3 points.", "warning");
            return;
        }
        const check = window.TAVIDMZoneEditor.validateSceneDocument(scene);
        if (!check.ok) {
            showToast("Invalid Scene", check.error || "Scene document is invalid.", "warning");
            return;
        }

        const body = Object.assign({
            zones: scene,
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
        initEditor(getInitialScene());
        showWizardStep("editor");
    });

    document.getElementById("btnWizardBack")?.addEventListener("click", function () {
        showWizardStep("choose");
    });

    document.getElementById("btnResetZone")?.addEventListener("click", function () {
        if (zoneEditor) zoneEditor.resetActiveZone();
        refreshSceneFields();
    });

    document.getElementById("btnNewSceneObject")?.addEventListener("click", function () {
        if (zoneEditor) zoneEditor.newObject();
        refreshSceneFields();
    });

    document.getElementById("btnDeleteSceneObject")?.addEventListener("click", function () {
        if (zoneEditor) zoneEditor.deleteSelected();
        refreshSceneFields();
    });

    sceneLaneSelect?.addEventListener("change", function () {
        const selected = zoneEditor && zoneEditor.selectedObject && zoneEditor.selectedObject();
        if (!selected || !sceneLaneSelect.value) return;
        selected.lane_ids = [sceneLaneSelect.value];
        if (zoneEditor) zoneEditor.draw();
    });

    sceneProhibitedFrom?.addEventListener("change", function () {
        if (!zoneEditor) return;
        zoneEditor.prohibitedFrom = sceneProhibitedFrom.value;
        const selected = zoneEditor.selectedObject && zoneEditor.selectedObject();
        if (selected) selected.prohibited_from = sceneProhibitedFrom.value;
        zoneEditor.draw();
    });

    document.getElementById("btnWizardSaveAnnotation")?.addEventListener("click", function () {
        if (!zoneEditor) return;
        wizardState.pendingScene = zoneEditor.getSceneDocument({ completeOnly: true });
        if (!window.TAVIDMZoneEditor.sceneSaveReady(wizardState.pendingScene, zoneTypes)) {
            showToast("Incomplete Zones", "Draw at least one zone or lane; each drawn polygon needs at least 3 points.", "warning");
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
        const recordedRaw = (uploadRecordedAt?.value || "").trim();
        if (recordedRaw) {
            // datetime-local is YYYY-MM-DDTHH:MM[:SS] — normalize to space-separated.
            formData.append("recorded_at", recordedRaw.replace("T", " "));
        }

        const xhr = new XMLHttpRequest();
        activeXhr = xhr;

        btnUpload.disabled = true;
        btnCancel.disabled = false;
        btnUpload.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Uploading...';
        progressWrap.classList.remove("d-none");
        setUploadStage("Preparing");

        xhr.upload.addEventListener("progress", function (e) {
            if (e.lengthComputable) {
                progressBar.style.width = Math.round((e.loaded / e.total) * 100) + "%";
            }
            setUploadStage("Uploading / saving");
        });

        xhr.upload.addEventListener("load", function () {
            // Bytes transferred; server is now saving/extracting before HTTP response.
            setUploadStage("Extracting metadata");
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
                if (payload.requires_annotation) {
                    setUploadStage(payload.frame_ready
                        ? "Extracting reference frame · Awaiting zone annotation"
                        : "Awaiting zone annotation");
                } else {
                    setUploadStage("Completed");
                }
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
                setUploadStage("Failed");
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
