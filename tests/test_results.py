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
