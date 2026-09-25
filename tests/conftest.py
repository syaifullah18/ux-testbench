import re
import shutil
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
