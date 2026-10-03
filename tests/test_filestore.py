"""Object storage: the bucket keeps the durable copy of every Studio study.

These run against an in-process S3 server (moto), the same API AWS, Cloudflare R2 and MinIO
speak through boto3. They are skipped when moto is not installed.
"""
import io
import uuid
from pathlib import Path

import pytest

moto_server = pytest.importorskip("moto.server")
import boto3   # noqa: E402

from testbench import create_app, filestore   # noqa: E402


@pytest.fixture(scope="session")
def s3_endpoint():
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def bucket(s3_endpoint, monkeypatch):
    name = f"tb-{uuid.uuid4().hex[:12]}"
    creds = {"S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test", "S3_REGION": "us-east-1"}
    client = boto3.client("s3", endpoint_url=s3_endpoint, region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    client.create_bucket(Bucket=name)
    env = {"STORAGE_BACKEND": "s3", "S3_BUCKET": name, "S3_ENDPOINT_URL": s3_endpoint, **creds}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return client, name


def keys(bucket, prefix="testbench/studies/"):
    client, name = bucket
    out = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=name, Prefix=prefix):
        out += [o["Key"] for o in page.get("Contents", [])]
    return sorted(out)


def superadmin(app):
    c = app.test_client()
    c.post("/admin/", data={"passcode": "root"})
    return c


def new_study(c, slug="checkout"):
    r = c.post("/admin/projects/new", data={"slug": slug, "name": "Checkout study", "source": "blank",
                                           "locale": "en", "identity": "code", "access": "passcode"})
    assert r.status_code == 302


def test_a_new_study_is_mirrored_to_the_bucket(bucket, projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    new_study(superadmin(app))
    stored = keys(bucket)
    assert "testbench/studies/checkout/project.yaml" in stored
    assert any(k.startswith("testbench/studies/checkout/modules/") for k in stored)
    assert not any(".history" in k for k in stored)


def test_uploads_and_deletions_follow_the_local_folder(bucket, projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    new_study(c)
    r = c.post("/checkout/admin/studio/files/upload", data={"files": (io.BytesIO(b"<h1>v1</h1>"), "proto.html")},
               content_type="multipart/form-data")
    assert r.status_code == 302
    key = "testbench/studies/checkout/prototypes/proto.html"
    assert key in keys(bucket)
    client, name = bucket
    assert client.get_object(Bucket=name, Key=key)["Body"].read() == b"<h1>v1</h1>"

    c.post("/checkout/admin/studio/files/delete", data={"path": "prototypes/proto.html"})
    assert key not in keys(bucket)


def test_a_fresh_volume_gets_every_study_back(bucket, projects_dir, make_app, tmp_path):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    new_study(c)
    c.post("/checkout/admin/studio/files/upload", data={"files": (io.BytesIO(b"<p>keep me</p>"), "keep.html")},
           content_type="multipart/form-data")

    # A new container: empty DATA_DIR, same bucket.
    fresh = create_app({"TESTING": True, "SECRET_KEY": "test", "DATA_DIR": str(tmp_path / "fresh"),
                        "PROJECTS_DIRS": [str(projects_dir)]})
    studio = tmp_path / "fresh" / "projects" / "checkout"
    assert (studio / "project.yaml").is_file()
    assert (studio / "prototypes" / "keep.html").read_bytes() == b"<p>keep me</p>"
    assert fresh.extensions["testbench"].get("checkout") is not None   # loaded, not just downloaded


def test_restore_never_deletes_and_pushes_what_only_the_disk_has(bucket, projects_dir, make_app, tmp_path):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    new_study(superadmin(app))
    local_only = Path(app.config["DATA_DIR"]) / "projects" / "checkout" / "prototypes" / "offline.html"
    local_only.parent.mkdir(parents=True, exist_ok=True)
    local_only.write_text("made while the bucket was unreachable")
    with app.app_context():
        down, up = filestore.restore(Path(app.config["DATA_DIR"]) / "projects")
    assert (down, up) == (0, 1)
    assert "testbench/studies/checkout/prototypes/offline.html" in keys(bucket)
    assert local_only.is_file()


def test_deleting_a_study_removes_its_copy(bucket, projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    new_study(c)
    assert keys(bucket, "testbench/studies/checkout/")
    c.post("/checkout/admin/studio/delete", data={"confirm": "checkout", "with_data": "1"})
    assert keys(bucket, "testbench/studies/checkout/") == []


def test_unchanged_files_are_not_uploaded_again(bucket, projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    new_study(superadmin(app))
    with app.app_context():
        assert filestore.sync_study("checkout", Path(app.config["DATA_DIR"]) / "projects") == (0, 0)


def test_participant_traffic_never_touches_the_bucket(bucket, projects_dir, make_app, monkeypatch):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    calls = []
    monkeypatch.setattr(filestore, "sync_study", lambda *a, **k: calls.append(a) or (0, 0))
    monkeypatch.setattr(filestore, "sync_all", lambda *a, **k: calls.append(a))
    p = app.test_client()
    p.post("/example/", data={"identity": "P01", "consent": "1"})
    p.get("/s/example/")
    assert calls == []


def test_a_bucket_failure_does_not_fail_the_edit(bucket, projects_dir, make_app, monkeypatch):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    def boom(*a, **k):
        raise RuntimeError("bucket down")
    monkeypatch.setattr(filestore, "sync_all", boom)
    r = c.post("/admin/projects/new", data={"slug": "offline", "name": "Offline", "source": "blank",
                                           "locale": "en", "identity": "code", "access": "passcode"})
    assert r.status_code == 302
    assert (Path(app.config["DATA_DIR"]) / "projects" / "offline" / "project.yaml").is_file()


def test_local_storage_is_the_default_and_needs_no_bucket(projects_dir, make_app, monkeypatch):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    app = make_app(projects_dir)
    with app.app_context():
        assert filestore.store() is None
        assert filestore.sync_study("anything", Path("/nonexistent")) == (0, 0)


# ---------------------------------------------------------------- exports

def follow_presigned(location):
    """Fetch a presigned URL the way a browser would, without the app."""
    import urllib.request
    with urllib.request.urlopen(location) as r:
        return r.read(), r.headers.get("Content-Disposition", "")


def test_a_study_export_is_handed_out_as_an_expiring_link(bucket, projects_dir, make_app):
    import zipfile
    from urllib.parse import parse_qs, urlparse
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    new_study(c)
    r = c.get("/checkout/admin/studio/export.zip")
    assert r.status_code == 303
    query = parse_qs(urlparse(r.location).query)
    assert query["X-Amz-Expires"] == [str(filestore.EXPORT_LINK_SECONDS)]
    body, disposition = follow_presigned(r.location)
    assert 'filename="checkout.zip"' in disposition
    assert "checkout/project.yaml" in zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert any(k.startswith("testbench/exports/checkout/") for k in keys(bucket, "testbench/exports/"))


def test_csv_exports_go_through_the_bucket_too(bucket, projects_dir, make_app):
    app = make_app(projects_dir, EXAMPLE_ADMIN_PASSCODE="adm")
    c = app.test_client()
    c.post("/example/admin/", data={"passcode": "adm"})
    for url, name in [("/example/admin/export.csv", "example_combined.csv"),
                      ("/example/admin/m/profile/export.csv", "example-profile.csv")]:
        r = c.get(url)
        assert r.status_code == 303, url
        body, disposition = follow_presigned(r.location)
        assert f'filename="{name}"' in disposition
        assert body.decode("utf-8-sig").splitlines()[0]      # a header row arrived


def test_local_exports_are_unchanged(projects_dir, make_app, monkeypatch):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    app = make_app(projects_dir, EXAMPLE_ADMIN_PASSCODE="adm")
    c = app.test_client()
    c.post("/example/admin/", data={"passcode": "adm"})
    r = c.get("/example/admin/export.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert r.headers["Content-Disposition"] == "attachment; filename=example_combined.csv"
