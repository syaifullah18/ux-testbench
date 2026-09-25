from testbench.modules.card_sort import CardSort
from testbench.config import Project

def test_card_sort_validation():
    # We pass a mock scope
    class Scope:
        def add(self, err):
            self.errs.append(err)
        def down(self, key):
            return self
        errs = []
        
    scope = Scope()
    class DummyModule:
        id = "c1"
        def __init__(self):
            self.project = None
    
    m = CardSort(DummyModule())
    
    raw = {
        "cards": ["Apple", "Banana", {"id": "c3", "label": "Orange"}],
        "categories": ["Fruit", "Vegetable"]
    }
    
    c = m.validate(raw, scope)
    assert not scope.errs
    assert len(c["cards"]) == 3
    assert c["cards"][0] == {"id": "c1", "label": "Apple"}
    assert c["cards"][2] == {"id": "c3", "label": "Orange"}
    assert c["categories"] == ["Fruit", "Vegetable"]
    assert c["allow_new_categories"] is False
    
    # Test open sorting default
    raw2 = {"cards": ["Car"]}
    c2 = m.validate(raw2, scope)
    assert not scope.errs
    assert c2["categories"] == []
    assert c2["allow_new_categories"] is True
