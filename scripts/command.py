from __future__ import annotations

import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.manage import (  # noqa: E402
    archive_dataset,
    backup_dataset,
    create_response,
    drop_dataset,
    get_dataset_info,
    get_dataset_path_info,
    list_archives,
    list_datasets,
    list_datasets_info,
    restore_archive,
    set_data_path_override,
)
from scripts.read import (  # noqa: E402
    count_records,
    get_record,
    list_records,
    read_dataset,
)
from scripts.schedule import (  # noqa: E402
    ALLOWED_SCHEDULED_OPERATIONS,
    VALID_SCHEDULE_STATUSES,
    cancel_scheduled_task,
    list_scheduled_tasks,
    run_scheduled_tasks,
    schedule_task,
)
from scripts.write import (  # noqa: E402
    append_to_dataset,
    batch_append_to_dataset,
    create_dataset,
    delete_dataset_record,
    update_dataset_record,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lance Dataset Management CLI")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Optional directory for dataset storage (overrides LANCE_DATA_PATH and current directory)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # create-dataset
    create_parser = subparsers.add_parser("create-dataset", help="Create a new dataset")
    create_parser.add_argument("dataset_name", help="Name of the dataset")
    create_parser.add_argument("fields", nargs="+", help="List of field names")

    # append-to-dataset
    append_parser = subparsers.add_parser("append-to-dataset", help="Append a record to a dataset")
    append_parser.add_argument("dataset_name", help="Name of the dataset")
    append_parser.add_argument("data", nargs="+", help="Data values for the record")

    # update-dataset-record
    update_parser = subparsers.add_parser("update-dataset-record", help="Update a record in a dataset")
    update_parser.add_argument("dataset_name", help="Name of the dataset")
    update_parser.add_argument("record_id", help="ID of the record to update")
    update_parser.add_argument("data", nargs="+", help="Updated data values")

    # delete-dataset-record
    delete_parser = subparsers.add_parser("delete-dataset-record", help="Delete a record from a dataset")
    delete_parser.add_argument("dataset_name", help="Name of the dataset")
    delete_parser.add_argument("record_id", help="ID of the record to delete")

    # batch-append-to-dataset
    batch_parser = subparsers.add_parser("batch-append-to-dataset", help="Append multiple records to a dataset")
    batch_parser.add_argument("dataset_name", help="Name of the dataset")
    batch_parser.add_argument(
        "batch_data",
        help="Batch data as a JSON array string, or '-' to read JSON from standard input",
    )

    # backup-dataset
    backup_parser = subparsers.add_parser("backup-dataset", help="Backup a dataset")
    backup_parser.add_argument("dataset_name", help="Name of the dataset")
    backup_parser.add_argument("backup_path", help="Path to backup location")

    # archive-dataset
    archive_parser = subparsers.add_parser("archive-dataset", help="Create a self-contained dataset archive snapshot")
    archive_parser.add_argument("dataset_name", help="Name of the dataset")
    archive_parser.add_argument(
        "--archive-dir",
        default="archives",
        help="Relative directory inside data storage to place archives (default: archives)",
    )
    archive_parser.add_argument(
        "--drop-source",
        action="store_true",
        help="Drop the active source dataset after verifying the archive snapshot",
    )

    # list-archives
    list_archives_parser = subparsers.add_parser("list-archives", help="List available dataset archives")
    list_archives_parser.add_argument(
        "--archive-dir",
        default="archives",
        help="Relative archive directory to inspect (default: archives)",
    )

    # restore-archive
    restore_parser = subparsers.add_parser("restore-archive", help="Restore an archived dataset and its metadata")
    restore_parser.add_argument("archive_ref", help="Archive ID or relative path to archive bundle")
    restore_parser.add_argument("--target-name", default=None, help="Optional new dataset name on restore")
    restore_parser.add_argument(
        "--archive-dir",
        default="archives",
        help="Relative archive directory when resolving an archive ID (default: archives)",
    )

    # list-datasets
    subparsers.add_parser("list-datasets", help="List all datasets")

    # get-dataset-info
    info_parser = subparsers.add_parser("get-dataset-info", help="Get information about a dataset")
    info_parser.add_argument("dataset_name", help="Name of the dataset")

    # list-datasets-info
    subparsers.add_parser("list-datasets-info", help="Get information about all datasets")

    # get-dataset-path-info
    path_parser = subparsers.add_parser("get-dataset-path-info", help="Get path information for a dataset")
    path_parser.add_argument("dataset_name", help="Name of the dataset")

    # drop-dataset
    drop_parser = subparsers.add_parser("drop-dataset", help="Delete a dataset and its metadata entry")
    drop_parser.add_argument("dataset_name", help="Name of the dataset")

    # read-dataset
    read_parser = subparsers.add_parser("read-dataset", help="Read all records from a dataset")
    read_parser.add_argument("dataset_name", help="Name of the dataset")

    # get-record
    record_parser = subparsers.add_parser("get-record", help="Get a specific record by ID")
    record_parser.add_argument("dataset_name", help="Name of the dataset")
    record_parser.add_argument("record_id", help="ID of the record")

    # list-records
    list_parser = subparsers.add_parser("list-records", help="List records from a dataset with pagination")
    list_parser.add_argument("dataset_name", help="Name of the dataset")
    list_parser.add_argument("--limit", type=int, default=100, help="Number of records to return")
    list_parser.add_argument("--offset", type=int, default=0, help="Offset for pagination")

    # count-records
    count_parser = subparsers.add_parser("count-records", help="Count records in a dataset")
    count_parser.add_argument("dataset_name", help="Name of the dataset")

    # schedule-task
    sched_parser = subparsers.add_parser(
        "schedule-task",
        help="Schedule a planned execution of an existing Data Vault operation",
    )
    sched_parser.add_argument(
        "operation",
        choices=sorted(ALLOWED_SCHEDULED_OPERATIONS),
        help="Allowlisted Data Vault operation to execute",
    )
    sched_parser.add_argument("dataset_name", help="Target dataset name")
    sched_parser.add_argument("--run-at", required=True, help="Planned execution timestamp in ISO-8601 format")
    sched_parser.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        help="Optional repeat interval in seconds for recurring non-destructive tasks",
    )
    sched_parser.add_argument("--backup-path", default=None, help="Relative backup path (for backup-dataset)")
    sched_parser.add_argument("--archive-dir", default=None, help="Relative archive directory (for archive-dataset)")
    sched_parser.add_argument(
        "--drop-source",
        action="store_true",
        help="Drop source dataset after archiving (for one-shot archive-dataset)",
    )
    sched_parser.add_argument("--record-id", default=None, help="Target record ID (for delete-dataset-record)")

    # list-scheduled-tasks
    list_sched_parser = subparsers.add_parser("list-scheduled-tasks", help="List scheduled Data Vault tasks")
    list_sched_parser.add_argument(
        "--status",
        choices=sorted(VALID_SCHEDULE_STATUSES),
        default=None,
        help="Optional status filter",
    )

    # cancel-scheduled-task
    cancel_sched_parser = subparsers.add_parser("cancel-scheduled-task", help="Cancel a pending scheduled task")
    cancel_sched_parser.add_argument("schedule_id", help="ID of the scheduled task to cancel")

    # run-scheduled-tasks
    run_sched_parser = subparsers.add_parser("run-scheduled-tasks", help="Execute all due scheduled tasks")
    run_sched_parser.add_argument(
        "--as-of",
        default=None,
        help="Optional ISO-8601 evaluation timestamp (defaults to current UTC time)",
    )

    return parser


def run_cli(argv: list[str] | None = None) -> str:
    parser = build_parser()
    args = parser.parse_args(argv)

    set_data_path_override(args.data_dir)
    try:
        if args.command == "create-dataset":
            return create_dataset(args.dataset_name, args.fields)
        if args.command == "append-to-dataset":
            return append_to_dataset(args.data, args.dataset_name)
        if args.command == "update-dataset-record":
            return update_dataset_record(args.dataset_name, args.record_id, args.data)
        if args.command == "delete-dataset-record":
            return delete_dataset_record(args.dataset_name, args.record_id)
        if args.command == "batch-append-to-dataset":
            raw_batch = sys.stdin.read() if args.batch_data == "-" else args.batch_data
            try:
                batch_data = json.loads(raw_batch)
            except json.JSONDecodeError as e:
                return create_response("batch_append_to_dataset", "error", None, f"Invalid JSON batch_data: {e}")
            return batch_append_to_dataset(args.dataset_name, batch_data)
        if args.command == "backup-dataset":
            return backup_dataset(args.dataset_name, args.backup_path)
        if args.command == "archive-dataset":
            return archive_dataset(args.dataset_name, archive_dir=args.archive_dir, drop_source=args.drop_source)
        if args.command == "list-archives":
            return list_archives(archive_dir=args.archive_dir)
        if args.command == "restore-archive":
            return restore_archive(args.archive_ref, target_name=args.target_name, archive_dir=args.archive_dir)
        if args.command == "list-datasets":
            return list_datasets()
        if args.command == "get-dataset-info":
            return get_dataset_info(args.dataset_name)
        if args.command == "list-datasets-info":
            return list_datasets_info()
        if args.command == "get-dataset-path-info":
            return get_dataset_path_info(args.dataset_name)
        if args.command == "drop-dataset":
            return drop_dataset(args.dataset_name)
        if args.command == "read-dataset":
            return read_dataset(args.dataset_name)
        if args.command == "get-record":
            return get_record(args.dataset_name, args.record_id)
        if args.command == "list-records":
            return list_records(args.dataset_name, args.limit, args.offset)
        if args.command == "count-records":
            return count_records(args.dataset_name)
        if args.command == "schedule-task":
            return schedule_task(
                operation=args.operation,
                dataset_name=args.dataset_name,
                run_at=args.run_at,
                interval_seconds=args.interval_seconds,
                backup_path=args.backup_path,
                archive_dir=args.archive_dir,
                drop_source=args.drop_source,
                record_id=args.record_id,
            )
        if args.command == "list-scheduled-tasks":
            return list_scheduled_tasks(status=args.status)
        if args.command == "cancel-scheduled-task":
            return cancel_scheduled_task(args.schedule_id)
        if args.command == "run-scheduled-tasks":
            return run_scheduled_tasks(as_of=args.as_of)
        return create_response("unknown", "error", None, "Unknown command")
    finally:
        set_data_path_override(None)


def main() -> None:
    print(run_cli())


if __name__ == "__main__":
    main()
