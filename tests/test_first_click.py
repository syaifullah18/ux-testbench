from testbench.modules.first_click import FirstClick
from testbench.config import Project, Module


class DummyProject:
    def __init__(self):
        self.slug = "test"
        self.dir = "/"
        self.modules = {}

class DummyModule:
    def __init__(self, p, conf):
        self.project = p
        self.id = "fc"
        self.conf = conf

def test_first_click_validation():
    p = DummyProject()
    m = DummyModule(p, {"type": "first_click", "tasks": [
        {"id": "t1", "prompt": "Click logo", "image": "logo.png"}
    ], "post": [{"id": "q1", "type": "text", "label": "Q"}]})
    fc = FirstClick(m)
    
    class DummyScope:
        def __init__(self):
            self.errors = []
        def add(self, err):
            self.errors.append(err)
            
    scope = DummyScope()
    out = fc.validate(m.conf, scope)
    assert not scope.errors
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["image"] == "logo.png"

def test_first_click_steps():
    p = DummyProject()
    m = DummyModule(p, {"type": "first_click", "tasks": [{"id": "t1"}, {"id": "t2"}], "post": [{"id": "q"}]})
    fc = FirstClick(m)
    assert fc.steps({}) == ["t1", "t2", "post1", "post2"]
