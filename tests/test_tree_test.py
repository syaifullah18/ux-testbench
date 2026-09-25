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
