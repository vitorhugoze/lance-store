from __future__ import annotations

from pathlib import Path

from scripts.command import run_cli
from scripts.manage import list_archives, list_datasets
from scripts.read import count_records, read_dataset
from scripts.schedule import (
    cancel_scheduled_task,
    list_scheduled_tasks,
    run_scheduled_tasks,
    schedule_task,
)
from scripts.write import append_to_dataset, batch_append_to_dataset, create_dataset, delete_dataset_record
from tests.conftest import parse_resp


def test_one_shot_future_task_waits_until_due(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("events", ["title", "kind"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["deploy", "release"], "events"))["status"] == "success"

    sched = parse_resp(
        schedule_task(
            operation="backup-dataset",
            dataset_name="events",
            run_at="2026-11-01T12:00:00Z",
            backup_path="backups",
        )
    )
    assert sched["status"] == "success"
    schedule_id = sched["data"]["schedule_id"]
    assert sched["data"]["status"] == "pending"

    # Running before due timestamp must not execute the backup
    early = parse_resp(run_scheduled_tasks(as_of="2026-11-01T11:59:59Z"))
    assert early["status"] == "success"
    assert early["data"]["executed_count"] == 0
    assert not (vault_dir / "backups" / "events").exists()

    # Running at exact due timestamp executes backup and marks task completed
    due = parse_resp(run_scheduled_tasks(as_of="2026-11-01T12:00:00Z"))
    assert due["status"] == "success"
    assert due["data"]["executed_count"] == 1
    assert due["data"]["succeeded_count"] == 1
    assert (vault_dir / "backups" / "events" / "dataset_metadata.json").is_file()

    completed = parse_resp(list_scheduled_tasks(status="completed"))
    assert len(completed["data"]) == 1
    assert completed["data"][0]["schedule_id"] == schedule_id
    assert completed["data"][0]["run_count"] == 1

    # Re-running later must be idempotent (one-shot task is already completed)
    again = parse_resp(run_scheduled_tasks(as_of="2026-11-01T13:00:00Z"))
    assert again["data"]["executed_count"] == 0


def test_chronological_execution_order_archive_before_drop(vault_dir: Path) -> None:
    """Tasks registered out of order must execute strictly sorted by run_at."""
    assert parse_resp(create_dataset("logs", ["message", "level"]))["status"] == "success"
    assert (
        parse_resp(batch_append_to_dataset("logs", [["boot", "info"], ["oom", "error"]]))["status"]
        == "success"
    )

    # Register drop-dataset FIRST (for 10:10), and archive-dataset SECOND (for 10:05)
    drop_sched = parse_resp(
        schedule_task(operation="drop-dataset", dataset_name="logs", run_at="2026-11-02T10:10:00Z")
    )
    archive_sched = parse_resp(
        schedule_task(
            operation="archive-dataset",
            dataset_name="logs",
            run_at="2026-11-02T10:05:00Z",
            archive_dir="archives",
        )
    )
    assert drop_sched["status"] == "success"
    assert archive_sched["status"] == "success"

    # Trigger at 10:15: archive must run BEFORE drop, otherwise archive would fail!
    batch_run = parse_resp(run_scheduled_tasks(as_of="2026-11-02T10:15:00Z"))
    assert batch_run["status"] == "success"
    assert batch_run["data"]["executed_count"] == 2
    assert batch_run["data"]["succeeded_count"] == 2
    assert batch_run["data"]["failed_count"] == 0
    assert [r["operation"] for r in batch_run["data"]["results"]] == ["archive-dataset", "drop-dataset"]

    # Source dataset was dropped, while archive snapshot has both records intact
    assert parse_resp(list_datasets())["data"] == []
    archives = parse_resp(list_archives("archives"))["data"]
    assert len(archives) == 1
    assert archives[0]["record_count"] == 2


def test_recurring_schedule_advances_next_run_without_duplicate_firing(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("metrics", ["metric", "val"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["cpu", "42"], "metrics"))["status"] == "success"

    sched = parse_resp(
        schedule_task(
            operation="archive-dataset",
            dataset_name="metrics",
            run_at="2026-11-03T08:00:00Z",
            interval_seconds=3600,
            archive_dir="hourly_archives",
        )
    )
    assert sched["status"] == "success"

    # First run at 08:05 executes once and reschedules to 09:00
    first = parse_resp(run_scheduled_tasks(as_of="2026-11-03T08:05:00Z"))
    assert first["data"]["executed_count"] == 1
    assert first["data"]["results"][0]["status"] == "pending"
    assert first["data"]["results"][0]["run_count"] == 1
    assert first["data"]["results"][0]["run_at"].startswith("2026-11-03T09:00:00")

    # Running again at 08:30 must NOT fire a duplicate archive
    mid = parse_resp(run_scheduled_tasks(as_of="2026-11-03T08:30:00Z"))
    assert mid["data"]["executed_count"] == 0
    assert len(parse_resp(list_archives("hourly_archives"))["data"]) == 1

    # Running at 09:00 fires second snapshot and advances to 10:00
    second = parse_resp(run_scheduled_tasks(as_of="2026-11-03T09:00:00Z"))
    assert second["data"]["executed_count"] == 1
    assert second["data"]["results"][0]["run_count"] == 2
    assert len(parse_resp(list_archives("hourly_archives"))["data"]) == 2


def test_cancel_scheduled_task_prevents_execution_and_rejects_invalid_states(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("temp_ds", ["col"]))["status"] == "success"

    sched = parse_resp(
        schedule_task(operation="drop-dataset", dataset_name="temp_ds", run_at="2026-11-04T00:00:00Z")
    )
    sid = sched["data"]["schedule_id"]

    cancelled = parse_resp(cancel_scheduled_task(sid))
    assert cancelled["status"] == "success"
    assert cancelled["data"]["status"] == "cancelled"

    # Double cancellation fails cleanly
    double_cancel = parse_resp(cancel_scheduled_task(sid))
    assert double_cancel["status"] == "error"

    # Running past due timestamp does not drop the dataset
    run_res = parse_resp(run_scheduled_tasks(as_of="2026-11-04T01:00:00Z"))
    assert run_res["data"]["executed_count"] == 0
    assert parse_resp(list_datasets())["data"] == ["temp_ds"]


def test_failure_isolation_when_scheduled_target_record_already_deleted(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("queue", ["item"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["job-1"], "queue"))["status"] == "success"
    assert parse_resp(append_to_dataset(["job-2"], "queue"))["status"] == "success"

    records = parse_resp(read_dataset("queue"))["data"]
    rec1_id = records[0]["_id"]

    # Schedule deletion of rec1 at 12:00 and backup at 12:01
    s1 = parse_resp(
        schedule_task(
            operation="delete-dataset-record",
            dataset_name="queue",
            run_at="2026-11-05T12:00:00Z",
            record_id=rec1_id,
        )
    )
    s2 = parse_resp(
        schedule_task(
            operation="backup-dataset",
            dataset_name="queue",
            run_at="2026-11-05T12:01:00Z",
            backup_path="backups",
        )
    )
    assert s1["status"] == "success"
    assert s2["status"] == "success"

    # Manually delete rec1 BEFORE scheduled execution so s1 will fail at runtime
    assert parse_resp(delete_dataset_record("queue", rec1_id))["status"] == "success"

    summary = parse_resp(run_scheduled_tasks(as_of="2026-11-05T12:05:00Z"))
    assert summary["status"] == "success"
    assert summary["data"]["executed_count"] == 2
    assert summary["data"]["failed_count"] == 1
    assert summary["data"]["succeeded_count"] == 1

    # Verify s1 marked error while s2 still succeeded and backed up remaining 1 record
    err_tasks = parse_resp(list_scheduled_tasks(status="error"))["data"]
    assert len(err_tasks) == 1
    assert "not found" in err_tasks[0]["last_error"]
    assert parse_resp(count_records("queue"))["data"] == 1
    assert (vault_dir / "backups" / "queue" / "dataset_metadata.json").is_file()


def test_schedule_validation_allowlist_and_security(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("secure_ds", ["secret"]))["status"] == "success"

    # Disallowed operation rejected
    bad_op = parse_resp(schedule_task("exec-shell", "secure_ds", "2026-11-06T00:00:00Z"))
    assert bad_op["status"] == "error"

    # Invalid ISO timestamp rejected
    bad_ts = parse_resp(schedule_task("drop-dataset", "secure_ds", "not-a-timestamp"))
    assert bad_ts["status"] == "error"

    # Path traversal in backup_path rejected at schedule creation
    bad_path = parse_resp(
        schedule_task(
            "backup-dataset",
            "secure_ds",
            "2026-11-06T00:00:00Z",
            backup_path="../escape",
        )
    )
    assert bad_path["status"] == "error"

    # Destructive operation cannot be recurring
    bad_recurring = parse_resp(
        schedule_task(
            "drop-dataset",
            "secure_ds",
            "2026-11-06T00:00:00Z",
            interval_seconds=60,
        )
    )
    assert bad_recurring["status"] == "error"

    # Schedule table must never leak into user dataset listings
    assert parse_resp(
        run_cli(["schedule-task", "drop-dataset", "secure_ds", "--run-at", "2026-12-01T00:00:00Z"])
    )["status"] == "success"
    assert parse_resp(list_datasets())["data"] == ["secure_ds"]
