/**
 * TAVIDM - Settings Page + Zone Templates Management
 */

(function () {
    "use strict";

    const sliders = [
        { input: "confidenceThreshold", display: "confidenceValue", suffix: "%" },
        { input: "reviewThreshold", display: "reviewValue", suffix: "%" },
        { input: "speedLimit", display: "speedValue", suffix: " km/h" },
    ];

    sliders.forEach(function (s) {
        const input = document.getElementById(s.input);
        const display = document.getElementById(s.display);
        if (!input || !display) return;
        input.addEventListener("input", function () { display.textContent = this.value + s.suffix; });
    });

    document.getElementById("btnSaveSettings")?.addEventListener("click", function () {
        showToast("Settings Saved", "Configuration saved locally (demo — no backend).", "success");
    });

    document.getElementById("btnAddUser")?.addEventListener("click", function () {
        showToast("Add User", "User creation form would open here (demo).", "info");
    });

    document.querySelectorAll(".btn-edit-user").forEach(function (btn) {
        btn.addEventListener("click", function () {
            showToast("Edit User", "Editing user #" + this.dataset.userId + " (demo).", "info");
        });
    });

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
