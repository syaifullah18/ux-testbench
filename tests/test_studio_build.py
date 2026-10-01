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


def test_survey_module_editor_flow(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. View journey in example study (read-only)
    res = c.get("/example/admin/studio/modules/journey")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Ensure raw dict string is NOT rendered in initial_data or html
    assert "{&#39;find_event&#39;: &#39;Finding an event&#39;" not in html
    assert "{'find_event': 'Finding an event'" not in html
    assert "find_event: Finding an event" in html
    assert "Answer from your own experience" in html
    assert "member_steps" in html
    assert "staff_steps" in html

    # 2. Test module_to_ui_data directly with the exact config
    from testbench.studio import module_to_ui_data
    import yaml
    project = app.extensions["testbench"].get("example")
    m_journey = project.modules["journey"]
    raw_yaml = (project.dir / "modules" / "journey.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw_yaml)
    ui_data = module_to_ui_data("journey", m_journey, data)

    # Check pages and intro
    assert len(ui_data["pages"]) == 2
    assert ui_data["pages"][0]["title"] == "Rate each step"
    assert "Never done" in ui_data["pages"][0]["intro"]

    # Check member_steps matrix
    q0 = ui_data["pages"][0]["questions"][0]
    assert q0["id"] == "member_steps"
    assert q0["type"] == "matrix"
    assert "find_event: Finding an event\n" in q0["rows"]
    assert q0["na_label"] == "Never done"
    assert q0["low"] == "Very easy"
    assert q0["high"] == "Very hard"
    assert q0["showIf"]["module"] == "profile"
    assert q0["showIf"]["q"] == "role"
    assert q0["showIf"]["op"] == "in"
    assert q0["showIf"]["value"] == "member, visitor"

    # Check staff_steps matrix
    q1 = ui_data["pages"][0]["questions"][1]
    assert q1["showIf"]["module"] == "profile"
    assert q1["showIf"]["q"] == "role"
    assert q1["showIf"]["op"] == "equals"
    assert q1["showIf"]["value"] == "staff"

    # Check priorities multi with optionsFrom
    q2 = ui_data["pages"][0]["questions"][2]
    assert q2["id"] == "priorities"
    assert q2["optionsFrom"] == "member_steps"
    assert q2["max"] == 2

    # Check Page 2 questions
    p2 = ui_data["pages"][1]
    q_interview = p2["questions"][2]
    assert q_interview["id"] == "interview"
    assert "yes: Yes\nno: No" in q_interview["options"]

    q_contact = p2["questions"][3]
    assert q_contact["id"] == "contact"
    assert q_contact["long"] is False
    assert q_contact["showIf"]["module"] == ""
    assert q_contact["showIf"]["q"] == "interview"
    assert q_contact["showIf"]["value"] == "yes"

    # 3. Create an editable study from example and test checking & saving survey module
    c.post("/admin/projects/new", data={"slug": "survey-study", "name": "Survey Study", "source": "example"})
    res_check = c.post("/survey-study/admin/studio/modules/journey", data={
        "action": "check",
        "content": raw_yaml
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_check.status_code == 200
    assert res_check.get_json()["ok"] is True

    res_save = c.post("/survey-study/admin/studio/modules/journey", data={
        "action": "save",
        "content": raw_yaml
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_save.status_code == 200
    assert res_save.get_json()["ok"] is True

    # 4. Test serialized YAML format (with options_from, ref: profile.role, rows mapping, na_label, etc.)
    serialized_yaml = """type: survey
title: Where things are hard
description: Rate each part of the portal and tell us what gets in your way.
minutes: 6
requires: [profile]

pages:
  - title: Rate each step
    intro: "Answer from your own experience. Choose \\"Never done\\" for anything you have not tried."
    questions:
      - id: member_steps
        type: matrix
        label: How difficult is each of these for you?
        rows:
          find_event: Finding an event
          book_event: Booking a seat at an event
          search_catalog: Searching the catalogue
          renew: Renewing a loan
          account: Managing my account
        na_label: Never done
        points: 5
        labels: [Very easy, Very hard]
        show_if: { ref: profile.role, in: [member, visitor] }
      - id: staff_steps
        type: matrix
        label: How difficult is each of these for you?
        rows:
          publish_event: Publishing an event
          manage_bookings: Managing bookings
          reports: Getting attendance reports
        na_label: Never done
        points: 5
        labels: [Very easy, Very hard]
        show_if: { ref: profile.role, equals: staff }
      - id: priorities
        type: multi
        label: Which should we fix first?
        options_from: member_steps
        max: 2
        show_if: { ref: profile.role, in: [member, visitor] }
      - id: staff_priorities
        type: multi
        label: Which should we fix first?
        options_from: staff_steps
        max: 2
        show_if: { ref: profile.role, equals: staff }
  - title: Your story
    questions:
      - id: stuck
        type: multi
        label: When you get stuck, what do you usually do?
        options: [Ask staff, Search the help page, Try until it works, Give up]
        columns: 2
        required: false
      - id: story
        type: text
        label: Describe the last time the portal got in your way.
        hint: What were you trying to do, where did you get stuck, and how did it end?
      - id: interview
        type: single
        label: Would you join a 20-minute follow-up interview?
        options:
          "yes": Yes
          "no": No
      - id: contact
        type: text
        label: Email or phone for scheduling
        hint: Used only to schedule the interview, then deleted.
        long: false
        show_if: { ref: interview, equals: "yes" }
"""
    res_serialized = c.post("/survey-study/admin/studio/modules/journey", data={
        "action": "check",
        "content": serialized_yaml
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res_serialized.status_code == 200
    assert res_serialized.get_json()["ok"] is True



