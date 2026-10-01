"""Turns stored clicks into a density grid small enough to send to the admin browser.

Pure functions, no Flask: bin every click into a coarse grid, blur it, and scale it to 0..255.
The browser draws the grid into a canvas and lets its own bilinear smoothing do the rest, so a
report over two thousand participants ships a few kilobytes instead of every coordinate.
"""
import base64
import statistics

GRID_W = 160                # cells across; rows follow the surface's aspect ratio
MAX_CELLS = 48_000          # one byte per cell, base64 in the page: stays under the 100 KB budget
CLIP_PERCENTILE = 0.95
HEATMAP_MIN_N = 5           # participants below which a density surface claims more than it knows
MOBILE_MAX_W = 767          # the one definition of "mobile"; ab_test imports it from here
LAYOUT_TOLERANCE = 0.10     # doc_w further than this from the bucket median is another layout
DOTS_MAX = 2000             # above this many clicks, dots are a solid block and are not sent


def grid_size(doc_w, doc_h, grid_w=GRID_W):
    """Square cells, grid_w across, shrunk evenly when a long page would exceed MAX_CELLS."""
    doc_w, doc_h = max(1, doc_w), max(1, doc_h)
    w, h = grid_w, max(1, round(grid_w * doc_h / doc_w))
    if w * h > MAX_CELLS:
        scale = (MAX_CELLS / (w * h)) ** 0.5
        w, h = max(1, int(w * scale)), max(1, int(h * scale))
    return w, h


def _box(line, radius):
    """Mean over a window of 2r+1, averaged over the cells that exist. Dividing by the in-bounds
    count rather than the full width keeps a uniform field uniform up to its edges."""
    n = len(line)
    prefix = [0.0] * (n + 1)
    for i, v in enumerate(line):
        prefix[i + 1] = prefix[i] + v
    out = [0.0] * n
    for i in range(n):
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        out[i] = (prefix[hi] - prefix[lo]) / (hi - lo)
    return out


def blur(cells, w, h, radius, passes=3):
    """Three passes of a separable box blur: close enough to a Gaussian for a density surface,
    O(cells) per pass, and no dependency."""
    if radius < 1:
        return list(cells)
    rows = [cells[r * w:(r + 1) * w] for r in range(h)]
    for _ in range(passes):
        rows = [_box(row, radius) for row in rows]
        cols = [_box([rows[r][c] for r in range(h)], radius) for c in range(w)]
        rows = [[cols[c][r] for c in range(w)] for r in range(h)]
    return [v for row in rows for v in row]


def grid(points, doc_w, doc_h, grid_w=GRID_W):
    """Density of `points` ((x, y) pairs in a doc_w x doc_h space) as 0..255 cells.

    Scaled against the 95th percentile of the non-empty cells, not the maximum, so one
    participant hammering one pixel does not fade everyone else to nothing. `clip` is that
    level in smoothed clicks per cell; `clipped` counts the cells drawn at full strength because
    they are above it. The report states both rather than hiding the saturation.
    """
    w, h = grid_size(doc_w, doc_h, grid_w)
    sx, sy = w / max(1, doc_w), h / max(1, doc_h)
    counts = [0.0] * (w * h)
    wmax, hmax = w - 1, h - 1
    for x, y in points:
        cx, cy = int(x * sx), int(y * sy)
        counts[(cy if cy < hmax else hmax) * w + (cx if cx < wmax else wmax)] += 1
    radius = max(1, round(w * 0.02))
    smooth = blur(counts, w, h, radius)
    peak = max(smooth) if smooth else 0.0
    nonzero = sorted(v for v in smooth if v > 1e-12)
    clip = nonzero[min(len(nonzero) - 1, int(len(nonzero) * CLIP_PERCENTILE))] if nonzero else 0.0
    if clip <= 0:
        return {"w": w, "h": h, "cells": [0] * (w * h), "peak": 0.0, "clip": 0.0, "clipped": 0,
                "cell_px": doc_w / w, "radius": radius}
    cells = [255 if v >= clip else int(v / clip * 255) for v in smooth]
    return {"w": w, "h": h, "cells": cells, "peak": peak, "clip": clip,
            "clipped": sum(1 for v in smooth if v > clip), "cell_px": doc_w / w, "radius": radius}


def device(point):
    vw = point.get("viewport_w")
    return "mobile" if vw is not None and vw <= MOBILE_MAX_W else "desktop"


def buckets(points):
    """Splits clicks by device, then drops the ones captured on a different layout.

    Within a device, a click whose document width is more than 10% away from the median was
    made on another layout (a replaced screenshot, a responsive breakpoint). Rescaling it would
    place it where the participant never clicked, so it is excluded and counted instead.
    A click with no viewport recorded counts as desktop.
    """
    out = {}
    for dev in ("desktop", "mobile"):
        pts = [p for p in points if device(p) == dev]
        if not pts:
            continue
        doc_w = statistics.median_low([p["doc_w"] for p in pts])
        kept = [p for p in pts if abs(p["doc_w"] - doc_w) <= doc_w * LAYOUT_TOLERANCE]
        doc_h = statistics.median_low([p["doc_h"] for p in kept])
        out[dev] = {"points": kept, "excluded": len(pts) - len(kept), "doc_w": doc_w, "doc_h": doc_h}
    return out


def panel(points, grid_w=GRID_W, min_n=HEATMAP_MIN_N):
    """Everything one report panel needs, per device, with no raw coordinate left in it except
    the dots, which are sent only as fractions of the surface and only while few enough to read.
    """
    out = {}
    for dev, b in buckets(points).items():
        pts, doc_w, doc_h = b["points"], b["doc_w"], b["doc_h"]
        n = len({p["session_id"] for p in pts})
        entry = {"n_participants": n, "n_clicks": len(pts), "excluded": b["excluded"],
                 "doc_w": doc_w, "doc_h": doc_h, "grid": None, "dots": None}
        if n >= min_n:
            g = grid([(p["x"], p["y"]) for p in pts], doc_w, doc_h, grid_w)
            g["cells"] = base64.b64encode(bytes(g["cells"])).decode("ascii")
            entry["grid"] = g
        if len(pts) <= DOTS_MAX:
            entry["dots"] = [[round(min(p["x"], doc_w) / doc_w, 4), round(min(p["y"], doc_h) / doc_h, 4)]
                             for p in pts]
        out[dev] = entry
    return out
