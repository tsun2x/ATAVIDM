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
    const uploadRecordedDate = document.getElementById("uploadRecordedDate");
    const uploadRecordedTime = document.getElementById("uploadRecordedTime");
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
    const sceneObjectLegend = document.getElementById("sceneObjectLegend");
    const sceneObjectLegendList = document.getElementById("sceneObjectLegendList");
    const sceneEditorLayout = document.getElementById("sceneEditorLayout");
    const sceneTemplateCopy = document.getElementById("sceneTemplateCopy");
    const sceneTemplateSelect = document.getElementById("sceneTemplateSelect");
    const btnApplySceneTemplate = document.getElementById("btnApplySceneTemplate");
    const btnSaveCameraAsTemplate = document.getElementById("btnSaveCameraAsTemplate");

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
        baseScene: null,
        draftStorageWarned: false,
        startAtEditor: false,
    };

    function draftIdentity() {
        if (wizardState.camera) return { type: "camera", id: wizardState.camera.id };
        if (wizardState.video && wizardState.video.db_id != null) return { type: "video", id: wizardState.video.db_id };
        return null;
    }

    function draftBaseScene() {
        return wizardState.baseScene || window.TAVIDMZoneEditor.emptySceneV2();
    }

    function readCurrentDraft() {
        const identity = draftIdentity();
        if (!identity || !window.TAVIDMAnnotationDrafts) return { ok: true, draft: null };
        const storage = getDraftStorage();
        if (!storage) return { ok: false, draft: null };
        const result = window.TAVIDMAnnotationDrafts.read(storage, window.TAVIDM_USER_ID, identity.type, identity.id);
        if (result.draft || !result.ok || identity.type !== "video") return result;
        try {
            const legacy = storage.getItem("tavidm.annotation-draft." + String(identity.id));
            if (!legacy) return result;
            const scene = JSON.parse(legacy);
            return scene && typeof scene === "object"
                ? { ok: true, draft: { version: 2, scene: scene, base_scene: null, updated_at: null }, legacy: true }
                : result;
        } catch (err) {
            return { ok: false, draft: null };
        }
    }

    function getDraftStorage() {
        try { return window.localStorage; } catch (err) { return null; }
    }

    function saveAnnotationDraft() {
        if (!zoneEditor || !window.TAVIDMAnnotationDrafts) return;
        const identity = draftIdentity();
        if (!identity) return;
        const storage = getDraftStorage();
        const result = storage && window.TAVIDMAnnotationDrafts.write(
            storage, window.TAVIDM_USER_ID, identity.type, identity.id,
            zoneEditor.getSceneDocument(), draftBaseScene()
        );
        if ((!result || !result.ok) && !wizardState.draftStorageWarned) {
            wizardState.draftStorageWarned = true;
            showToast("Draft Recovery Unavailable", "Browser storage is unavailable or full; unfinished annotation edits may not survive navigation.", "warning");
        }
    }

    function clearAnnotationDraft() {
        const identity = draftIdentity();
        if (!identity || !window.TAVIDMAnnotationDrafts) return;
        const storage = getDraftStorage();
        if (storage) {
            window.TAVIDMAnnotationDrafts.remove(storage, window.TAVIDM_USER_ID, identity.type, identity.id);
            if (identity.type === "video") {
                try { storage.removeItem("tavidm.annotation-draft." + String(identity.id)); } catch (err) { /* Keep the saved scene authoritative. */ }
            }
        }
    }

    function sceneKey(scene) {
        return JSON.stringify(window.TAVIDMZoneEditor.parseSceneDocument(scene, zoneTypes));
    }

    function recoverDraftOrDiscard(draft, sourceLabel, serverScene) {
        if (!draft) return { recovered: false, scene: serverScene || null };
        const changedOnServer = window.TAVIDMAnnotationDrafts.isStale(draft, serverScene, sceneKey);
        const staleWarning = changedOnServer
            ? " The saved scene has changed since this draft was made. Recovering it will replace the newer scene when you save."
            : "";
        const updated = draft.updated_at ? " (saved " + new Date(draft.updated_at).toLocaleString() + ")" : "";
        const recover = window.confirm(
            "An unfinished " + sourceLabel + " annotation draft exists" + updated + ". Recover this draft?" + staleWarning +
            "\n\nChoose Cancel to discard the draft and use the current saved scene."
        );
        if (recover) return { recovered: true, scene: draft.scene };
        clearAnnotationDraft();
        return { recovered: false, scene: serverScene || null };
    }

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
                ? ((zoneEditor.isEditingSelected ? "Editing: " : "Drawing: ") + selected.id + " (" + selected.type + ")")
                : "Choose a type, then draw on the frame";
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
        refreshSceneLegend();
    }

    function refreshSceneLegend() {
        if (!zoneEditor || !sceneObjectLegend || !sceneObjectLegendList) return;
        const scene = zoneEditor.scene.objects;
        const kinds = window.TAVIDMZoneEditor.SCENE_KINDS || [];
        const lanes = scene.lanes || [];
        const entries = [];
        Object.keys(scene).forEach(function (kindKey) {
            const kind = kinds.find(function (item) { return item.key === kindKey; });
            (scene[kindKey] || []).forEach(function (object, index) {
                let label = object.type;
                if (kindKey === "lanes") {
                    const match = String(object.id).match(/(?:lane|lanes)[-_]?(\d+)$/i);
                    label = "Lane " + (match ? match[1] : index + 1);
                } else if (kindKey === "zones") {
                    const zoneType = zoneTypes.find(function (item) { return item.key === object.type; });
                    label = zoneType ? zoneType.label : object.type;
                } else {
                    const type = kind && (kind.types || []).find(function (item) { return item.key === object.type; });
                    label = type ? type.label : object.type;
                }
                const laneIds = Array.isArray(object.lane_ids) ? object.lane_ids : [];
                const laneLabels = laneIds.map(function (laneId) {
                    const laneIndex = lanes.findIndex(function (lane) { return lane.id === laneId; });
                    const lane = laneIndex >= 0 ? lanes[laneIndex] : null;
                    const match = String(laneId).match(/(?:lane|lanes)[-_]?(\d+)$/i);
                    return lane ? ("Lane " + (match ? match[1] : laneIndex + 1)) : laneId;
                });
                const detail = kindKey === "lanes"
                    ? "Approach lane"
                    : (laneLabels.length ? laneLabels.join(", ") : "No lane linked");
                const type = kindKey === "zones"
                    ? zoneTypes.find(function (item) { return item.key === object.type; })
                    : kind && (kind.types || []).find(function (item) { return item.key === object.type; });
                const color = type ? type.color : (kind && kind.color) || "#64748b";
                entries.push({ id: object.id, label: label, detail: detail, color: color });
            });
        });

        sceneObjectLegend.classList.toggle("d-none", entries.length === 0);
        sceneEditorLayout?.classList.toggle("has-annotations", entries.length > 0);
        sceneObjectLegendList.replaceChildren();
        entries.forEach(function (entry) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "scene-object-legend-item" + (zoneEditor.scene.selectedId === entry.id ? " active" : "");
            button.setAttribute("aria-pressed", zoneEditor.scene.selectedId === entry.id ? "true" : "false");
            button.setAttribute("aria-label", entry.label + ", " + entry.detail + ". Select to edit points.");
            const swatch = document.createElement("span");
            swatch.className = "scene-object-legend-swatch";
            swatch.style.backgroundColor = entry.color;
            swatch.setAttribute("aria-hidden", "true");
            const title = document.createElement("span");
            title.textContent = entry.label;
            const detail = document.createElement("span");
            detail.className = "scene-object-legend-detail";
            detail.textContent = entry.detail;
            button.append(swatch, title, detail);
            button.addEventListener("mouseenter", function () { zoneEditor.setHoveredObject(entry.id); });
            button.addEventListener("mouseleave", function () { zoneEditor.setHoveredObject(null); });
            button.addEventListener("focus", function () { zoneEditor.setHoveredObject(entry.id); });
            button.addEventListener("blur", function () { zoneEditor.setHoveredObject(null); });
            button.addEventListener("click", function () {
                if (!zoneEditor.selectObjectForEditing(entry.id)) return;
                buildZoneTabs();
            });
            sceneObjectLegendList.appendChild(button);
        });
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
            imageUrl: wizardState.camera ? wizardState.frameUrl : wizardState.frameUrl + "?t=" + Date.now(),
            zoneTypes: zoneTypes,
            sceneDocument: sceneDoc || window.TAVIDMZoneEditor.emptySceneV2(),
            activeZone: zoneTypes[0] && zoneTypes[0].key,
            onChange: function () {
                refreshSceneFields();
                saveAnnotationDraft();
            },
        });
        buildZoneTabs();
        populateSceneTemplateCopy();
    }

    function populateSceneTemplateCopy() {
        if (!sceneTemplateCopy || !sceneTemplateSelect) return;
        sceneTemplateSelect.innerHTML = '<option value="">— Select template —</option>';
        (wizardState.templates || []).forEach(function (template) {
            const option = document.createElement("option");
            option.value = String(template.id);
            option.textContent = template.template_name;
            sceneTemplateSelect.appendChild(option);
        });
        sceneTemplateCopy.hidden = !(wizardState.templates || []).length;
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
        wizardState.camera = payload.camera || null;
        wizardState.onCameraSaved = payload.on_camera_saved || null;
        wizardState.pendingScene = null;
        wizardState.video = payload.video;
        wizardState.frameUrl = payload.frame_url;
        wizardState.templates = payload.templates || (wizardState.camera ? window.TAVIDM_ZONE_TEMPLATES : []) || [];
        wizardState.mode = wizardState.templates.length ? "choose" : "new";
        wizardState.sourceTemplateId = null;
        wizardState.baseScene = payload.existing_scene || null;
        wizardState.draftStorageWarned = false;
        const draftResult = readCurrentDraft();
        if (!draftResult.ok) {
            wizardState.draftStorageWarned = true;
            showToast("Draft Recovery Unavailable", "Browser storage could not be read; only the saved scene will be opened.", "warning");
        }
        const recovered = recoverDraftOrDiscard(
            draftResult.draft,
            wizardState.camera ? "camera" : "video",
            wizardState.baseScene
        );
        wizardState.existingScene = recovered.scene;
        wizardState.startAtEditor = !!payload.start_at_editor || recovered.recovered;

        populateTemplateSelect(wizardState.templates);
        document.getElementById("optionUseTemplate")?.classList.toggle("d-none", !wizardState.templates.length);
        document.getElementById("optionNewAnnotation")?.classList.add("active");
        document.getElementById("optionUseTemplate")?.classList.remove("active");
        wizardTemplateSelect.value = "";
        document.getElementById("btnWizardBack")?.classList.toggle("d-none", !!wizardState.camera);
        btnSaveCameraAsTemplate?.classList.toggle("d-none", !wizardState.camera || window.TAVIDM_USER_ROLE !== "admin");

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

        const existingCheckbox = videoList.querySelector('.bulk-video-check[value="' + video.db_id + '"]');
        const existingRow = existingCheckbox && existingCheckbox.closest(".camera-item-row");
        if (existingRow) {
            const existingButton = existingRow.querySelector(".camera-item");
            const statusLabel = existingRow.querySelector(".video-status-label");
            const ready = !!(video.has_annotation && (video.status === "ready" || video.status === "processed"));
            existingCheckbox.disabled = !ready;
            existingCheckbox.checked = false;
            existingCheckbox.setAttribute("data-ready", ready ? "1" : "0");
            if (existingButton) existingButton.dataset.video = JSON.stringify(video);
            if (statusLabel) {
                const condition = video.condition || "peak";
                statusLabel.textContent = condition.charAt(0).toUpperCase() + condition.slice(1) +
                    " · " + (video.status || (video.has_annotation ? "ready" : "annotating"));
            }
            existingRow.querySelector(".cam-status-dot")?.classList.toggle("online", !!video.processed);
            existingRow.querySelector(".cam-status-dot")?.classList.toggle("offline", !video.processed);
            existingButton?.click();
            return;
        }

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

    function waitForUploadedFrame(payload, attempts) {
        const videoId = payload.video && payload.video.db_id;
        if (!videoId) return;
        fetch("/api/videos/" + videoId + "/frame-status", { credentials: "same-origin" })
            .then(function (response) {
                return response.json().then(function (body) {
                    return { ok: response.ok, body: body };
                });
            })
            .then(function (result) {
                const status = result.body;
                if (!result.ok || !status.success) {
                    showToast("Preview Status", "The video was uploaded, but its preview status could not be checked. Check Uploaded Videos before retrying.", "warning");
                    return;
                }
                if (status.state === "ready") {
                    payload.frame_ready = true;
                    payload.frame_pending = false;
                    payload.video = status.video || payload.video;
                    const active = videoList?.querySelector(".camera-item.active[data-video]");
                    let activeId = null;
                    try { activeId = JSON.parse(active?.dataset.video || "{}").db_id; } catch (err) { /* keep selection */ }
                    if (Number(activeId) === Number(videoId)) {
                        appendVideoToList(payload.video);
                        openAnnotationWizard(payload);
                    } else {
                        showToast("Preview Ready", "The upload is saved. Select it in Uploaded Videos to annotate.", "success");
                    }
                    return;
                }
                if (status.state === "failed") {
                    showToast("Upload Saved; Preview Failed", status.error || "The video was saved, but its reference frame could not be prepared.", "danger");
                    return;
                }
                if (attempts >= 120) {
                    showToast("Upload Saved; Preview Still Preparing", "The video is saved. Its reference frame is taking longer than expected; check Uploaded Videos before retrying.", "warning");
                    return;
                }
                window.setTimeout(function () { waitForUploadedFrame(payload, attempts + 1); }, 1500);
            })
            .catch(function () {
                showToast("Upload Saved; Preview Status Unknown", "The video is saved. Check Uploaded Videos before retrying.", "warning");
            });
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
                clearAnnotationDraft();
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
        if (zoneEditor) {
            zoneEditor.draw();
            refreshSceneLegend();
            saveAnnotationDraft();
        }
    });

    sceneProhibitedFrom?.addEventListener("change", function () {
        if (!zoneEditor) return;
        zoneEditor.prohibitedFrom = sceneProhibitedFrom.value;
        const selected = zoneEditor.selectedObject && zoneEditor.selectedObject();
        if (selected) selected.prohibited_from = sceneProhibitedFrom.value;
        zoneEditor.draw();
        saveAnnotationDraft();
    });

    document.getElementById("btnWizardSaveAnnotation")?.addEventListener("click", function () {
        if (!zoneEditor) return;
        wizardState.pendingScene = zoneEditor.getSceneDocument({ completeOnly: true });
        if (!window.TAVIDMZoneEditor.sceneSaveReady(wizardState.pendingScene, zoneTypes)) {
            showToast("Incomplete Zones", "Draw at least one zone or lane; each drawn polygon needs at least 3 points.", "warning");
            return;
        }
        if (wizardState.camera) {
            const check = window.TAVIDMZoneEditor.validateSceneDocument(wizardState.pendingScene);
            if (!check.ok) {
                showToast("Invalid Scene", check.error || "Scene document is invalid.", "warning");
                return;
            }
            saveCameraScene(wizardState.pendingScene, false);
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

    function saveCameraScene(scene, alsoCreateTemplate) {
        const saveButton = document.getElementById("btnWizardSaveAnnotation");
        const templateButton = btnSaveCameraAsTemplate;
        if (saveButton) saveButton.disabled = true;
        if (templateButton) templateButton.disabled = true;
        const cameraId = wizardState.camera.id;
        const templateName = alsoCreateTemplate
            ? window.prompt("Name this reusable copy of the camera scene:")
            : null;
        if (alsoCreateTemplate && !templateName?.trim()) {
            if (saveButton) saveButton.disabled = false;
            if (templateButton) templateButton.disabled = false;
            return;
        }
        const createTemplate = alsoCreateTemplate
            ? fetch("/api/zone-templates", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ template_name: templateName.trim(), zones_json: scene }),
            }).then(function (response) {
                return response.json().then(function (body) {
                    if (!response.ok || !body.success) throw new Error(body.error || "Could not create the template copy.");
                    return body.template;
                });
            })
            : Promise.resolve(null);
        let templateCreated = false;
        createTemplate.then(function () {
            templateCreated = alsoCreateTemplate;
            return fetch("/api/cameras/" + cameraId, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ zones_json: scene, expected_zones_json: wizardState.baseScene || {} }),
            });
        }).then(function (response) {
            if (!response) return null;
            return response.json().then(function (body) {
                if (!response.ok || !body.success) {
                    const error = new Error(body.error || "Could not save camera annotation.");
                    error.status = response.status;
                    throw error;
                }
                return body;
            });
        }).then(function (result) {
            if (!result) return;
            if (wizardState.onCameraSaved) wizardState.onCameraSaved(result.camera);
            clearAnnotationDraft();
            wizardModal?.hide();
            showToast("Camera Annotation Saved", alsoCreateTemplate
                ? "The camera scene and reusable template copy were saved."
                : "The live stream is reconnecting with the saved scene.", "success");
        }).catch(function (error) {
            const detail = templateCreated
                ? " The template copy was created, but the camera scene was not saved; check the camera before retrying."
                : "";
            const conflict = error.status === 409
                ? " Reopen the camera editor to load and review the current saved scene before applying your changes."
                : "";
            showToast("Save Error", (error.message || "Could not save camera annotation.") + conflict + detail, "danger");
        }).finally(function () {
            if (saveButton) saveButton.disabled = false;
            if (templateButton) templateButton.disabled = false;
        });
    }

    btnApplySceneTemplate?.addEventListener("click", function () {
        if (!zoneEditor || !sceneTemplateSelect?.value) {
            showToast("Select Template", "Choose a scene template to copy.", "warning");
            return;
        }
        const template = wizardState.templates.find(function (item) {
            return String(item.id) === String(sceneTemplateSelect.value);
        });
        if (!template) return;
        if (!window.confirm("Copy “" + template.template_name + "” to this scene? This copies the full scene and does not link it. Check the copied geometry against the reference frame; it assumes the same view and framing.")) return;
        zoneEditor.loadSceneDocument(window.TAVIDMZoneEditor.parseSceneDocument(template.zones_json, zoneTypes));
        showToast("Template Copied", "The full scene was copied. Inspect alignment against the target reference frame before saving.", "info");
    });

    btnSaveCameraAsTemplate?.addEventListener("click", function () {
        if (!zoneEditor || !wizardState.camera) return;
        const scene = zoneEditor.getSceneDocument({ completeOnly: true });
        if (!window.TAVIDMZoneEditor.sceneSaveReady(scene, zoneTypes)) {
            showToast("Incomplete Zones", "Draw at least one zone or lane before saving.", "warning");
            return;
        }
        const check = window.TAVIDMZoneEditor.validateSceneDocument(scene);
        if (!check.ok) {
            showToast("Invalid Scene", check.error || "Scene document is invalid.", "warning");
            return;
        }
        saveCameraScene(scene, true);
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
        if (wizardState.camera && wizardState.frameUrl) URL.revokeObjectURL(wizardState.frameUrl);
        wizardState.camera = null;
    });

    function uploadFile() {
        if (!selectedFile || activeXhr) return;

        const recordedDate = (uploadRecordedDate?.value || "").trim();
        const recordedTime = (uploadRecordedTime?.value || "").trim();
        if (Boolean(recordedDate) !== Boolean(recordedTime)) {
            showToast("Recording Time Needed", "Enter both the recording date and time, or leave both blank.", "warning");
            (recordedDate ? uploadRecordedTime : uploadRecordedDate)?.focus();
            return;
        }

        const formData = new FormData();
        formData.append("video", selectedFile);
        formData.append("condition", uploadCondition?.value || "peak");
        if (recordedDate && recordedTime) {
            // Preserve the API's local wall-clock timestamp format.
            formData.append("recorded_at", recordedDate + " " + recordedTime);
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
            // Bytes reached the server; it is saving the upload and recording its row.
            setUploadStage("Saving upload");
        });

        xhr.addEventListener("load", function () {
            activeXhr = null;
            btnCancel.disabled = true;
            let payload;
            try { payload = JSON.parse(xhr.responseText); } catch (err) {
                showToast("Upload Error", "Server returned a non-JSON response (HTTP " + xhr.status + "). Check Uploaded Videos before retrying.", "danger");
                btnUpload.disabled = false;
                btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
                return;
            }
            if (xhr.status >= 200 && xhr.status < 300 && payload.success) {
                if (payload.requires_annotation) {
                    setUploadStage(payload.frame_ready ? "Awaiting zone annotation" : "Preparing reference frame");
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
                    appendVideoToList(payload.video);
                    if (payload.frame_pending) {
                        showToast("Upload Saved", "Preparing the reference frame. The annotation step will open when it is ready.", "info");
                        waitForUploadedFrame(payload, 0);
                    } else if (payload.frame_ready) {
                        openAnnotationWizard(payload);
                    } else if (payload.frame_error) {
                        showToast("Upload Saved; Preview Failed", payload.frame_error, "danger");
                    } else {
                        waitForUploadedFrame(payload, 0);
                    }
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
