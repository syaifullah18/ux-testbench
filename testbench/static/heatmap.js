// Draws the click-density panels rendered by templates/modules/_heatmap.html.
// The server sends a coarse grid of 0..255 values. It is written into an offscreen canvas one
// pixel per cell and scaled up with smoothing on, so the browser's own bilinear filter turns
// 160 cells into a smooth surface for free.
(function () {
    // One hue, light to dark, validated for monotone lightness. Deliberately not a rainbow
    // (hue does not read as magnitude and breaks under colour-vision deficiency) and not the
    // project's brand colour, which could be the very colour of the UI underneath.
    const RAMP = ['#fde3d3', '#f9bf9a', '#f39866', '#eb6834', '#c94f1f', '#9c3a12', '#6b2408'];
    const DOT = [201, 79, 31];

    const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const LUT = (() => {
        const stops = RAMP.map(hex), out = new Uint8ClampedArray(256 * 4);
        for (let v = 1; v < 256; v++) {
            const t = v / 255, pos = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(pos)), f = pos - i;
            for (let c = 0; c < 3; c++) out[v * 4 + c] = stops[i][c] + (stops[i + 1][c] - stops[i][c]) * f;
            // Alpha rises from zero with density: near zero recedes into the screenshot, the
            // peak is opaque enough to read over anything. Starting above zero would outline the
            // blur's square footprint around every cluster.
            out[v * 4 + 3] = 255 * 0.85 * Math.pow(t, 0.75);
        }
        return out;
    })();

    function decode(b64) {
        const s = atob(b64), out = new Uint8Array(s.length);
        for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
        return out;
    }

    function gridCanvas(g) {
        const off = document.createElement('canvas');
        off.width = g.w; off.height = g.h;
        const ctx = off.getContext('2d'), img = ctx.createImageData(g.w, g.h), cells = decode(g.cells);
        for (let i = 0; i < cells.length; i++) img.data.set(LUT.subarray(cells[i] * 4, cells[i] * 4 + 4), i * 4);
        ctx.putImageData(img, 0, 0);
        return off;
    }

    function drawRamp(canvas) {
        const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, w, h);
        for (let x = 0; x < w; x++) {
            const v = Math.max(1, Math.round(x / (w - 1) * 255)), c = LUT.subarray(v * 4, v * 4 + 4);
            ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${c[3] / 255})`;
            ctx.fillRect(x, 0, 1, h);
        }
    }

    function setup(panel) {
        const data = JSON.parse(panel.querySelector('[data-heatmap-data]').textContent);
        const img = panel.querySelector('.hm-stage img'), canvas = panel.querySelector('.hm-stage canvas');
        const modeBtns = [...panel.querySelectorAll('[data-mode]')];
        const devBtns = [...panel.querySelectorAll('[data-device]')];
        const grids = {};
        let dev = Object.keys(data)[0], mode = null;

        panel.querySelectorAll('.hm-ramp').forEach(drawRamp);

        function draw() {
            const w = img.clientWidth, h = img.clientHeight, dpr = window.devicePixelRatio || 1;
            if (!w || !h) return;
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
            const ctx = canvas.getContext('2d'), b = data[dev];
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (mode === 'heat' && b.grid) {
                grids[dev] = grids[dev] || gridCanvas(b.grid);
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                // The grid covers the bucket's document, which for an image is the image itself.
                ctx.drawImage(grids[dev], 0, 0, canvas.width, canvas.height * (b.grid.h * b.doc_w / b.grid.w) / b.doc_h);
            } else if (mode === 'dots' && b.dots) {
                const r = 5 * dpr;
                ctx.lineWidth = 1.5 * dpr;
                ctx.strokeStyle = '#fff';
                ctx.fillStyle = `rgba(${DOT.join(',')},0.7)`;
                for (const [fx, fy] of b.dots) {
                    ctx.beginPath();
                    ctx.arc(fx * canvas.width, fy * canvas.height, r, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.stroke();
                }
            }
        }

        function render() {
            const b = data[dev];
            if (!mode || (mode === 'heat' && !b.grid) || (mode === 'dots' && !b.dots)) {
                mode = b.grid ? 'heat' : (b.dots ? 'dots' : 'image');
            }
            modeBtns.forEach((btn) => {
                const m = btn.dataset.mode, off = (m === 'heat' && !b.grid) || (m === 'dots' && !b.dots);
                btn.disabled = off;
                btn.setAttribute('aria-pressed', String(m === mode));
            });
            devBtns.forEach((btn) => btn.setAttribute('aria-pressed', String(btn.dataset.device === dev)));
            panel.querySelectorAll('[data-bucket]').forEach((el) => { el.hidden = el.dataset.bucket !== dev; });
            panel.querySelectorAll('[data-legend-for]').forEach((el) => {
                el.classList.toggle('hm-dim', el.dataset.legendFor !== mode);
            });
            draw();
        }

        modeBtns.forEach((btn) => btn.addEventListener('click', () => { mode = btn.dataset.mode; render(); }));
        devBtns.forEach((btn) => btn.addEventListener('click', () => { dev = btn.dataset.device; mode = null; render(); }));
        if (img.complete) render(); else img.addEventListener('load', render);
        if (window.ResizeObserver) new ResizeObserver(draw).observe(img);
    }

    function init() { document.querySelectorAll('[data-heatmap]').forEach((p) => { if (p.querySelector('[data-heatmap-data]')) setup(p); }); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
