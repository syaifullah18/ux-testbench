from testbench.modules.ab_test import (
    sign_test, bootstrap_interval, paired_time_check, 
    paired_success_check, paired_survey_check
)
import math

def test_sign_test():
    assert sign_test(10, 10) == 1.0
    assert sign_test(0, 0) == 1.0
    assert abs(sign_test(10, 0) - (2 * 1 / 1024)) < 1e-6
    # 8 wins, 2 losses -> p-value = 2 * (comb(10,8) + comb(10,9) + comb(10,10)) / 1024 = 2 * (45 + 10 + 1) / 1024 = 112 / 1024 = 0.109375
    assert abs(sign_test(8, 2) - 0.109375) < 1e-6

def test_bootstrap_interval():
    import random
    random.seed(42)
    lo, hi = bootstrap_interval([5, 5, 5, 5, 5], iterations=100)
    assert lo == 5.0
    assert hi == 5.0
    
    # normal distribution
    diffs = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    lo, hi = bootstrap_interval(diffs, iterations=1000)
    assert 2.0 < lo < 4.0
    assert 6.0 < hi < 8.0

def test_paired_time_check():
    # pairs is a list of dicts. each dict is p[variant]["total"]
    pairs = [
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
        {"base": {"total": 100}, "var": {"total": 80}},
    ]
    faster, time_p, verdict, ok = paired_time_check(pairs, "base", "var")
    assert faster == 8
    assert time_p < 0.05
    assert verdict == "evidence for"
    assert ok is True

def test_paired_success_check():
    # mock rows for success_rate
    # pairs[base]["rows"] -> dict of tasks -> grade
    pairs = [
        {
            "base": {"rows": {"t1": {"grade": "fail", "gave_up": False, "auto_pass": False}}},
            "var": {"rows": {"t1": {"grade": "success", "gave_up": False, "auto_pass": True}}}
        }
        for _ in range(5)
    ]
    srb, srk, lo, hi, verdict, ok = paired_success_check(pairs, "base", "var")
    assert srb == 0.0
    assert srk == 1.0
    assert lo == 1.0
    assert hi == 1.0
    assert verdict == "evidence for"
    assert ok is True

def test_paired_survey_check():
    pairs = [
        {"base": {"survey": {"q1": 3}}, "var": {"survey": {"q1": 5}}},
        {"base": {"survey": {"q1": 3}}, "var": {"survey": {"q1": 5}}},
        {"base": {"survey": {"q1": 3}}, "var": {"survey": {"q1": 5}}},
    ]
    by_variant = {
        "base": {"survey": {"q1": 3.0}},
        "var": {"survey": {"q1": 5.0}},
    }
    sb, sk, gain, lo, hi, verdict, ok = paired_survey_check(pairs, "base", "var", by_variant, ["q1"], 1.0)
    assert sb == 3.0
    assert sk == 5.0
    assert gain == 2.0
    assert lo == 2.0
    assert hi == 2.0
    assert verdict == "evidence for"
    assert ok is True

