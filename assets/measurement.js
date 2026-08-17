// assets/measurement.js
// Mess-Tool fuer den PyBack-Playground (Plotly 3.x, QWebEngineView).
//
// USER-REQ (17.08.2026): Mess-Tool aus PyTrader (chart/js/05_measurement.js,
// Lightweight Charts v5) wirtschaftlich uebernommen und auf Plotly adaptiert.
// Aktivierung: Shift + RECHTS-Maustaste zieht eine Messbox; linker Klick oder
// Escape entfernt sie.
//
// Adaption (Plotly statt LWC v5):
//   - Koordinaten: gd._fullLayout.xaxis.p2l/l2p + yaxis.p2l/l2p (exakte ms,
//     Plotly linearisiert Datumsachsen als UTC-ms -> Wanduhr-Korrektur, da der
//     Chart Wanduhr-ISO-Strings zeigt; kein Berlin-Offset, Projekt-Konvention).
//   - Trigger: e.shiftKey && e.button === 2 (statt e.ctrlKey + Linksklick).
//   - Kein pyBridge-Sync (PyBack-Playground haelt den Zustand clientseitig;
//     die Box ist temporaer bis zum naechsten linken Klick/Escape).
//   - Beibehalten: CSS-Overlay (#measurement-region/-box), Live-Messtext
//     (Delta Preis % + absolut, Delta Zeit Bars + DD:HH:MM, Start/Ende),
//     updatePositions bei Zoom/Pan/react (plotly_afterplot/plotly_relayout).
//
// Die reinen Helfer (formatDT, formatDuration, computeMeasurementData,
// formatMeasurementText, fracIndexAtMs) sind separat testbar.

var Measurement = (function() {

    // ---- Zustand ----------------------------------------------------------
    // state: { from: { ms, price }, to: { ms, price } } oder null
    var state = null;
    var dragging = false;
    var draft = null;  // { x1, y1, x2, y2 } in Client-Pixeln (waehrend Drag)

    // ---- Basis-Helfer -----------------------------------------------------

    function _gd() {
        return document.getElementById('pg-chart');
    }

    function _axes() {
        var gd = _gd();
        if (!gd || !gd._fullLayout) return null;
        var xax = gd._fullLayout.xaxis;
        var yax = gd._fullLayout.yaxis;
        if (!xax || !yax) return null;
        return { gd: gd, xax: xax, yax: yax };
    }

    // Plotly linearisiert Datumsachsen als UTC-ms. Wanduhr-Konvention: die
    // Chart-X-Achse zeigt Wanduhr (naive ISO-Strings). Umrechnung zwischen
    // Plotly-UTC-ms und Wanduhr-ms (DST-robust ueber getTimezoneOffset).
    function _utcFromWall(wallMs) {
        return wallMs - new Date(wallMs).getTimezoneOffset() * 60000;
    }
    function _wallFromUtc(utcMs) {
        return utcMs + new Date(utcMs).getTimezoneOffset() * 60000;
    }

    // Client-Pixel -> { ms (Wanduhr), price } oder null-Werte
    function _toData(clientX, clientY) {
        var a = _axes();
        if (!a) return { ms: null, price: null };
        var rect = a.gd.getBoundingClientRect() || { left: 0, top: 0 };
        var px = clientX - (rect.left || 0) - a.xax._offset;
        var py = clientY - (rect.top || 0) - a.yax._offset;
        var utcMs = a.xax.p2l(px);
        var price = a.yax.p2l(py);
        if (utcMs === null || utcMs === undefined || !isFinite(utcMs)) {
            return { ms: null, price: null };
        }
        if (price === null || price === undefined || !isFinite(price)) {
            return { ms: null, price: null };
        }
        return { ms: _wallFromUtc(utcMs), price: price };
    }

    // Wanduhr-ms + Preis -> Pixel (relativ zur gd-Client-Kante)
    function _toPixel(ms, price) {
        var a = _axes();
        if (!a) return { x: 0, y: 0 };
        var x = a.xax.l2p(_utcFromWall(ms));
        var y = a.yax.l2p(price);
        if (x === null || !isFinite(x)) x = 0;
        if (y === null || !isFinite(y)) y = 0;
        return { x: x, y: y };
    }

    function _hide(el) { if (el && el.style.display !== 'none') el.style.display = 'none'; }
    function _show(el) { if (el && el.style.display !== 'block') el.style.display = 'block'; }

    // ---- Pure Funktionen (testbar) ---------------------------------------

    // Wanduhr-ms -> "YYYY-MM-DD HH:MM:SS" (naiv, KEIN Berlin-Offset)
    function formatDT(ms) {
        if (typeof ms !== 'number' || !isFinite(ms)) return '--';
        var d = new Date(ms);
        function p(n) { return (n < 10 ? '0' : '') + n; }
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
               ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    }

    // Dauer als DD:HH:MM (Sekunden werden nicht angezeigt - wie PyTrader)
    function formatDuration(seconds) {
        seconds = Math.max(0, Math.round(seconds || 0));
        var d = Math.floor(seconds / 86400);
        var h = Math.floor((seconds % 86400) / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        function p(n) { return String(n).padStart(2, '0'); }
        return p(d) + ':' + p(h) + ':' + p(m);
    }

    function computeMeasurementData(fromMs, fromPrice, toMs, toPrice,
                                    precision, bars) {
        var deltaPrice = toPrice - fromPrice;
        var pct = (fromPrice && fromPrice !== 0) ? (deltaPrice / fromPrice * 100) : 0;
        return {
            deltaPrice: deltaPrice,
            pctChange: pct,
            candleCount: bars,
            durationSeconds: Math.abs(toMs - fromMs) / 1000,
            fromTime: formatDT(fromMs),
            toTime: formatDT(toMs),
            fromPrice: fromPrice,
            toPrice: toPrice,
            precision: precision
        };
    }

    function formatMeasurementText(data) {
        var prec = data.precision;
        var dpStr = (data.deltaPrice >= 0 ? '+' : '') + data.deltaPrice.toFixed(prec);
        var pctStr = (data.pctChange >= 0 ? '+' : '') + data.pctChange.toFixed(2) + '%';
        var lines = [];
        // 1) Delta Preis: Prozentwert, exakte Differenz in Klammern
        lines.push('Δ Preis: ' + pctStr + '  (' + dpStr + ')');
        // 2) Delta Zeit: Anzahl Bars + Dauer DD:HH:MM
        lines.push('Δ Zeit:  ' + data.candleCount + ' Bars · ' + formatDuration(data.durationSeconds));
        // 3) Start/Ende: Preis (Symbol-Nachkommastellen) + Wanduhr-Zeit
        lines.push('Start:   ' + data.fromPrice.toFixed(prec) + '  ' + data.fromTime);
        lines.push('Ende:    ' + data.toPrice.toFixed(prec) + '  ' + data.toTime);
        return lines.join('\n');
    }

    // Sichtbare Candles (Trace 0, x = ISO-Strings) -> Wanduhr-ms-Array
    function _candleMsArray() {
        var gd = _gd();
        if (!gd || !gd.data || !gd.data.length) return [];
        var x = gd.data[0].x || [];
        var out = [];
        for (var i = 0; i < x.length; i++) {
            var t = new Date(x[i]).getTime();
            if (isFinite(t)) out.push(t);
        }
        return out;
    }

    // Fraktioneller Index per Binary Search (Bars-Zaehlung wie contTimeAtLogical)
    function fracIndexAtMs(ms, arr) {
        if (!arr || !arr.length) return null;
        if (ms <= arr[0]) return 0;
        if (ms >= arr[arr.length - 1]) return arr.length - 1;
        var lo = 0, hi = arr.length - 1;
        while (hi - lo > 1) {
            var mid = (lo + hi) >> 1;
            if (arr[mid] <= ms) lo = mid; else hi = mid;
        }
        var span = arr[hi] - arr[lo];
        if (span <= 0) return lo;
        return lo + (ms - arr[lo]) / span;
    }

    // Nachkommastellen aus den Close-Werten des Candlestick-Trace ableiten
    function _precisionFromData() {
        var gd = _gd();
        if (!gd || !gd.data || !gd.data.length) return 2;
        var arr = gd.data[0].close || gd.data[0].y || [];
        var maxD = 2, n = 0;
        for (var i = 0; i < arr.length && n < 200; i++) {
            var v = arr[i];
            if (typeof v !== 'number' || !isFinite(v)) continue;
            n++;
            var s = String(v);
            var dot = s.indexOf('.');
            var dec = dot >= 0 ? s.length - dot - 1 : 0;
            if (dec > maxD) maxD = dec;
        }
        return Math.min(maxD, 6);
    }

    // ---- Rendering (CSS-Overlay wie PyTrader) ------------------------------

    function render() {
        var a = _axes();
        var region = document.getElementById('measurement-region');
        var box = document.getElementById('measurement-box');
        if (!region || !box) return;
        if (!a) { _hide(region); _hide(box); return; }

        var pts = null;
        if (dragging && draft) {
            pts = { x1: draft.x1, y1: draft.y1, x2: draft.x2, y2: draft.y2 };
        } else if (state) {
            var p1 = _toPixel(state.from.ms, state.from.price);
            var p2 = _toPixel(state.to.ms, state.to.price);
            var rect0 = a.gd.getBoundingClientRect() || { left: 0, top: 0 };
            pts = { x1: (rect0.left || 0) + p1.x, y1: (rect0.top || 0) + p1.y,
                    x2: (rect0.left || 0) + p2.x, y2: (rect0.top || 0) + p2.y };
        }
        if (!pts) { _hide(region); _hide(box); return; }

        var rect = a.gd.getBoundingClientRect() || { left: 0, top: 0 };
        var left = Math.min(pts.x1, pts.x2) - (rect.left || 0);
        var top = Math.min(pts.y1, pts.y2) - (rect.top || 0);
        var width = Math.abs(pts.x2 - pts.x1);
        var height = Math.abs(pts.y2 - pts.y1);

        region.style.display = 'block';
        region.style.left = left + 'px';
        region.style.top = top + 'px';
        region.style.width = Math.max(width, 1) + 'px';
        region.style.height = Math.max(height, 1) + 'px';

        var d1 = _toData(pts.x1, pts.y1);
        var d2 = _toData(pts.x2, pts.y2);
        if (d1.ms === null || d1.price === null || d2.ms === null || d2.price === null) {
            _hide(box);
            return;
        }
        var barsArr = _candleMsArray();
        var f1 = fracIndexAtMs(d1.ms, barsArr);
        var f2 = fracIndexAtMs(d2.ms, barsArr);
        var bars = (f1 !== null && f2 !== null) ? Math.round(Math.abs(f2 - f1)) : 0;
        var data = computeMeasurementData(d1.ms, d1.price, d2.ms, d2.price,
                                          _precisionFromData(), bars);
        var text = formatMeasurementText(data);
        if (box.innerText !== text) box.innerText = text;
        _show(box);

        // Info-Box neben dem Endpunkt positionieren (im gd geclampt)
        var gdW = a.gd.clientWidth || 0;
        var gdH = a.gd.clientHeight || 0;
        var boxW = box.offsetWidth || 220;
        var boxH = box.offsetHeight || 80;
        var bx = left + width + 15;
        var by = top + 15;
        if (bx + boxW > gdW) bx = Math.max(2, left - boxW - 15);
        if (by + boxH > gdH) by = Math.max(2, top - boxH - 15);
        box.style.left = bx + 'px';
        box.style.top = by + 'px';
    }

    // ---- Event-Handler -----------------------------------------------------

    function onMouseDown(e) {
        var a = _axes();
        if (!a || dragging) return;
        // Nur Shift + rechte Maustaste startet eine Messung
        if (e.shiftKey && e.button === 2) {
            e.preventDefault();
            e.stopPropagation();
            dragging = true;
            draft = { x1: e.clientX, y1: e.clientY,
                      x2: e.clientX, y2: e.clientY };
            render();
            return;
        }
        // Normaler linker Klick schliesst die Messbox (temporaere Anzeige)
        if (e.button === 0 && state) {
            state = null;
            draft = null;
            dragging = false;
            render();
        }
    }

    function onMouseMove(e) {
        if (!dragging || !draft) return;
        e.preventDefault();
        draft.x2 = e.clientX;
        draft.y2 = e.clientY;
        render();
    }

    function onMouseUp(e) {
        if (!dragging) return;
        e.preventDefault();
        dragging = false;
        if (draft) {
            var d1 = _toData(draft.x1, draft.y1);
            var d2 = _toData(draft.x2, draft.y2);
            if (d1.ms !== null && d1.price !== null &&
                    d2.ms !== null && d2.price !== null) {
                state = { from: d1, to: d2 };
            }
            draft = null;
        }
        render();
    }

    function onKeyDown(e) {
        if (e.key === 'Escape' || e.keyCode === 27) {
            state = null;
            draft = null;
            dragging = false;
            render();
        }
    }

    function onContextMenu(e) {
        // Shift-Rechtsklick ist fuer die Messung reserviert -> kein Kontextmenue
        if (e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
        }
    }

    // ---- Binding & Oeffentliche API ---------------------------------------

    function _bind() {
        var gd = _gd();
        if (!gd) return false;
        gd.removeEventListener('mousedown', onMouseDown, true);
        gd.addEventListener('mousedown', onMouseDown, true);
        window.removeEventListener('mousemove', onMouseMove);
        window.addEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
        window.addEventListener('mouseup', onMouseUp);
        window.removeEventListener('keydown', onKeyDown);
        window.addEventListener('keydown', onKeyDown);
        gd.removeEventListener('contextmenu', onContextMenu, true);
        gd.addEventListener('contextmenu', onContextMenu, true);
        return true;
    }

    // Nach dem ersten Plotly.react aufrufen: haengt updatePositions an die
    // Plotly-Events (Zoom/Pan/react -> Box folgt in Datenkoordinaten).
    function init() {
        var gd = _gd();
        if (!gd) return false;
        if (gd.__measurementBound) return true;
        gd.__measurementBound = true;
        try {
            gd.on('plotly_afterplot', updatePositions);
            gd.on('plotly_relayout', updatePositions);
        } catch (e) { /* Plotly noch nicht bereit -> updatePositions ueberspringt */ }
        return true;
    }

    function updatePositions() {
        if (dragging) return;
        if (!state) return;
        var a = _axes();
        if (!a) return;
        render();
    }

    function clear() {
        state = null;
        draft = null;
        dragging = false;
        _hide(document.getElementById('measurement-region'));
        _hide(document.getElementById('measurement-box'));
    }

    function hasState() {
        return state !== null;
    }

    // Skript steht am Body-Ende: pg-chart-Div existiert bereits.
    try { _bind(); } catch (e) { /* DOM noch nicht fertig -> App ruft init() */ }

    return {
        render: render,
        updatePositions: updatePositions,
        init: init,
        clear: clear,
        hasState: hasState,
        // pure Funktionen fuer Tests exportieren
        formatDT: formatDT,
        formatDuration: formatDuration,
        computeMeasurementData: computeMeasurementData,
        formatMeasurementText: formatMeasurementText,
        fracIndexAtMs: fracIndexAtMs,
        _state: function () { return state; },
        _setStateForTest: function (s) { state = s; },
        _toData: _toData,
        _toPixel: _toPixel
    };
})();
