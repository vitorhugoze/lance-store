from __future__ import annotations

import os
import uuid
from datetime import datetime

import lance
import pandas as pd

from scripts.manage import (
    check_dataset_exists,
    create_response,
    get_dataset_path,
    overwrite_lance_table,
    register_dataset_metadata,
)


def create_dataset(dataset_name: str, fields: list[str]) -> str:
    try:
        register_dataset_metadata(dataset_name, fields)
        return create_response("create_dataset", "success", None, None)
    except Exception as e:
        return create_response("create_dataset", "error", None, str(e))


def validate_field_count(fields: list[str], new_data: list) -> None:
    if not isinstance(new_data, (list, tuple)):
        raise ValueError("Record values must be provided as a list")
    if len(fields) != len(new_data):
        raise ValueError(f"Number of fields mismatch: expected {len(fields)}, got {len(new_data)}")


def append_to_dataset(new_data: list, dataset_name: str) -> str:
    try:
        fields = check_dataset_exists(dataset_name)
        validate_field_count(fields, new_data)

        # Create a dictionary with _id, _updated_at, and the field data
        data_dict = {
            "_id": str(uuid.uuid4()),
            "_updated_at": datetime.now(),
        }
        for field, value in zip(fields, new_data):
            data_dict[field] = value

        df = pd.DataFrame([data_dict])

        dataset_path = get_dataset_path(dataset_name)
        write_mode = "append" if os.path.exists(dataset_path) else "create"
        lance.write_dataset(df, dataset_path, mode=write_mode)
        return create_response("append_to_dataset", "success", None, None)
    except Exception as e:
        return create_response("append_to_dataset", "error", None, str(e))


def update_dataset_record(dataset_name: str, record_id: str, updated_data: list) -> str:
    try:
        fields = check_dataset_exists(dataset_name)
        validate_field_count(fields, updated_data)

        dataset_path = get_dataset_path(dataset_name)
        if not os.path.exists(dataset_path):
            raise ValueError(f"Record with ID {record_id} not found in dataset {dataset_name}")

        # Load the dataset
        ds = lance.dataset(dataset_path)
        original_schema = ds.schema
        df = ds.to_table().to_pandas()
        del ds

        # Find the record to update
        record_index = df[df["_id"] == record_id].index
        if len(record_index) == 0:
            raise ValueError(f"Record with ID {record_id} not found in dataset {dataset_name}")

        # Update the record
        idx = record_index[0]
        for field, value in zip(fields, updated_data):
            df.at[idx, field] = value
        df.at[idx, "_updated_at"] = pd.Timestamp(datetime.now())

        # Write back the updated dataset preserving original PyArrow schema
        overwrite_lance_table(df, dataset_path, original_schema)
        return create_response("update_dataset_record", "success", None, None)
    except Exception as e:
        return create_response("update_dataset_record", "error", None, str(e))


def delete_dataset_record(dataset_name: str, record_id: str) -> str:
    try:
        check_dataset_exists(dataset_name)

        dataset_path = get_dataset_path(dataset_name)
        if not os.path.exists(dataset_path):
            raise ValueError(f"Record with ID {record_id} not found in dataset {dataset_name}")

        # Load the dataset
        ds = lance.dataset(dataset_path)
        original_schema = ds.schema
        df = ds.to_table().to_pandas()
        del ds

        # Find the record to delete
        record_index = df[df["_id"] == record_id].index
        if len(record_index) == 0:
            raise ValueError(f"Record with ID {record_id} not found in dataset {dataset_name}")

        # Remove the record and preserve schema even when 0 records remain
        remaining_df = df.drop(record_index[0]).reset_index(drop=True)
        overwrite_lance_table(remaining_df, dataset_path, original_schema)
        return create_response("delete_dataset_record", "success", None, None)
    except Exception as e:
        return create_response("delete_dataset_record", "error", None, str(e))


def batch_append_to_dataset(dataset_name: str, batch_data: list[list]) -> str:
    try:
        fields = check_dataset_exists(dataset_name)
        if not isinstance(batch_data, list) or len(batch_data) == 0:
            raise ValueError("Batch data must be a non-empty list of records")

        # Validate all records in the batch
        for data in batch_data:
            validate_field_count(fields, data)

        # Create DataFrame for batch data
        records = []
        for data in batch_data:
            data_dict = {
                "_id": str(uuid.uuid4()),
                "_updated_at": datetime.now(),
            }
            for field, value in zip(fields, data):
                data_dict[field] = value
            records.append(data_dict)

        df = pd.DataFrame(records)

        # Append the batch
        dataset_path = get_dataset_path(dataset_name)
        write_mode = "append" if os.path.exists(dataset_path) else "create"
        lance.write_dataset(df, dataset_path, mode=write_mode)
        return create_response("batch_append_to_dataset", "success", None, None)
    except Exception as e:
        return create_response("batch_append_to_dataset", "error", None, str(e))
