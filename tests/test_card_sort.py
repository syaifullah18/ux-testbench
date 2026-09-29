import tempfile
from pathlib import Path
import yaml

from testbench.modules.card_sort import CardSort
from testbench.config import Problems, Scope, load_project
from testbench.modules import MODULE_TYPES
from testbench.i18n import translator


def test_card_sort_validation():
    problems = Problems()
    scope = Scope(problems, "modules/c1.yaml")

    class DummyModule:
        id = "c1"
        def __init__(self):
            self.project = None
            self.conf = {}
    
    mod = DummyModule()
    m = CardSort(mod)
    
    raw = {
        "title": "Grocery Sort",
        "cards": ["Apple", "Banana", {"id": "c3", "label": "Orange"}],
        "categories": ["Fruit", "Vegetable"],
        "pre": [{"id": "q_pre", "type": "text", "prompt": "Pre question?"}],
        "post": [{"id": "q_post", "type": "text", "prompt": "Post question?"}],
        "final": [{"id": "q_final", "type": "text", "prompt": "Final question?"}]
    }
    
    c = m.validate(raw, scope)
    assert not problems
    assert len(c["cards"]) == 3
    assert c["cards"][0] == {"id": "c1", "label": "Apple"}
    assert c["cards"][2] == {"id": "c3", "label": "Orange"}
    assert c["categories"] == ["Fruit", "Vegetable"]
    assert c["allow_new_categories"] is False
    assert len(c["pre"]) == 1
    assert len(c["post"]) == 1
    assert len(c["final"]) == 1
    
    mod.conf = c
    assert m.question_ids() == {"q_pre", "q_post", "q_final"}
    
    # Check steps
    steps = m.steps({})
    assert steps == ["pre", "sort", "post", "final"]
    
    t = translator("en")
    assert m.step_label("sort", t) == "Card Sorting"
    
    # Test open sorting default
    problems2 = Problems()
    scope2 = Scope(problems2, "modules/c2.yaml")
    raw2 = {"cards": ["Car"]}
    c2 = m.validate(raw2, scope2)
    assert not problems2
    assert c2["categories"] == []
    assert c2["allow_new_categories"] is True


def test_card_sort_load_project_integration():
    with tempfile.TemporaryDirectory() as tmp:
        pdir = Path(tmp) / "card-study"
        pdir.mkdir()
        (pdir / "modules").mkdir()
        
        project_yaml = {
            "name": "Card Sort Study",
            "locale": "en",
            "modules": ["sorting"]
        }
        (pdir / "project.yaml").write_text(yaml.dump(project_yaml), encoding="utf-8")
        
        sorting_yaml = {
            "type": "card_sort",
            "title": "Department Sort",
            "cards": [
                {"id": "card_ops", "label": "Operations"},
                {"id": "card_mkt", "label": "Marketing"},
                "Design"
            ],
            "categories": ["Core", "Growth"],
            "allow_new_categories": True,
            "pre": [
                {"id": "exp", "type": "single", "prompt": "Experience level?", "options": ["Junior", "Senior"]}
            ],
            "post": [
                {"id": "difficulty", "type": "scale", "prompt": "How difficult was this?", "points": 5}
            ]
        }
        (pdir / "modules" / "sorting.yaml").write_text(yaml.dump(sorting_yaml), encoding="utf-8")
        
        problems = Problems()
        project = load_project(pdir, MODULE_TYPES, problems)
        assert not problems, f"Project load produced errors: {problems}"
        assert "sorting" in project.modules
        assert project.modules["sorting"].type == "card_sort"
        assert project.modules["sorting"].impl.question_ids() == {"exp", "difficulty"}

