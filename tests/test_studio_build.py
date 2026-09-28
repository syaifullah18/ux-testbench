"""Tests for the Build and Prototypes pages (Page 3 of the Admin redesign)."""
from pathlib import Path


def superadmin(app):
    c = app.test_client()
    c.post("/admin/", data={"passcode": "root"})
    return c


def test_build_modules_page_and_actions(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # Create a new editable study
    c.post("/admin/projects/new", data={"slug": "flow-study", "name": "Flow Study", "source": "ab"})

    # 1. View Build page
    res = c.get("/flow-study/admin/studio/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Build · Flow Study" in html
    assert "Modules" in html
    assert "What participants see" in html
    assert "events-ab" in html
    assert "feedback" in html
    # Check that inner <main> is NOT nested and page-head is outside/before main
    assert html.count('<main id="main"') == 1
    assert html.find('class="page-head"') < html.find('<main id="main"')

    # 2. Add a new module with custom title
    res_add = c.post("/flow-study/admin/studio/modules/new", data={
        "id": "survey-onboarding",
        "title": "Onboarding Feedback",
        "type": "survey"
    })
    assert res_add.status_code == 302
    assert "modules/survey-onboarding.yaml" in res_add.location
    project = app.extensions["testbench"].get("flow-study")
    assert "survey-onboarding" in project.modules
    assert project.modules["survey-onboarding"].title == "Onboarding Feedback"

    # 3. Duplicate a module
    res_dup = c.post("/flow-study/admin/studio/modules/feedback/duplicate")
    assert res_dup.status_code == 302
    project = app.extensions["testbench"].get("flow-study")
    assert "feedback-copy" in project.modules
    assert "feedback (copy)" in project.modules["feedback-copy"].title

    # 4. Move module up / down
    orig_order = list(project.modules.keys())
    # Move the last module up
    last_mid = orig_order[-1]
    res_move = c.post(f"/flow-study/admin/studio/modules/{last_mid}/move", data={"direction": "up"})
    assert res_move.status_code == 302
    project = app.extensions["testbench"].get("flow-study")
    new_order = list(project.modules.keys())
    assert new_order.index(last_mid) == len(orig_order) - 2

    # 5. Reorder all modules with explicit order list
    reversed_order = list(reversed(new_order))
    res_reorder = c.post(f"/flow-study/admin/studio/modules/{last_mid}/move", data={"order": ",".join(reversed_order)})
    assert res_reorder.status_code == 302
    project = app.extensions["testbench"].get("flow-study")
    assert list(project.modules.keys()) == reversed_order


def test_prototypes_page_and_file_management(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    c.post("/admin/projects/new", data={"slug": "proto-study", "name": "Proto Study", "source": "ab"})

    # 1. View Prototypes and files page
    res = c.get("/proto-study/admin/studio/prototypes")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Prototypes and files · Proto Study" in html
    assert "Upload prototypes" in html
    assert "variant-a.html" in html
    assert "variant-b.html" in html
    assert html.count('<main id="main"') == 1

    # 2. Used-by detection: variant-a.html is used by events-ab module, so it should be locked
    assert "Used by module" in html or "Entry file for variant" in html

    # Attempting to delete locked file should fail
    res_del_locked = c.post("/proto-study/admin/studio/files/delete", data={"path": "prototypes/variant-a.html"})
    assert "error=" in res_del_locked.location

    # 3. Upload a new unused file
    import io
    dummy = (io.BytesIO(b"<h1>Test page</h1>"), "test.html")
    res_up = c.post("/proto-study/admin/studio/files/upload", data={"files": dummy}, content_type="multipart/form-data")
    assert res_up.status_code == 302
    studio_root = Path(app.config["DATA_DIR"]) / "projects" / "proto-study"
    assert (studio_root / "prototypes" / "test.html").is_file()

    # 4. View page and delete the unused file
    res_after = c.get("/proto-study/admin/studio/prototypes")
    html_after = res_after.get_data(as_text=True)
    assert "test.html" in html_after

    res_del_ok = c.post("/proto-study/admin/studio/files/delete", data={"path": "prototypes/test.html"})
    assert res_del_ok.status_code == 302
    assert not (studio_root / "prototypes" / "test.html").exists()


def test_read_only_study_cannot_modify_modules_or_files(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # Bundled example is read-only
    res_build = c.get("/example/admin/studio/")
    assert res_build.status_code == 200
    assert "This study is read-only" in res_build.get_data(as_text=True)

    res_proto = c.get("/example/admin/studio/prototypes")
    assert res_proto.status_code == 200
    assert "This study is read-only" in res_proto.get_data(as_text=True)

    # Mutation endpoints return 403
    assert c.post("/example/admin/studio/modules/new", data={"id": "x", "type": "survey"}).status_code == 403
    assert c.post("/example/admin/studio/modules/feedback/duplicate").status_code == 403
    assert c.post("/example/admin/studio/modules/feedback/move", data={"direction": "up"}).status_code == 403
    assert c.post("/example/admin/studio/files/upload", data={}).status_code == 403
    assert c.post("/example/admin/studio/files/delete", data={"path": "prototypes/x"}).status_code == 403


def test_visual_module_editor_flow(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # Create study
    c.post("/admin/projects/new", data={"slug": "mod-study", "name": "Module Study", "source": "ab"})

    # 1. GET visual editor for an A/B test module
    res = c.get("/mod-study/admin/studio/modules/events-ab")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Edit Try two events pages" in html or "Edit" in html
    assert "Setup" in html and "Content" in html and "Advanced" in html
    assert "What a participant does" in html
    assert "savebar" in html
    assert "openKeys = {};" in html
    # Check header outside main
    assert html.count('<main id="main"') == 1
    assert html.find('class="page-head"') < html.find('<main id="main"')

    # 2. Check action via AJAX
    # Invalid YAML check
    res_bad_check = c.post("/mod-study/admin/studio/modules/events-ab", data={
        "action": "check",
        "content": "type: ab_test\ntitle: Broken\nvariants: {}\n"
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_bad_check.status_code == 200
    data_bad = res_bad_check.get_json()
    assert data_bad["ok"] is False
    assert len(data_bad["problems"]) > 0

    # Valid YAML check
    studio_root = Path(app.config["DATA_DIR"]) / "projects" / "mod-study"
    mod_yaml = (studio_root / "modules" / "events-ab.yaml").read_text(encoding="utf-8")
    res_good_check = c.post("/mod-study/admin/studio/modules/events-ab", data={
        "action": "check",
        "content": mod_yaml
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_good_check.status_code == 200
    assert res_good_check.get_json()["ok"] is True

    # 3. Save action via AJAX
    import re
    updated_yaml = re.sub(r"^title:.*$", "title: Test Two Event Designs", mod_yaml, flags=re.MULTILINE)
    res_save = c.post("/mod-study/admin/studio/modules/events-ab", data={
        "action": "save",
        "content": updated_yaml
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_save.status_code == 200
    assert res_save.get_json()["ok"] is True

    # Verify disk content & reload
    saved_yaml = (studio_root / "modules" / "events-ab.yaml").read_text(encoding="utf-8")
    assert "Test Two Event Designs" in saved_yaml
    project = app.extensions["testbench"].get("mod-study")
    assert project.modules["events-ab"].title == "Test Two Event Designs"

    # Verify backup exists
    assert list((studio_root / ".history").glob("modules__events-ab.yaml.*.bak"))

    # 4. View in read-only study (example project)
    res_ro = c.get("/example/admin/studio/modules/events-ab")
    assert res_ro.status_code == 200
    ro_html = res_ro.get_data(as_text=True)
    assert "You have view access" in ro_html or "disabled" in ro_html

