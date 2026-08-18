/**
 * TAVIDM - Settings Page: rule parameters, users, cameras, zone templates
 */

(function () {
    "use strict";

    // --- Rule parameter form -------------------------------------------------

    const confidenceInput = document.getElementById("confidenceThreshold");
    const confidenceValue = document.getElementById("confidenceValue");
    confidenceInput?.addEventListener("input", function () {
        if (confidenceValue) confidenceValue.textContent = this.value + "%";
    });

    document.getElementById("btnSaveSettings")?.addEventListener("click", function () {
        const enabledViolations = [];
        document.querySelectorAll(".violation-toggle:checked").forEach(function (el) {
            if (el.dataset.toggleable === "true") {
                enabledViolations.push(el.value);
            }
        });
        const body = {
            confidence_threshold: (parseFloat(confidenceInput?.value || "60") / 100).toFixed(2),
            truck_ban_start: document.getElementById("truckBanStart")?.value,
            truck_ban_end: document.getElementById("truckBanEnd")?.value,
            frame_skip: document.getElementById("frameSkip")?.value,
            stopping_dwell_sec: document.getElementById("stoppingDwell")?.value,
            parking_dwell_sec: document.getElementById("parkingDwell")?.value,
            obstruction_dwell_sec: document.getElementById("obstructionDwell")?.value,
            loading_dwell_sec: document.getElementById("loadingDwell")?.value,
            crossing_block_sec: document.getElementById("crossingBlock")?.value,
            lane_flow_degrees: document.getElementById("laneFlow")?.value,
            flow_tolerance_degrees: document.getElementById("flowTolerance")?.value,
            enabled_violations: enabledViolations,
        };
        fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    showToast("Settings Saved", "Rule parameters and violation toggles updated.", "success");
                } else {
                    showToast("Error", payload.error || "Save failed.", "danger");
                }
            })
            .catch(function () { showToast("Error", "Save failed. Please try again.", "danger"); });
    });

    // --- User management -----------------------------------------------------

    const userModalEl = document.getElementById("userEditorModal");
    const userModal = userModalEl && window.bootstrap ? new bootstrap.Modal(userModalEl) : null;

    function openUserEditor(user) {
        document.getElementById("editUserId").value = user ? user.id : "";
        document.getElementById("editUserUsername").value = user ? user.username : "";
        document.getElementById("editUserUsername").disabled = !!user;
        document.getElementById("editUserFullName").value = user ? (user.name || "") : "";
        document.getElementById("editUserPassword").value = "";
        document.getElementById("editUserRole").value = user ? user.role : "enforcer";
        document.getElementById("editUserActive").checked = user ? user.is_active : true;
        document.getElementById("userEditorTitle").textContent = user ? "Edit User" : "Add User";
        userModal?.show();
    }

    document.getElementById("btnAddUser")?.addEventListener("click", function () {
        openUserEditor(null);
    });

    document.getElementById("usersTableBody")?.addEventListener("click", function (e) {
        const btn = e.target.closest(".btn-edit-user");
        if (!btn) return;
        const row = btn.closest("tr");
        try { openUserEditor(JSON.parse(row.dataset.user)); } catch (err) { /* ignore */ }
    });

    document.getElementById("btnSaveUser")?.addEventListener("click", function () {
        const id = document.getElementById("editUserId").value;
        const body = {
            username: document.getElementById("editUserUsername").value.trim(),
            full_name: document.getElementById("editUserFullName").value.trim(),
            password: document.getElementById("editUserPassword").value || null,
            role: document.getElementById("editUserRole").value,
            is_active: document.getElementById("editUserActive").checked,
        };
        if (!id && (!body.username || !body.password)) {
            showToast("Missing Fields", "Username and password are required for new users.", "warning");
            return;
        }
        fetch(id ? "/api/users/" + id : "/api/users", {
            method: id ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    showToast("User Saved", payload.user.username, "success");
                    userModal?.hide();
                    window.location.reload();
                } else {
                    showToast("Error", payload.error || "Save failed.", "danger");
                }
            })
            .catch(function () { showToast("Error", "Save failed. Please try again.", "danger"); });
    });

    // --- Camera management ---------------------------------------------------

    const cameraModalEl = document.getElementById("cameraEditorModal");
    const cameraModal = cameraModalEl && window.bootstrap ? new bootstrap.Modal(cameraModalEl) : null;

    function openCameraEditor(camera) {
        document.getElementById("editCameraId").value = camera ? camera.id : "";
        document.getElementById("editCameraName").value = camera ? camera.name : "";
        document.getElementById("editCameraLocation").value = camera ? (camera.location || "") : "";
        document.getElementById("editCameraUrl").value = camera ? camera.rtsp_url : "";
        document.getElementById("editCameraZones").value = camera ? camera.zones_json : "";
        document.getElementById("editCameraActive").checked = camera ? camera.is_active : true;
        document.getElementById("cameraEditorTitle").textContent = camera ? "Edit Camera" : "Add Camera";
        cameraModal?.show();
    }

    document.getElementById("btnAddCamera")?.addEventListener("click", function () {
        openCameraEditor(null);
    });

    document.getElementById("camerasTableBody")?.addEventListener("click", function (e) {
        const row = e.target.closest("tr");
        if (!row || !row.dataset.camera) return;
        let camera;
        try { camera = JSON.parse(row.dataset.camera); } catch (err) { return; }

        if (e.target.closest(".btn-edit-camera")) openCameraEditor(camera);

        if (e.target.closest(".btn-delete-camera")) {
            if (!confirm('Delete camera "' + camera.name + '"?')) return;
            fetch("/api/cameras/" + camera.id, { method: "DELETE" })
                .then(function (r) { return r.json(); })
                .then(function (payload) {
                    if (payload.success) {
                        showToast("Camera Deleted", camera.name, "success");
                        window.location.reload();
                    } else {
                        showToast("Error", payload.error || "Delete failed.", "danger");
                    }
                });
        }
    });

    document.getElementById("btnSaveCamera")?.addEventListener("click", function () {
        const id = document.getElementById("editCameraId").value;
        let zones = null;
        const zonesRaw = document.getElementById("editCameraZones").value.trim();
        if (zonesRaw) {
            try { zones = JSON.parse(zonesRaw); } catch (e) {
                showToast("Invalid JSON", "Zone polygons must be valid JSON.", "danger");
                return;
            }
        }
        const body = {
            name: document.getElementById("editCameraName").value.trim(),
            location: document.getElementById("editCameraLocation").value.trim(),
            rtsp_url: document.getElementById("editCameraUrl").value.trim(),
            is_active: document.getElementById("editCameraActive").checked,
        };
        if (zones !== null) body.zones_json = zones;
        if (!body.name || !body.rtsp_url) {
            showToast("Missing Fields", "Camera name and RTSP URL are required.", "warning");
            return;
        }
        fetch(id ? "/api/cameras/" + id : "/api/cameras", {
            method: id ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    showToast("Camera Saved", payload.camera.name, "success");
                    cameraModal?.hide();
                    window.location.reload();
                } else {
                    showToast("Error", payload.error || "Save failed.", "danger");
                }
            })
            .catch(function () { showToast("Error", "Save failed. Please try again.", "danger"); });
    });

    // --- Zone templates --------------------------------------------------------

    const editorModalEl = document.getElementById("templateEditorModal");
    const previewModalEl = document.getElementById("templatePreviewModal");
    const editorModal = editorModalEl && window.bootstrap ? new bootstrap.Modal(editorModalEl) : null;
    const previewModal = previewModalEl && window.bootstrap ? new bootstrap.Modal(previewModalEl) : null;
    const tableBody = document.getElementById("templatesTableBody");

    function emptyZonesJson() {
        const zones = {};
        (window.TAVIDM_ZONE_TYPES || []).forEach(function (zt) { zones[zt.key] = []; });
        return JSON.stringify(zones, null, 2);
    }

    function openEditor(template) {
        document.getElementById("editTemplateId").value = template ? template.id : "";
        document.getElementById("editTemplateName").value = template ? template.template_name : "";
        document.getElementById("editTemplateDescription").value = template ? (template.description || "") : "";
        document.getElementById("editTemplateZones").value = template
            ? (typeof template.zones_json === "string" ? template.zones_json : JSON.stringify(template.zones_json, null, 2))
            : emptyZonesJson();
        document.getElementById("templateEditorTitle").textContent = template ? "Edit Zone Template" : "New Zone Template";
        editorModal?.show();
    }

    function fetchTemplate(id) {
        return fetch("/api/zone-templates/" + id).then(function (r) { return r.json(); });
    }

    function refreshTemplates() {
        return fetch("/api/zone-templates")
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (!payload.success || !tableBody) return;
                tableBody.innerHTML = "";
                if (!payload.templates.length) {
                    tableBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-4">No zone templates yet.</td></tr>';
                    return;
                }
                payload.templates.forEach(function (tpl) {
                    const tr = document.createElement("tr");
                    tr.dataset.templateId = tpl.id;
                    tr.innerHTML =
                        '<td><div class="fw-semibold">' + tpl.template_name + '</div>' +
                        (tpl.description ? '<small class="text-muted">' + tpl.description + '</small>' : '') + '</td>' +
                        '<td><span class="badge bg-secondary-subtle text-secondary">' + (tpl.usage_count || 0) + '</span></td>' +
                        '<td class="small text-muted">' + (tpl.last_used_at || "—") + '</td>' +
                        '<td><div class="btn-group btn-group-sm">' +
                        '<button class="btn btn-outline-secondary btn-preview-template" title="Preview"><i class="bi bi-eye"></i></button>' +
                        '<button class="btn btn-outline-secondary btn-edit-template" title="Edit"><i class="bi bi-pencil"></i></button>' +
                        '<button class="btn btn-outline-secondary btn-duplicate-template" title="Duplicate"><i class="bi bi-copy"></i></button>' +
                        '<button class="btn btn-outline-danger btn-delete-template" title="Delete"><i class="bi bi-trash"></i></button>' +
                        '</div></td>';
                    tableBody.appendChild(tr);
                });
                bindTemplateRowActions();
            });
    }

    function bindTemplateRowActions() {
        document.querySelectorAll(".btn-preview-template").forEach(function (btn) {
            btn.addEventListener("click", function () {
                const id = btn.closest("tr")?.dataset.templateId;
                fetchTemplate(id).then(function (payload) {
                    if (!payload.success) return;
                    document.getElementById("templatePreviewTitle").textContent = payload.template.template_name;
                    let zones = payload.template.zones_json;
                    try {
                        zones = JSON.stringify(JSON.parse(zones), null, 2);
                    } catch (e) { /* keep raw */ }
                    document.getElementById("templatePreviewJson").textContent = zones;
                    previewModal?.show();
                });
            });
        });

        document.querySelectorAll(".btn-edit-template").forEach(function (btn) {
            btn.addEventListener("click", function () {
                const id = btn.closest("tr")?.dataset.templateId;
                fetchTemplate(id).then(function (payload) {
                    if (payload.success) openEditor(payload.template);
                });
            });
        });

        document.querySelectorAll(".btn-duplicate-template").forEach(function (btn) {
            btn.addEventListener("click", function () {
                const id = btn.closest("tr")?.dataset.templateId;
                const name = prompt("Name for duplicated template:");
                if (!name) return;
                fetch("/api/zone-templates/" + id + "/duplicate", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ template_name: name }),
                })
                    .then(function (r) { return r.json(); })
                    .then(function (payload) {
                        if (payload.success) {
                            showToast("Template Duplicated", name, "success");
                            refreshTemplates();
                        } else {
                            showToast("Error", payload.error || "Duplicate failed.", "danger");
                        }
                    });
            });
        });

        document.querySelectorAll(".btn-delete-template").forEach(function (btn) {
            btn.addEventListener("click", function () {
                const id = btn.closest("tr")?.dataset.templateId;
                if (!confirm("Delete this zone template?")) return;
                fetch("/api/zone-templates/" + id, { method: "DELETE" })
                    .then(function (r) { return r.json(); })
                    .then(function (payload) {
                        if (payload.success) {
                            showToast("Template Deleted", "Zone template removed.", "success");
                            refreshTemplates();
                        } else {
                            showToast("Error", payload.error || "Delete failed.", "danger");
                        }
                    });
            });
        });
    }

    document.getElementById("btnCreateTemplate")?.addEventListener("click", function () {
        openEditor(null);
    });

    document.getElementById("btnSaveTemplate")?.addEventListener("click", function () {
        const id = document.getElementById("editTemplateId").value;
        const name = document.getElementById("editTemplateName").value.trim();
        const description = document.getElementById("editTemplateDescription").value.trim();
        let zones;
        try {
            zones = JSON.parse(document.getElementById("editTemplateZones").value);
        } catch (e) {
            showToast("Invalid JSON", "Zone polygons must be valid JSON.", "danger");
            return;
        }
        if (!name) {
            showToast("Name Required", "Enter a template name.", "warning");
            return;
        }

        const body = { template_name: name, description: description, zones_json: zones };
        const url = id ? "/api/zone-templates/" + id : "/api/zone-templates";
        const method = id ? "PUT" : "POST";

        fetch(url, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    showToast("Template Saved", name, "success");
                    editorModal?.hide();
                    refreshTemplates();
                } else {
                    showToast("Error", payload.error || "Save failed.", "danger");
                }
            });
    });

    bindTemplateRowActions();
})();
