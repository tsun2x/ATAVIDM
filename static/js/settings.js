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
        document.querySelectorAll(".violation-group-toggle").forEach(function (el) {
            if (el.dataset.toggleable !== "true") return;
            let rules = [];
            let memberStates = {};
            try { rules = JSON.parse(el.dataset.canonicalRules || "[]"); } catch (e) { rules = []; }
            try { memberStates = JSON.parse(el.dataset.memberStates || "{}"); } catch (e) { memberStates = {}; }
            if (el.indeterminate || el.dataset.enabledState === "mixed") {
                // Preserve legacy mixed per-rule states — do not silently normalize.
                Object.keys(memberStates).forEach(function (rule) {
                    if (memberStates[rule]) enabledViolations.push(rule);
                });
            } else if (el.checked) {
                rules.forEach(function (rule) { enabledViolations.push(rule); });
            }
        });
        // Backward-compatible flat toggles if present.
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

    // Mixed (indeterminate) group switches: click resolves to on/off explicitly.
    // Coordinate with TavidmViolationSwitch.sync so DOMContentLoaded re-sync
    // cannot wipe Mixed into Inactive, and wrap/state classes stay consistent.
    document.querySelectorAll(".violation-group-toggle").forEach(function (el) {
        if (el.dataset.enabledState === "mixed") {
            el.indeterminate = true;
            el.checked = false;
            el.setAttribute("aria-checked", "mixed");
            if (window.TavidmViolationSwitch && window.TavidmViolationSwitch.sync) {
                window.TavidmViolationSwitch.sync(el);
            }
        }
        el.addEventListener("change", function () {
            el.indeterminate = false;
            el.dataset.enabledState = el.checked ? "on" : "off";
            try {
                const rules = JSON.parse(el.dataset.canonicalRules || "[]");
                const map = {};
                rules.forEach(function (rule) { map[rule] = !!el.checked; });
                el.dataset.memberStates = JSON.stringify(map);
            } catch (e) { /* ignore */ }
            if (window.TavidmViolationSwitch && window.TavidmViolationSwitch.sync) {
                window.TavidmViolationSwitch.sync(el);
            }
        });
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

    const accessModalEl = document.getElementById("userAccessModal");
    const accessModal = accessModalEl && window.bootstrap ? new bootstrap.Modal(accessModalEl) : null;
    const accessBody = document.getElementById("userAccessBody");
    let accessGeneration = 0;
    let activeAccessRequest = null;
    const grantableAccessPermissions = new Set([
        "confirm_case", "attest_print", "propose_policy", "approve_policy", "verify_plate", "confirm_event_time",
    ]);
    const accessLabels = {
        confirm_case: "Confirm enforcement case",
        attest_print: "Attest that a notice was printed",
        propose_policy: "Propose legal policy",
        approve_policy: "Approve legal policy",
        verify_plate: "Verify a license plate",
        confirm_event_time: "Confirm event time",
        confirm_plate_identity: "Confirm plate identity (administrator only)",
    };

    function isCurrentAccessRequest(userId, generation) {
        return !!activeAccessRequest &&
            activeAccessRequest.userId === userId &&
            activeAccessRequest.generation === generation;
    }

    accessModalEl?.addEventListener("hidden.bs.modal", function () {
        accessGeneration += 1;
        activeAccessRequest = null;
    });

    function appendCell(row, value, tag) {
        const cell = document.createElement(tag || "td");
        cell.textContent = value;
        row.appendChild(cell);
        return cell;
    }

    function setAccessControlsDisabled(disabled) {
        accessBody?.querySelectorAll(".access-permission-change").forEach(function (button) {
            button.disabled = disabled;
        });
    }

    function appendAccessMessage(message, isError) {
        if (!accessBody) return;
        const notice = document.createElement("div");
        notice.className = isError ? "alert alert-danger py-2" : "small text-muted";
        notice.textContent = message;
        accessBody.prepend(notice);
    }

    function renderUserAccess(access, userId, generation) {
        if (!accessBody || !isCurrentAccessRequest(userId, generation) ||
            !access || String(access.id) !== userId) return;
        accessBody.replaceChildren();
        const heading = document.createElement("p");
        heading.className = "mb-1";
        heading.textContent = access.username + " · " + access.role_label + " · " +
            (access.is_active ? "Active" : "Inactive");
        accessBody.appendChild(heading);
        const inactive = document.createElement("p");
        inactive.className = "small text-muted";
        inactive.textContent = access.is_active
            ? "Effective access combines role defaults with any active explicit legal-policy grant."
            : "This account is inactive, so it currently has no effective permissions.";
        accessBody.appendChild(inactive);
        const inheritedNote = document.createElement("p");
        inheritedNote.className = "small text-muted";
        inheritedNote.textContent =
            "Revoking an explicit grant does not remove access inherited from the account role. " +
            "Legal-policy grants are limited to the six permissions shown here.";
        accessBody.appendChild(inheritedNote);

        const grants = new Map((access.explicit_policy_grants || []).map(function (grant) {
            return [grant.permission, grant];
        }));
        const table = document.createElement("table");
        table.className = "table table-sm table-bordered align-middle";
        const thead = document.createElement("thead");
        const header = document.createElement("tr");
        ["Capability", "Role default", "Explicit grant", "Effective", "Change"].forEach(function (label) {
            appendCell(header, label, "th");
        });
        thead.appendChild(header);
        table.appendChild(thead);
        const tbody = document.createElement("tbody");
        Object.keys(accessLabels).forEach(function (permission) {
            const row = document.createElement("tr");
            appendCell(row, accessLabels[permission]);
            appendCell(row, access.role_defaults[permission] ? "Yes" : "No");
            const grant = grants.get(permission);
            appendCell(row, grant ? "Yes" : "No");
            appendCell(row, access.effective_permissions[permission] ? "Allowed" : "Not allowed");
            const actionCell = document.createElement("td");
            if (grantableAccessPermissions.has(permission)) {
                const action = document.createElement("button");
                action.type = "button";
                action.className = "btn btn-sm " +
                    (grant ? "btn-outline-danger" : "btn-outline-primary") +
                    " access-permission-change";
                action.dataset.permission = permission;
                action.dataset.action = grant ? "revoke" : "grant";
                action.dataset.userId = userId;
                action.dataset.generation = String(generation);
                action.textContent = grant ? "Revoke grant" : "Grant";
                action.disabled = !!activeAccessRequest.writePending;
                actionCell.appendChild(action);
            } else {
                actionCell.textContent = "Administrator only";
            }
            row.appendChild(actionCell);
            tbody.appendChild(row);
        });
        table.appendChild(tbody);
        accessBody.appendChild(table);

        const explicitGrants = Array.from(grants.values());
        const grantsHeading = document.createElement("h6");
        grantsHeading.textContent = "Active explicit legal-policy grants";
        accessBody.appendChild(grantsHeading);
        if (!explicitGrants.length) {
            const none = document.createElement("p");
            none.className = "small text-muted";
            none.textContent = "None";
            accessBody.appendChild(none);
        } else {
            const list = document.createElement("ul");
            explicitGrants.forEach(function (grant) {
                const item = document.createElement("li");
                const details = [grant.label];
                if (grant.granted_by_username) details.push("granted by " + grant.granted_by_username);
                if (grant.granted_at) details.push(grant.granted_at);
                if (grant.reason) details.push(grant.reason);
                item.textContent = details.join(" · ");
                list.appendChild(item);
            });
            accessBody.appendChild(list);
        }
    }

    accessBody?.addEventListener("click", function (e) {
        const button = e.target.closest(".access-permission-change");
        if (!button || !accessBody.contains(button)) return;
        const userId = button.dataset.userId;
        const generation = Number(button.dataset.generation);
        if (!isCurrentAccessRequest(userId, generation)) return;
        if (activeAccessRequest.writePending) return;
        const permission = button.dataset.permission;
        const action = button.dataset.action;
        if (!grantableAccessPermissions.has(permission) || !["grant", "revoke"].includes(action)) return;
        const body = action === "grant"
            ? {
                grants: [{
                    permission: permission,
                    reason: window.prompt("Reason for this legal permission grant (optional):"),
                }],
            }
            : { revokes: [permission] };
        if (action === "grant" && body.grants[0].reason === null) return;
        if (action === "revoke" && !window.confirm(
            "Revoke the explicit grant? Any access inherited from the account role will remain."
        )) return;
        if (!isCurrentAccessRequest(userId, generation) || !accessBody.contains(button)) return;
        activeAccessRequest.writePending = true;
        setAccessControlsDisabled(true);
        fetch("/api/users/" + encodeURIComponent(userId) + "/access", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        }).then(function (response) {
            return response.json().then(function (payload) {
                if (!response.ok) throw new Error(payload.error || "Could not update access.");
                if (payload.success === false) throw new Error(payload.error || "Could not update access.");
                if (!payload.access || String(payload.access.id) !== userId) {
                    throw new Error("The access response did not match the selected account.");
                }
                return payload;
            });
        }).then(function () {
            if (isCurrentAccessRequest(userId, generation)) {
                refreshUserAccess(userId, generation);
            }
        }).catch(function (error) {
            if (isCurrentAccessRequest(userId, generation)) {
                refreshUserAccess(userId, generation, {
                    mutationMessage: "The update outcome may be uncertain: " +
                        (error.message || "the request failed.") +
                        " Current access will be reloaded from the server.",
                    lockOnFailure: true,
                });
            }
        });
    });

    function refreshUserAccess(userId, generation, options) {
        if (!accessBody || !isCurrentAccessRequest(userId, generation)) return;
        const settings = options || {};
        fetch("/api/users/" + encodeURIComponent(userId) + "/access")
            .then(function (response) {
                return response.json().then(function (payload) {
                    if (!response.ok) throw new Error(payload.error || "Could not refresh account access.");
                    if (!payload.access || String(payload.access.id) !== userId) {
                        throw new Error("The access response did not match the selected account.");
                    }
                    return payload;
                });
            })
            .then(function (payload) {
                if (!isCurrentAccessRequest(userId, generation)) return;
                activeAccessRequest.writePending = false;
                renderUserAccess(payload.access, userId, generation);
                if (settings.mutationMessage) appendAccessMessage(settings.mutationMessage, true);
            })
            .catch(function (error) {
                if (!isCurrentAccessRequest(userId, generation)) return;
                if (settings.lockOnFailure) {
                    activeAccessRequest.writePending = true;
                    accessBody.textContent = "The last update could not be verified because access could not be refreshed (" +
                        (error.message || "request failed") +
                        "). Close and reopen this account before making another change.";
                } else {
                    activeAccessRequest.writePending = false;
                    accessBody.textContent = error.message || "Could not load account access.";
                }
            });
    }

    document.getElementById("usersTableBody")?.addEventListener("click", function (e) {
        const button = e.target.closest(".btn-user-access");
        if (!button) return;
        const row = button.closest("tr");
        let user;
        try { user = JSON.parse(row.dataset.user); } catch (err) { return; }
        const userId = String(user.id);
        const generation = ++accessGeneration;
        activeAccessRequest = { userId: userId, generation: generation, writePending: false };
        const title = document.getElementById("userAccessTitle");
        if (title) title.textContent = "Account Access: " + user.username;
        if (accessBody) accessBody.textContent = "Loading access details…";
        accessModal?.show();
        refreshUserAccess(userId, generation);
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
                    const nameCell = document.createElement("td");
                    const name = document.createElement("div");
                    name.className = "fw-semibold";
                    name.textContent = tpl.template_name || "";
                    nameCell.appendChild(name);
                    if (tpl.description) {
                        const description = document.createElement("small");
                        description.className = "text-muted";
                        description.textContent = tpl.description;
                        nameCell.appendChild(description);
                    }
                    const usageCell = document.createElement("td");
                    const usage = document.createElement("span");
                    usage.className = "badge bg-secondary-subtle text-secondary";
                    usage.textContent = String(Number(tpl.usage_count) || 0);
                    usageCell.appendChild(usage);
                    const lastUsedCell = document.createElement("td");
                    lastUsedCell.className = "small text-muted";
                    lastUsedCell.textContent = tpl.last_used_at || "—";
                    const actionsCell = document.createElement("td");
                    actionsCell.innerHTML = '<div class="btn-group btn-group-sm">' +
                        '<button class="btn btn-outline-secondary btn-preview-template" title="Preview"><i class="bi bi-eye"></i></button>' +
                        '<button class="btn btn-outline-secondary btn-edit-template" title="Edit"><i class="bi bi-pencil"></i></button>' +
                        '<button class="btn btn-outline-secondary btn-duplicate-template" title="Duplicate"><i class="bi bi-copy"></i></button>' +
                        '<button class="btn btn-outline-danger btn-delete-template" title="Delete"><i class="bi bi-trash"></i></button>' +
                        '</div>';
                    tr.append(nameCell, usageCell, lastUsedCell, actionsCell);
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
