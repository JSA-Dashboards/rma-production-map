"""The wheat class fit (app._wc_ipf): class totals met exactly, row totals kept, weights steer where a class lands."""
import app


def test_class_totals_and_rows():
    rows = {"a": 10.0, "b": 20.0, "c": 5.0}
    x = app._wc_ipf(rows, {"x": 20.0, "y": 15.0}, {("a", "x"): 5, ("b", "y"): 3, ("c", "x"): 1})
    for c, t in (("x", 20.0), ("y", 15.0)):
        assert abs(sum(v for (i, k), v in x.items() if k == c) - t) < 1e-6
    for i, t in rows.items():
        assert abs(sum(v for (j, k), v in x.items() if j == i) - t) < 1e-6


def test_weights_steer_the_class():
    rows = {"wet": 10.0, "dry": 10.0}
    flat = app._wc_ipf(rows, {"soft": 10.0, "hard": 10.0}, {})
    steered = app._wc_ipf(rows, {"soft": 10.0, "hard": 10.0}, {("wet", "soft"): 9, ("wet", "hard"): 1, ("dry", "soft"): 1, ("dry", "hard"): 9})
    assert abs(flat[("wet", "soft")] - 5.0) < 1e-6
    assert steered[("wet", "soft")] > 8.0 and steered[("dry", "hard")] > 8.0


def test_class_total_beats_row_total_when_they_disagree():
    x = app._wc_ipf({"a": 100.0}, {"x": 30.0, "y": 60.0}, {})        # classes only add to 90: rows are scaled, class totals stay exact
    assert abs(x[("a", "x")] - 30.0) < 1e-6 and abs(x[("a", "y")] - 60.0) < 1e-6
