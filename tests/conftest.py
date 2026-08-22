import pytest

from ddg_search import state as state_mod


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point every SearchState at a per-test state file."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(state_mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(state_mod, "STATE_FILE", state_dir / "router-state.json")
    yield state_dir / "router-state.json"


@pytest.fixture
def state_file_corrupt(isolated_state):
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text("{not json at all")
    return isolated_state
