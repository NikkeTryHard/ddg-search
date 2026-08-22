from ddg_search.config import BACKENDS
from ddg_search.models import BackendConfig
from ddg_search.state import SearchState, now_iso, trim_timestamps


def backend(backend_id: str) -> BackendConfig:
    return next(b for b in BACKENDS if b.id == backend_id)


def test_fresh_load_creates_all_backend_entries(tmp_path):
    st = SearchState()
    assert set(st.backends) == {b.id for b in BACKENDS}


def test_save_then_reload_roundtrip():
    st = SearchState()
    entry = st.entry(backend("local"))
    entry.last_status = "ok"
    entry.last_error = None
    entry.search_timestamps = [123.0]
    st.save()

    reloaded = SearchState()
    assert reloaded.entry(backend("local")).last_status == "ok"
    assert reloaded.entry(backend("local")).search_timestamps == [123.0]


def test_corrupt_state_file_recovers_to_fresh(state_file_corrupt):
    st = SearchState()
    assert set(st.backends) == {b.id for b in BACKENDS}


def test_trim_timestamps_keeps_only_recent():
    now = 1_000_000.0
    kept = trim_timestamps([now - 10, now - 59, now - 61], now)
    assert kept == [now - 10, now - 59]


def test_now_iso_is_timezone_aware():
    assert "+00:00" in now_iso()
