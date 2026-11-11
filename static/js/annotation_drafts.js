/* Account- and source-scoped browser drafts for scene annotations. */
(function (global) {
    "use strict";

    function key(accountId, sourceType, sourceId) {
        return "tavidm.annotation-draft.v2." + [accountId, sourceType, sourceId]
            .map(function (part) { return encodeURIComponent(String(part)); })
            .join(".");
    }

    function read(storage, accountId, sourceType, sourceId) {
        try {
            var raw = storage.getItem(key(accountId, sourceType, sourceId));
            if (!raw) return { ok: true, draft: null };
            var parsed = JSON.parse(raw);
            if (!parsed || parsed.version !== 2 || !parsed.scene || typeof parsed.scene !== "object") {
                return { ok: true, draft: null, invalid: true };
            }
            return { ok: true, draft: parsed };
        } catch (error) {
            return { ok: false, draft: null };
        }
    }

    function write(storage, accountId, sourceType, sourceId, scene, baseScene) {
        try {
            storage.setItem(key(accountId, sourceType, sourceId), JSON.stringify({
                version: 2,
                scene: scene,
                base_scene: baseScene,
                updated_at: new Date().toISOString(),
            }));
            return { ok: true };
        } catch (error) {
            return { ok: false };
        }
    }

    function remove(storage, accountId, sourceType, sourceId) {
        try {
            storage.removeItem(key(accountId, sourceType, sourceId));
            return { ok: true };
        } catch (error) {
            return { ok: false };
        }
    }

    function isStale(draft, currentScene, serialize) {
        if (!draft || !draft.base_scene) return true;
        serialize = serialize || JSON.stringify;
        return serialize(draft.base_scene) !== serialize(currentScene || {});
    }

    global.TAVIDMAnnotationDrafts = { key: key, read: read, write: write, remove: remove, isStale: isStale };
})(typeof window !== "undefined" ? window : global);
