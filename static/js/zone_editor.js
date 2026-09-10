/**
 * TAVIDM - Canvas polygon zone editor for traffic annotations.
 */
(function (global) {
    "use strict";

    var HIT_RADIUS = 8;
    var V2_ARRAY_KEYS = ["zones", "lanes", "flow_arrows", "threshold_lines", "markings", "signs", "activity_regions"];
    var MIN_POINTS = {
        zones: 3,
        lanes: 3,
        flow_arrows: 2,
        threshold_lines: 2,
        markings: 2,
        signs: 2,
        activity_regions: 3,
    };
    var SCENE_KINDS = [
        { key: "zones", label: "Zones", color: "#e63946" },
        {
            key: "lanes",
            label: "Lanes",
            color: "#3b82f6",
            types: [{ key: "active_lane", label: "Active Lane", color: "#3b82f6" }],
        },
        {
            key: "flow_arrows",
            label: "Flow arrows",
            color: "#22d3ee",
            types: [{ key: "lane_flow", label: "Lane flow", color: "#22d3ee" }],
        },
        {
            key: "threshold_lines",
            label: "Thresholds",
            color: "#f97316",
            types: [
                { key: "threshold", label: "Threshold", color: "#f97316" },
                { key: "no_entry_threshold", label: "No-entry threshold", color: "#ef4444" },
            ],
        },
        {
            key: "markings",
            label: "Markings",
            color: "#eab308",
            types: [
                { key: "double_solid", label: "Double solid", color: "#eab308" },
                { key: "single_solid", label: "Single solid", color: "#facc15" },
                { key: "solid_broken", label: "Solid/broken", color: "#fde047" },
                { key: "restricted_lane_boundary", label: "Restricted boundary", color: "#ec4899" },
                { key: "generic_marking", label: "Generic marking", color: "#a3a3a3" },
            ],
        },
        {
            key: "signs",
            label: "Signs",
            color: "#ef4444",
            types: [
                { key: "no_entry", label: "No entry", color: "#ef4444" },
                { key: "no_left_turn", label: "No left turn", color: "#f87171" },
                { key: "no_right_turn", label: "No right turn", color: "#fb7185" },
                { key: "no_u_turn", label: "No U-turn", color: "#e11d48" },
                { key: "no_overtaking", label: "No overtaking", color: "#be123c" },
                { key: "generic_sign", label: "Generic sign", color: "#9f1239" },
            ],
        },
        {
            key: "activity_regions",
            label: "Activity",
            color: "#a855f7",
            types: [
                { key: "passenger_activity", label: "Passenger activity", color: "#a855f7" },
                { key: "boarding_alighting", label: "Boarding/alighting", color: "#c084fc" },
            ],
        },
    ];

    function emptySceneV2() {
        return {
            schema_version: 2,
            zones: [],
            lanes: [],
            flow_arrows: [],
            threshold_lines: [],
            markings: [],
            signs: [],
            activity_regions: [],
        };
    }

    function cloneJson(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function looksV2(data) {
        if (!data || typeof data !== "object") return false;
        if (data.schema_version === 2) return true;
        return V2_ARRAY_KEYS.some(function (key) {
            return key !== "zones" && Array.isArray(data[key]);
        });
    }

    function typeColor(kind, type, zoneTypes) {
        var i;
        if (kind === "zones") {
            for (i = 0; i < (zoneTypes || []).length; i++) {
                if (zoneTypes[i].key === type) return zoneTypes[i].color;
            }
        }
        for (i = 0; i < SCENE_KINDS.length; i++) {
            if (SCENE_KINDS[i].key !== kind) continue;
            var types = SCENE_KINDS[i].types || [];
            for (var j = 0; j < types.length; j++) {
                if (types[j].key === type) return types[j].color;
            }
            return SCENE_KINDS[i].color;
        }
        return "#e63946";
    }

    function ZoneEditor(canvas, options) {
        options = options || {};
        this.canvas = canvas;
        this.ctx = canvas.getContext("2d");
        this.imageUrl = options.imageUrl;
        this.zoneTypes = options.zoneTypes || [];
        this.zones = options.zones || {};
        this.activeZone = options.activeZone || (this.zoneTypes[0] && this.zoneTypes[0].key);
        this.objectKind = options.objectKind || "zones";
        this.objectType = options.objectType || this.activeZone || "no_parking";
        this.prohibitedFrom = options.prohibitedFrom || "both";
        this.onChange = options.onChange || null;
        this.scale = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.image = options.image || null;
        this.dragIndex = -1;
        this.isPanning = false;
        this.panStart = null;
        this._destroyed = false;
        this.scene = new SceneObjectEditor({
            document: options.sceneDocument || null,
        });
        if (!options.sceneDocument && options.zones) {
            this.scene.loadDocument(buildSceneDocument(options.zones, {}));
        }
        this._syncLegacyFromScene();
        if (this.activeZone === "active_lane" && this.objectKind === "zones") {
            this.objectKind = "lanes";
            this.objectType = "active_lane";
        }

        this._onMouseDown = this._handleMouseDown.bind(this);
        this._onMouseMove = this._handleMouseMove.bind(this);
        this._onMouseUp = this._handleMouseUp.bind(this);
        this._onDblClick = this._handleDblClick.bind(this);
        this._onWheel = this._handleWheel.bind(this);

        this.canvas.addEventListener("mousedown", this._onMouseDown);
        this.canvas.addEventListener("mousemove", this._onMouseMove);
        window.addEventListener("mouseup", this._onMouseUp);
        this.canvas.addEventListener("dblclick", this._onDblClick);
        this.canvas.addEventListener("wheel", this._onWheel, { passive: false });

        if (this.image) {
            this.canvas.width = this.image.naturalWidth || this.canvas.width || 400;
            this.canvas.height = this.image.naturalHeight || this.canvas.height || 300;
            this.draw();
        } else {
            this._loadImage();
        }
    }

    ZoneEditor.prototype._emitChange = function () {
        this._syncLegacyFromScene();
        if (typeof this.onChange === "function") this.onChange(this);
    };

    ZoneEditor.prototype._syncLegacyFromScene = function () {
        this.zones = parseZones(this.scene.toDocument(), this.zoneTypes);
    };

    ZoneEditor.prototype._loadImage = function () {
        var self = this;
        if (!this.imageUrl) return;
        var img = new Image();
        img.onload = function () {
            if (self._destroyed) return;
            self.image = img;
            self.canvas.width = img.naturalWidth;
            self.canvas.height = img.naturalHeight;
            self._fitToContainer();
            self.draw();
        };
        img.onerror = function () {
            if (typeof showToast === "function") {
                showToast("Editor Error", "Could not load reference frame.", "danger");
            }
        };
        img.src = this.imageUrl;
    };

    ZoneEditor.prototype._fitToContainer = function () {
        var parent = this.canvas.parentElement;
        if (!parent || !this.image) return;
        var maxW = parent.clientWidth || this.image.naturalWidth;
        var maxH = parent.clientHeight || 480;
        var ratio = Math.min(maxW / this.image.naturalWidth, maxH / this.image.naturalHeight, 1);
        this.scale = ratio;
        this.canvas.style.width = Math.round(this.image.naturalWidth * ratio) + "px";
        this.canvas.style.height = Math.round(this.image.naturalHeight * ratio) + "px";
    };

    ZoneEditor.prototype._toImageCoords = function (clientX, clientY) {
        var rect = this.canvas.getBoundingClientRect();
        var x = (clientX - rect.left) / this.scale;
        var y = (clientY - rect.top) / this.scale;
        return [x, y];
    };

    ZoneEditor.prototype._activePoints = function () {
        var found = this.scene.findById(this.scene.selectedId);
        if (found) return found.object.points;
        if (!this.activeZone) return [];
        if (!this.zones[this.activeZone]) this.zones[this.activeZone] = [];
        return this.zones[this.activeZone];
    };

    ZoneEditor.prototype._zoneMeta = function (key) {
        for (var i = 0; i < this.zoneTypes.length; i++) {
            if (this.zoneTypes[i].key === key) return this.zoneTypes[i];
        }
        return { label: key, color: "#e63946" };
    };

    ZoneEditor.prototype._hitPoint = function (x, y) {
        var radius = HIT_RADIUS / this.scale;
        var kinds = Object.keys(this.scene.objects);
        var k, j, i, arr, pts, dx, dy;
        if (this.scene.selectedId) {
            var selected = this.scene.findById(this.scene.selectedId);
            if (selected) {
                pts = selected.object.points || [];
                for (i = pts.length - 1; i >= 0; i--) {
                    dx = pts[i][0] - x;
                    dy = pts[i][1] - y;
                    if (Math.sqrt(dx * dx + dy * dy) <= radius) {
                        return { id: selected.object.id, kind: selected.kind, pointIndex: i };
                    }
                }
            }
        }
        for (k = 0; k < kinds.length; k++) {
            arr = this.scene.objects[kinds[k]] || [];
            for (j = 0; j < arr.length; j++) {
                pts = arr[j].points || [];
                for (i = pts.length - 1; i >= 0; i--) {
                    dx = pts[i][0] - x;
                    dy = pts[i][1] - y;
                    if (Math.sqrt(dx * dx + dy * dy) <= radius) {
                        return { id: arr[j].id, kind: kinds[k], pointIndex: i };
                    }
                }
            }
        }
        return null;
    };

    ZoneEditor.prototype._selectedOrFirstLane = function () {
        var found = this.scene.findById(this.scene.selectedId);
        if (found && found.kind === "lanes") return found.object;
        return this.scene.objects.lanes[0] || null;
    };

    ZoneEditor.prototype._ensureCreating = function (coords) {
        var found = this.scene.findById(this.scene.selectedId);
        if (
            found &&
            found.kind === this.objectKind &&
            found.object.type === this.objectType
        ) {
            found.object.points.push([coords[0], coords[1]]);
            return found.object;
        }
        var fields = {};
        if (this.objectKind === "flow_arrows") {
            var lane = this._selectedOrFirstLane();
            if (lane) fields.lane_ids = [lane.id];
        }
        if (this.objectKind === "markings" && this.objectType === "double_solid") {
            fields.prohibited_from = this.prohibitedFrom || "both";
        }
        return this.scene.create(this.objectKind, this.objectType, [coords], fields);
    };

    ZoneEditor.prototype._handleMouseDown = function (e) {
        if (!this.image) return;
        var coords = this._toImageCoords(e.clientX, e.clientY);

        if (e.button === 2 || e.shiftKey) {
            this.isPanning = true;
            this.panStart = { x: e.clientX, y: e.clientY, ox: this.offsetX, oy: this.offsetY };
            e.preventDefault();
            return;
        }

        var hit = this._hitPoint(coords[0], coords[1]);
        if (hit) {
            this.scene.select(hit.id);
            this.objectKind = hit.kind;
            this.objectType = this.scene.findById(hit.id).object.type;
            this.dragIndex = hit.pointIndex;
            this.draw();
            this._emitChange();
            return;
        }

        this._ensureCreating(coords);
        this.draw();
        this._emitChange();
    };

    ZoneEditor.prototype._handleMouseMove = function (e) {
        if (!this.image) return;
        if (this.isPanning && this.panStart) {
            this.offsetX = this.panStart.ox + (e.clientX - this.panStart.x);
            this.offsetY = this.panStart.oy + (e.clientY - this.panStart.y);
            this.draw();
            return;
        }
        if (this.dragIndex < 0 || !this.scene.selectedId) return;
        var coords = this._toImageCoords(e.clientX, e.clientY);
        this.scene.movePoint(this.scene.selectedId, this.dragIndex, coords);
        this.draw();
        this._emitChange();
    };

    ZoneEditor.prototype._handleMouseUp = function () {
        this.dragIndex = -1;
        this.isPanning = false;
        this.panStart = null;
    };

    ZoneEditor.prototype._handleDblClick = function (e) {
        if (!this.image) return;
        var coords = this._toImageCoords(e.clientX, e.clientY);
        var hit = this._hitPoint(coords[0], coords[1]);
        if (!hit) return;
        var found = this.scene.findById(hit.id);
        if (!found) return;
        found.object.points.splice(hit.pointIndex, 1);
        if (!found.object.points.length) this.scene.delete(hit.id);
        else this.scene.select(hit.id);
        this.draw();
        this._emitChange();
    };

    ZoneEditor.prototype._handleWheel = function (e) {
        e.preventDefault();
        var delta = e.deltaY > 0 ? -0.1 : 0.1;
        this.scale = Math.min(Math.max(this.scale + delta, 0.2), 3);
        if (this.image) {
            this.canvas.style.width = Math.round(this.image.naturalWidth * this.scale) + "px";
            this.canvas.style.height = Math.round(this.image.naturalHeight * this.scale) + "px";
        }
        this.draw();
    };

    ZoneEditor.prototype._drawArrowHead = function (ctx, from, to, color) {
        var dx = to[0] - from[0];
        var dy = to[1] - from[1];
        var len = Math.sqrt(dx * dx + dy * dy) || 1;
        var ux = dx / len;
        var uy = dy / len;
        var size = 12;
        ctx.beginPath();
        ctx.moveTo(to[0], to[1]);
        ctx.lineTo(to[0] - ux * size - uy * size * 0.5, to[1] - uy * size + ux * size * 0.5);
        ctx.lineTo(to[0] - ux * size + uy * size * 0.5, to[1] - uy * size - ux * size * 0.5);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
    };

    ZoneEditor.prototype._drawObject = function (kind, obj) {
        var ctx = this.ctx;
        var points = obj.points || [];
        if (!points.length) return;
        var selected = obj.id === this.scene.selectedId;
        var color = typeColor(kind, obj.type, this.zoneTypes);
        var closed = kind === "zones" || kind === "lanes" || kind === "activity_regions" || kind === "signs";
        ctx.beginPath();
        points.forEach(function (pt, i) {
            if (i === 0) ctx.moveTo(pt[0], pt[1]);
            else ctx.lineTo(pt[0], pt[1]);
        });
        if (closed && points.length >= 3) ctx.closePath();
        ctx.strokeStyle = color;
        ctx.lineWidth = selected ? 3 : 2;
        if (closed && points.length >= 3) {
            ctx.fillStyle = this._hexToRgba(color, selected ? 0.35 : 0.18);
            ctx.fill();
        }
        ctx.stroke();
        if ((kind === "flow_arrows" || kind === "threshold_lines") && points.length >= 2) {
            this._drawArrowHead(ctx, points[0], points[points.length - 1], color);
        }
        var self = this;
        points.forEach(function (pt, i) {
            ctx.beginPath();
            ctx.arc(pt[0], pt[1], 5, 0, Math.PI * 2);
            ctx.fillStyle = selected && i === self.dragIndex ? "#fff" : color;
            ctx.fill();
            ctx.strokeStyle = "#fff";
            ctx.lineWidth = 1;
            ctx.stroke();
        });
        if (selected && points[0]) {
            ctx.fillStyle = "#fff";
            ctx.font = "12px sans-serif";
            ctx.fillText(obj.id + " · " + obj.type, points[0][0] + 8, points[0][1] - 8);
        }
    };

    ZoneEditor.prototype.draw = function () {
        if (!this.ctx || !this.image) return;
        var ctx = this.ctx;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.save();
        ctx.translate(this.offsetX, this.offsetY);
        ctx.drawImage(this.image, 0, 0);
        var self = this;
        V2_ARRAY_KEYS.forEach(function (kind) {
            (self.scene.objects[kind] || []).forEach(function (obj) {
                self._drawObject(kind, obj);
            });
        });
        ctx.restore();
    };

    ZoneEditor.prototype._hexToRgba = function (hex, alpha) {
        var h = hex.replace("#", "");
        var r = parseInt(h.substring(0, 2), 16);
        var g = parseInt(h.substring(2, 4), 16);
        var b = parseInt(h.substring(4, 6), 16);
        return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
    };

    ZoneEditor.prototype.setActiveZone = function (key) {
        this.activeZone = key;
        if (key === "active_lane") {
            this.objectKind = "lanes";
            this.objectType = "active_lane";
        } else {
            this.objectKind = "zones";
            this.objectType = key;
        }
        this.draw();
    };

    ZoneEditor.prototype.setObjectKind = function (kind, type) {
        this.objectKind = kind || "zones";
        if (type) this.objectType = type;
        else if (kind === "zones") this.objectType = this.activeZone || "no_parking";
        else {
            var def = SCENE_KINDS.filter(function (k) { return k.key === kind; })[0];
            this.objectType = def && def.types && def.types[0] ? def.types[0].key : kind;
        }
        if (kind === "lanes") this.activeZone = "active_lane";
        else if (kind === "zones") this.activeZone = this.objectType;
        this.scene.selectedId = null;
        this.draw();
        this._emitChange();
    };

    ZoneEditor.prototype.newObject = function () {
        this.scene.selectedId = null;
        this.draw();
        this._emitChange();
        return true;
    };

    ZoneEditor.prototype.deleteSelected = function () {
        if (!this.scene.selectedId) return false;
        var ok = this.scene.delete(this.scene.selectedId);
        this.draw();
        this._emitChange();
        return ok;
    };

    ZoneEditor.prototype.resetActiveZone = function () {
        if (this.scene.selectedId) {
            this.deleteSelected();
            return;
        }
        var kind = this.objectKind;
        var type = this.objectType;
        var remaining = [];
        (this.scene.objects[kind] || []).forEach(function (obj) {
            if (obj.type !== type) remaining.push(obj);
        });
        this.scene.objects[kind] = remaining;
        this.draw();
        this._emitChange();
    };

    ZoneEditor.prototype.setZones = function (zones) {
        this.scene.loadDocument(buildSceneDocument(zones || {}, {}));
        this._syncLegacyFromScene();
        this.draw();
    };

    ZoneEditor.prototype.getZones = function () {
        this._syncLegacyFromScene();
        return cloneJson(this.zones);
    };

    ZoneEditor.prototype.getSceneDocument = function (options) {
        return this.scene.toDocument(options);
    };

    ZoneEditor.prototype.loadSceneDocument = function (doc) {
        var loaded = this.scene.loadDocument(doc);
        this._syncLegacyFromScene();
        this.draw();
        this._emitChange();
        return loaded;
    };

    ZoneEditor.prototype.selectedObject = function () {
        var found = this.scene.findById(this.scene.selectedId);
        return found ? found.object : null;
    };

    ZoneEditor.prototype.destroy = function () {
        this._destroyed = true;
        this.canvas.removeEventListener("mousedown", this._onMouseDown);
        this.canvas.removeEventListener("mousemove", this._onMouseMove);
        window.removeEventListener("mouseup", this._onMouseUp);
        this.canvas.removeEventListener("dblclick", this._onDblClick);
        this.canvas.removeEventListener("wheel", this._onWheel);
    };

    /**
     * Structured v2 scene object layer. Extends the legacy polygon editor
     * without replacing it. Objects: lanes, flow_arrows, threshold_lines,
     * markings, signs, activity_regions. A→B orientation is point[0]→point[n].
     */
    function SceneObjectEditor(options) {
        options = options || {};
        this.objects = {
            zones: [],
            lanes: [],
            flow_arrows: [],
            threshold_lines: [],
            markings: [],
            signs: [],
            activity_regions: [],
        };
        this.activeKind = options.activeKind || "lanes";
        this.selectedId = null;
        this._idSeq = 1;
        if (options.document) this.loadDocument(options.document);
    }

    SceneObjectEditor.prototype._newId = function (prefix) {
        var id = prefix + "-" + this._idSeq++;
        while (this.findById(id)) {
            id = prefix + "-" + this._idSeq++;
        }
        return id;
    };

    SceneObjectEditor.prototype.findById = function (id) {
        var kinds = Object.keys(this.objects);
        for (var i = 0; i < kinds.length; i++) {
            var arr = this.objects[kinds[i]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].id === id) return { kind: kinds[i], object: arr[j], index: j };
            }
        }
        return null;
    };

    SceneObjectEditor.prototype.create = function (kind, type, points, fields) {
        kind = kind || this.activeKind;
        if (!this.objects[kind]) throw new Error("Unsupported kind: " + kind);
        var obj = {
            id: (fields && fields.id) || this._newId(kind.replace(/s$/, "")),
            type: type,
            points: (points || []).map(function (pt) { return [pt[0], pt[1]]; }),
        };
        if (fields) {
            Object.keys(fields).forEach(function (k) {
                if (k === "id" || k === "type" || k === "points") return;
                obj[k] = fields[k];
            });
        }
        this.objects[kind].push(obj);
        this.selectedId = obj.id;
        return obj;
    };

    SceneObjectEditor.prototype.select = function (id) {
        this.selectedId = this.findById(id) ? id : null;
        return this.selectedId;
    };

    SceneObjectEditor.prototype.movePoint = function (id, pointIndex, xy) {
        var found = this.findById(id);
        if (!found) return false;
        if (pointIndex < 0 || pointIndex >= found.object.points.length) return false;
        found.object.points[pointIndex] = [xy[0], xy[1]];
        return true;
    };

    SceneObjectEditor.prototype.delete = function (id) {
        var found = this.findById(id);
        if (!found) return false;
        found.object; // keep reference clarity
        this.objects[found.kind].splice(found.index, 1);
        if (this.selectedId === id) this.selectedId = null;
        return true;
    };

    SceneObjectEditor.prototype.loadDocument = function (doc) {
        var empty = emptySceneV2();
        var data = doc || empty;
        if (typeof data === "string") {
            try { data = JSON.parse(data); } catch (e) { data = empty; }
        }
        var self = this;
        Object.keys(this.objects).forEach(function (key) {
            self.objects[key] = cloneJson(data[key] || []);
        });
        this.selectedId = null;
        var max = 0;
        Object.keys(this.objects).forEach(function (key) {
            self.objects[key].forEach(function (obj) {
                var m = String(obj.id || "").match(/(\d+)$/);
                if (m) max = Math.max(max, parseInt(m[1], 10));
            });
        });
        this._idSeq = max + 1;
        return this.toDocument();
    };

    SceneObjectEditor.prototype.toDocument = function (options) {
        options = options || {};
        var doc = emptySceneV2();
        var self = this;
        Object.keys(this.objects).forEach(function (key) {
            var arr = cloneJson(self.objects[key]);
            if (options.completeOnly) {
                arr = arr.filter(function (obj) {
                    var min = MIN_POINTS[key] || 2;
                    if (!obj.points || obj.points.length < min) return false;
                    if (key === "flow_arrows" && (!obj.lane_ids || !obj.lane_ids.length)) return false;
                    if (key === "markings" && obj.type === "double_solid" && !obj.prohibited_from) return false;
                    return true;
                });
            }
            doc[key] = arr;
        });
        return doc;
    };

    SceneObjectEditor.prototype.orientationHint = function (id) {
        var found = this.findById(id);
        if (!found || found.object.points.length < 2) return null;
        var a = found.object.points[0];
        var b = found.object.points[found.object.points.length - 1];
        return { from: a, to: b, label: "A→B (prohibited_from relative to this orientation)" };
    };

    function emptyZones(zoneTypes) {
        var zones = {};
        (zoneTypes || []).forEach(function (zt) { zones[zt.key] = []; });
        return zones;
    }

    function parseZones(raw, zoneTypes) {
        var zones = emptyZones(zoneTypes);
        var data = raw;
        if (typeof raw === "string") {
            try { data = JSON.parse(raw); } catch (e) { data = {}; }
        }
        if (!data || typeof data !== "object") return zones;
        Object.keys(zones).forEach(function (key) {
            if (data[key] && Array.isArray(data[key]) && !(data[key][0] && data[key][0].type)) {
                zones[key] = data[key].map(function (pt) { return [pt[0], pt[1]]; });
            }
        });
        if (data.schema_version === 2 || looksV2(data)) {
            (data.zones || []).forEach(function (obj) {
                if (obj && obj.type && zones.hasOwnProperty(obj.type) && (!zones[obj.type] || !zones[obj.type].length)) {
                    zones[obj.type] = (obj.points || []).map(function (pt) { return [pt[0], pt[1]]; });
                }
            });
            if ((!zones.active_lane || !zones.active_lane.length) && data.lanes && data.lanes[0]) {
                zones.active_lane = (data.lanes[0].points || []).map(function (pt) { return [pt[0], pt[1]]; });
            }
        }
        return zones;
    }

    function buildSceneDocument(legacyZones, structured) {
        var doc = emptySceneV2();
        structured = structured || {};
        V2_ARRAY_KEYS.forEach(function (key) {
            if (Array.isArray(structured[key])) doc[key] = structured[key];
        });
        Object.keys(legacyZones || {}).forEach(function (key) {
            var pts = legacyZones[key] || [];
            if (pts.length < 3) return;
            if (key === "active_lane" && (!doc.lanes || !doc.lanes.length)) {
                doc.lanes.push({
                    id: "lane-" + key,
                    type: "active_lane",
                    points: pts.map(function (pt) { return [pt[0], pt[1]]; }),
                });
                return;
            }
            doc.zones.push({
                id: "zone-" + key,
                type: key,
                points: pts.map(function (pt) { return [pt[0], pt[1]]; }),
            });
        });
        return doc;
    }

    function parseSceneDocument(raw, zoneTypes) {
        var data = raw;
        if (typeof raw === "string") {
            try { data = JSON.parse(raw); } catch (e) { data = {}; }
        }
        if (!data || typeof data !== "object") return emptySceneV2();
        if (data.schema_version === 2 || looksV2(data)) {
            var doc = emptySceneV2();
            V2_ARRAY_KEYS.forEach(function (key) {
                doc[key] = cloneJson(data[key] || []);
            });
            Object.keys(data).forEach(function (key) {
                if (key === "schema_version" || doc.hasOwnProperty(key)) return;
                doc[key] = cloneJson(data[key]);
            });
            return doc;
        }
        return buildSceneDocument(parseZones(data, zoneTypes), {});
    }

    function validateSceneDocument(doc) {
        if (!doc || typeof doc !== "object") return { ok: false, error: "Scene must be an object." };
        if (doc.schema_version === 2) {
            var ids = {};
            for (var ai = 0; ai < V2_ARRAY_KEYS.length; ai++) {
                var arr = doc[V2_ARRAY_KEYS[ai]] || [];
                if (!Array.isArray(arr)) return { ok: false, error: V2_ARRAY_KEYS[ai] + " must be a list." };
                for (var i = 0; i < arr.length; i++) {
                    var obj = arr[i];
                    if (!obj || !obj.id || !obj.type || !Array.isArray(obj.points)) {
                        return { ok: false, error: "Each scene object needs id, type, points." };
                    }
                    if (ids[obj.id]) return { ok: false, error: "Duplicate id: " + obj.id };
                    ids[obj.id] = true;
                    if (obj.prohibited_from && ["left", "right", "both"].indexOf(obj.prohibited_from) < 0) {
                        return { ok: false, error: "prohibited_from must be left|right|both." };
                    }
                }
            }
            return { ok: true };
        }
        return { ok: true };
    }

    function zonesComplete(zones, zoneTypes, minPoints) {
        minPoints = minPoints || 3;
        var drawn = (zoneTypes || [])
            .map(function (zt) { return zones[zt.key] || []; })
            .filter(function (pts) { return pts.length > 0; });
        if (!drawn.length) return false;
        return drawn.every(function (pts) { return pts.length >= minPoints; });
    }

    function sceneSaveReady(doc, zoneTypes) {
        return zonesComplete(parseZones(doc, zoneTypes), zoneTypes, 3);
    }

    global.TAVIDMZoneEditor = {
        SCENE_KINDS: SCENE_KINDS,
        create: function (canvas, options) {
            return new ZoneEditor(canvas, options);
        },
        createSceneObjectEditor: function (options) {
            return new SceneObjectEditor(options || {});
        },
        emptyZones: emptyZones,
        parseZones: parseZones,
        emptySceneV2: emptySceneV2,
        buildSceneDocument: buildSceneDocument,
        parseSceneDocument: parseSceneDocument,
        validateSceneDocument: validateSceneDocument,
        sceneSaveReady: sceneSaveReady,
        zonesComplete: zonesComplete,
    };
})(typeof window !== "undefined" ? window : global);
