from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import lance
import pandas as pd
import pyarrow as pa

from scripts.manage import (
    SCHEDULES_STORE,
    archive_dataset,
    backup_dataset,
    check_dataset_exists,
    create_response,
    drop_dataset,
    get_data_path,
    overwrite_lance_table,
    validate_dataset_name,
    validate_relative_path,
)
from scripts.write import delete_dataset_record

ALLOWED_SCHEDULED_OPERATIONS = {
    "backup-dataset",
    "archive-dataset",
    "delete-dataset-record",
    "drop-dataset",
}
VALID_SCHEDULE_STATUSES = {"pending", "completed", "cancelled", "error"}

SCHEDULES_SCHEMA = pa.schema(
    [
        pa.field("schedule_id", pa.string()),
        pa.field("operation", pa.string()),
        pa.field("dataset_name", pa.string()),
        pa.field("params_json", pa.string()),
        pa.field("run_at", pa.string()),
        pa.field("interval_seconds", pa.int64()),
        pa.field("status", pa.string()),
        pa.field("run_count", pa.int64()),
        pa.field("created_at", pa.string()),
        pa.field("last_run_at", pa.string()),
        pa.field("last_result_json", pa.string()),
        pa.field("last_error", pa.string()),
    ]
)


def parse_iso_utc(timestamp_str: str) -> datetime:
    """Parse an ISO-8601 timestamp string and normalize it to UTC."""
    if not timestamp_str or not isinstance(timestamp_str, str) or not timestamp_str.strip():
        raise ValueError("Timestamp must be a non-empty ISO-8601 string")

    cleaned = timestamp_str.strip()
    if cleaned.endswith(("Z", "z")):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as e:
        raise ValueError(f"Invalid ISO-8601 timestamp '{timestamp_str}': {e}") from e

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _get_schedules_path() -> str:
    return os.path.join(get_data_path(), SCHEDULES_STORE)


def _load_schedules_df() -> pd.DataFrame:
    schedules_path = _get_schedules_path()
    if not os.path.exists(schedules_path):
        return pd.DataFrame(columns=[field.name for field in SCHEDULES_SCHEMA])

    ds = lance.dataset(schedules_path)
    df = ds.to_table().to_pandas()
    del ds
    return df


def _format_schedule_row(row: dict) -> dict:
    params = json.loads(row["params_json"]) if row.get("params_json") else {}
    last_result = json.loads(row["last_result_json"]) if row.get("last_result_json") else None
    return {
        "schedule_id": row["schedule_id"],
        "operation": row["operation"],
        "dataset_name": row["dataset_name"],
        "params": params,
        "run_at": row["run_at"],
        "interval_seconds": int(row["interval_seconds"]),
        "status": row["status"],
        "run_count": int(row["run_count"]),
        "created_at": row["created_at"],
        "last_run_at": row["last_run_at"] or None,
        "last_result": last_result,
        "last_error": row["last_error"] or None,
    }


def _validate_operation_params(
    operation: str,
    dataset_name: str,
    interval_val: int,
    backup_path: str | None = None,
    archive_dir: str | None = None,
    drop_source: bool = False,
    record_id: str | None = None,
) -> dict:
    if operation not in ALLOWED_SCHEDULED_OPERATIONS:
        allowed_csv = ", ".join(sorted(ALLOWED_SCHEDULED_OPERATIONS))
        raise ValueError(f"Unsupported scheduled operation '{operation}'. Allowed: {allowed_csv}")

    validate_dataset_name(dataset_name)
    check_dataset_exists(dataset_name)

    if interval_val > 0 and (
        operation in {"delete-dataset-record", "drop-dataset"} or (operation == "archive-dataset" and drop_source)
    ):
        raise ValueError(f"Recurring interval is not supported for one-time destructive operation '{operation}'")

    if operation == "backup-dataset":
        if not backup_path:
            raise ValueError("Operation 'backup-dataset' requires --backup-path")
        validate_relative_path(backup_path, label="Backup path")
        return {"backup_path": backup_path}

    if operation == "archive-dataset":
        resolved_archive_dir = archive_dir if archive_dir is not None else "archives"
        validate_relative_path(resolved_archive_dir, label="Archive directory")
        return {"archive_dir": resolved_archive_dir, "drop_source": bool(drop_source)}

    if operation == "delete-dataset-record":
        if not record_id or not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("Operation 'delete-dataset-record' requires --record-id")
        return {"record_id": record_id.strip()}

    return {}


def schedule_task(
    operation: str,
    dataset_name: str,
    run_at: str,
    interval_seconds: int | None = None,
    backup_path: str | None = None,
    archive_dir: str | None = None,
    drop_source: bool = False,
    record_id: str | None = None,
) -> str:
    try:
        if interval_seconds is not None and interval_seconds <= 0:
            raise ValueError("interval_seconds must be a positive integer when specified")
        interval_val = int(interval_seconds) if interval_seconds is not None else 0

        run_at_dt = parse_iso_utc(run_at)
        params = _validate_operation_params(
            operation=operation,
            dataset_name=dataset_name,
            interval_val=interval_val,
            backup_path=backup_path,
            archive_dir=archive_dir,
            drop_source=drop_source,
            record_id=record_id,
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        row = {
            "schedule_id": str(uuid.uuid4()),
            "operation": operation,
            "dataset_name": dataset_name,
            "params_json": json.dumps(params),
            "run_at": run_at_dt.isoformat(),
            "interval_seconds": interval_val,
            "status": "pending",
            "run_count": 0,
            "created_at": now_iso,
            "last_run_at": "",
            "last_result_json": "",
            "last_error": "",
        }

        schedules_path = _get_schedules_path()
        write_mode = "append" if os.path.exists(schedules_path) else "create"
        table = pa.Table.from_pylist([row], schema=SCHEDULES_SCHEMA)
        lance.write_dataset(table, schedules_path, mode=write_mode)
        return create_response("schedule_task", "success", _format_schedule_row(row), None)
    except Exception as e:
        return create_response("schedule_task", "error", None, str(e))


def list_scheduled_tasks(status: str | None = None) -> str:
    try:
        if status is not None and status not in VALID_SCHEDULE_STATUSES:
            valid_csv = ", ".join(sorted(VALID_SCHEDULE_STATUSES))
            raise ValueError(f"Invalid status filter '{status}'. Valid statuses: {valid_csv}")

        df = _load_schedules_df()
        if df.empty:
            return create_response("list_scheduled_tasks", "success", [], None)

        if status is not None:
            df = df[df["status"] == status]

        rows = df.to_dict("records")
        rows.sort(key=lambda r: (parse_iso_utc(r["run_at"]), r["created_at"]))
        formatted = [_format_schedule_row(r) for r in rows]
        return create_response("list_scheduled_tasks", "success", formatted, None)
    except Exception as e:
        return create_response("list_scheduled_tasks", "error", None, str(e))


def cancel_scheduled_task(schedule_id: str) -> str:
    try:
        if not schedule_id or not isinstance(schedule_id, str) or not schedule_id.strip():
            raise ValueError("schedule_id must be a non-empty string")

        schedules_path = _get_schedules_path()
        if not os.path.exists(schedules_path):
            raise ValueError(f"Scheduled task '{schedule_id}' not found")

        df = _load_schedules_df()
        matches = df[df["schedule_id"] == schedule_id].index
        if len(matches) == 0:
            raise ValueError(f"Scheduled task '{schedule_id}' not found")

        idx = matches[0]
        current_status = df.at[idx, "status"]
        if current_status != "pending":
            raise ValueError(f"Cannot cancel scheduled task '{schedule_id}' with status '{current_status}'")

        df.at[idx, "status"] = "cancelled"
        overwrite_lance_table(df, schedules_path, SCHEDULES_SCHEMA)

        updated_row = df.loc[idx].to_dict()
        return create_response("cancel_scheduled_task", "success", _format_schedule_row(updated_row), None)
    except Exception as e:
        return create_response("cancel_scheduled_task", "error", None, str(e))


def _dispatch_scheduled_operation(operation: str, dataset_name: str, params: dict) -> str:
    if operation == "backup-dataset":
        return backup_dataset(dataset_name, params["backup_path"])
    if operation == "archive-dataset":
        return archive_dataset(
            dataset_name,
            archive_dir=params.get("archive_dir", "archives"),
            drop_source=bool(params.get("drop_source", False)),
        )
    if operation == "delete-dataset-record":
        return delete_dataset_record(dataset_name, params["record_id"])
    if operation == "drop-dataset":
        return drop_dataset(dataset_name)
    raise ValueError(f"Unsupported scheduled operation '{operation}'")


def run_scheduled_tasks(as_of: str | None = None) -> str:
    try:
        cutoff_dt = parse_iso_utc(as_of) if as_of is not None else datetime.now(timezone.utc)
        cutoff_iso = cutoff_dt.isoformat()

        schedules_path = _get_schedules_path()
        if not os.path.exists(schedules_path):
            return create_response(
                "run_scheduled_tasks",
                "success",
                {
                    "as_of": cutoff_iso,
                    "executed_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "results": [],
                },
                None,
            )

        df = _load_schedules_df()
        if df.empty:
            return create_response(
                "run_scheduled_tasks",
                "success",
                {
                    "as_of": cutoff_iso,
                    "executed_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "results": [],
                },
                None,
            )

        due_indices = [
            idx
            for idx, row in df.iterrows()
            if row["status"] == "pending" and parse_iso_utc(row["run_at"]) <= cutoff_dt
        ]
        due_indices.sort(key=lambda i: (parse_iso_utc(df.at[i, "run_at"]), df.at[i, "created_at"]))

        execution_results = []
        succeeded_count = 0
        failed_count = 0

        for idx in due_indices:
            operation = df.at[idx, "operation"]
            dataset_name = df.at[idx, "dataset_name"]
            params = json.loads(df.at[idx, "params_json"]) if df.at[idx, "params_json"] else {}
            scheduled_dt = parse_iso_utc(df.at[idx, "run_at"])
            interval_val = int(df.at[idx, "interval_seconds"])

            try:
                raw_response = _dispatch_scheduled_operation(operation, dataset_name, params)
                parsed_response = json.loads(raw_response)
            except Exception as dispatch_err:
                parsed_response = {
                    "skill": "data-vault",
                    "operation": operation,
                    "status": "error",
                    "data": None,
                    "error": str(dispatch_err),
                }
                raw_response = json.dumps(parsed_response)

            df.at[idx, "run_count"] = int(df.at[idx, "run_count"]) + 1
            df.at[idx, "last_run_at"] = cutoff_iso
            df.at[idx, "last_result_json"] = raw_response

            if parsed_response.get("status") == "success":
                succeeded_count += 1
                df.at[idx, "last_error"] = ""
                if interval_val > 0:
                    step = timedelta(seconds=interval_val)
                    next_dt = scheduled_dt + step
                    while next_dt <= cutoff_dt:
                        next_dt += step
                    df.at[idx, "run_at"] = next_dt.isoformat()
                    df.at[idx, "status"] = "pending"
                else:
                    df.at[idx, "status"] = "completed"
            else:
                failed_count += 1
                df.at[idx, "status"] = "error"
                df.at[idx, "last_error"] = str(parsed_response.get("error") or "Scheduled execution failed")

            execution_results.append(_format_schedule_row(df.loc[idx].to_dict()))

        if due_indices:
            overwrite_lance_table(df, schedules_path, SCHEDULES_SCHEMA)

        summary = {
            "as_of": cutoff_iso,
            "executed_count": len(execution_results),
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "results": execution_results,
        }
        return create_response("run_scheduled_tasks", "success", summary, None)
    except Exception as e:
        return create_response("run_scheduled_tasks", "error", None, str(e))
