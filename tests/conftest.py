from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.manage import set_data_path_override


@pytest.fixture()
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated temporary Data Vault directory via LANCE_DATA_PATH."""
    isolated = tmp_path / "vault"
    isolated.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LANCE_DATA_PATH", str(isolated))
    set_data_path_override(None)
    yield isolated
    set_data_path_override(None)


def parse_resp(raw_json: str) -> dict:
    """Parse and assert basic Data Vault JSON envelope structure."""
    parsed = json.loads(raw_json)
    assert parsed["skill"] == "data-vault"
    assert parsed["status"] in {"success", "error"}
    return parsed
