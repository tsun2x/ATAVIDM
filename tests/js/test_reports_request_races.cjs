"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

const elements = new Map();
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            id, value: "", textContent: "", innerHTML: "", hidden: false, disabled: false, dataset: {},
            listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; }
        });
    }
    return elements.get(id);
}

const requests = [];
const context = {
    document: { getElementById: element },
    window: { location: {} },
    URLSearchParams,
    AbortController,
    showToast() {},
    fetch(url, options) {
        const gate = deferred();
        requests.push({ url: String(url), options, gate });
        return gate.promise;
    },
    console,
};
vm.runInNewContext(fs.readFileSync("static/js/reports.js", "utf8"), context);

function change(id, value) {
    const node = element(id);
    node.value = value;
    node.listeners.change?.();
}

function success(payload) {
    return { ok: true, json: () => Promise.resolve({ success: true, ...payload }) };
}

function preview(id, page = 1, pages = 1) {
    return {
        total_cases: 1,
        result_state: "completed_with_cases",
        category_counts: [{ violation_type: "Illegal Parking", count: 1 }],
        pagination: { page, per_page: 25, pages },
        items: [{ id, violation_type: "Illegal Parking", detected_at: "now", status: "pending" }],
    };
}

async function flush() {
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
}

function lastRequest(match) {
    const found = [...requests].reverse().find(item => match(item.url));
    assert.ok(found, "expected matching request");
    return found;
}

async function run() {
    // Clear selection while the old response is pending.
    change("reportVideo", "3");
    const selected = lastRequest(url => url.includes("/api/reports/preview"));
    change("reportVideo", "");
    selected.gate.resolve(success(preview(3)));
    await flush();
    assert.strictEqual(element("reportPreviewState").textContent, "Choose a video");
    assert.ok(!element("reportPreviewCases").innerHTML.includes("VIO-000003"));

    // Video A resolves after video B and must not replace B.
    change("reportVideo", "A");
    const requestA = lastRequest(url => url.includes("video_id=A"));
    change("reportVideo", "B");
    const requestB = lastRequest(url => url.includes("video_id=B"));
    requestB.gate.resolve(success(preview(22)));
    await flush();
    requestA.gate.resolve(success(preview(11)));
    await flush();
    assert.ok(element("reportPreviewCases").innerHTML.includes("VIO-000022"));
    assert.ok(!element("reportPreviewCases").innerHTML.includes("VIO-000011"));

    // A stale rejected response must not overwrite a newer successful response.
    change("reportVideo", "error-A");
    const errorA = lastRequest(url => url.includes("video_id=error-A"));
    change("reportVideo", "success-B");
    const successB = lastRequest(url => url.includes("video_id=success-B"));
    successB.gate.resolve(success(preview(33)));
    await flush();
    errorA.gate.reject(new Error("obsolete request failed"));
    await flush();
    assert.strictEqual(element("reportPreviewState").textContent, "Completed · cases available");
    assert.ok(element("reportPreviewCases").innerHTML.includes("VIO-000033"));

    // Rapid uploader changes must keep the options for the active account.
    change("reportUploader", "source-A");
    const sourceAList = lastRequest(url => url.includes("/api/videos?uploader_id=source-A"));
    sourceAList.gate.resolve(success({ videos: [{ db_id: 301, filename: "source-A.mp4", status: "processed" }] }));
    await flush();
    change("reportVideo", "301");
    const sourceAPreview = lastRequest(url => url.includes("video_id=301"));
    change("reportUploader", "source-B");
    const sourceBList = lastRequest(url => url.includes("/api/videos?uploader_id=source-B"));
    sourceBList.gate.resolve({ ok: false, json: () => Promise.resolve({ success: false, error: "Could not load B videos." }) });
    await flush();
    sourceAPreview.gate.resolve(success(preview(301)));
    await flush();
    assert.strictEqual(element("reportPreviewState").textContent, "Unavailable");
    assert.strictEqual(element("reportPreviewTotal").textContent, "—");
    assert.strictEqual(element("reportVideo").disabled, true);
    assert.strictEqual(element("btnGeneratePdf").disabled, true);
    assert.ok(!element("reportPreviewCases").innerHTML.includes("VIO-000301"));

    // Rapid uploader changes must keep the options for the active account.
    change("reportUploader", "uploader-A");
    const uploaderA = lastRequest(url => url.includes("/api/videos?uploader_id=uploader-A"));
    change("reportUploader", "uploader-B");
    const uploaderB = lastRequest(url => url.includes("/api/videos?uploader_id=uploader-B"));
    uploaderB.gate.resolve(success({ videos: [{ db_id: 22, filename: "B.mp4", status: "processed" }] }));
    await flush();
    uploaderA.gate.resolve(success({ videos: [{ db_id: 11, filename: "A.mp4", status: "processed" }] }));
    await flush();
    assert.ok(element("reportVideo").innerHTML.includes("B.mp4"));
    assert.ok(!element("reportVideo").innerHTML.includes("A.mp4"));

    // Invalidate both a late success and a late error while the next source list is pending.
    change("reportUploader", "pending-A");
    const pendingAList = lastRequest(url => url.includes("uploader_id=pending-A"));
    pendingAList.gate.resolve(success({ videos: [{ db_id: 401, filename: "pending-A.mp4" }] }));
    await flush();
    change("reportVideo", "401");
    const pendingASuccess = lastRequest(url => url.includes("video_id=401"));
    change("reportVideo", "402");
    const pendingAError = lastRequest(url => url.includes("video_id=402"));
    change("reportUploader", "pending-B");
    const pendingBList = lastRequest(url => url.includes("uploader_id=pending-B"));
    assert.strictEqual(element("reportPreviewState").textContent, "Loading");
    assert.strictEqual(element("reportPreviewTotal").textContent, "—");
    assert.strictEqual(element("reportVideo").value, "");
    assert.strictEqual(element("reportVideo").disabled, true);
    assert.strictEqual(element("btnGeneratePdf").disabled, true);
    const previewRequestsBeforeBlockedActions = requests.filter(item => item.url.includes("/api/reports/preview")).length;
    element("reportVideo").value = "401";
    element("reportVideo").listeners.change();
    element("btnGeneratePdf").listeners.click();
    element("btnGenerateExcel").listeners.click();
    assert.strictEqual(requests.filter(item => item.url.includes("/api/reports/preview")).length, previewRequestsBeforeBlockedActions);
    assert.strictEqual(requests.filter(item => item.url === "/api/reports" && item.options?.method === "POST").length, 0);
    pendingASuccess.gate.resolve(success(preview(401)));
    pendingAError.gate.reject(new Error("stale source preview error"));
    await flush();
    assert.strictEqual(element("reportPreviewState").textContent, "Loading");
    assert.ok(!element("reportPreviewCases").innerHTML.includes("VIO-000401"));
    pendingBList.gate.resolve(success({ videos: [{ db_id: 502, filename: "pending-B.mp4", status: "processed" }] }));
    await flush();
    assert.strictEqual(element("reportVideo").disabled, false);
    assert.strictEqual(element("btnGeneratePdf").disabled, false);
    assert.ok(element("reportVideo").innerHTML.includes("pending-B.mp4"));
    change("reportVideo", "502");
    const pendingBPreview = lastRequest(url => url.includes("video_id=502"));
    pendingBPreview.gate.resolve(success(preview(502)));
    await flush();
    assert.ok(element("reportPreviewCases").innerHTML.includes("VIO-000502"));
    pendingASuccess.gate.resolve(success(preview(401)));
    await flush();
    assert.ok(element("reportPreviewCases").innerHTML.includes("VIO-000502"));
    assert.ok(!element("reportPreviewCases").innerHTML.includes("VIO-000401"));

    // Three rapid account changes apply only the newest options response.
    change("reportUploader", "rapid-A");
    const rapidA = lastRequest(url => url.includes("uploader_id=rapid-A"));
    change("reportUploader", "rapid-B");
    const rapidB = lastRequest(url => url.includes("uploader_id=rapid-B"));
    change("reportUploader", "rapid-C");
    const rapidC = lastRequest(url => url.includes("uploader_id=rapid-C"));
    rapidC.gate.resolve(success({ videos: [{ db_id: 603, filename: "rapid-C.mp4" }] }));
    await flush();
    rapidB.gate.resolve({ ok: false, json: () => Promise.resolve({ success: false, error: "stale list error" }) });
    rapidA.gate.resolve(success({ videos: [{ db_id: 601, filename: "rapid-A.mp4" }] }));
    await flush();
    assert.ok(element("reportVideo").innerHTML.includes("rapid-C.mp4"));
    assert.ok(!element("reportVideo").innerHTML.includes("rapid-A.mp4"));
    assert.ok(!element("reportVideo").innerHTML.includes("rapid-B.mp4"));
    assert.strictEqual(element("reportVideo").value, "");

    // All accounts remains usable; empty and then valid lists update the preview state.
    change("reportUploader", "");
    const allAccounts = lastRequest(url => url === "/api/videos");
    allAccounts.gate.resolve(success({ videos: [{ db_id: 700, filename: "all-accounts.mp4" }] }));
    await flush();
    assert.strictEqual(element("reportVideo").disabled, false);
    change("reportVideo", "700");
    const allPreview = lastRequest(url => url.includes("video_id=700"));
    allPreview.gate.resolve(success(preview(700)));
    await flush();
    assert.ok(element("reportPreviewCases").innerHTML.includes("VIO-000700"));

    change("reportUploader", "empty-account");
    const emptyList = lastRequest(url => url.includes("uploader_id=empty-account"));
    emptyList.gate.resolve(success({ videos: [] }));
    await flush();
    assert.strictEqual(element("reportPreviewState").textContent, "No videos");
    assert.strictEqual(element("reportPreviewTotal").textContent, "—");
    assert.ok(!element("reportPreviewCases").innerHTML.includes("VIO-000700"));
    change("reportUploader", "valid-account");
    const validList = lastRequest(url => url.includes("uploader_id=valid-account"));
    validList.gate.resolve(success({ videos: [{ db_id: 701, filename: "valid.mp4" }] }));
    await flush();
    change("reportVideo", "701");
    const validPreview = lastRequest(url => url.includes("video_id=701"));
    validPreview.gate.resolve(success(preview(701)));
    await flush();
    assert.ok(element("reportPreviewCases").innerHTML.includes("VIO-000701"));

    // Normal pagination and filter changes continue to request the right page.
    change("reportVideo", "5");
    const firstPage = lastRequest(url => url.includes("video_id=5"));
    firstPage.gate.resolve(success(preview(50, 1, 2)));
    await flush();
    element("reportPreviewNext").listeners.click();
    const secondPage = lastRequest(url => url.includes("video_id=5") && url.includes("page=2"));
    secondPage.gate.resolve(success(preview(51, 2, 2)));
    await flush();
    element("reportStatus").value = "confirmed";
    element("reportStatus").listeners.change();
    const filtered = lastRequest(url => url.includes("video_id=5") && url.includes("status=confirmed"));
    assert.ok(filtered.url.includes("page=1"));
    filtered.gate.resolve(success(preview(52, 1, 2)));
    await flush();

    console.log("Reports request race behavior tests passed");
}

run().catch(error => { console.error(error); process.exitCode = 1; });
