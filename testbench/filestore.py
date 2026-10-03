"""Object storage (S3, Cloudflare R2 or MinIO) for the Studio's study folders and for exports.

Off by default. With STORAGE_BACKEND=s3 the bucket becomes the durable copy of every study made
in the Studio (its YAML, prototypes and uploaded images) and the local Studio folder becomes a
working cache of it:

- **Mirror on write.** After a request that changes a study's files (any Studio POST, a status
  change, creating, importing or deleting a study), that study's folder is mirrored to the bucket:
  new and changed files are uploaded, files deleted locally are deleted remotely, and a study
  whose folder is gone has its prefix removed.
- **Restore on start.** When the app starts it downloads every file the bucket has and the disk
  does not, before the projects are loaded. A container on a fresh, empty volume comes back with
  every study. It never deletes anything at start, and uploads files only the disk has, so a
  volume that was ahead of the bucket is not rolled back.
- **Serving is unchanged.** Prototypes and task images are still served from the local copy, on
  the app's own origin, which is what the task runner needs to instrument them (ADR 001).

Exports are written to the bucket under `exports/` and handed to the browser as a presigned
link that expires; see `export_response()`.

The `.history` folder (the Studio's edit backups) stays local and is not mirrored.

Keys are `<S3_PREFIX>/studies/<slug>/<path>` and `<S3_PREFIX>/exports/<slug>/<file>`. One
client serves AWS, R2 and MinIO; S3_ENDPOINT_URL selects the provider.
"""
import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

from flask import Response, current_app, redirect, request

log = logging.getLogger(__name__)

EXPORT_LINK_SECONDS = 15 * 60


def enabled(app=None):
    app = app or current_app
    return (app.config.get("STORAGE_BACKEND") or "local").lower() == "s3"


class S3Store:
    def __init__(self, cfg):
        import boto3
        from botocore.config import Config
        self.bucket = cfg["S3_BUCKET"]
        if not self.bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        self.prefix = (cfg.get("S3_PREFIX") or "testbench").strip("/")
        endpoint = cfg.get("S3_ENDPOINT_URL") or None
        style = cfg.get("S3_ADDRESSING_STYLE") or ("path" if endpoint else "auto")
        self.client = boto3.client(
            "s3", endpoint_url=endpoint, region_name=cfg.get("S3_REGION") or "us-east-1",
            aws_access_key_id=cfg.get("S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=cfg.get("S3_SECRET_ACCESS_KEY") or None,
            config=Config(signature_version="s3v4", s3={"addressing_style": style},
                          retries={"max_attempts": 3, "mode": "standard"}))

    # ---- keys

    def study_prefix(self, slug):
        return f"{self.prefix}/studies/{slug}/"

    def export_key(self, slug, filename):
        return f"{self.prefix}/exports/{slug}/{time.strftime('%Y%m%dT%H%M%S')}-{filename}"

    # ---- primitives

    def list(self, prefix):
        """{key: etag} under a prefix."""
        out = {}
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                out[obj["Key"]] = obj["ETag"].strip('"')
        return out

    def put_file(self, path, key):
        # One part up to 100 MB, so the ETag stays the file's MD5 and an unchanged file is never
        # uploaded twice (a multipart ETag is not an MD5).
        from boto3.s3.transfer import TransferConfig
        self.client.upload_file(str(path), self.bucket, key,
                                Config=TransferConfig(multipart_threshold=100 * 1024 * 1024))

    def put_bytes(self, data, key, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_file(self, key, path):
        """Download atomically: a half-written file is never visible under its real name."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dl-")
        os.close(fd)
        try:
            self.client.download_file(self.bucket, key, tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def delete(self, keys):
        keys = list(keys)
        for start in range(0, len(keys), 1000):
            self.client.delete_objects(Bucket=self.bucket,
                                       Delete={"Objects": [{"Key": k} for k in keys[start:start + 1000]]})

    def temporary_url(self, key, seconds=EXPORT_LINK_SECONDS, filename=None):
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)


def store(app=None):
    """The configured S3Store, or None when storage is local."""
    app = app or current_app
    if not enabled(app):
        return None
    st = app.extensions.get("tb_filestore")
    if st is None:
        st = app.extensions["tb_filestore"] = S3Store(app.config)
    return st


# ---------------------------------------------------------------- study folders

def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_files(study_dir):
    """{relative posix path: Path} for a study folder, skipping hidden files and .history."""
    out = {}
    if study_dir.is_dir():
        for p in study_dir.rglob("*"):
            rel = p.relative_to(study_dir)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                out[rel.as_posix()] = p
    return out


def sync_study(slug, studio_dir, app=None):
    """Make the bucket match one local study folder (local is the truth). Returns
    (uploaded, deleted) counts."""
    st = store(app)
    if st is None:
        return 0, 0
    prefix = st.study_prefix(slug)
    remote = st.list(prefix)
    local = _local_files(Path(studio_dir) / slug)
    uploaded = 0
    for rel, path in local.items():
        key = prefix + rel
        if remote.get(key) != _md5(path):
            st.put_file(path, key)
            uploaded += 1
    stale = [k for k in remote if k[len(prefix):] not in local]
    if stale:
        st.delete(stale)
    return uploaded, len(stale)


def sync_all(studio_dir, app=None):
    """Mirror every local study, and remove the bucket copy of studies deleted locally."""
    st = store(app)
    if st is None:
        return
    studio_dir = Path(studio_dir)
    local_slugs = {p.name for p in studio_dir.iterdir() if p.is_dir() and not p.name.startswith(".")} \
        if studio_dir.is_dir() else set()
    remote_slugs = {k[len(st.prefix) + len("/studies/"):].split("/", 1)[0]
                    for k in st.list(f"{st.prefix}/studies/")}
    for slug in sorted(local_slugs | remote_slugs):
        sync_study(slug, studio_dir, app)


def restore(studio_dir, app=None, log_=None):
    """At start: download what the bucket has and the disk lacks; upload what only the disk has.
    Never deletes. Returns (downloaded, uploaded)."""
    st = store(app)
    if st is None:
        return 0, 0
    studio_dir = Path(studio_dir)
    root = f"{st.prefix}/studies/"
    remote = st.list(root)
    downloaded = 0
    for key, etag in remote.items():
        rel = key[len(root):]
        if not rel or rel.endswith("/") or any(part.startswith(".") or part == ".." for part in rel.split("/")):
            continue
        path = studio_dir / rel
        if not path.exists():
            st.get_file(key, path)
            downloaded += 1
    uploaded = 0
    remote_rels = {k[len(root):] for k in remote}
    if studio_dir.is_dir():
        for study in (p for p in studio_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for rel, path in _local_files(study).items():
                if f"{study.name}/{rel}" not in remote_rels:
                    st.put_file(path, f"{root}{study.name}/{rel}")
                    uploaded += 1
    if log_ and (downloaded or uploaded):
        log_(f"object storage: restored {downloaded} file(s), uploaded {uploaded} local-only file(s)")
    return downloaded, uploaded


# Requests after which a study's files may have changed. Participant traffic never matches.
MUTATING_BLUEPRINTS = {"studio"}
MUTATING_ENDPOINTS = {"admin.set_status", "public.update_status", "public.new_project", "auth.account"}


def after_request(response):
    """Mirror the study the request changed. A failure is logged, never shown to the user: the
    local copy is already saved, and the next change or restart mirrors it again."""
    if request.method != "POST" or response.status_code >= 400 or not enabled():
        return response
    endpoint = request.endpoint or ""
    if request.blueprint not in MUTATING_BLUEPRINTS and endpoint not in MUTATING_ENDPOINTS:
        return response
    from .config import studio_dir
    studio = studio_dir(current_app.config["DATA_DIR"])
    slug = (request.view_args or {}).get("slug")
    try:
        if slug:
            sync_study(slug, studio)
        else:
            # A new, imported or duplicated study (its slug is not in the URL), or an account
            # deletion that took the studies it owned with it.
            sync_all(studio)
    except Exception:
        log.exception("could not mirror study files to object storage")
    return response


# ---------------------------------------------------------------- exports

def export_response(data, filename, mimetype, slug):
    """Send an export. Local storage: straight from memory, as before. S3: upload it, then
    redirect to a presigned link that expires in EXPORT_LINK_SECONDS, so a large file is served
    by the bucket instead of holding an app worker."""
    st = store()
    body = data.read() if hasattr(data, "read") else data
    if st is None:
        return Response(body, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"})
    if isinstance(body, str):
        body = body.encode("utf-8")
    key = st.export_key(slug, filename)
    st.put_bytes(body, key, mimetype)
    return redirect(st.temporary_url(key, filename=filename), code=303)
