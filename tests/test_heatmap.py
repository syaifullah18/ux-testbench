"""Aggregation: binning, blur, clipping and layout bucketing, without an app context."""
import base64
import random
import time

from testbench import heatmap


def cell(g, x, y):
    return g["cells"][y * g["w"] + x]


def test_grid_rows_follow_the_aspect_ratio_and_long_pages_stay_in_budget():
    assert heatmap.grid_size(1600, 1200) == (160, 120)
    w, h = heatmap.grid_size(375, 20_000)
    assert w * h <= heatmap.MAX_CELLS and h > w
    assert heatmap.grid_size(0, 0) == (160, 160)


def test_a_single_point_peaks_at_its_cell_and_falls_away():
    g = heatmap.grid([(805, 605)], 1600, 1200)
    cx, cy = 80, 60
    assert cell(g, cx, cy) == max(g["cells"]) == 255
    row = [cell(g, cx + d, cy) for d in range(0, 15)]
    col = [cell(g, cx, cy + d) for d in range(0, 15)]
    for line in (row, col):
        assert all(a >= b for a, b in zip(line, line[1:])), line
        assert line[-1] == 0            # beyond three blur radii there is nothing
    assert cell(g, cx - 3, cy) == cell(g, cx + 3, cy)   # symmetric
    assert g["cells"][0] == 0


def test_a_uniform_field_stays_uniform_to_its_edges():
    pts = [(x * 10 + 5, y * 10 + 5) for x in range(160) for y in range(120)]
    g = heatmap.grid(pts, 1600, 1200)
    assert set(g["cells"]) == {255}


def test_one_repeated_point_does_not_flatten_everyone_else():
    rng = random.Random(1)
    spread = [(rng.randint(100, 500), rng.randint(100, 500)) for _ in range(200)]
    hammer = [(1300, 900)] * 40
    g = heatmap.grid(spread + hammer, 1600, 1200)
    # Against the maximum the spread region would be nearly invisible; against the 95th
    # percentile it is drawn at a readable strength, and the saturation is reported.
    assert cell(g, 30, 30) > 120
    assert g["clipped"] > 0 and g["peak"] > g["clip"] * 3


def test_an_empty_grid_is_all_zero():
    g = heatmap.grid([], 100, 100)
    assert set(g["cells"]) == {0} and g["clip"] == 0


def pt(sid, x=10, y=10, doc_w=1000, doc_h=800, vw=1280):
    return {"session_id": sid, "x": x, "y": y, "doc_w": doc_w, "doc_h": doc_h, "viewport_w": vw}


def test_points_from_another_layout_are_excluded_and_counted():
    pts = [pt(i) for i in range(10)] + [pt(20, doc_w=1200), pt(21, doc_w=1200)]   # 20% wider
    b = heatmap.buckets(pts)["desktop"]
    assert b["doc_w"] == 1000 and b["excluded"] == 2 and len(b["points"]) == 10
    pts = [pt(i) for i in range(10)] + [pt(20, doc_w=1050)]                         # within 10%
    assert heatmap.buckets(pts)["desktop"]["excluded"] == 0


def test_mobile_and_desktop_are_separate_and_unknown_viewport_is_desktop():
    pts = [pt(1, vw=390, doc_w=390), pt(2, vw=None), pt(3, vw=heatmap.MOBILE_MAX_W)]
    b = heatmap.buckets(pts)
    assert len(b["desktop"]["points"]) == 1
    # Each device is judged against its own median width, not the other's: the 1000-wide
    # document on a 767 px viewport is a different layout from the 390-wide one.
    assert len(b["mobile"]["points"]) == 1 and b["mobile"]["excluded"] == 1


def test_panel_withholds_the_grid_below_the_minimum_n():
    few = heatmap.panel([pt(i) for i in range(4)])["desktop"]
    assert few["grid"] is None and len(few["dots"]) == 4 and few["n_participants"] == 4
    many = heatmap.panel([pt(i % 5, x=i) for i in range(50)])["desktop"]
    g = many["grid"]
    assert many["n_participants"] == 5 and many["n_clicks"] == 50
    assert len(base64.b64decode(g["cells"])) == g["w"] * g["h"]
    assert len(g["cells"]) < 100_000


def test_dots_are_not_sent_once_they_would_be_a_solid_block():
    pts = [pt(i % 50, x=i % 1000) for i in range(heatmap.DOTS_MAX + 1)]
    entry = heatmap.panel(pts)["desktop"]
    assert entry["dots"] is None and entry["grid"] is not None


def test_600k_points_aggregate_inside_the_report_budget():
    rng = random.Random(7)
    pts = [(rng.randint(0, 1439), rng.randint(0, 899)) for _ in range(600_000)]
    start = time.perf_counter()
    g = heatmap.grid(pts, 1440, 900)
    elapsed = time.perf_counter() - start
    assert max(g["cells"]) == 255
    assert elapsed < 2.0, f"grid() took {elapsed:.2f}s for 600k points"
