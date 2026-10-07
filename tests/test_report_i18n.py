"""Report wording lives in the locale files, so an Indonesian study gets an Indonesian report."""
import yaml

from testbench.i18n import LOCALE_DIR

from .conftest import extract_json, write_project


def keys(locale):
    return set((yaml.safe_load((LOCALE_DIR / f"{locale}.yaml").read_text(encoding="utf-8")) or {})["report"])


def test_every_report_string_exists_in_both_languages():
    assert keys("en") == keys("id")


AB = {"type": "ab_test", "title": "AB", "order": "fixed", "ease_question": False, "preference": False,
      "variants": {"A": {"label": "Lama", "file": "p/a.html"}, "B": {"label": "Baru <i>x</i>", "file": "p/b.html"}},
      "tasks": [{"id": "t", "prompt": "Ketik 42", "fields": [{"id": "x", "label": "X"}], "accept": {"x": ["42"]}}],
      "post_survey": [{"id": "sus", "preset": "sus"}], "decision_rule": {"planned_participants": 3}}


def test_indonesian_study_gets_indonesian_report(projects_dir, make_app):
    write_project(projects_dir, "idr", {"name": "ID", "locale": "id", "status": "live", "identity": "anonymous",
                                        "access": "open"}, {"ab": AB}, {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"})
    app = make_app(projects_dir, IDR_ADMIN_PASSCODE="adm")
    c = app.test_client()
    c.post("/idr/", data={"action": "start", "consent": "1"})
    c.get("/idr/m/ab/")
    c.post("/idr/m/ab/s/intro")
    for n in (1, 2):
        c.post(f"/idr/m/ab/s/brief{n}")
        cfg = extract_json(c.get(f"/idr/m/ab/s/tasks{n}").get_data(as_text=True), "TASKS")
        r = c.post(cfg["submitUrl"].replace("__T__", "t"), json={"answer": {"x": "42"}, "metrics": {"time_ms": 1000}})
        c.post(f"/idr/m/ab/s/survey{n}", data={f"sus.{i}": "3" for i in range(1, 11)})
    admin = app.test_client()
    admin.post("/idr/admin/", data={"passcode": "adm"})
    html = admin.get("/idr/admin/m/ab/").get_data(as_text=True)
    for text in ("Apakah Baru &lt;i&gt;x&lt;/i&gt; lebih baik dari Lama?", "Seberapa mudah menurut orang",
                 "Per tugas", "Waktu umum", "Belum bisa disimpulkan", "Detail untuk peneliti",
                 "Aturan yang ditetapkan sebelum pengujian"):
        assert text in html, text
    assert "<i>x</i>" not in html                       # design labels stay escaped inside sentences
    # A term keeps its explanation for hover, focus and tap.
    assert 'class="term" tabindex="0" role="button"' in html and 'data-tip="Median: separuh orang' in html
