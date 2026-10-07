import os
import re
import shutil
import uuid
from pathlib import Path

import pytest
import yaml

from testbench import create_app

ROOT = Path(__file__).resolve().parent.parent


def write_project(base, slug, project, modules, files=None):
    pdir = base / slug
    (pdir / "modules").mkdir(parents=True)
    project = {**project, "modules": list(modules)}
    (pdir / "project.yaml").write_text(yaml.safe_dump(project, allow_unicode=True), encoding="utf-8")
    for mid, conf in modules.items():
        (pdir / "modules" / f"{mid}.yaml").write_text(yaml.safe_dump(conf, allow_unicode=True), encoding="utf-8")
    for rel, text in (files or {}).items():
        (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pdir / rel).write_text(text, encoding="utf-8")
    return pdir


PROTO = "<!doctype html><html><body><a href='#x'>Link</a><link rel=stylesheet href='style.css'></body></html>"

MINI_AB = {
    "type": "ab_test", "title": "Mini AB",
    "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
    "tasks": [
        {"id": "t1", "title": "One", "prompt": "Type 42", "fields": [{"id": "x", "label": "X"}], "accept": {"x": ["42"]}},
        {"id": "t2", "title": "Two", "prompt": "Anything"},
    ],
    "post_survey": [{"id": "easy", "type": "scale", "label": "Easy?"}],
    "decision_rule": {"min_participants": 2, "survey_questions": ["easy"]},
}


# The suite runs on SQLite by default. With TEST_DATABASE_URL=postgresql://… it runs the same
# tests on PostgreSQL: every app built during one test gets that database and a schema prefix
# unique to the test, which is the PostgreSQL equivalent of each test's own tmp_path.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
on_postgres = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs TEST_DATABASE_URL")
sqlite_only = pytest.mark.skipif(bool(TEST_DATABASE_URL), reason="about SQLite files")


@pytest.fixture(autouse=True)
def _local_file_storage(monkeypatch):
    """testbench loads .env on import, so a developer's real bucket settings would otherwise reach
    every test (uploads to the real bucket, network on every request). Tests that want S3 pass
    STORAGE_BACKEND and S3_* to make_app themselves."""
    import os
    for k in list(os.environ):
        if k == "STORAGE_BACKEND" or k.startswith("S3_"):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _database_backend(monkeypatch):
    if TEST_DATABASE_URL:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        monkeypatch.setenv("DB_SCHEMA_PREFIX", f"t{uuid.uuid4().hex[:10]}_")
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def projects_dir(tmp_path):
    base = tmp_path / "projects"
    base.mkdir()
    shutil.copytree(ROOT / "projects" / "example", base / "example")
    return base


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    def factory(projects_dir, **env):
        import os
        for k in list(os.environ):
            if k.endswith("_PASSCODE"):
                monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("TESTBENCH_MODE", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATA_DIR": str(tmp_path / "data"),
                          "PROJECTS_DIRS": [str(projects_dir)]})
        return app
    return factory


def csrf_free_post(client, url, data=None, **kw):
    return client.post(url, data=data or {}, **kw)


def extract_json(html, var):
    import json
    return json.loads(re.search(rf"window.{var} = (.*?);</script>", html, re.S).group(1))
