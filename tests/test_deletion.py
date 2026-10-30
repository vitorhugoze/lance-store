from __future__ import annotations

from pathlib import Path

import pytest

from scripts.manage import drop_dataset, get_dataset_info, list_datasets
from scripts.read import count_records, get_record, list_records, read_dataset
from scripts.write import append_to_dataset, batch_append_to_dataset, create_dataset, delete_dataset_record
from tests.conftest import parse_resp


def test_delete_single_record_preserves_siblings_and_schema(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("tasks", ["title", "owner"]))["status"] == "success"
    assert (
        parse_resp(
            batch_append_to_dataset(
                "tasks",
                [["Write tests", "Alice"], ["Setup CI", "Bob"], ["Update docs", "Carol"]],
            )
        )["status"]
        == "success"
    )

    rows = parse_resp(read_dataset("tasks"))["data"]
    assert len(rows) == 3
    first_id, second_id, third_id = rows[0]["_id"], rows[1]["_id"], rows[2]["_id"]

    # Delete first row (index 0 — regression test against Pandas index column leakage!)
    del_res = parse_resp(delete_dataset_record("tasks", first_id))
    assert del_res["status"] == "success"

    assert parse_resp(get_record("tasks", first_id))["status"] == "error"
    assert parse_resp(count_records("tasks"))["data"] == 2

    remaining = parse_resp(read_dataset("tasks"))["data"]
    assert [r["_id"] for r in remaining] == [second_id, third_id]
    assert "__index_level_0__" not in remaining[0]

    # Appending after non-zero index drop must succeed without schema mismatch
    assert parse_resp(append_to_dataset(["Ship release", "Dave"], "tasks"))["status"] == "success"
    assert parse_resp(count_records("tasks"))["data"] == 3

    page = parse_resp(list_records("tasks", limit=1, offset=0))["data"]
    assert len(page["records"]) == 1
    assert page["total"] == 3


def test_delete_last_remaining_record_preserves_schema_and_allows_reappend(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("ephemeral", ["key", "value"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["session_1", "active"], "ephemeral"))["status"] == "success"

    only_row = parse_resp(read_dataset("ephemeral"))["data"][0]
    info_before = parse_resp(get_dataset_info("ephemeral"))["data"]

    # Delete the ONLY record in the dataset
    del_res = parse_resp(delete_dataset_record("ephemeral", only_row["_id"]))
    assert del_res["status"] == "success"

    assert parse_resp(count_records("ephemeral"))["data"] == 0
    assert parse_resp(read_dataset("ephemeral"))["data"] == []

    info_empty = parse_resp(get_dataset_info("ephemeral"))["data"]
    assert info_empty["record_count"] == 0
    assert info_empty["field_types"] == info_before["field_types"]

    # Subsequent append must work cleanly with the preserved schema
    assert parse_resp(append_to_dataset(["session_2", "renewed"], "ephemeral"))["status"] == "success"
    assert parse_resp(count_records("ephemeral"))["data"] == 1


def test_delete_missing_record_double_delete_and_uninitialized_dataset(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("empty_ds", ["field1"]))["status"] == "success"

    # Deleting from dataset before any records were ever written
    uninit = parse_resp(delete_dataset_record("empty_ds", "non-existent-uuid"))
    assert uninit["status"] == "error"
    assert "not found" in uninit["error"]

    assert parse_resp(append_to_dataset(["hello"], "empty_ds"))["status"] == "success"
    rec_id = parse_resp(read_dataset("empty_ds"))["data"][0]["_id"]

    assert parse_resp(delete_dataset_record("empty_ds", rec_id))["status"] == "success"
    # Second delete of same ID must return error cleanly
    double_del = parse_resp(delete_dataset_record("empty_ds", rec_id))
    assert double_del["status"] == "error"
    assert "not found" in double_del["error"]


def test_drop_dataset_including_last_dataset_and_recreate(vault_dir: Path) -> None:
    assert parse_resp(create_dataset("alpha", ["a"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["1"], "alpha"))["status"] == "success"
    assert parse_resp(create_dataset("beta", ["b1", "b2"]))["status"] == "success"

    assert set(parse_resp(list_datasets())["data"]) == {"alpha", "beta"}
    assert (vault_dir / "alpha").is_dir()

    # Drop populated dataset 'alpha'
    assert parse_resp(drop_dataset("alpha"))["status"] == "success"
    assert not (vault_dir / "alpha").exists()
    assert parse_resp(list_datasets())["data"] == ["beta"]

    # Drop empty dataset 'beta' (the LAST remaining dataset in metadata.lance!)
    assert parse_resp(drop_dataset("beta"))["status"] == "success"
    assert parse_resp(list_datasets())["data"] == []

    # Recreating 'alpha' with a brand-new schema after metadata.lance became empty must work!
    assert parse_resp(create_dataset("alpha", ["x", "y", "z"]))["status"] == "success"
    assert parse_resp(append_to_dataset(["10", "20", "30"], "alpha"))["status"] == "success"
    assert parse_resp(count_records("alpha"))["data"] == 1

    # Dropping non-existent dataset returns error
    assert parse_resp(drop_dataset("ghost"))["status"] == "error"


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "../escape",
        "..\\escape",
        "sub/dir",
        "sub\\dir",
        "C:drive",
        "metadata",
        "metadata.lance",
        "schedules",
        "schedules.lance",
        "custom.lance",
    ],
)
def test_dataset_operations_reject_invalid_and_reserved_names(vault_dir: Path, bad_name: str) -> None:
    assert parse_resp(create_dataset(bad_name, ["f"]))["status"] == "error"
    assert parse_resp(delete_dataset_record(bad_name, "id-1"))["status"] == "error"
    assert parse_resp(drop_dataset(bad_name))["status"] == "error"
