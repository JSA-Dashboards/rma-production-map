"""The typed USDA baseline table adds up to its own published totals (catches a mistyped number)."""
import pytest

import usda_baseline as ub


def test_every_series_covers_every_year():
    n = len(ub.YEARS)
    for d in (ub.PLANTED, ub.HARVESTED):
        assert set(d) == set(ub.CROPS) and all(len(v) == n for v in d.values())
    assert all(len(v) == n for v in (ub.PLANTED_TOTAL, ub.CRP, ub.PLANTED_PLUS_CRP, ub.HARVESTED_TOTAL))


@pytest.mark.parametrize("i", range(12))
def test_columns_add_to_the_published_totals(i):
    assert sum(ub.PLANTED[c][i] for c in ub.CROPS) == pytest.approx(ub.PLANTED_TOTAL[i], abs=0.25)        # the table is rounded to 0.1 a crop
    assert ub.PLANTED_TOTAL[i] + ub.CRP[i] == pytest.approx(ub.PLANTED_PLUS_CRP[i], abs=0.15)
    assert sum(ub.HARVESTED[c][i] for c in ub.CROPS) == pytest.approx(ub.HARVESTED_TOTAL[i], abs=0.25)
    assert all(ub.HARVESTED[c][i] <= ub.PLANTED[c][i] for c in ub.CROPS)


def test_series_and_rows():
    assert ub.series("Corn")[2027] == 95.5 and ub.series("CRP")[2036] == 26.9
    r = ub.rows()
    assert r[0]["year"] == 2025 and r[-1]["year"] == 2036 and r[1]["planted"]["Soybeans"] == 86.9
