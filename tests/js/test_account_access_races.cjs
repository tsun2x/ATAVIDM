"use strict";

// Deterministic behavioral harness for the account-access section of settings.js.
// Run with: node tests/js/test_account_access_races.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
    constructor(tagName, id) {
        this.tagName = tagName;
        this.id = id || "";
        this.dataset = {};
        this.children = [];
        this.listeners = {};
        this.parentNode = null;
        this._textContent = "";
        this.className = "";
        this.disabled = false;
    }
    set textContent(value) { this.replaceChildren(); this._textContent = String(value); }
    get textContent() { return this._textContent + this.children.map((child) => child.textContent).join(""); }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    dispatch(type, event = {}) {
        for (const callback of this.listeners[type] || []) callback({ target: this, ...event });
    }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    append(...children) { children.forEach((child) => this.appendChild(child)); }
    prepend(child) { child.parentNode = this; this.children.unshift(child); }
    contains(candidate) {
        if (candidate === this) return true;
        return this.children.some((child) => child.contains(candidate));
    }
    querySelectorAll(selector) {
        const className = selector.startsWith(".") ? selector.slice(1) : "";
        const found = [];
        const visit = (node) => {
            node.children.forEach((child) => {
                if (className && child.className.split(/\s+/).includes(className)) found.push(child);
                visit(child);
            });
        };
        visit(this);
        return found;
    }
    replaceChildren(...children) {
        this.children.forEach((child) => { child.parentNode = null; });
        this._textContent = "";
        this.children = [];
        children.forEach((child) => this.appendChild(child));
    }
    closest(selector) {
        const className = selector.startsWith(".") ? selector.slice(1) : "";
        for (let node = this; node; node = node.parentNode) {
            if (className && node.className.split(/\s+/).includes(className)) return node;
            if (selector === "tr" && node.tagName === "tr") return node;
        }
        return null;
    }
}

function makeHarness() {
    const elements = new Map();
    for (const id of ["usersTableBody", "userAccessModal", "userAccessBody", "userAccessTitle"]) {
        elements.set(id, new Element(id === "usersTableBody" || id === "userAccessBody" ? "div" : "div", id));
    }
    const document = {
        getElementById: (id) => elements.get(id) || null,
        createElement: (tag) => new Element(tag),
        querySelectorAll: () => [],
    };
    const modalElement = elements.get("userAccessModal");
    const bootstrap = { Modal: class { show() {} hide() { modalElement.dispatch("hidden.bs.modal"); } } };
    const requests = [];
    const context = {
        document,
        window: { bootstrap, prompt: () => "Coverage", confirm: () => true, TAVIDM_ZONE_TYPES: [] },
        bootstrap,
        fetch(url, options = {}) {
            let resolve;
            let reject;
            const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
            requests.push({ url, options, resolve, reject });
            return promise;
        },
        showToast() {},
        console,
        Map,
        Array,
        JSON,
        Number,
        String,
        Object,
        Promise,
        Error,
    };
    vm.runInNewContext(fs.readFileSync("static/js/settings.js", "utf8"), context, { filename: "settings.js" });
    return { elements, requests };
}

function access(user, role, granted = false, additionalGrants = []) {
    const names = ["confirm_case", "attest_print", "propose_policy", "approve_policy", "verify_plate", "confirm_event_time", "confirm_plate_identity"];
    const roleDefaults = Object.fromEntries(names.map((name) => [name, role === "admin" || (role === "enforcer" && ["confirm_case", "attest_print", "verify_plate", "confirm_event_time"].includes(name))]));
    roleDefaults.approve_policy = false;
    roleDefaults.confirm_plate_identity = role === "admin";
    const grantedPermissions = new Set(additionalGrants);
    if (granted) grantedPermissions.add("confirm_case");
    const effective = { ...roleDefaults };
    grantedPermissions.forEach((permission) => { effective[permission] = true; });
    return {
        success: true,
        access: {
            id: user.id,
            username: user.username,
            role_label: { admin: "System Administrator", enforcer: "Traffic Enforcement Officer", viewer: "Guest Viewer" }[role],
            is_active: true,
            role_defaults: roleDefaults,
            effective_permissions: effective,
            explicit_policy_grants: Array.from(grantedPermissions, (permission) => ({
                permission,
                label: { confirm_case: "Confirm enforcement case", attest_print: "Attest printed notice" }[permission] || permission,
            })),
        },
    };
}

function response(payload, ok = true) { return { ok, json: async () => payload }; }
function clickUser(harness, id, username) {
    const row = new Element("tr");
    row.dataset.user = JSON.stringify({ id, username });
    const button = new Element("button");
    button.className = "btn-user-access";
    row.appendChild(button);
    harness.elements.get("usersTableBody").dispatch("click", { target: button });
}
function accessButtons(root) {
    const found = [];
    const visit = (node) => {
        if (node.className.split(/\s+/).includes("access-permission-change")) found.push(node);
        node.children.forEach(visit);
    };
    visit(root);
    return found;
}
function findTextRow(root, text) {
    const rows = [];
    const visit = (node) => { if (node.tagName === "tr") rows.push(node); node.children.forEach(visit); };
    visit(root);
    return rows.find((row) => row.textContent.includes(text)) || null;
}
async function flush() { await new Promise((resolve) => setImmediate(resolve)); }
async function settle(request, payload, ok = true) { request.resolve(response(payload, ok)); await flush(); }

async function run() {
    // An older GET success must not replace the newer selection's panel.
    {
        const h = makeHarness();
        clickUser(h, 1, "officer-a");
        const a = h.requests[0];
        clickUser(h, 2, "officer-b");
        assert.equal(h.requests.length, 2, "account clicks must dispatch access GETs");
        const b = h.requests[1];
        await settle(b, access({ id: 2, username: "officer-b" }, "enforcer"));
        await settle(a, access({ id: 1, username: "officer-a" }, "viewer"));
        assert.equal(h.elements.get("userAccessTitle").textContent, "Account Access: officer-b");
        assert.match(h.elements.get("userAccessBody").textContent, /Traffic Enforcement Officer/);
        assert.match(h.elements.get("userAccessBody").textContent, /officer-b/);
        assert.doesNotMatch(h.elements.get("userAccessBody").textContent, /officer-a/);
    }

    // An older GET failure must not inject its error into the newer panel.
    {
        const h = makeHarness();
        clickUser(h, 1, "officer-a");
        const a = h.requests[0];
        clickUser(h, 2, "officer-b");
        await settle(h.requests[1], access({ id: 2, username: "officer-b" }, "enforcer"));
        await settle(a, { error: "old account failed" }, false);
        assert.doesNotMatch(h.elements.get("userAccessBody").textContent, /old account failed/);
        assert.match(h.elements.get("userAccessBody").textContent, /Traffic Enforcement Officer/);
    }

    // A submitted write stays targeted to A, and its late response/error cannot alter B.
    for (const fail of [false, true]) {
        const h = makeHarness();
        clickUser(h, 1, "officer-a");
        await settle(h.requests[0], access({ id: 1, username: "officer-a" }, "viewer"));
        const button = accessButtons(h.elements.get("userAccessBody")).find((item) => item.dataset.permission === "confirm_case");
        h.elements.get("userAccessBody").dispatch("click", { target: button });
        const put = h.requests[1];
        assert.equal(put.url, "/api/users/1/access");
        assert.deepEqual(JSON.parse(put.options.body), { grants: [{ permission: "confirm_case", reason: "Coverage" }] });
        clickUser(h, 2, "officer-b");
        await settle(h.requests[2], access({ id: 2, username: "officer-b" }, "enforcer"));
        if (fail) put.reject(new Error("late write error"));
        else put.resolve(response(access({ id: 1, username: "officer-a" }, "viewer", true)));
        await flush();
        assert.match(h.elements.get("userAccessBody").textContent, /Traffic Enforcement Officer/);
        assert.doesNotMatch(h.elements.get("userAccessBody").textContent, /late write error/);
    }

    // Close/reopen invalidates old callbacks even when the same account is selected.
    {
        const h = makeHarness();
        clickUser(h, 1, "officer-a");
        const old = h.requests[0];
        h.elements.get("userAccessModal").dispatch("hidden.bs.modal");
        clickUser(h, 1, "officer-a");
        await settle(h.requests[1], access({ id: 1, username: "officer-a" }, "viewer", true));
        await settle(old, access({ id: 1, username: "officer-a" }, "viewer", false));
        assert.match(h.elements.get("userAccessBody").textContent, /Allowed/);
    }

    // A late write must not refresh a reopened generation of the same account.
    {
        const h = makeHarness();
        clickUser(h, 3, "officer-c");
        await settle(h.requests[0], access({ id: 3, username: "officer-c" }, "viewer"));
        const button = accessButtons(h.elements.get("userAccessBody")).find((item) => item.dataset.permission === "confirm_case");
        h.elements.get("userAccessBody").dispatch("click", { target: button });
        const pendingPut = h.requests[1];
        assert.equal(pendingPut.url, "/api/users/3/access");
        h.elements.get("userAccessModal").dispatch("hidden.bs.modal");
        clickUser(h, 3, "officer-c");
        await settle(h.requests[2], access({ id: 3, username: "officer-c" }, "viewer"));
        pendingPut.resolve(response(access({ id: 3, username: "officer-c" }, "viewer", true)));
        await flush();
        assert.equal(h.requests.length, 3, "stale write completion must not start a refresh for the reopened panel");
        assert.equal(accessButtons(h.elements.get("userAccessBody")).find((item) => item.dataset.permission === "confirm_case").dataset.action, "grant");
    }

    // Only the six API-supported permissions have controls; plate identity is informational.
    {
        const h = makeHarness();
        clickUser(h, 7, "plate-admin");
        await settle(h.requests[0], access({ id: 7, username: "plate-admin" }, "admin"));
        const buttons = accessButtons(h.elements.get("userAccessBody"));
        assert.deepEqual(buttons.map((button) => button.dataset.permission).sort(), [
            "approve_policy", "attest_print", "confirm_case", "confirm_event_time", "propose_policy", "verify_plate",
        ]);
        assert.equal(buttons.length, 6);
        const plateIdentityRow = findTextRow(h.elements.get("userAccessBody"), "Confirm plate identity");
        assert.match(plateIdentityRow.textContent, /Yes.*No.*Allowed.*Administrator only/);
        h.elements.get("userAccessBody").dispatch("click", { target: plateIdentityRow.children[4] });
        assert.equal(h.requests.length, 1, "informational plate identity must not issue a permission update");
    }

    // Same-account grant and revoke actions keep their target and refresh from the API.
    {
        const h = makeHarness();
        clickUser(h, 5, "reviewer");
        await settle(h.requests[0], access({ id: 5, username: "reviewer" }, "viewer"));
        const root = h.elements.get("userAccessBody");
        const grant = accessButtons(root).find((item) => item.dataset.permission === "confirm_case");
        root.dispatch("click", { target: grant });
        const putGrant = h.requests[1];
        assert.equal(putGrant.url, "/api/users/5/access");
        assert.equal(grant.disabled, true);
        assert.equal(accessButtons(root).every((button) => button.disabled), true);
        await settle(putGrant, access({ id: 5, username: "reviewer" }, "viewer", true));
        assert.equal(h.requests[2].url, "/api/users/5/access");
        assert.equal(h.requests[2].options.method, undefined);
        await settle(h.requests[2], access({ id: 5, username: "reviewer" }, "viewer", true));
        const revoke = accessButtons(root).find((item) => item.dataset.permission === "confirm_case");
        assert.equal(revoke.dataset.action, "revoke");
        root.dispatch("click", { target: revoke });
        const putRevoke = h.requests[3];
        assert.equal(putRevoke.url, "/api/users/5/access");
        assert.deepEqual(JSON.parse(putRevoke.options.body), { revokes: ["confirm_case"] });
        await settle(putRevoke, access({ id: 5, username: "reviewer" }, "viewer"));
        await settle(h.requests[4], access({ id: 5, username: "reviewer" }, "viewer"));
        assert.equal(accessButtons(root).find((item) => item.dataset.permission === "confirm_case").dataset.action, "grant");
    }

    // A rapid second click is serialized, then a failed second write refreshes authoritative state.
    {
        const h = makeHarness();
        clickUser(h, 8, "rapid-reviewer");
        await settle(h.requests[0], access({ id: 8, username: "rapid-reviewer" }, "viewer"));
        const root = h.elements.get("userAccessBody");
        const confirm = accessButtons(root).find((item) => item.dataset.permission === "confirm_case");
        const attest = accessButtons(root).find((item) => item.dataset.permission === "attest_print");
        root.dispatch("click", { target: confirm });
        root.dispatch("click", { target: attest });
        assert.equal(h.requests.length, 2, "rapid second click must not submit an overlapping PUT");
        assert.equal(accessButtons(root).every((button) => button.disabled), true);
        assert.equal(JSON.parse(h.requests[1].options.body).grants[0].permission, "confirm_case");
        await settle(h.requests[1], access({ id: 8, username: "rapid-reviewer" }, "viewer", true));
        assert.equal(h.requests[2].url, "/api/users/8/access");
        assert.equal(h.requests[2].options.method, undefined, "a fresh GET must follow the first PUT");
        await settle(h.requests[2], access({ id: 8, username: "rapid-reviewer" }, "viewer", true));
        assert.equal(accessButtons(root).every((button) => !button.disabled), true);

        const attestAfterFailure = accessButtons(root).find((item) => item.dataset.permission === "attest_print");
        root.dispatch("click", { target: attestAfterFailure });
        const failedPut = h.requests[3];
        assert.equal(failedPut.url, "/api/users/8/access");
        assert.deepEqual(JSON.parse(failedPut.options.body), { grants: [{ permission: "attest_print", reason: "Coverage" }] });
        failedPut.reject(new Error("connection lost"));
        await flush();
        assert.equal(h.requests[4].url, "/api/users/8/access");
        await settle(h.requests[4], access({ id: 8, username: "rapid-reviewer" }, "viewer", true));
        assert.equal(accessButtons(root).every((button) => !button.disabled), true);
        assert.equal(accessButtons(root).find((item) => item.dataset.permission === "attest_print").dataset.action, "grant");
        assert.match(root.textContent, /outcome may be uncertain/);
        assert.match(findTextRow(root, "Confirm enforcement case").textContent, /Allowed/);

        const retryAttest = accessButtons(root).find((item) => item.dataset.permission === "attest_print");
        root.dispatch("click", { target: retryAttest });
        assert.deepEqual(JSON.parse(h.requests[5].options.body), { grants: [{ permission: "attest_print", reason: "Coverage" }] });
        await settle(h.requests[5], access({ id: 8, username: "rapid-reviewer" }, "viewer", true, ["attest_print"]));
        assert.equal(h.requests[6].options.method, undefined);
        await settle(h.requests[6], access({ id: 8, username: "rapid-reviewer" }, "viewer", true, ["attest_print"]));
        const finalAttest = accessButtons(root).find((item) => item.dataset.permission === "attest_print");
        assert.equal(finalAttest.dataset.action, "revoke");
        assert.match(findTextRow(root, "Attest that a notice was printed").textContent, /NoYesAllowedRevoke grant/);
    }
    process.stdout.write("account-access race harness: 8 scenarios passed\n");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
