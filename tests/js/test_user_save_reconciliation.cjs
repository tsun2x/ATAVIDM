"use strict";

// Run with: node tests/js/test_user_save_reconciliation.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
    constructor(id) {
        this.id = id;
        this.dataset = {};
        this.value = "";
        this.checked = false;
        this.disabled = false;
        this.listeners = {};
        this.rows = [];
    }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    dispatch(type) { (this.listeners[type] || []).forEach((callback) => callback({ target: this })); }
    querySelectorAll(selector) { return selector === "tr[data-user]" ? this.rows : []; }
}

function makeHarness({ existing = [], toastThrows = false } = {}) {
    const ids = ["confidenceThreshold", "btnSaveSettings", "userEditorModal", "userAccessModal",
        "userAccessBody", "userAccessTitle", "usersTableBody", "btnSaveUser", "editUserId",
        "editUserUsername", "editUserFullName", "editUserPassword", "editUserRole",
        "editUserActive", "userEditorTitle"];
    const elements = new Map(ids.map((id) => [id, new Element(id)]));
    elements.get("usersTableBody").rows = existing.map((user) => ({ dataset: { user: JSON.stringify(user) } }));
    elements.get("editUserUsername").value = "new-user";
    elements.get("editUserFullName").value = "New User";
    elements.get("editUserPassword").value = "test-password";
    elements.get("editUserRole").value = "enforcer";
    elements.get("editUserActive").checked = true;
    let hideCount = 0;
    let reloadCount = 0;
    const toasts = [];
    const requests = [];
    const document = {
        getElementById: (id) => elements.get(id) || null,
        querySelectorAll: () => [],
    };
    const bootstrap = { Modal: class { show() {} hide() { hideCount += 1; } } };
    const context = {
        document, bootstrap, window: { bootstrap, location: { reload() { reloadCount += 1; } } },
        fetch(url, options) {
            let resolve;
            let reject;
            const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
            requests.push({ url, options, resolve, reject });
            return promise;
        },
        showToast(...args) { toasts.push(args); if (toastThrows) throw new Error("UI failure"); },
        console, Map, Array, JSON, Number, String, Object, Promise, Error,
    };
    vm.runInNewContext(fs.readFileSync("static/js/settings.js", "utf8"), context, { filename: "settings.js" });
    return { elements, requests, toasts, get hideCount() { return hideCount; }, get reloadCount() { return reloadCount; } };
}

function response(status, payload, jsonFails = false, redirected = false, url = "") {
    return { status, ok: status >= 200 && status < 300, redirected, url, json: async () => {
        if (jsonFails) throw new Error("not json");
        return payload;
    } };
}
async function flush() { await new Promise((resolve) => setTimeout(resolve, 0)); }
function clickSave(h) { h.elements.get("btnSaveUser").dispatch("click"); }
function lastToast(h) { return h.toasts[h.toasts.length - 1]; }
const oldAccount = { id: 2, username: "new-user", full_name_raw: "Old Name", name: "Old Name", role: "viewer", is_active: true };

async function run() {
    // A definitive duplicate rejection must never be reinterpreted as create success.
    {
        const h = makeHarness({ existing: [oldAccount] });
        clickSave(h);
        h.requests[0].resolve(response(400, { success: false, error: "Username already exists." }));
        await flush();
        assert.equal(h.requests.length, 1);
        assert.equal(lastToast(h)[0], "Error");
        assert.equal(h.hideCount, 0);
        assert.equal(h.reloadCount, 0);
        assert.equal(h.elements.get("editUserUsername").value, "new-user");
    }

    // A rejected/de-layed deactivation is not successful if the GET still says active.
    {
        const h = makeHarness();
        h.elements.get("editUserId").value = "2";
        h.elements.get("editUserUsername").value = "staff";
        h.elements.get("editUserFullName").value = "Staff";
        h.elements.get("editUserPassword").value = "";
        h.elements.get("editUserRole").value = "enforcer";
        h.elements.get("editUserActive").checked = false;
        clickSave(h);
        h.requests[0].resolve(response(500, { success: false }));
        await flush();
        h.requests[1].resolve(response(200, { success: true, users: [
            { id: 2, username: "staff", full_name_raw: "Staff", name: "Staff", role: "enforcer", is_active: true }
        ] }));
        await flush();
        assert.equal(lastToast(h)[0], "Save Outcome Unclear");
        assert.equal(h.reloadCount, 0);
        assert.equal(h.hideCount, 0);
    }

    // Clearing a name compares the raw persisted field, not the UI fallback name.
    {
        const h = makeHarness();
        h.elements.get("editUserId").value = "2";
        h.elements.get("editUserUsername").value = "staff";
        h.elements.get("editUserFullName").value = "";
        h.elements.get("editUserPassword").value = "";
        h.elements.get("editUserRole").value = "enforcer";
        h.elements.get("editUserActive").checked = true;
        clickSave(h);
        h.requests[0].reject(new Error("connection lost"));
        await flush();
        h.requests[1].resolve(response(200, { success: true, users: [
            { id: 2, username: "staff", full_name_raw: "Staff", name: "staff", role: "enforcer", is_active: true }
        ] }));
        await flush();
        assert.equal(lastToast(h)[0], "Save Outcome Unclear");
        assert.equal(h.reloadCount, 0);
    }

    // A verified metadata-only edit can be acknowledged, including a blank raw name.
    {
        const h = makeHarness();
        h.elements.get("editUserId").value = "2";
        h.elements.get("editUserUsername").value = "staff";
        h.elements.get("editUserFullName").value = "";
        h.elements.get("editUserPassword").value = "";
        h.elements.get("editUserRole").value = "enforcer";
        h.elements.get("editUserActive").checked = false;
        clickSave(h);
        h.requests[0].reject(new Error("connection lost"));
        await flush();
        h.requests[1].resolve(response(200, { success: true, users: [
            { id: 2, username: "staff", full_name_raw: "", name: "staff", role: "enforcer", is_active: false }
        ] }));
        await flush();
        assert.equal(lastToast(h)[0], "Account Saved");
        assert.equal(h.reloadCount, 1);
    }

    // Matching list metadata cannot prove that an ambiguous create used this request/password.
    {
        const h = makeHarness();
        clickSave(h);
        h.requests[0].reject(new Error("connection lost"));
        await flush();
        h.requests[1].resolve(response(200, { success: true, users: [
            { id: 9, username: "new-user", full_name_raw: "New User", name: "New User", role: "enforcer", is_active: true }
        ] }));
        await flush();
        assert.equal(lastToast(h)[0], "Save Outcome Unclear");
        assert.equal(h.reloadCount, 0);
        assert.equal(h.hideCount, 0);
    }

    // Validation and authorization rejections, including non-JSON errors, are definitive.
    for (const status of [400, 401, 403]) {
        const h = makeHarness();
        clickSave(h);
        h.requests[0].resolve(response(status, null, true));
        await flush();
        assert.equal(h.requests.length, 1);
        assert.equal(lastToast(h)[0], "Error");
        assert.equal(h.reloadCount, 0);
    }
    {
        const h = makeHarness();
        clickSave(h);
        h.requests[0].resolve(response(200, null, true, true, "/login"));
        await flush();
        assert.equal(h.requests.length, 1);
        assert.equal(lastToast(h)[0], "Error");
        assert.equal(h.reloadCount, 0);
    }

    // One pending save disables the button and dispatches only one write.
    {
        const h = makeHarness();
        clickSave(h);
        clickSave(h);
        assert.equal(h.requests.length, 1);
        assert.equal(h.elements.get("btnSaveUser").disabled, true);
        h.requests[0].resolve(response(200, { success: true, user: { username: "new-user" } }));
        await flush();
        assert.equal(h.reloadCount, 1);
        assert.equal(h.elements.get("btnSaveUser").disabled, false);
    }

    // A rendering exception after a confirmed write must not trigger reconciliation.
    {
        const h = makeHarness({ toastThrows: true });
        clickSave(h);
        h.requests[0].resolve(response(200, { success: true, user: { username: "new-user" } }));
        await flush();
        assert.equal(h.requests.length, 1);
        assert.equal(h.reloadCount, 0);
    }

    console.log("User save reconciliation tests passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
