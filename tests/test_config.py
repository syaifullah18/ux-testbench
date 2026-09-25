import pytest

from testbench.config import ConfigError, load_all
from testbench.modules import MODULE_TYPES

from .conftest import PROTO, write_project


def test_example_project_is_valid(projects_dir):
    projects = load_all(MODULE_TYPES, [projects_dir])
    assert list(projects["example"].modules) == ["profile", "journey", "info-arch", "events-ab"]


def test_reports_every_problem_at_once(tmp_path):
    write_project(tmp_path, "broken", {"identity": {"mode": "fingerprint"}, "brand": {"primary": "blue"}}, {
        "a": {"type": "survey", "requires": ["ghost"], "questions": [
            {"id": "q", "type": "slider"},
            {"id": "r", "type": "single", "options": ["x"], "show_if": {"ref": "other.q", "equals": 1}},
            {"id": "m", "type": "multi", "options_from": "nope"},
        ]},
        "b": {"type": "ab_test", "variants": {"A": {"file": "missing.html"}}, "tasks": [],
              "decision_rule": {"survey_questions": ["zzz"]}},
        "c": {"type": "carousel"},
    })
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    text = "\n".join(err.value.problems)
    for expected in ["identity.mode", "brand.primary", "requires unknown module 'ghost'", "type must be one of",
                     "unknown module", "options_from", "does not exist", "at least one task", "decision_rule",
                     "modules/c.yaml"]:
        assert expected in text, expected


def test_prototype_must_stay_inside_project(tmp_path):
    (tmp_path / "outside.html").write_text(PROTO)
    write_project(tmp_path / "p", "x", {}, {"ab": {"type": "ab_test", "variants": {"A": {"file": "../../outside.html"}},
                                                  "tasks": [{"id": "t", "prompt": "p"}]}})
    with pytest.raises(ConfigError, match="inside the project folder"):
        load_all(MODULE_TYPES, [tmp_path / "p"])


def test_reserved_slug_rejected(tmp_path):
    write_project(tmp_path, "admin", {}, {"s": {"type": "survey", "questions": [{"id": "q", "type": "text"}]}})
    with pytest.raises(ConfigError, match="must be lowercase"):
        load_all(MODULE_TYPES, [tmp_path])
