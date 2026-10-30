from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.manage import (
    archive_dataset,
    backup_dataset,
    drop_dataset,
    get_dataset_info,
    list_archives,
    list_datasets,
    restore_archive,
)
from scripts.read import count_records, read_dataset
from scripts.write import append_to_dataset, batch_append_to_dataset, create_dataset
from tests.conftest import parse_resp


def test_backup_populated_and_empty_datasets_preserves_metadata(vault_dir: Path) -> None:
    # 1. Empty dataset (metadata exists, 0 records appended yet)
    assert parse_resp(create_dataset("empty_notes", ["title", "body"]))["status"] == "success"
    empty_bkp = parse_resp(backup_dataset("empty_notes", "backups"))
    assert empty_bkp["status"] == "success"
    assert empty_bkp["data"]["record_count"] == 0
    assert empty_bkp["data"]["has_lance_data"] is False

    meta_file = vault_dir / "backups" / "empty_notes" / "dataset_metadata.json"
    assert meta_file.is_file()
    meta_json = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta_json["fields"] == ["title", "body"]

    # 2. Populated dataset
    assert parse_resp(create_dataset("customers", ["name", "tier"]))["status"] == "success"
    assert (
        parse_resp(batch_append_to_dataset("customers", [["Alice", "pro"], ["Bob", "free"]]))["status"]
        == "success"
    )
    pop_bkp = parse_resp(backup_dataset("customers", "backups/daily"))
    assert pop_bkp["status"] == "success"
    assert pop_bkp["data"]["record_count"] == 2
    assert pop_bkp["data"]["has_lance_data"] is True
    assert (vault_dir / "backups" / "daily" / "customers" / "dataset_metadata.json").is_file()


def test_cold_archive_with_drop_source_and_full_restore(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("orders", ["sku", "qty", "status"]))["status"] == "success"
    assert (
        parse_resp(
            batch_append_to_dataset(
                "orders",
                [
                    ["KB-01", "2", "shipped"],
                    ["MS-09", "1", "processing"],
                    ["MN-27", "4", "delivered"],
                ],
            )
        )["status"]
        == "success"
    )
    original_rows = parse_resp(read_dataset("orders"))["data"]

    # Cold-archive with drop_source=True
    arch_res = parse_resp(archive_dataset("orders", archive_dir="cold_storage", drop_source=True))
    assert arch_res["status"] == "success"
    archive_id = arch_res["data"]["archive_id"]
    assert arch_res["data"]["record_count"] == 3
    assert arch_res["data"]["dropped_source"] is True

    # Active dataset must be completely removed
    assert parse_resp(list_datasets())["data"] == []
    assert not (vault_dir / "orders").exists()

    # Archive appears in list_archives
    listed = parse_resp(list_archives("cold_storage"))
    assert listed["status"] == "success"
    assert len(listed["data"]) == 1
    assert listed["data"][0]["archive_id"] == archive_id

    # Restore by archive_id back into original dataset name
    restored = parse_resp(restore_archive(archive_id, archive_dir="cold_storage"))
    assert restored["status"] == "success"
    assert restored["data"]["dataset_name"] == "orders"
    assert restored["data"]["record_count"] == 3

    # Verify schema, fields, record count, and exact rows including UUIDs
    info = parse_resp(get_dataset_info("orders"))["data"]
    assert info["fields"] == ["sku", "qty", "status"]
    assert info["record_count"] == 3

    restored_rows = parse_resp(read_dataset("orders"))["data"]
    assert restored_rows == original_rows

    # Appending new records after restore continues to work seamlessly
    assert parse_resp(append_to_dataset(["HD-99", "1", "new"], "orders"))["status"] == "success"
    assert parse_resp(count_records("orders"))["data"] == 4


def test_restore_archive_to_alternate_name_and_prevent_overwrite(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("inventory", ["item"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["laptop"], "inventory"))["status"] == "success"

    arch = parse_resp(archive_dataset("inventory", archive_dir="archives", drop_source=False))
    archive_path = arch["data"]["archive_path"]

    # Restoring onto still-active 'inventory' must fail safely instead of overwriting
    conflict = parse_resp(restore_archive(archive_path))
    assert conflict["status"] == "error"
    assert "already exists" in conflict["error"]

    # Restoring under a new target_name succeeds as an independent clone
    cloned = parse_resp(restore_archive(archive_path, target_name="inventory_v1_copy"))
    assert cloned["status"] == "success"
    assert set(parse_resp(list_datasets())["data"]) == {"inventory", "inventory_v1_copy"}
    assert parse_resp(count_records("inventory_v1_copy"))["data"] == 1


def test_archive_and_restore_zero_record_dataset(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("schema_only", ["col_a", "col_b"]))["status"] == "success"
    arch = parse_resp(archive_dataset("schema_only", drop_source=True))
    assert arch["status"] == "success"
    assert arch["data"]["has_lance_data"] is False

    assert parse_resp(list_datasets())["data"] == []

    restored = parse_resp(restore_archive(arch["data"]["archive_id"]))
    assert restored["status"] == "success"
    assert parse_resp(get_dataset_info("schema_only"))["data"]["fields"] == ["col_a", "col_b"]
    assert parse_resp(append_to_dataset(["v1", "v2"], "schema_only"))["status"] == "success"
    assert parse_resp(count_records("schema_only"))["data"] == 1
    assert parse_resp(drop_dataset("schema_only"))["status"] == "success"


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../outside",
        "..\\outside",
        "backups/../../escape",
        "/etc/passwd",
        "\\Windows\\Temp",
        "C:\\Temp\\escape",
        "C:relative_drive_escape",
    ],
)
def test_backup_and_archive_reject_path_traversal(vault_dir: Path, malicious_path: str) -> None:
    if not parse_resp(list_datasets())["data"]:
        assert parse_resp(create_dataset("safe_ds", ["x"]))["status"] == "success"

    bkp = parse_resp(backup_dataset("safe_ds", malicious_path))
    assert bkp["status"] == "error"

    arch = parse_resp(archive_dataset("safe_ds", archive_dir=malicious_path))
    assert arch["status"] == "error"

    lst = parse_resp(list_archives(archive_dir=malicious_path))
    assert lst["status"] == "error"

    rst = parse_resp(restore_archive(malicious_path))
    assert rst["status"] == "error"
