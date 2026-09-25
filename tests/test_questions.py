from testbench.questions import normalize, parse, condition_holds, flatten
from testbench.config import Problems

def test_normalize():
    scope = Problems()
    raw = [
        {"id": "q1", "type": "single", "options": ["A", "B"]},
        {"id": "q2", "type": "matrix", "rows": ["R1", "R2"], "points": 5},
        {"id": "q3", "type": "multi", "options_from": "q2"},
    ]
    qs = normalize(raw, scope)
    assert not scope
    assert len(qs) == 3
    assert qs[0]["options"] == [("A", "A"), ("B", "B")]
    assert qs[1]["rows"] == [("R1", "R1"), ("R2", "R2")]
    assert qs[2]["options"] == [("R1", "R1"), ("R2", "R2")]  # options_from test

def test_condition_holds():
    assert condition_holds({"equals": "yes"}, "yes")
    assert not condition_holds({"equals": "yes"}, "no")
    
    assert condition_holds({"in": ["a", "b"]}, "a")
    assert not condition_holds({"in": ["a", "b"]}, "c")
    
    assert condition_holds({"not_in": ["a", "b"]}, "c")
    assert not condition_holds({"not_in": ["a", "b"]}, "a")
    
    # List of values (e.g. from multi select)
    assert condition_holds({"in": ["a", "b"]}, ["b", "c"])

def test_parse():
    scope = Problems()
    qs = normalize([
        {"id": "q1", "type": "single", "options": ["A", "B"]},
        {"id": "q2", "type": "multi", "options": ["A", "B", "C"]},
        {"id": "q3", "type": "matrix", "rows": ["R1", "R2"]},
        {"id": "q4", "type": "text", "required": False}
    ], scope)
    
    class FakeForm:
        def __init__(self, data):
            self.data = data
        def get(self, k, default=""):
            return self.data.get(k, default)
        def getlist(self, k):
            v = self.data.get(k, [])
            return v if isinstance(v, list) else [v]
            
    form = FakeForm({
        "q1": "A",
        "q2": ["A", "C"],
        "q3.R1": "3",
        "q3.R2": "4"
    })
    
    data, errors = parse(qs, form, prior={}, lookup=lambda m, q: None)
    assert not errors
    assert data["q1"] == "A"
    assert data["q2"] == ["A", "C"]
    assert data["q3"] == {"R1": 3, "R2": 4}
    assert data["q4"] == ""

def test_flatten():
    qs = [
        {"id": "q1", "type": "single"},
        {"id": "q2", "type": "multi"},
        {"id": "q3", "type": "matrix", "rows": [("r1", "R1"), ("r2", "R2")]}
    ]
    answers = {
        "q1": "val1",
        "q2": ["val2", "val3"],
        "q3": {"r1": 4, "r2": "na"}
    }
    cols = flatten(qs, answers)
    assert cols["q1"] == "val1"
    assert cols["q2"] == "val2|val3"
    assert cols["q3[r1]"] == 4
    assert cols["q3[r2]"] == "na"
