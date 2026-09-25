"""The Studio: managing projects from the admin site."""
import io
import zipfile
from pathlib import Path


def superadmin(app):
    c = app.test_client()
    c.post("/admin/", data={"passcode": "root"})
    return c


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    buf.seek(0)
    return buf


def test_create_edit_and_run_a_project_without_touching_files(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    r = c.post("/admin/projects/new", data={"slug": "checkout", "name": "Checkout study", "source": "blank",
                                            "locale": "id", "identity": "code", "access": "passcode"})
    assert r.status_code == 302 and "/checkout/admin/studio/" in r.location
    studio_root = Path(app.config["DATA_DIR"]) / "projects"
    assert (studio_root / "checkout" / "project.yaml").is_file()

    # Participants are locked out until a passcode is set in the Studio.
    p = app.test_client()
    assert "belum dibuka" in p.post("/checkout/", data={"identity": "P01", "passcode": "x", "consent": "1"}).get_data(as_text=True)
    assert "error=" in c.post("/checkout/admin/studio/passcodes", data={"participant": "123"}).location  # too short
    c.post("/checkout/admin/studio/passcodes", data={"participant": "secret1", "admin": "admin12"})
    assert c.post("/checkout/", data={"identity": "P01", "passcode": "secret1", "consent": "1"}).status_code == 302
    db = (Path(app.config["DATA_DIR"]) / "_system.db").read_bytes()
    assert b"secret1" not in db  # stored hashed

    # An invalid edit is rejected and the live file is untouched.
    rel = "modules/feedback.yaml"
    before = (studio_root / "checkout" / rel).read_text()
    r = c.post("/checkout/admin/studio/edit", data={"file": rel, "action": "save",
                                                    "content": "type: survey\nquestions: [{id: q, type: slider}]\n"})
    assert "Belum disimpan" in r.get_data(as_text=True)
    assert (studio_root / "checkout" / rel).read_text() == before

    # A valid edit goes live immediately and the previous version is kept.
    r = c.post("/checkout/admin/studio/edit", data={"file": rel, "action": "save",
                                                    "content": before.replace("Quick feedback", "Masukan singkat")})
    assert "saved=1" in r.location
    home = app.test_client()
    home.post("/checkout/", data={"identity": "P02", "passcode": "secret1", "consent": "1"})
    assert "Masukan singkat" in home.get("/checkout/").get_data(as_text=True)
    assert list((studio_root / "checkout" / ".history").glob("modules__feedback.yaml.*.bak"))

    # Add an A/B module: placeholders are created so the project stays valid.
    r = c.post("/checkout/admin/studio/modules/new", data={"id": "compare", "type": "ab_test"})
    assert "file=modules/compare.yaml" in r.location
    assert (studio_root / "checkout" / "prototypes" / "variant-a.html").is_file()
    assert "compare" in app.extensions["testbench"].get("checkout").modules

    # Files used by a module cannot be deleted; unused ones can.
    r = c.post("/checkout/admin/studio/files/delete", data={"path": "prototypes/variant-a.html"})
    assert "error=" in r.location and (studio_root / "checkout" / "prototypes" / "variant-a.html").is_file()

    # Upload a zip with a folder, a disallowed file and a traversal attempt.
    bad = zip_bytes({"../escape.html": "x"})
    r = c.post("/checkout/admin/studio/files/upload", data={"files": (bad, "bad.zip"), "extract": "1"},
               content_type="multipart/form-data")
    assert "Unsafe" in r.location or "error=" in r.location
    assert not (studio_root / "checkout" / "escape.html").exists()
    good = zip_bytes({"site/index.html": "<h1>hi</h1>", "site/app.css": "h1{}", "site/run.sh": "rm -rf /"})
    c.post("/checkout/admin/studio/files/upload", data={"files": (good, "site.zip"), "folder": "v2"},
           content_type="multipart/form-data")
    assert (studio_root / "checkout" / "prototypes" / "v2" / "site" / "index.html").is_file()
    assert not (studio_root / "checkout" / "prototypes" / "v2" / "site" / "run.sh").exists()
    assert c.get("/checkout/admin/studio/files/raw/prototypes/v2/site/app.css").status_code == 200

    # Remove a module; export and re-import the project under a new name.
    assert "notice=" in c.post("/checkout/admin/studio/modules/compare/delete").location
    exported = c.get("/checkout/admin/studio/export.zip")
    names = zipfile.ZipFile(io.BytesIO(exported.data)).namelist()
    assert "checkout/project.yaml" in names and not any(".history" in n for n in names)
    r = c.post("/admin/projects/import", data={"archive": (io.BytesIO(exported.data), "checkout.zip"), "slug": "checkout-b"},
               content_type="multipart/form-data")
    assert "/checkout-b/admin/studio/" in r.location

    # Delete needs the typed confirmation.
    assert "error=" in c.post("/checkout-b/admin/studio/delete", data={"confirm": "nope"}).location
    c.post("/checkout-b/admin/studio/delete", data={"confirm": "checkout-b", "with_data": "1"})
    assert not (studio_root / "checkout-b").exists()
    assert app.extensions["testbench"].get("checkout-b") is None


def test_repo_projects_are_read_only_but_can_be_duplicated(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    page = c.get("/example/admin/studio/").get_data(as_text=True)
    assert "read-only" in page
    r = c.post("/example/admin/studio/edit", data={"file": "project.yaml", "action": "save", "content": "name: x"})
    assert r.status_code == 403
    assert c.post("/example/admin/studio/files/upload", data={}).status_code == 403
    r = c.post("/admin/projects/new", data={"slug": "library", "source": "example"})
    assert "/library/admin/studio/" in r.location
    assert app.extensions["testbench"].get("library").editable
    assert c.get("/library/admin/studio/").status_code == 200


def test_permissions(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root", EXAMPLE_ADMIN_PASSCODE="adm")
    anon = app.test_client()
    assert anon.get("/example/admin/studio/").status_code == 403
    assert anon.post("/admin/projects/new", data={"slug": "x", "source": "blank"}).status_code == 403
    proj_admin = app.test_client()
    proj_admin.post("/example/admin/", data={"passcode": "adm"})
    assert proj_admin.get("/example/admin/studio/").status_code == 200
    assert proj_admin.post("/admin/projects/new", data={"slug": "x", "source": "blank"}).status_code == 403
    # Bad slugs and duplicates are refused.
    c = superadmin(app)
    assert "error=" in c.post("/admin/projects/new", data={"slug": "admin", "source": "blank"}).location
    assert "error=" in c.post("/admin/projects/new", data={"slug": "example", "source": "blank"}).location


def test_env_passcode_overrides_studio(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root", EXAMPLE_ADMIN_PASSCODE="from-env")
    c = superadmin(app)
    c.post("/example/admin/studio/passcodes", data={"admin": "from-studio"})
    other = app.test_client()
    assert "does not match" in other.post("/example/admin/", data={"passcode": "from-studio"}).get_data(as_text=True)
    assert other.post("/example/admin/", data={"passcode": "from-env"}).status_code == 302
