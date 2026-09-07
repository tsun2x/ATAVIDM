/**
 * Executable regression: grouped Mixed switch must survive shared switch init.
 *
 * Reproduces event ordering from settings.html:
 *   1) violation_switch.js registers DOMContentLoaded (sync all switches)
 *   2) settings.js sets Mixed/indeterminate on grouped toggles
 *   3) DOMContentLoaded fires — sync must NOT wipe Mixed to Inactive
 *
 * Run: node tests/js/test_violation_switch_mixed_state.mjs
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..", "..");

function createMinimalDom(htmlBody) {
    const listeners = new Map(); // targetKey -> [{type, fn, capture}]
    const docListeners = [];

    function parseClassList(el) {
        return {
            _el: el,
            add(...names) {
                const set = new Set((el.className || "").split(/\s+/).filter(Boolean));
                names.forEach((n) => set.add(n));
                el.className = Array.from(set).join(" ");
            },
            remove(...names) {
                const set = new Set((el.className || "").split(/\s+/).filter(Boolean));
                names.forEach((n) => set.delete(n));
                el.className = Array.from(set).join(" ");
            },
            toggle(name, force) {
                const set = new Set((el.className || "").split(/\s+/).filter(Boolean));
                const on = force === undefined ? !set.has(name) : !!force;
                if (on) set.add(name);
                else set.delete(name);
                el.className = Array.from(set).join(" ");
                return on;
            },
            contains(name) {
                return (el.className || "").split(/\s+/).includes(name);
            },
        };
    }

    function makeEl(tag, attrs = {}) {
        const el = {
            tagName: tag.toUpperCase(),
            id: attrs.id || "",
            className: attrs.class || "",
            type: attrs.type || "",
            role: attrs.role || "",
            value: attrs.value || "",
            disabled: !!attrs.disabled,
            checked: !!attrs.checked,
            indeterminate: false,
            textContent: attrs.textContent || "",
            children: [],
            parentElement: null,
            dataset: Object.assign({}, attrs.dataset || {}),
            attributes: {},
            _listeners: [],
            classList: null,
            style: {},
        };
        el.classList = parseClassList(el);
        Object.keys(attrs).forEach((k) => {
            if (["id", "class", "type", "role", "value", "disabled", "checked", "textContent", "dataset"].includes(k)) {
                return;
            }
            el.attributes[k] = attrs[k];
        });
        if (attrs["data-enabled-state"]) el.dataset.enabledState = attrs["data-enabled-state"];
        if (attrs["data-toggleable"]) el.dataset.toggleable = attrs["data-toggleable"];
        if (attrs["data-canonical-rules"]) el.dataset.canonicalRules = attrs["data-canonical-rules"];
        if (attrs["data-member-states"]) el.dataset.memberStates = attrs["data-member-states"];
        if (attrs["data-state-for"]) el.attributes["data-state-for"] = attrs["data-state-for"];
        if (attrs["aria-checked"]) el.attributes["aria-checked"] = attrs["aria-checked"];

        el.setAttribute = function (name, value) {
            if (name === "aria-checked") el.attributes["aria-checked"] = String(value);
            else if (name.startsWith("data-")) {
                const key = name
                    .slice(5)
                    .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
                el.dataset[key] = String(value);
            } else {
                el.attributes[name] = String(value);
            }
        };
        el.getAttribute = function (name) {
            if (name === "aria-checked") return el.attributes["aria-checked"] || null;
            if (name === "data-state-for") return el.attributes["data-state-for"] || null;
            if (name === "role") return el.role || null;
            if (name.startsWith("data-")) {
                const key = name
                    .slice(5)
                    .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
                return el.dataset[key] != null ? String(el.dataset[key]) : null;
            }
            return el.attributes[name] != null ? String(el.attributes[name]) : null;
        };
        el.matches = function (selector) {
            if (selector === "input[role='switch']") {
                return el.tagName === "INPUT" && el.role === "switch";
            }
            if (selector.startsWith(".")) {
                return el.classList.contains(selector.slice(1));
            }
            return false;
        };
        el.closest = function (selector) {
            let cur = el;
            while (cur) {
                if (selector.startsWith(".") && cur.classList.contains(selector.slice(1))) {
                    return cur;
                }
                cur = cur.parentElement;
            }
            return null;
        };
        el.addEventListener = function (type, fn) {
            el._listeners.push({ type, fn });
        };
        el.dispatchEvent = function (evt) {
            const type = evt.type;
            el._listeners.filter((l) => l.type === type).forEach((l) => l.fn.call(el, evt));
            // bubble to document
            document.dispatchEvent({ type, target: el, bubbles: true });
        };
        el.querySelectorAll = function () {
            return [];
        };
        el.querySelector = function () {
            return null;
        };
        return el;
    }

    function append(parent, child) {
        parent.children.push(child);
        child.parentElement = parent;
        return child;
    }

    const documentElement = makeEl("html");
    const body = makeEl("body");
    append(documentElement, body);

    // Build fixture matching settings grouped mixed markup.
    const list = append(body, makeEl("div", { id: "violationToggleList" }));
    const row = append(list, makeEl("div", { class: "list-group-item" }));
    const switchRow = append(row, makeEl("div", { class: "tavidm-switch-row" }));
    const labels = append(switchRow, makeEl("div", { class: "tavidm-switch-labels" }));
    append(labels, makeEl("label", { textContent: "Obstruction of Traffic Flow" }));
    const control = append(switchRow, makeEl("div", { class: "tavidm-switch-control" }));
    const state = append(
        control,
        makeEl("span", {
            class: "tavidm-switch-state is-mixed",
            textContent: "Mixed",
            "data-state-for": "set-vg-1",
        })
    );
    state.attributes["data-state-for"] = "set-vg-1";
    const wrap = append(
        control,
        makeEl("label", { class: "tavidm-switch switch-mixed", for: "set-vg-1" })
    );
    const mixedInput = append(
        wrap,
        makeEl("input", {
            id: "set-vg-1",
            class: "violation-group-toggle",
            type: "checkbox",
            role: "switch",
            "data-enabled-state": "mixed",
            "data-toggleable": "true",
            "data-canonical-rules": JSON.stringify([
                "Illegal Parking",
                "Obstruction",
            ]),
            "data-member-states": JSON.stringify({
                "Illegal Parking": true,
                Obstruction: false,
            }),
            "aria-checked": "mixed",
        })
    );
    mixedInput.className = "violation-group-toggle";
    mixedInput.role = "switch";
    mixedInput.type = "checkbox";
    mixedInput.checked = false;
    mixedInput.dataset.enabledState = "mixed";
    mixedInput.dataset.toggleable = "true";
    mixedInput.dataset.canonicalRules = JSON.stringify([
        "Illegal Parking",
        "Obstruction",
    ]);
    mixedInput.dataset.memberStates = JSON.stringify({
        "Illegal Parking": true,
        Obstruction: false,
    });
    mixedInput.setAttribute("aria-checked", "mixed");

    // Binary on switch
    const row2 = append(list, makeEl("div", { class: "list-group-item" }));
    const switchRow2 = append(row2, makeEl("div", { class: "tavidm-switch-row" }));
    const control2 = append(switchRow2, makeEl("div", { class: "tavidm-switch-control" }));
    const state2 = append(
        control2,
        makeEl("span", {
            class: "tavidm-switch-state is-active",
            textContent: "Active",
        })
    );
    state2.attributes["data-state-for"] = "set-vg-2";
    const wrap2 = append(control2, makeEl("label", { class: "tavidm-switch switch-on" }));
    const onInput = append(
        wrap2,
        makeEl("input", {
            id: "set-vg-2",
            class: "violation-group-toggle",
            type: "checkbox",
            role: "switch",
            checked: true,
            "data-enabled-state": "on",
            "data-toggleable": "true",
            "data-canonical-rules": JSON.stringify(["Counterflow"]),
            "data-member-states": JSON.stringify({ Counterflow: true }),
        })
    );
    onInput.className = "violation-group-toggle";
    onInput.role = "switch";
    onInput.checked = true;
    onInput.dataset.enabledState = "on";
    onInput.dataset.toggleable = "true";
    onInput.dataset.canonicalRules = JSON.stringify(["Counterflow"]);
    onInput.dataset.memberStates = JSON.stringify({ Counterflow: true });
    onInput.setAttribute("aria-checked", "true");

    // Disabled switch
    const row3 = append(list, makeEl("div", { class: "list-group-item" }));
    const switchRow3 = append(
        row3,
        makeEl("div", { class: "tavidm-switch-row is-disabled" })
    );
    const control3 = append(switchRow3, makeEl("div", { class: "tavidm-switch-control" }));
    const state3 = append(
        control3,
        makeEl("span", {
            class: "tavidm-switch-state is-inactive",
            textContent: "Inactive",
        })
    );
    state3.attributes["data-state-for"] = "set-vg-3";
    const wrap3 = append(
        control3,
        makeEl("label", { class: "tavidm-switch switch-off switch-disabled" })
    );
    const disabledInput = append(
        wrap3,
        makeEl("input", {
            id: "set-vg-3",
            class: "violation-group-toggle",
            type: "checkbox",
            role: "switch",
            disabled: true,
            "data-enabled-state": "off",
            "data-toggleable": "false",
            "data-canonical-rules": "[]",
            "data-member-states": "{}",
        })
    );
    disabledInput.className = "violation-group-toggle";
    disabledInput.role = "switch";
    disabledInput.disabled = true;
    disabledInput.dataset.enabledState = "off";
    disabledInput.dataset.toggleable = "false";
    disabledInput.dataset.canonicalRules = "[]";
    disabledInput.dataset.memberStates = "{}";
    disabledInput.setAttribute("aria-checked", "false");

    const all = [mixedInput, onInput, disabledInput, state, state2, state3, wrap, wrap2, wrap3];

    function walk(node, out) {
        out.push(node);
        (node.children || []).forEach((c) => walk(c, out));
    }

    function queryAll(selector, root) {
        const nodes = [];
        walk(root || documentElement, nodes);
        if (selector === "input[role='switch']") {
            return nodes.filter((n) => n.tagName === "INPUT" && n.role === "switch");
        }
        if (selector === ".violation-group-toggle") {
            return nodes.filter((n) => (n.className || "").includes("violation-group-toggle"));
        }
        if (selector === ".violation-toggle:checked") {
            return [];
        }
        if (selector.startsWith(".") && !selector.includes(" ")) {
            const cls = selector.slice(1);
            return nodes.filter((n) => (n.className || "").split(/\s+/).includes(cls));
        }
        if (selector.startsWith("[data-state-for=")) {
            const id = selector.match(/"([^"]+)"/)[1];
            return nodes.filter((n) => n.attributes["data-state-for"] === id);
        }
        return [];
    }

    const document = {
        body,
        documentElement,
        readyState: "loading",
        addEventListener(type, fn) {
            docListeners.push({ type, fn });
        },
        dispatchEvent(evt) {
            docListeners
                .filter((l) => l.type === evt.type)
                .forEach((l) => l.fn.call(document, evt));
        },
        getElementById(id) {
            const nodes = [];
            walk(documentElement, nodes);
            return nodes.find((n) => n.id === id) || null;
        },
        querySelector(selector) {
            return queryAll(selector)[0] || null;
        },
        querySelectorAll(selector) {
            return queryAll(selector);
        },
        createElement(tag) {
            return makeEl(tag);
        },
    };

    return {
        document,
        window: { document },
        mixedInput,
        onInput,
        disabledInput,
        state,
        state2,
        wrap,
        wrap2,
        fireDomContentLoaded() {
            document.readyState = "interactive";
            document.dispatchEvent({ type: "DOMContentLoaded" });
            document.readyState = "complete";
        },
    };
}

function loadScript(fileRel, sandbox) {
    const code = fs.readFileSync(path.join(ROOT, fileRel), "utf8");
    vm.runInNewContext(code, sandbox, { filename: fileRel });
}

function applySettingsMixedInit(document, window) {
    // Mirror settings.js mixed-group initialization + change handlers.
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
                rules.forEach(function (rule) {
                    map[rule] = !!el.checked;
                });
                el.dataset.memberStates = JSON.stringify(map);
            } catch (e) {
                /* ignore */
            }
            if (window.TavidmViolationSwitch && window.TavidmViolationSwitch.sync) {
                window.TavidmViolationSwitch.sync(el);
            }
        });
    });
}

function collectSavePayload(document) {
    const enabledViolations = [];
    document.querySelectorAll(".violation-group-toggle").forEach(function (el) {
        if (el.dataset.toggleable !== "true") return;
        let rules = [];
        let memberStates = {};
        try {
            rules = JSON.parse(el.dataset.canonicalRules || "[]");
        } catch (e) {
            rules = [];
        }
        try {
            memberStates = JSON.parse(el.dataset.memberStates || "{}");
        } catch (e) {
            memberStates = {};
        }
        if (el.indeterminate || el.dataset.enabledState === "mixed") {
            Object.keys(memberStates).forEach(function (rule) {
                if (memberStates[rule]) enabledViolations.push(rule);
            });
        } else if (el.checked) {
            rules.forEach(function (rule) {
                enabledViolations.push(rule);
            });
        }
    });
    return enabledViolations;
}

function snapshot(input, state, wrap) {
    return {
        text: state.textContent,
        aria: input.getAttribute("aria-checked"),
        indeterminate: !!input.indeterminate,
        checked: !!input.checked,
        enabledState: input.dataset.enabledState,
        wrapOn: wrap.classList.contains("switch-on"),
        wrapOff: wrap.classList.contains("switch-off"),
        wrapMixed: wrap.classList.contains("switch-mixed"),
        stateMixed: state.classList.contains("is-mixed"),
        stateActive: state.classList.contains("is-active"),
        stateInactive: state.classList.contains("is-inactive"),
    };
}

function run() {
    const env = createMinimalDom();
    const sandbox = {
        window: env.window,
        document: env.document,
        console,
    };
    env.window.document = env.document;

    // 1) Load shared switch helper — registers DOMContentLoaded sync.
    loadScript("static/js/violation_switch.js", sandbox);
    assert.ok(sandbox.window.TavidmViolationSwitch, "TavidmViolationSwitch exported");

    // 2) Settings mixed init (same moment as settings.js IIFE after DOM present).
    applySettingsMixedInit(env.document, sandbox.window);
    const before = snapshot(env.mixedInput, env.state, env.wrap);
    assert.strictEqual(before.text, "Mixed", "pre-init label should be Mixed");
    assert.strictEqual(before.aria, "mixed");
    assert.strictEqual(before.indeterminate, true);

    // 3) DOMContentLoaded — this is where the regression overwrote Mixed → Inactive.
    env.fireDomContentLoaded();
    const after = snapshot(env.mixedInput, env.state, env.wrap);
    assert.strictEqual(
        after.text,
        "Mixed",
        "Mixed must survive DOMContentLoaded sync; got " + after.text
    );
    assert.strictEqual(after.aria, "mixed");
    assert.strictEqual(after.indeterminate, true);
    assert.strictEqual(after.checked, false);
    assert.ok(after.wrapMixed, "wrap should keep switch-mixed");
    assert.ok(!after.wrapOn && !after.wrapOff, "wrap must not be purely on/off while mixed");
    assert.ok(after.stateMixed && !after.stateActive && !after.stateInactive);

    // Binary + disabled remain correct after init.
    assert.strictEqual(env.state2.textContent, "Active");
    assert.strictEqual(env.onInput.getAttribute("aria-checked"), "true");
    assert.ok(env.wrap2.classList.contains("switch-on"));
    assert.strictEqual(env.disabledInput.disabled, true);
    assert.strictEqual(env.disabledInput.getAttribute("aria-checked"), "false");

    // Untouched mixed save preserves only previously enabled members.
    const savedMixed = collectSavePayload(env.document);
    assert.deepStrictEqual(savedMixed, ["Illegal Parking", "Counterflow"]);

    // Explicit Mixed → on
    env.mixedInput.indeterminate = false;
    env.mixedInput.checked = true;
    env.mixedInput.dispatchEvent({ type: "change", target: env.mixedInput, bubbles: true });
    const onSnap = snapshot(env.mixedInput, env.state, env.wrap);
    assert.strictEqual(onSnap.text, "Active");
    assert.strictEqual(onSnap.aria, "true");
    assert.strictEqual(onSnap.indeterminate, false);
    assert.strictEqual(onSnap.enabledState, "on");
    assert.ok(onSnap.wrapOn && !onSnap.wrapOff && !onSnap.wrapMixed);
    const savedOn = collectSavePayload(env.document);
    assert.ok(savedOn.includes("Illegal Parking"));
    assert.ok(savedOn.includes("Obstruction"));

    // Explicit on → off
    env.mixedInput.checked = false;
    env.mixedInput.dispatchEvent({ type: "change", target: env.mixedInput, bubbles: true });
    const offSnap = snapshot(env.mixedInput, env.state, env.wrap);
    assert.strictEqual(offSnap.text, "Inactive");
    assert.strictEqual(offSnap.aria, "false");
    assert.strictEqual(offSnap.enabledState, "off");
    assert.ok(offSnap.wrapOff && !offSnap.wrapOn && !offSnap.wrapMixed);
    const savedOff = collectSavePayload(env.document);
    assert.ok(!savedOff.includes("Illegal Parking"));
    assert.ok(!savedOff.includes("Obstruction"));
    assert.ok(savedOff.includes("Counterflow"));

    console.log("PASS tests/js/test_violation_switch_mixed_state.cjs");
}

try {
    run();
} catch (err) {
    console.error("FAIL:", err && err.message ? err.message : err);
    process.exit(1);
}
