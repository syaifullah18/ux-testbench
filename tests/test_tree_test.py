from testbench.modules.tree_test import TreeTest
from testbench.config import Problems
from types import SimpleNamespace

def test_flatten_tree():
    raw = [
        {"id": "t1", "label": "L1", "children": [
            {"id": "t1_1", "label": "L1.1"}
        ]},
        {"id": "t2", "label": "L2"}
    ]
    tt = TreeTest(SimpleNamespace(conf={}, project=None, id="tree_test"))
    scope = Problems()
    val = tt.validate({"tree": raw, "tasks": [{"id": "tsk", "prompt": "P", "accept": ["t1_1"]}]}, scope)
    assert not scope
    assert len(val["flat_tree"]) == 3
    
    # test validation failures
    scope = Problems()
    val = tt.validate({"tree": raw, "tasks": [{"id": "tsk", "prompt": "P", "accept": ["t1"]}]}, scope)
    assert len(scope) == 1
    assert "not a leaf node" in scope[0]

    # test missing tree
    scope_no_tree = Problems()
    tt.validate({"tasks": []}, scope_no_tree)
    assert any("needs a `tree:` list" in s for s in scope_no_tree)

    # test duplicate node ids
    scope_dup = Problems()
    tt.validate({"tree": [{"id": "dup", "label": "A"}, {"id": "dup", "label": "B"}], "tasks": [{"id": "t", "prompt": "P", "accept": ["dup"]}]}, scope_dup)
    assert any("tree node ids must be unique" in s for s in scope_dup)

    # test invalid task ID and empty accept
    scope_bad_task = Problems()
    tt.validate({"tree": [{"id": "leaf", "label": "L"}], "tasks": [{"id": "BAD ID!", "prompt": ""}]}, scope_bad_task)
    assert any("needs a valid `id:`" in s for s in scope_bad_task)
    assert any("needs a `prompt:`" in s for s in scope_bad_task)
    assert any("needs an `accept:` list" in s for s in scope_bad_task)


def test_tree_test_validation_missing_prompt_key():
    tt = TreeTest(SimpleNamespace(conf={}, project=None, id="tree_test"))
    scope = Problems()
    val = tt.validate({"tree": [{"id": "leaf", "label": "L"}], "tasks": [{"id": "t1", "accept": ["leaf"]}]}, scope)
    assert any("needs a `prompt:`" in s for s in scope)
    assert len(val["tasks"]) == 1
    assert val["tasks"][0]["prompt"] == ""


def test_tree_test_validation_non_dict_task():
    tt = TreeTest(SimpleNamespace(conf={}, project=None, id="tree_test"))
    scope = Problems()
    val = tt.validate({"tree": [{"id": "leaf", "label": "L"}], "tasks": ["not_a_dict"]}, scope)
    assert any("must be a mapping" in s for s in scope)


