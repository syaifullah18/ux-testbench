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


def test_card_sort_report_and_export(tmp_path):
    from testbench import create_app
    from testbench.context import Ctx
    from testbench import storage

    app = create_app()
    with app.test_request_context():
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "card-study"
            pdir.mkdir()
            (pdir / "modules").mkdir()
            
            project_yaml = {"name": "Card Sort Study", "locale": "en", "modules": ["sort1"]}
            (pdir / "project.yaml").write_text(yaml.dump(project_yaml), encoding="utf-8")
            
            sorting_yaml = {
                "type": "card_sort",
                "title": "Topic Sort",
                "cards": [
                    {"id": "c1", "label": "Card 1"},
                    {"id": "c2", "label": "Card 2"},
                ],
                "categories": ["Cat A", "Cat B"],
                "allow_new_categories": True,
            }
            (pdir / "modules" / "sort1.yaml").write_text(yaml.dump(sorting_yaml), encoding="utf-8")
            
            problems = Problems()
            project = load_project(pdir, MODULE_TYPES, problems)
            assert not problems

            mod = project.modules["sort1"]
            mctx = Ctx(project, module=mod, admin=True)

            # Insert sample participant and session
            mctx.conn.execute("INSERT OR REPLACE INTO participants (id, identity, created_at, last_seen_at) VALUES (999, 'P99', '2026-01-01', '2026-01-01')")
            mctx.conn.execute("INSERT OR REPLACE INTO sessions (id, participant_id, module_id, step, state, started_at, finished_at) VALUES (999, 999, 'sort1', 'done', '{}', '2026-01-01T10:00:00', '2026-01-01T10:05:00')")
            storage.save_page(mctx.conn, 999, "sort", {"c1": "Cat A", "c2": "New Group"})
            mctx.conn.commit()

            # Test report rendering (ensures no SQL 'no such column: identity' error!)
            html = mod.impl.report(mctx)
            assert "Where each card went" in str(html)
            assert "Card 1" in str(html)
            assert "Cat A" in str(html)
            assert "New Group" in str(html)

            # Test export
            header, rows = mod.impl.export(mctx)
            assert "participant" in header
            assert "card_c1" in header
            assert any(r[0] == "P99" for r in rows)
            p99_row = next(r for r in rows if r[0] == "P99")
            assert p99_row[2] == "Cat A"

            # Test detail
            sess_row = mctx.conn.execute("SELECT * FROM sessions WHERE id = 999").fetchone()
            detail_html = mod.impl.detail(mctx, sess_row)
            assert "Cat A" in str(detail_html)
            assert "Card 1" in str(detail_html)

