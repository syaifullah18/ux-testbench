import pytest
from testbench.context import Ctx
from testbench.web import get_project, registry


def superadmin(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["tb_admin"] = ["*"]
    return c


def test_results_summary_view(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. Summary view for example project
    r = c.get("/example/admin/results")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # Sidebar checks
    assert 'aria-label="Results"' in html
    assert '<span>Summary</span>' in html
    assert 'aria-current="page"' in html
    assert 'class="grp">Modules</div>' in html
    assert '<span>Participants</span>' in html
    assert '<span>Export</span>' in html

    # Findings by module section
    assert "Findings by module" in html
    assert "Try two events pages" in html
    assert "Open report" in html


def test_results_module_view(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. Module view via query param ?m=events-ab
    r = c.get("/example/admin/results?m=events-ab")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # Header and action buttons
    assert "Try two events pages" in html
    assert "Export CSV" in html
    assert "Preview this module" in html
    assert "started" in html
    assert "finished" in html

    # 2. Module view via legacy /m/<mid>/ endpoint
    r_leg = c.get("/example/admin/m/events-ab/")
    assert r_leg.status_code == 200
    html_leg = r_leg.get_data(as_text=True)
    assert "Try two events pages" in html_leg


def test_results_blank_study_empty_state(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # Create new study
    c.post("/admin/projects/new", data={
        "slug": "res-study",
        "name": "Results Test Study",
        "source": "blank",
        "locale": "en",
        "identity": "code",
        "access": "passcode"
    })

    # Results view with 0 participants
    r = c.get("/res-study/admin/results")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Results Test Study" in html
    assert "No one has entered this study yet" in html
    assert "Go to Launch" in html


def test_export_view(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. Export view via /example/admin/export
    r = c.get("/example/admin/export")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # Check sidebar Export active
    assert 'aria-label="Results"' in html
    assert '<span>Export</span>' in html

    # Check Data section
    assert "Data</h2>" in html
    assert "One file per module, or everything in one file" in html
    assert "All modules together" in html
    assert "Download combined CSV" in html

    # Check Study design section
    assert "Study design</h2>" in html
    assert "Download study zip" in html

    # Check File layout documentation section
    assert "How the files are laid out</h2>" in html
    assert "Column names are ids" in html
    assert "One row per person" in html
    assert "Opens in spreadsheets" in html

    # 2. Export view via /app/p/example/export
    r2 = c.get("/app/p/example/export")
    assert r2.status_code == 200


def test_participants_view_and_detail(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. Participants list view (empty state)
    r = c.get("/example/admin/participants")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    assert "Participants</h2>" in html
    assert "No one has entered this study yet" in html

    # 2. Add a participant and check table and detail view
    from testbench.web import get_project
    with app.app_context():
        proj = get_project("example")
        ctx = Ctx(proj, admin=True)
        conn = ctx.conn
        conn.execute("INSERT INTO participants (identity, created_at, last_seen_at) VALUES ('tester-1', '2026-04-18T10:00:00Z', '2026-04-18T10:30:00Z')")
        pid = conn.execute("SELECT id FROM participants WHERE identity = 'tester-1'").fetchone()[0]
        import json
        st_json = json.dumps({"order": ["list", "filter"]})
        conn.execute("INSERT INTO sessions (participant_id, module_id, step, started_at, finished_at, state) VALUES (?, 'events-ab', 'done', '2026-04-18T10:00:00Z', '2026-04-18T10:30:00Z', ?)", (pid, st_json))
        conn.commit()

    r2 = c.get("/example/admin/participants")
    assert r2.status_code == 200
    html2 = r2.get_data(as_text=True)

    assert 'id="q"' in html2
    assert 'placeholder="Search by code"' in html2
    assert 'data-f="all"' in html2
    assert 'data-f="finished"' in html2
    assert 'data-f="progress"' in html2
    assert 'data-f="none"' in html2
    assert 'id="dlg-del"' in html2
    assert "tester-1" in html2

    r_detail = c.get(f"/example/admin/p/{pid}")
    assert r_detail.status_code == 200
    d_html = r_detail.get_data(as_text=True)

    assert "Results" in d_html
    assert "Participants" in d_html
    assert "tester-1" in d_html
    assert "Save grades and notes" in d_html
    assert 'class="acc"' in d_html

