"""Tests never read the real, private mill lists: the durum list is pointed at nothing unless a test sets its own (the flour tests patch flour_mills.PLAIN themselves)."""
import pytest


@pytest.fixture(autouse=True)
def _no_real_durum_mills(tmp_path, monkeypatch):
    import flour_mills as fm
    monkeypatch.setattr(fm, "DURUM_PLAIN", str(tmp_path / "no_durum.csv"))
    monkeypatch.setattr(fm, "DURUM_BLOB", str(tmp_path / "no_durum.enc"))
    fm.load_durum.cache_clear()
    yield
    fm.load_durum.cache_clear()
