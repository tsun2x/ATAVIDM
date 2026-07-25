/**
 * TAVIDM - Canvas polygon zone editor for traffic annotations.
 */
(function (global) {
    "use strict";

    var HIT_RADIUS = 8;

    function ZoneEditor(canvas, options) {
        this.canvas = canvas;
        this.ctx = canvas.getContext("2d");
        this.imageUrl = options.imageUrl;
        this.zoneTypes = options.zoneTypes || [];
        this.zones = options.zones || {};
        this.activeZone = options.activeZone || (this.zoneTypes[0] && this.zoneTypes[0].key);
        this.scale = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.image = null;
        this.dragIndex = -1;
        this.isPanning = false;
        this.panStart = null;
        this._destroyed = false;

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

        this._loadImage();
    }

    ZoneEditor.prototype._loadImage = function () {
        var self = this;
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

    ZoneEditor.prototype._findPointIndex = function (x, y) {
        var points = this._activePoints();
        for (var i = points.length - 1; i >= 0; i--) {
            var dx = points[i][0] - x;
            var dy = points[i][1] - y;
            if (Math.sqrt(dx * dx + dy * dy) <= HIT_RADIUS / this.scale) return i;
        }
        return -1;
    };

    ZoneEditor.prototype._handleMouseDown = function (e) {
        if (!this.image) return;
        var coords = this._toImageCoords(e.clientX, e.clientY);
        var idx = this._findPointIndex(coords[0], coords[1]);

        if (e.button === 2 || e.shiftKey) {
            this.isPanning = true;
            this.panStart = { x: e.clientX, y: e.clientY, ox: this.offsetX, oy: this.offsetY };
            e.preventDefault();
            return;
        }

        if (idx >= 0) {
            this.dragIndex = idx;
            return;
        }

        this._activePoints().push(coords);
        this.draw();
    };

    ZoneEditor.prototype._handleMouseMove = function (e) {
        if (!this.image) return;
        if (this.isPanning && this.panStart) {
            this.offsetX = this.panStart.ox + (e.clientX - this.panStart.x);
            this.offsetY = this.panStart.oy + (e.clientY - this.panStart.y);
            this.draw();
            return;
        }
        if (this.dragIndex < 0) return;
        var coords = this._toImageCoords(e.clientX, e.clientY);
        this._activePoints()[this.dragIndex] = coords;
        this.draw();
    };

    ZoneEditor.prototype._handleMouseUp = function () {
        this.dragIndex = -1;
        this.isPanning = false;
        this.panStart = null;
    };

    ZoneEditor.prototype._handleDblClick = function (e) {
        if (!this.image) return;
        var coords = this._toImageCoords(e.clientX, e.clientY);
        var idx = this._findPointIndex(coords[0], coords[1]);
        if (idx >= 0) {
            this._activePoints().splice(idx, 1);
            this.draw();
        }
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

    ZoneEditor.prototype.draw = function () {
        if (!this.ctx || !this.image) return;
        var ctx = this.ctx;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.save();
        ctx.translate(this.offsetX, this.offsetY);
        ctx.drawImage(this.image, 0, 0);

        var self = this;
        this.zoneTypes.forEach(function (zt) {
            var points = self.zones[zt.key] || [];
            if (points.length === 0) return;
            ctx.beginPath();
            points.forEach(function (pt, i) {
                if (i === 0) ctx.moveTo(pt[0], pt[1]);
                else ctx.lineTo(pt[0], pt[1]);
            });
            if (points.length >= 3) ctx.closePath();
            ctx.fillStyle = self._hexToRgba(zt.color, zt.key === self.activeZone ? 0.35 : 0.2);
            ctx.strokeStyle = zt.color;
            ctx.lineWidth = zt.key === self.activeZone ? 3 : 2;
            if (points.length >= 3) ctx.fill();
            ctx.stroke();

            points.forEach(function (pt, i) {
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], 5, 0, Math.PI * 2);
                ctx.fillStyle = zt.key === self.activeZone && i === self.dragIndex ? "#fff" : zt.color;
                ctx.fill();
                ctx.strokeStyle = "#fff";
                ctx.lineWidth = 1;
                ctx.stroke();
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
        this.draw();
    };

    ZoneEditor.prototype.resetActiveZone = function () {
        if (this.activeZone) this.zones[this.activeZone] = [];
        this.draw();
    };

    ZoneEditor.prototype.setZones = function (zones) {
        this.zones = zones || {};
        this.draw();
    };

    ZoneEditor.prototype.getZones = function () {
        return JSON.parse(JSON.stringify(this.zones));
    };

    ZoneEditor.prototype.destroy = function () {
        this._destroyed = true;
        this.canvas.removeEventListener("mousedown", this._onMouseDown);
        this.canvas.removeEventListener("mousemove", this._onMouseMove);
        window.removeEventListener("mouseup", this._onMouseUp);
        this.canvas.removeEventListener("dblclick", this._onDblClick);
        this.canvas.removeEventListener("wheel", this._onWheel);
    };

    global.TAVIDMZoneEditor = {
        create: function (canvas, options) {
            return new ZoneEditor(canvas, options);
        },
        emptyZones: function (zoneTypes) {
            var zones = {};
            (zoneTypes || []).forEach(function (zt) { zones[zt.key] = []; });
            return zones;
        },
        parseZones: function (raw, zoneTypes) {
            var zones = global.TAVIDMZoneEditor.emptyZones(zoneTypes);
            var data = raw;
            if (typeof raw === "string") {
                try { data = JSON.parse(raw); } catch (e) { data = {}; }
            }
            Object.keys(zones).forEach(function (key) {
                if (data && data[key]) zones[key] = data[key].map(function (pt) { return [pt[0], pt[1]]; });
            });
            return zones;
        },
        zonesComplete: function (zones, zoneTypes, minPoints) {
            // At least one zone drawn; every drawn zone needs >= minPoints points.
            minPoints = minPoints || 3;
            const drawn = (zoneTypes || [])
                .map(function (zt) { return zones[zt.key] || []; })
                .filter(function (pts) { return pts.length > 0; });
            if (!drawn.length) return false;
            return drawn.every(function (pts) { return pts.length >= minPoints; });
        },
    };
})(window);
