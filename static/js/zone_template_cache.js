/* Small client-side cache helpers for current zone templates. */
(function (global) {
    "use strict";

    function upsert(current, template) {
        var templates = (current || []).filter(function (item) {
            return String(item.id) !== String(template.id);
        });
        templates.push(template);
        return templates;
    }

    function merge(latest, createdTemplate) {
        var templates = [];
        (latest || []).forEach(function (template) {
            templates = upsert(templates, template);
        });
        if (createdTemplate) templates = upsert(templates, createdTemplate);
        return templates;
    }

    function refresh(current, createdTemplate, loadLatest) {
        return Promise.resolve()
            .then(loadLatest)
            .then(function (latest) {
                if (!Array.isArray(latest)) throw new Error("Template list response is invalid.");
                return { ok: true, templates: merge(latest, createdTemplate), error: null };
            })
            .catch(function (error) {
                return {
                    ok: false,
                    templates: upsert(current, createdTemplate),
                    error: error && error.message ? error.message : "Template list refresh failed.",
                };
            });
    }

    global.TAVIDMZoneTemplateCache = { upsert: upsert, merge: merge, refresh: refresh };
})(typeof window !== "undefined" ? window : global);
