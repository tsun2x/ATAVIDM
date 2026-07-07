/**
 * TAVIDM - Video Upload (MP4)
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
    const videoList = document.getElementById("videoList");

    if (!dropzone || !fileInput) return;

    const maxMb = parseInt(dropzone.dataset.maxMb || "500", 10);
    const maxBytes = maxMb * 1024 * 1024;
    let selectedFile = null;
    let activeXhr = null;

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

    function appendVideoToList(video) {
        if (!videoList) return;
        const btn = document.createElement("button");
        btn.className = "camera-item";
        btn.dataset.videoId = video.id;
        btn.dataset.videoName = video.name;
        btn.dataset.videoLocation = video.location;
        btn.dataset.videoCondition = video.condition;
        btn.dataset.videoDuration = video.duration;
        btn.dataset.videoThumbnail = video.thumbnail;
        btn.dataset.videoProcessed = video.processed ? "true" : "false";
        btn.innerHTML =
            '<div class="camera-thumb"><img src="/static/images/camera_thumb.svg" alt="">' +
            '<span class="cam-status-dot offline"></span></div>' +
            '<div class="camera-info"><span class="camera-name">' + video.filename +
            ' <span class="upload-badge">NEW</span></span>' +
            '<span class="camera-location">' + video.condition + " · Pending processing</span></div>" +
            '<i class="bi bi-chevron-right"></i>';
        videoList.insertBefore(btn, videoList.firstChild);
        videoList.querySelectorAll(".camera-item").forEach(function (el) { el.classList.remove("active"); });
        btn.classList.add("active");
        btn.click();
    }

    function uploadFile() {
        if (!selectedFile || activeXhr) return;

        const formData = new FormData();
        formData.append("video", selectedFile);
        formData.append("condition", uploadCondition?.value || "peak");

        const xhr = new XMLHttpRequest();
        activeXhr = xhr;

        btnUpload.disabled = true;
        btnCancel.disabled = false;
        btnUpload.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Uploading...';
        progressWrap.classList.remove("d-none");

        xhr.upload.addEventListener("progress", function (e) {
            if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                progressBar.style.width = pct + "%";
            }
        });

        xhr.addEventListener("load", function () {
            activeXhr = null;
            btnCancel.disabled = true;
            let payload;
            try { payload = JSON.parse(xhr.responseText); } catch (err) {
                showToast("Upload Error", "Invalid server response.", "danger");
                btnUpload.disabled = false;
                btnUpload.innerHTML = '<i class="bi bi-upload me-1"></i> Upload';
                return;
            }
            if (xhr.status >= 200 && xhr.status < 300 && payload.success) {
                showToast("Upload Complete", payload.message, "success");
                appendVideoToList(payload.video);
                resetSelection();
            } else {
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
        if (activeXhr) {
            activeXhr.abort();
        } else {
            resetSelection();
        }
    });

    window.TAVIDM_upload = { resetSelection: resetSelection };
})();
