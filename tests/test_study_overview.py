import pytest
from pathlib import Path
from testbench.context import Ctx
from testbench.web import get_project, registry


def superadmin(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["tb_admin"] = ["*"]
    return c


def test_study_overview_rendering(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    
    # 1. Create a study via new study endpoint
    r = c.post("/admin/projects/new", data={
        "slug": "eval-study",
        "name": "Evaluation Study",
        "source": "blank",
        "locale": "en",
        "identity": "code",
        "access": "passcode"
    })
    assert r.status_code == 302
    
    # 2. Set to draft and view study dashboard
    c.post("/eval-study/admin/status", data={"status": "draft"})
    r_dash = c.get("/eval-study/admin/?created=1")
    assert r_dash.status_code == 200
    html = r_dash.get_data(as_text=True)
    
    # Check header & shell
    assert "Evaluation Study" in html
    assert "/eval-study" in html
    assert "Overview" in html
    assert "Build" in html
    assert "Launch" in html
    assert "Results" in html
    
    # Check created notice
    assert "Study created." in html
    
    # Check phase panel & rail
    assert "Draft: not open to participants" in html
    assert "Finish the checklist, then open the study to participants from Launch." in html
    assert "Finish setup" in html
    assert 'aria-label="Study phases"' in html
    assert "Build" in html
    assert "Collect" in html
    
    # Check checklist
    assert "Setup checklist" in html
    assert "The study has modules" in html
    assert "Participant passcode is set" in html
    assert "Blocks launch" in html
    
    # Check modules table
    assert "Modules" in html
    assert "Open Build" in html


def test_study_status_change(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    
    # Create study
    c.post("/admin/projects/new", data={
        "slug": "status-study",
        "name": "Status Study",
        "source": "blank",
        "locale": "en",
        "identity": "code",
        "access": "passcode"
    })
    
    # Switch status to live
    r_live = c.post("/status-study/admin/status", data={"status": "live"}, follow_redirects=True)
    assert r_live.status_code == 200
    live_html = r_live.get_data(as_text=True)
    assert "Live: collecting responses" in html_clean(live_html)
    assert "Read results" in live_html
    
    # Switch status to closed
    r_closed = c.post("/status-study/admin/status", data={"status": "closed"}, follow_redirects=True)
    assert r_closed.status_code == 200
    closed_html = r_closed.get_data(as_text=True)
    assert "Closed: no new participants" in html_clean(closed_html)
    assert "Export results" in closed_html


def test_studies_list_dropdown_and_markup(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # View studies list
    res = c.get("/admin/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Check table and study row
    assert "City Library Portal" in html
    assert "class=\"pop\"" in html
    assert "data-pop" in html
    assert "class=\"menu is-hidden\"" in html

    # Check action items inside dropdown
    assert "Results" in html
    assert "Build" in html
    assert "Copy participant link" in html
    assert "data-copy=" in html
    assert "Duplicate" in html
    assert "Export as zip" in html
    assert "Delete study" in html
    assert "data-delete=" in html

    # Check script handlers
    assert "closeMenus" in html
    assert "data-pop" in html
    assert "data-copy" in html


def html_clean(text):
    return " ".join(text.split())

