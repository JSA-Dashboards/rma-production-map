"""
USDA long-term baseline projections of US crop acreage (million acres), the forward-looking series for the Acreage Summary tab.

Source: USDA, Economic Research Service, based on data from the Interagency Agricultural Projections Committee, Table 5 "U.S. acreage for major field crops and CRP assumptions,
long-term projections" (projections completed October 2026). Years are the calendar planting year (2025 is the base year, 2026 onward are projections). National only: the
baseline has no state detail. Typed from the published table; test_usda_baseline.py checks that each column adds to the published totals.
"""

YEARS = list(range(2025, 2037))
CROPS = ["Corn", "Sorghum", "Barley", "Oats", "Wheat", "Rice", "Upland cotton", "Soybeans"]

PLANTED = {
    "Corn":          [98.7, 96.8, 95.5, 95.0, 94.5, 94.0, 93.5, 93.0, 92.5, 92.0, 91.5, 91.0],
    "Sorghum":       [6.6, 6.0] + [6.5] * 10,
    "Barley":        [2.3, 2.2] + [2.3] * 10,
    "Oats":          [2.4, 2.6] + [2.3] * 10,
    "Wheat":         [45.5, 42.5, 44.0, 44.0, 44.0, 43.5, 43.5, 43.0, 43.0, 43.0, 42.5, 42.5],
    "Rice":          [2.8, 2.1] + [2.5] * 10,
    "Upland cotton": [9.1, 10.3, 10.2, 10.2, 10.2, 10.2, 10.2, 10.3, 10.3, 10.3, 10.3, 10.3],
    "Soybeans":      [81.2, 86.9, 87.5, 87.0, 87.0, 87.0, 86.5, 86.5, 86.5, 86.5, 86.5, 86.5],
}
PLANTED_TOTAL = [248.7, 249.4, 250.7, 249.7, 249.2, 248.2, 247.2, 246.3, 245.9, 245.4, 244.4, 243.9]
CRP = [24.6, 25.8, 26.1, 26.4, 26.8, 26.9, 26.8, 26.8, 26.9, 26.9, 26.8, 26.9]
PLANTED_PLUS_CRP = [273.3, 275.2, 276.8, 276.1, 276.0, 275.1, 274.0, 273.1, 272.8, 272.2, 271.2, 270.7]

HARVESTED = {
    "Corn":          [90.9, 88.5, 87.1, 86.6, 86.1, 85.6, 85.2, 84.8, 84.3, 84.0, 83.5, 83.1],
    "Sorghum":       [6.0, 5.2] + [5.7] * 10,
    "Barley":        [1.8, 1.6] + [1.8] * 10,
    "Oats":          [0.9, 0.9] + [0.8] * 10,
    "Wheat":         [37.3, 31.9, 35.2, 35.2, 35.2, 34.8, 34.8, 34.4, 34.4, 34.4, 34.0, 34.0],
    "Rice":          [2.7, 2.1, 2.4, 2.4, 2.4, 2.4, 2.4, 2.4, 2.5, 2.5, 2.5, 2.5],
    "Upland cotton": [7.7, 8.0, 8.6, 7.6, 7.6, 7.6, 7.6, 7.7, 7.7, 7.7, 7.7, 7.7],
    "Soybeans":      [80.4, 85.9, 86.6, 86.1, 86.1, 86.1, 85.6, 85.6, 85.6, 85.6, 85.6, 85.6],
}
HARVESTED_TOTAL = [227.9, 224.1, 228.2, 226.2, 225.7, 224.8, 223.9, 223.2, 222.7, 222.4, 221.5, 221.1]

SOURCE = ("USDA Economic Research Service, from the Interagency Agricultural Projections Committee: Table 5, U.S. acreage for major field crops and CRP assumptions, "
          "long-term projections (completed October 2026).")

# The baseline series that mean the same thing as a column of the Acreage Summary history, for the trend chart: {history column key: baseline planted series}.
# Upland cotton, the baseline's eight-crop total and rice are not matched (the history's cotton is all cotton, its totals include sunflower and canola, and rice sits in "other").
CHART_MATCH = {"Corn": "Corn", "Soybeans": "Soybeans", "Wheat": "Wheat", "Sorghum": "Sorghum", "Barley": "Barley", "Oats": "Oats", "CRP": "CRP"}


def series(key: str) -> dict:
    """{calendar year: million acres} for a planted crop, 'CRP', 'Total' or 'Total + CRP'."""
    src = {"CRP": CRP, "Total": PLANTED_TOTAL, "Total + CRP": PLANTED_PLUS_CRP}.get(key) or PLANTED[key]
    return dict(zip(YEARS, src))


def rows() -> list:
    """One dict per year: planted (crop -> M ac), 'total', 'crp', 'total_crp', harvested (crop -> M ac), 'harv_total'."""
    out = []
    for i, y in enumerate(YEARS):
        out.append({"year": y, "planted": {c: PLANTED[c][i] for c in CROPS}, "total": PLANTED_TOTAL[i], "crp": CRP[i], "total_crp": PLANTED_PLUS_CRP[i],
                    "harvested": {c: HARVESTED[c][i] for c in CROPS}, "harv_total": HARVESTED_TOTAL[i]})
    return out
