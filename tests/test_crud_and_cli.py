from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts.command import run_cli
from scripts.read import get_record, list_records, read_dataset
from scripts.write import append_to_dataset, create_dataset, update_dataset_record
from tests.conftest import parse_resp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMAND_SCRIPT = PROJECT_ROOT / "scripts" / "command.py"


def test_update_record_advances_timestamp_and_enforces_type_strictness(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("profiles", ["username", "score"]))["status"] == "success"
    # Initialize with integer type for score via Python API
    assert parse_resp(append_to_dataset(["vitor", 100], "profiles"))["status"] == "success"

    rec = parse_resp(read_dataset("profiles"))["data"][0]
    rec_id = rec["_id"]
    initial_ts = rec["_updated_at"]

    # Type mismatch on append (string instead of int64) must fail per Lance strictness
    mismatch = parse_resp(append_to_dataset(["alice", "not_an_int"], "profiles"))
    assert mismatch["status"] == "error"

    time.sleep(0.01)
    upd = parse_resp(update_dataset_record("profiles", rec_id, ["vitor_updated", 250]))
    assert upd["status"] == "success"

    fetched = parse_resp(get_record("profiles", rec_id))["data"]
    assert fetched["username"] == "vitor_updated"
    assert fetched["score"] == 250
    assert fetched["_updated_at"] >= initial_ts

    # Duplicate dataset creation must be rejected
    dup = parse_resp(create_dataset("profiles", ["other"]))
    assert dup["status"] == "error"


def test_pagination_total_reflects_full_dataset_and_rejects_negative_bounds(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("items", ["code"]))["status"] == "success"
    rows = [[f"ITEM-{i:02d}"] for i in range(5)]
    assert parse_resp(run_cli(["batch-append-to-dataset", "items", json.dumps(rows)]))["status"] == "success"

    page1 = parse_resp(list_records("items", limit=2, offset=0))
    assert page1["status"] == "success"
    assert len(page1["data"]["records"]) == 2
    assert page1["data"]["total"] == 5

    page3 = parse_resp(list_records("items", limit=2, offset=4))
    assert len(page3["data"]["records"]) == 1
    assert page3["data"]["total"] == 5

    assert parse_resp(list_records("items", limit=-1, offset=0))["status"] == "error"
    assert parse_resp(list_records("items", limit=2, offset=-2))["status"] == "error"


def test_cli_subprocess_with_temp_working_dir_and_data_dir_override(tmp_path: Path) -> None:
    """Verify real subprocess CLI invocation on Windows/Linux without touching repo root."""
    external_cwd = tmp_path / "caller_cwd"
    external_cwd.mkdir()
    explicit_vault = tmp_path / "explicit_temp_vault"

    env = os.environ.copy()
    env.pop("LANCE_DATA_PATH", None)

    def invoke(*cli_args: str) -> dict:
        proc = subprocess.run(
            [sys.executable, str(COMMAND_SCRIPT), "--data-dir", str(explicit_vault), *cli_args],
            cwd=str(external_cwd),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout.strip())

    assert invoke("list-datasets")["data"] == []
    assert invoke("create-dataset", "incidents", "title", "severity")["status"] == "success"
    assert (
        invoke(
            "batch-append-to-dataset",
            "incidents",
            json.dumps([["DB spike", "high"], ["Cache miss", "low"]]),
        )["status"]
        == "success"
    )
    assert invoke("count-records", "incidents")["data"] == 2

    # Also verify stdin '-' mode for batch-append-to-dataset (Windows PowerShell 5.1 friendly)
    stdin_proc = subprocess.run(
        [
            sys.executable,
            str(COMMAND_SCRIPT),
            "--data-dir",
            str(explicit_vault),
            "batch-append-to-dataset",
            "incidents",
            "-",
        ],
        cwd=str(external_cwd),
        env=env,
        input=json.dumps([["TLS renewal", "medium"]]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(stdin_proc.stdout.strip())["status"] == "success"
    assert invoke("count-records", "incidents")["data"] == 3

    # Schedule an archive via CLI, execute it via CLI, and verify
    assert (
        invoke(
            "schedule-task",
            "archive-dataset",
            "incidents",
            "--run-at",
            "2026-01-01T00:00:00Z",
            "--archive-dir",
            "archives",
        )["status"]
        == "success"
    )
    run_out = invoke("run-scheduled-tasks", "--as-of", "2026-01-02T00:00:00Z")
    assert run_out["data"]["succeeded_count"] == 1

    # Ensure caller_cwd remained completely empty while explicit_vault holds the Lance files
    assert list(external_cwd.iterdir()) == []
    assert (explicit_vault / "metadata.lance").is_dir()
    assert (explicit_vault / "archives").is_dir()


def test_run_cli_all_subcommands_and_error_branches(vault_dir: Path) -> None:
    """Exercise every CLI subcommand and malformed-input branch via run_cli."""
    assert parse_resp(run_cli([]))["status"] == "error"

    assert parse_resp(run_cli(["create-dataset", "books", "title", "author"]))["status"] == "success"
    assert parse_resp(run_cli(["append-to-dataset", "books", "Dune", "Herbert"]))["status"] == "success"

    bad_batch = parse_resp(run_cli(["batch-append-to-dataset", "books", "{invalid-json"]))
    assert bad_batch["status"] == "error"

    all_info = parse_resp(run_cli(["list-datasets-info"]))
    assert all_info["status"] == "success"
    assert len(all_info["data"]) == 1
    assert all_info["data"][0]["dataset_name"] == "books"

    ds_info = parse_resp(run_cli(["get-dataset-info", "books"]))
    assert ds_info["status"] == "success"
    assert ds_info["data"]["record_count"] == 1

    path_info = parse_resp(run_cli(["get-dataset-path-info", "books"]))
    assert path_info["status"] == "success"
    assert Path(path_info["data"]) == vault_dir / "books"

    rows = parse_resp(run_cli(["read-dataset", "books"]))["data"]
    rec_id = rows[0]["_id"]

    assert (
        parse_resp(run_cli(["update-dataset-record", "books", rec_id, "Dune Messiah", "Frank Herbert"]))["status"]
        == "success"
    )
    single = parse_resp(run_cli(["get-record", "books", rec_id]))
    assert single["status"] == "success"
    assert single["data"]["title"] == "Dune Messiah"

    listed = parse_resp(run_cli(["list-records", "books", "--limit", "10", "--offset", "0"]))
    assert listed["data"]["total"] == 1
    assert parse_resp(run_cli(["count-records", "books"]))["data"] == 1

    assert parse_resp(run_cli(["backup-dataset", "books", "backups"]))["status"] == "success"
    arch = parse_resp(run_cli(["archive-dataset", "books", "--archive-dir", "archives"]))
    assert arch["status"] == "success"
    archive_id = arch["data"]["archive_id"]
    assert len(parse_resp(run_cli(["list-archives", "--archive-dir", "archives"]))["data"]) == 1

    sched = parse_resp(
        run_cli(["schedule-task", "drop-dataset", "books", "--run-at", "2027-01-01T00:00:00Z"])
    )
    assert sched["status"] == "success"
    sched_id = sched["data"]["schedule_id"]
    assert len(parse_resp(run_cli(["list-scheduled-tasks", "--status", "pending"]))["data"]) == 1
    assert parse_resp(run_cli(["cancel-scheduled-task", sched_id]))["status"] == "success"
    assert parse_resp(run_cli(["run-scheduled-tasks", "--as-of", "2027-01-02T00:00:00Z"]))["status"] == "success"

    assert parse_resp(run_cli(["delete-dataset-record", "books", rec_id]))["status"] == "success"
    assert parse_resp(run_cli(["drop-dataset", "books"]))["status"] == "success"
    assert parse_resp(run_cli(["restore-archive", archive_id, "--archive-dir", "archives"]))["status"] == "success"
    assert parse_resp(run_cli(["list-datasets"]))["data"] == ["books"]

