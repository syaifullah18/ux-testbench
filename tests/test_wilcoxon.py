"""Wilcoxon signed-rank test as a secondary (or chosen) test for time-on-task."""
import pytest

from testbench.modules.ab_test import paired_time_check, wilcoxon


def test_exact_all_positive():
    w = wilcoxon(list(range(1, 11)))
    assert w["p"] == pytest.approx(2 / 1024) and w["r"] == 1 and w["exact"]


def test_exact_mixed_signs():
    # Ranks 1..5, W+ = 10, W- = 5; 10 of 32 sign patterns give W <= 5 -> p = 0.625.
    w = wilcoxon([1, 2, 3, 4, -5])
    assert (w["w_plus"], w["w_minus"]) == (10, 5)
    assert w["p"] == pytest.approx(0.625)
    assert w["r"] == pytest.approx((10 - 5) / 15)


def test_ties_and_zeros():
    # Zeros drop out; |1| three times shares rank 2, |2| gets rank 4. W+ = 8, W- = 2.
    w = wilcoxon([0, 1, 1, -1, 2, 0])
    assert w["n"] == 4 and (w["w_plus"], w["w_minus"]) == (8, 2)
    assert w["p"] == pytest.approx(0.5)
    assert wilcoxon([0, 0]) == {"n": 0, "w_plus": 0.0, "w_minus": 0.0, "p": 1.0, "r": 0.0, "exact": True}


def test_normal_approximation_above_threshold():
    w = wilcoxon(list(range(1, 31)))
    assert not w["exact"] and w["p"] < 1e-5
    w = wilcoxon([x if x % 2 else -x for x in range(1, 31)])
    assert not w["exact"] and w["p"] > 0.5


def pair(base_ms, k_ms):
    return {"A": {"total": base_ms}, "B": {"total": k_ms}}


def test_time_test_choice_changes_only_the_p_value_used():
    # B is faster for 6 of 7 people, by a lot; slower once, by a little (rank 1 -> W- = 1, p = 4/128).
    pairs = [pair(10000, 4000 + i) for i in range(6)] + [pair(5000, 5100)]
    _, sign_p, sign_verdict, _ = paired_time_check(pairs, "A", "B")
    _, wx_p, wx_verdict, ok = paired_time_check(pairs, "A", "B", "wilcoxon")
    assert sign_p == pytest.approx(0.125) and sign_verdict == "not enough evidence yet"
    assert wx_p == pytest.approx(0.03125) and wx_verdict == "evidence for" and ok


def test_config_rejects_unknown_time_test(tmp_path):
    from testbench.config import ConfigError, load_all
    from testbench.modules import MODULE_TYPES
    from .conftest import MINI_AB, PROTO, write_project
    write_project(tmp_path, "w", {"name": "W"}, {"ab": dict(MINI_AB, decision_rule={"time_test": "t-test"})},
                  {"p/a.html": PROTO, "p/b.html": PROTO})
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    assert "decision_rule.time_test must be sign or wilcoxon" in "\n".join(err.value.problems)
