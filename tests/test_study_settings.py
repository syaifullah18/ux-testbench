"""Tests for Study Settings page and studio.settings route."""
from pathlib import Path
import yaml


def superadmin(app):
    c = app.test_client()
    c.post("/admin/", data={"passcode": "root"})
    return c


def test_study_settings_get_renders_settings_page(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    r = c.get("/example/admin/studio/settings")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Study settings" in html
    assert "How participants identify themselves" in html
    assert "Who may enter" in html
    assert "Edit as YAML" in html
    assert "id=\"savebar\"" in html


def test_study_settings_ajax_check_and_save(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # First, create an editable studio project
    r = c.post("/admin/projects/new", data={
        "slug": "custom-study", "name": "Custom Study", "source": "blank",
        "locale": "en", "identity": "code", "access": "passcode"
    })
    assert r.status_code == 302

    # Check with invalid YAML content
    bad_yaml = "name: [broken yaml"
    r = c.post(
        "/custom-study/admin/studio/settings",
        data={"action": "check", "content": bad_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is False
    assert len(data["problems"]) > 0

    # Check with valid YAML content
    studio_root = Path(app.config["DATA_DIR"]) / "projects"
    current_yaml = (studio_root / "custom-study" / "project.yaml").read_text(encoding="utf-8")
    updated_yaml = current_yaml.replace("Custom Study", "Brand New Title")

    r = c.post(
        "/custom-study/admin/studio/settings",
        data={"action": "check", "content": updated_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["problems"] == []

    # Save via AJAX
    r = c.post(
        "/custom-study/admin/studio/settings",
        data={"action": "save", "content": updated_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True

    # Verify that file on disk is updated and previous version backed up in .history
    saved_text = (studio_root / "custom-study" / "project.yaml").read_text(encoding="utf-8")
    assert "Brand New Title" in saved_text
    assert list((studio_root / "custom-study" / ".history").glob("project.yaml.*.bak"))


def test_study_settings_unauthenticated(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = app.test_client()
    r = c.get("/example/admin/studio/settings")
    # Unauthenticated study admin returns 403
    assert r.status_code == 403
