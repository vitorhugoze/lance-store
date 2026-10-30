from __future__ import annotations

import os

import lance
import pandas as pd

from scripts.manage import check_dataset_exists, create_response, get_dataset_path


def _serialize_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame into JSON-serializable record dicts with ISO datetime strings."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return out.to_dict("records")


def read_dataset(dataset_name: str) -> str:
    try:
        dataset_path = get_dataset_path(dataset_name)

        # Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        # If exists on metadata but not on disk, return empty list
        if not os.path.exists(dataset_path):
            return create_response("read_dataset", "success", [], None)

        dataset = lance.dataset(dataset_path)
        df = dataset.to_table().to_pandas()
        del dataset

        data = _serialize_records(df)
        return create_response("read_dataset", "success", data, None)
    except Exception as e:
        return create_response("read_dataset", "error", None, str(e))


def get_record(dataset_name: str, record_id: str) -> str:
    try:
        dataset_path = get_dataset_path(dataset_name)

        # Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        # Returns the same error as if the record is not found
        if not os.path.exists(dataset_path):
            return create_response("get_record", "error", None, f"Record with ID '{record_id}' not found")

        dataset = lance.dataset(dataset_path)
        df = dataset.to_table().to_pandas()
        del dataset

        filtered = df[df["_id"] == record_id]
        if filtered.empty:
            return create_response("get_record", "error", None, f"Record with ID '{record_id}' not found")

        data = _serialize_records(filtered)[0]
        return create_response("get_record", "success", data, None)
    except Exception as e:
        return create_response("get_record", "error", None, str(e))


def list_records(dataset_name: str, limit: int = 100, offset: int = 0) -> str:
    try:
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative integers")

        dataset_path = get_dataset_path(dataset_name)

        # Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        # If exists on metadata but not on disk, return empty list
        if not os.path.exists(dataset_path):
            return create_response("list_records", "success", {"records": [], "total": 0}, None)

        dataset = lance.dataset(dataset_path)
        df = dataset.to_table().to_pandas()
        del dataset

        total_count = len(df)
        page_df = df.iloc[offset : offset + limit]
        data = {
            "records": _serialize_records(page_df),
            "total": total_count,
        }
        return create_response("list_records", "success", data, None)
    except Exception as e:
        return create_response("list_records", "error", None, str(e))


def count_records(dataset_name: str) -> str:
    try:
        dataset_path = get_dataset_path(dataset_name)

        # Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        # If exists on metadata but not on disk, return count as 0
        if not os.path.exists(dataset_path):
            return create_response("count_records", "success", 0, None)

        dataset = lance.dataset(dataset_path)
        count = dataset.count_rows()
        del dataset
        return create_response("count_records", "success", count, None)
    except Exception as e:
        return create_response("count_records", "error", None, str(e))
