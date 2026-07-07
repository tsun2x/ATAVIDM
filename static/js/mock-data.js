/**
 * TAVIDM — Centralized mock data for frontend prototype.
 * Decision-support tool only. Not for autonomous enforcement.
 */
window.TAVIDM = window.TAVIDM || {};

(function () {
    "use strict";

    const VIOLATION_TYPES = [
        "Illegal Parking",
        "Counterflowing",
        "Obstruction",
        "Illegal Loading/Unloading",
        "Blocking Pedestrian Crossing",
        "Truck Ban",
        "Speeding",
        "Reckless Driving",
    ];

    const STATUSES = ["confirmed", "dismissed", "pending"];

    const CONDITIONS = ["morning", "peak", "nighttime"];

    const VIDEOS = [
        {
            id: "vid-01",
            name: "Morning Traffic — Footbridge Cam A",
            filename: "morning_traffic.mp4",
            location: "Normal Road Sector — Northbound",
            condition: "morning",
            duration: "12:34",
            processed: true,
            thumbnail: "cctv_inbound.svg",
        },
        {
            id: "vid-02",
            name: "Peak Hour — Footbridge Cam B",
            filename: "peak_traffic.mp4",
            location: "Normal Road Sector — Southbound",
            condition: "peak",
            duration: "18:02",
            processed: true,
            thumbnail: "cctv_outbound.svg",
        },
        {
            id: "vid-03",
            name: "Nighttime — Footbridge Cam A",
            filename: "night_traffic.mp4",
            location: "Normal Road Sector — Northbound",
            condition: "nighttime",
            duration: "09:47",
            processed: false,
            thumbnail: "cctv_feed.svg",
        },
    ];

    function vtypeSlug(type) {
        return type.toLowerCase().replace(/\s+/g, "-").replace(/\//g, "-");
    }

    function generateViolations(count) {
        const violations = [];
        const base = new Date();
        for (let i = 1; i <= count; i++) {
            const type = VIOLATION_TYPES[i % VIOLATION_TYPES.length];
            const video = VIDEOS[i % VIDEOS.length];
            const ts = new Date(base - (i * 3600000 + Math.random() * 1800000));
            const confidence = Math.round((0.58 + Math.random() * 0.38) * 100) / 100;
            const status = confidence < 0.75 ? "pending" : (Math.random() > 0.85 ? "dismissed" : "confirmed");
            violations.push({
                id: "VIO-" + String(2026) + String(i).padStart(4, "0"),
                type: type,
                type_slug: vtypeSlug(type),
                video_id: video.id,
                video_name: video.name,
                track_id: Math.floor(Math.random() * 20) + 1,
                timestamp: ts.toISOString().slice(0, 19).replace("T", " "),
                timestamp_iso: ts.toISOString(),
                confidence: confidence,
                status: status,
                condition: video.condition,
                evidence_image: "evidence_" + ((i % 6) + 1) + ".svg",
                reason_log: "Rule-based detection via YOLOv8 + ByteTrack pipeline (mock).",
                frame_number: Math.floor(Math.random() * 18000) + 100,
            });
        }
        violations.sort(function (a, b) {
            return b.timestamp_iso.localeCompare(a.timestamp_iso);
        });
        return violations;
    }

    const MOCK_VIOLATIONS = generateViolations(48);

    const REVIEW_QUEUE = MOCK_VIOLATIONS
        .filter(function (v) { return v.status === "pending"; })
        .slice(0, 12)
        .map(function (v, i) {
            return {
                id: "RQ-" + String(i + 1).padStart(4, "0"),
                violation_id: v.id,
                video_id: v.video_id,
                video_name: v.video_name,
                track_id: v.track_id,
                violation_type: v.type,
                type_slug: v.type_slug,
                confidence: v.confidence,
                frame_number: v.frame_number,
                evidence_image: v.evidence_image,
                reason_log: v.reason_log,
                queued_at: v.timestamp,
                status: "pending",
            };
        });

    const DASHBOARD_STATS = {
        total_today: 34,
        counterflow: 8,
        illegal_parking: 11,
        review_queue: REVIEW_QUEUE.length,
        videos_processed: 2,
        total_videos: VIDEOS.length,
        avg_confidence: 81.4,
        trend_today: 6.2,
    };

    const VIOLATION_SUMMARY = [
        { type: "Counterflowing", count: 42, change: 5.1, color: "#e63946", share: 22.3 },
        { type: "Illegal Parking", count: 38, change: -2.4, color: "#f59e0b", share: 20.2 },
        { type: "Obstruction", count: 28, change: 3.8, color: "#f97316", share: 14.9 },
        { type: "Speeding", count: 24, change: 1.2, color: "#f43f5e", share: 12.8 },
        { type: "Illegal Loading/Unloading", count: 19, change: -1.0, color: "#22c55e", share: 10.1 },
    ];

    const CHART_DATA = {
        hourly_labels: ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"],
        hourly_values: [2, 5, 8, 6, 11, 9, 7, 4],
        distribution_labels: VIOLATION_TYPES,
        distribution_values: [38, 42, 28, 19, 15, 12, 24, 11],
    };

    const ANALYTICS_DATA = {
        daily_labels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        daily_values: [28, 35, 31, 42, 48, 22, 18],
        monthly_labels: ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
        monthly_values: [312, 348, 329, 401, 438, 389],
        breakdown_labels: VIOLATION_TYPES,
        breakdown_values: [38, 42, 28, 19, 15, 12, 24, 11],
        condition_labels: ["Morning", "Peak", "Nighttime"],
        condition_values: [142, 198, 89],
        confidence_labels: ["≥85%", "65–84%", "<65%"],
        confidence_values: [112, 58, 19],
        kpi: {
            total_week: 224,
            peak_hour: "16:00",
            most_common: "Counterflowing",
            accuracy: 87.6,
        },
    };

    const REPORT_HISTORY = [
        { id: "RPT-001", name: "Weekly Violation Summary", type: "PDF", date: "2026-06-20", size: "2.4 MB", status: "Ready", filters: "Jun 14–20 · All types" },
        { id: "RPT-002", name: "Counterflowing Analysis", type: "Excel", date: "2026-06-15", size: "1.8 MB", status: "Ready", filters: "Jun 1–15 · Counterflowing" },
        { id: "RPT-003", name: "Peak Hour Violations", type: "PDF", date: "2026-06-10", size: "3.1 MB", status: "Ready", filters: "Peak condition" },
        { id: "RPT-004", name: "Review Queue Export", type: "Excel", date: "2026-06-05", size: "956 KB", status: "Ready", filters: "Pending items" },
    ];

    const USERS = [
        { id: 1, username: "admin", name: "Admin User", role: "admin", created_at: "2026-01-15" },
        { id: 2, username: "enforcer1", name: "Juan Dela Cruz", role: "enforcer", created_at: "2026-02-03" },
        { id: 3, username: "enforcer2", name: "Maria Santos", role: "enforcer", created_at: "2026-02-10" },
    ];

    const SYSTEM_SETTINGS = {
        confidence_threshold: 60,
        review_threshold: 75,
        speed_limit: 60,
        truck_ban_start: "06:00",
        truck_ban_end: "09:00",
        frame_skip: 2,
        evidence_retention_days: 90,
        fps: 30,
        calibration_ppm: 12.5,
    };

    const DETECTION_BOXES = [
        { label: "car", track_id: 3, x: 12, y: 35, w: 28, h: 22, confidence: 0.91 },
        { label: "jeepney", track_id: 7, x: 48, y: 28, w: 22, h: 18, confidence: 0.87 },
        { label: "motorcycle", track_id: 12, x: 68, y: 42, w: 10, h: 14, confidence: 0.84 },
    ];

    const SYSTEM_STATUS = {
        pipeline: "Ready",
        model: "YOLOv8n (mock)",
        tracker: "ByteTrack",
        db: "SQLite",
        last_processed: "peak_traffic.mp4",
    };

    window.TAVIDM.VIOLATION_TYPES = VIOLATION_TYPES;
    window.TAVIDM.STATUSES = STATUSES;
    window.TAVIDM.VIDEOS = VIDEOS;
    window.TAVIDM.VIOLATIONS = MOCK_VIOLATIONS;
    window.TAVIDM.REVIEW_QUEUE = REVIEW_QUEUE;
    window.TAVIDM.DASHBOARD_STATS = DASHBOARD_STATS;
    window.TAVIDM.VIOLATION_SUMMARY = VIOLATION_SUMMARY;
    window.TAVIDM.CHART_DATA = CHART_DATA;
    window.TAVIDM.ANALYTICS = ANALYTICS_DATA;
    window.TAVIDM.REPORT_HISTORY = REPORT_HISTORY;
    window.TAVIDM.USERS = USERS;
    window.TAVIDM.SETTINGS = SYSTEM_SETTINGS;
    window.TAVIDM.DETECTION_BOXES = DETECTION_BOXES;
    window.TAVIDM.SYSTEM_STATUS = SYSTEM_STATUS;
    window.TAVIDM.vtypeSlug = vtypeSlug;
})();
