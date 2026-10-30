from __future__ import annotations

import gc
import json
import os
import shutil
import stat
import sys
import time
import uuid
from datetime import datetime, timezone

import lance
import pandas as pd
import pyarrow as pa

METADATA_STORE = "metadata.lance"
SCHEDULES_STORE = "schedules.lance"
RESERVED_DATASET_NAMES = {"metadata", METADATA_STORE, "schedules", SCHEDULES_STORE}

_DATA_PATH_OVERRIDE: str | None = None


def set_data_path_override(path: str | None) -> None:
    """Set or clear an in-process override for the data storage directory."""
    global _DATA_PATH_OVERRIDE
    _DATA_PATH_OVERRIDE = path


def get_data_path() -> str:
    """Resolve the active data directory.

    Precedence:
    1. Explicit CLI/in-process override (--data-dir)
    2. LANCE_DATA_PATH environment variable
    3. Current working directory ('.')
    """
    configured = _DATA_PATH_OVERRIDE
    if configured is None:
        env_path = os.environ.get("LANCE_DATA_PATH")
        if env_path and env_path.strip():
            configured = env_path.strip()

    if configured is not None:
        resolved = os.path.realpath(configured)
        os.makedirs(resolved, exist_ok=True)
        return resolved

    return os.path.realpath(".")


def create_response(operation: str, status: str, data=None, error: str | None = None) -> str:
    response = {
        "skill": "data-vault",
        "operation": operation,
        "status": status,
        "data": data,
        "error": error,
    }
    return json.dumps(response)


def _clear_readonly_and_retry(func, target_path, exc_info_or_exc) -> None:
    try:
        os.chmod(target_path, stat.S_IWRITE)
        func(target_path)
    except OSError:
        if isinstance(exc_info_or_exc, BaseException):
            raise exc_info_or_exc from None
        raise exc_info_or_exc[1] from None


def safe_rmtree(path: str, retries: int = 5, base_delay: float = 0.05) -> None:
    """Remove a directory tree reliably on Windows NTFS even after memory-mapped Lance reads."""
    if not os.path.exists(path):
        return

    gc.collect()
    for attempt in range(retries):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_clear_readonly_and_retry)
            else:
                shutil.rmtree(path, onerror=_clear_readonly_and_retry)
            return
        except OSError:
            if not os.path.exists(path):
                return
            if attempt == retries - 1:
                raise
            gc.collect()
            time.sleep(base_delay * (attempt + 1))


def overwrite_lance_table(df: pd.DataFrame, path: str, schema: pa.Schema) -> None:
    """Overwrite a Lance dataset while preserving its exact PyArrow schema even when empty."""
    clean_df = df.reset_index(drop=True)
    table = pa.Table.from_pandas(clean_df, schema=schema, preserve_index=False)
    lance.write_dataset(table, path, mode="overwrite")


def validate_dataset_name(dataset_name: str) -> bool:
    """Validate dataset name to prevent path traversal attacks.

    Rejects names containing path separators, parent-directory references,
    or absolute paths. This ensures dataset paths stay within the data directory.
    """
    if not dataset_name or not isinstance(dataset_name, str) or not dataset_name.strip():
        raise ValueError("Dataset name must be a non-empty string")

    if dataset_name != dataset_name.strip():
        raise ValueError(f"Dataset name '{dataset_name}' must not have leading or trailing whitespace")

    # Reject names with path separators or Windows drive/stream colons
    if "/" in dataset_name or "\\" in dataset_name or ":" in dataset_name:
        raise ValueError(f"Dataset name '{dataset_name}' contains invalid path characters")

    # Reject parent-directory traversal
    if ".." in dataset_name or dataset_name == ".":
        raise ValueError(f"Dataset name '{dataset_name}' must not contain '..' or '.'")

    lowered = dataset_name.lower()
    if lowered in RESERVED_DATASET_NAMES or lowered.endswith(".lance"):
        raise ValueError(f"Dataset name '{dataset_name}' is reserved for internal storage")

    # Reject names that resolve to a parent or absolute path
    data_path = get_data_path()
    resolved = os.path.realpath(os.path.join(data_path, dataset_name))
    if not resolved.startswith(data_path + os.sep) or resolved == data_path:
        raise ValueError(f"Dataset name '{dataset_name}' escapes the data directory")

    return True


def validate_relative_path(rel_path: str, label: str = "Backup path") -> str:
    """Validate a relative subdirectory path inside the active data directory."""
    if not rel_path or not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError(f"{label} must be a non-empty string")

    # Reject absolute paths, root-anchored paths, and Windows drive/UNC prefixes
    if os.path.isabs(rel_path) or rel_path.startswith(("/", "\\")) or ":" in rel_path:
        raise ValueError(f"{label} '{rel_path}' must be a relative path, not absolute")

    # Reject parent-directory traversal
    normalized_parts = rel_path.replace("\\", "/").split("/")
    if ".." in rel_path or ".." in normalized_parts:
        raise ValueError(f"{label} '{rel_path}' must not contain '..'")

    data_path = get_data_path()
    resolved = os.path.realpath(os.path.join(data_path, rel_path))
    if not resolved.startswith(data_path + os.sep) or resolved == data_path:
        raise ValueError(f"{label} '{rel_path}' escapes the data directory")

    return resolved


def validate_backup_path(backup_path: str) -> bool:
    """Validate backup path to prevent path traversal attacks.

    Rejects absolute paths and parent-directory references to ensure backups
    stay within the intended relative location.
    """
    validate_relative_path(backup_path, label="Backup path")
    return True


def dataset_exists_in_metadata(dataset_name: str) -> bool:
    """Return True if dataset_name is registered in metadata.lance."""
    validate_dataset_name(dataset_name)
    metadata_path = os.path.join(get_data_path(), METADATA_STORE)
    if not os.path.exists(metadata_path):
        return False

    metadata_ds = lance.dataset(metadata_path)
    metadata_df = metadata_ds.to_table().to_pandas()
    del metadata_ds
    if metadata_df.empty or "dataset_name" not in metadata_df.columns:
        return False
    return bool((metadata_df["dataset_name"] == dataset_name).any())


def register_dataset_metadata(dataset_name: str, fields: list[str]) -> None:
    """Append a validated dataset entry to metadata.lance."""
    validate_dataset_name(dataset_name)
    if not fields or not isinstance(fields, list) or not all(isinstance(f, str) and f.strip() for f in fields):
        raise ValueError("Fields must be a non-empty list of field names")
    if len(set(fields)) != len(fields):
        raise ValueError("Field names must be unique")
    if any(f in {"_id", "_updated_at"} for f in fields):
        raise ValueError("Field names '_id' and '_updated_at' are reserved internal columns")
    if dataset_exists_in_metadata(dataset_name):
        raise ValueError(f"Dataset '{dataset_name}' already exists in metadata")

    df = pd.DataFrame(
        {
            "_id": [str(uuid.uuid4())],
            "dataset_name": [dataset_name],
            "fields": [json.dumps(fields)],
            "_created_at": [datetime.now()],
        }
    )
    metadata_path = os.path.join(get_data_path(), METADATA_STORE)
    write_mode = "append" if os.path.exists(metadata_path) else "create"
    lance.write_dataset(df, metadata_path, mode=write_mode)


def check_dataset_exists(dataset_name: str) -> list[str]:
    validate_dataset_name(dataset_name)
    metadata_path = os.path.join(get_data_path(), METADATA_STORE)
    if not os.path.exists(metadata_path):
        raise ValueError(f"Dataset '{dataset_name}' does not exist in metadata.")

    try:
        metadata_ds = lance.dataset(metadata_path)
        metadata_df = metadata_ds.to_table().to_pandas()
        del metadata_ds
    except Exception as e:
        raise ValueError(f"Error checking dataset metadata: {e}") from e

    matching_rows = metadata_df[metadata_df["dataset_name"] == dataset_name]
    if matching_rows.empty:
        raise ValueError(f"Dataset '{dataset_name}' does not exist in metadata.")

    fields_data = matching_rows.iloc[0]["fields"]
    if isinstance(fields_data, str):
        return list(json.loads(fields_data))
    return list(fields_data)


def get_dataset_path(dataset_name: str) -> str:
    validate_dataset_name(dataset_name)
    return os.path.join(get_data_path(), dataset_name)


def backup_dataset(dataset_name: str, backup_path: str) -> str:
    try:
        validate_dataset_name(dataset_name)
        backup_root = validate_relative_path(backup_path, label="Backup path")
        fields = check_dataset_exists(dataset_name)

        dest_path = os.path.join(backup_root, os.path.basename(dataset_name))
        os.makedirs(dest_path, exist_ok=True)

        source_path = get_dataset_path(dataset_name)
        if os.path.exists(source_path):
            ds = lance.dataset(source_path)
            record_count = ds.count_rows()
            del ds
            shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
            has_lance_data = True
        else:
            record_count = 0
            has_lance_data = False

        metadata_snapshot = {
            "dataset_name": dataset_name,
            "fields": fields,
            "record_count": record_count,
            "has_lance_data": has_lance_data,
            "backed_up_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(os.path.join(dest_path, "dataset_metadata.json"), "w", encoding="utf-8") as fh:
            json.dump(metadata_snapshot, fh, indent=2)

        rel_dest = os.path.relpath(dest_path, get_data_path()).replace("\\", "/")
        return create_response(
            "backup_dataset",
            "success",
            {
                "dataset_name": dataset_name,
                "backup_path": rel_dest,
                "record_count": record_count,
                "has_lance_data": has_lance_data,
            },
            None,
        )
    except Exception as e:
        return create_response("backup_dataset", "error", None, str(e))


def _drop_dataset_internal(dataset_name: str) -> None:
    validate_dataset_name(dataset_name)
    check_dataset_exists(dataset_name)

    dataset_path = get_dataset_path(dataset_name)
    if os.path.exists(dataset_path):
        safe_rmtree(dataset_path)

    metadata_path = os.path.join(get_data_path(), METADATA_STORE)
    metadata_ds = lance.dataset(metadata_path)
    original_schema = metadata_ds.schema
    metadata_df = metadata_ds.to_table().to_pandas()
    del metadata_ds

    remaining = metadata_df[metadata_df["dataset_name"] != dataset_name]
    overwrite_lance_table(remaining, metadata_path, original_schema)


def archive_dataset(dataset_name: str, archive_dir: str = "archives", drop_source: bool = False) -> str:
    try:
        validate_dataset_name(dataset_name)
        archive_root = validate_relative_path(archive_dir, label="Archive directory")
        fields = check_dataset_exists(dataset_name)

        now_utc = datetime.now(timezone.utc)
        archive_id = f"{dataset_name}_{now_utc.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        bundle_path = os.path.join(archive_root, archive_id)
        archived_data_path = os.path.join(bundle_path, "data")
        os.makedirs(bundle_path, exist_ok=True)

        source_path = get_dataset_path(dataset_name)
        if os.path.exists(source_path):
            ds = lance.dataset(source_path)
            record_count = ds.count_rows()
            del ds

            shutil.copytree(source_path, archived_data_path)

            # Verify archived Lance snapshot integrity before allowing optional source drop
            verify_ds = lance.dataset(archived_data_path)
            archived_count = verify_ds.count_rows()
            del verify_ds
            if archived_count != record_count:
                raise RuntimeError(
                    f"Archive verification failed: expected {record_count} rows, archived {archived_count}"
                )
            has_lance_data = True
        else:
            record_count = 0
            has_lance_data = False

        rel_bundle = os.path.relpath(bundle_path, get_data_path()).replace("\\", "/")
        manifest = {
            "archive_id": archive_id,
            "dataset_name": dataset_name,
            "fields": fields,
            "record_count": record_count,
            "has_lance_data": has_lance_data,
            "archive_path": rel_bundle,
            "archived_at": now_utc.isoformat(),
            "dropped_source": bool(drop_source),
        }
        manifest_path = os.path.join(bundle_path, "archive_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

        if drop_source:
            _drop_dataset_internal(dataset_name)

        return create_response("archive_dataset", "success", manifest, None)
    except Exception as e:
        return create_response("archive_dataset", "error", None, str(e))


def list_archives(archive_dir: str = "archives") -> str:
    try:
        archive_root = validate_relative_path(archive_dir, label="Archive directory")
        if not os.path.exists(archive_root):
            return create_response("list_archives", "success", [], None)

        manifests = []
        for entry in os.listdir(archive_root):
            manifest_file = os.path.join(archive_root, entry, "archive_manifest.json")
            if os.path.isfile(manifest_file):
                with open(manifest_file, encoding="utf-8") as fh:
                    manifests.append(json.load(fh))

        manifests.sort(key=lambda item: item.get("archived_at", ""), reverse=True)
        return create_response("list_archives", "success", manifests, None)
    except Exception as e:
        return create_response("list_archives", "error", None, str(e))


def restore_archive(archive_ref: str, target_name: str | None = None, archive_dir: str = "archives") -> str:
    try:
        if not archive_ref or not isinstance(archive_ref, str) or not archive_ref.strip():
            raise ValueError("Archive reference must be a non-empty string")

        direct_candidate = validate_relative_path(archive_ref, label="Archive path")
        if os.path.isfile(os.path.join(direct_candidate, "archive_manifest.json")):
            bundle_path = direct_candidate
        elif "/" not in archive_ref and "\\" not in archive_ref:
            archive_root = validate_relative_path(archive_dir, label="Archive directory")
            fallback_rel = os.path.join(os.path.relpath(archive_root, get_data_path()), archive_ref)
            bundle_path = validate_relative_path(fallback_rel, label="Archive path")
        else:
            bundle_path = direct_candidate

        manifest_file = os.path.join(bundle_path, "archive_manifest.json")
        if not os.path.isfile(manifest_file):
            raise ValueError(f"Archive manifest not found for '{archive_ref}'")

        with open(manifest_file, encoding="utf-8") as fh:
            manifest = json.load(fh)

        restore_name = target_name if target_name is not None else manifest["dataset_name"]
        validate_dataset_name(restore_name)

        dest_dataset_path = get_dataset_path(restore_name)
        if dataset_exists_in_metadata(restore_name) or os.path.exists(dest_dataset_path):
            raise ValueError(
                f"Dataset '{restore_name}' already exists; drop it first or specify a different --target-name"
            )

        fields = list(manifest["fields"])
        if manifest.get("has_lance_data"):
            archived_data_path = os.path.join(bundle_path, "data")
            if not os.path.isdir(archived_data_path):
                raise ValueError(f"Archived Lance data directory missing in '{archive_ref}'")
            shutil.copytree(archived_data_path, dest_dataset_path)

        register_dataset_metadata(restore_name, fields)

        return create_response(
            "restore_archive",
            "success",
            {
                "dataset_name": restore_name,
                "archive_id": manifest["archive_id"],
                "record_count": manifest["record_count"],
                "fields": fields,
            },
            None,
        )
    except Exception as e:
        return create_response("restore_archive", "error", None, str(e))


def list_datasets() -> str:
    try:
        metadata_path = os.path.join(get_data_path(), METADATA_STORE)
        if not os.path.exists(metadata_path):
            return create_response("list_datasets", "success", [], None)
        metadata_ds = lance.dataset(metadata_path)
        metadata_df = metadata_ds.to_table().to_pandas()
        del metadata_ds
        if metadata_df.empty:
            return create_response("list_datasets", "success", [], None)
        datasets = metadata_df["dataset_name"].unique().tolist()
        return create_response("list_datasets", "success", datasets, None)
    except Exception as e:
        return create_response("list_datasets", "error", None, str(e))


def drop_dataset(dataset_name: str) -> str:
    try:
        _drop_dataset_internal(dataset_name)
        return create_response("drop_dataset", "success", None, None)
    except Exception as e:
        return create_response("drop_dataset", "error", None, str(e))


def get_dataset_info(dataset_name: str) -> str:
    try:
        fields = check_dataset_exists(dataset_name)
        dataset_path = get_dataset_path(dataset_name)

        if os.path.exists(dataset_path):
            ds = lance.dataset(dataset_path)
            schema = ds.schema
            df = ds.to_table().to_pandas()
            del ds
            record_count = len(df)
            columns = [field.name for field in schema]
            field_types = {field.name: str(field.type) for field in schema}
            last_updated = (
                pd.Timestamp(df["_updated_at"].max()).isoformat()
                if "_updated_at" in df.columns and len(df) > 0
                else None
            )
        else:
            record_count = 0
            columns = ["_id", "_updated_at", *fields]
            field_types = {}
            last_updated = None

        info = {
            "dataset_name": dataset_name,
            "path": dataset_path,
            "fields": fields,
            "field_types": field_types,
            "record_count": record_count,
            "columns": columns,
            "last_updated": last_updated,
        }
        return create_response("get_dataset_info", "success", info, None)
    except Exception as e:
        return create_response("get_dataset_info", "error", None, str(e))


def list_datasets_info() -> str:
    try:
        datasets = list_datasets()
        parsed = json.loads(datasets)
        if parsed["status"] == "error":
            return datasets

        infos = []
        for name in parsed["data"]:
            info_parsed = json.loads(get_dataset_info(name))
            if info_parsed["status"] == "success":
                infos.append(info_parsed["data"])
        return create_response("list_datasets_info", "success", infos, None)
    except Exception as e:
        return create_response("list_datasets_info", "error", None, str(e))


def get_dataset_path_info(dataset_name: str) -> str:
    try:
        path = get_dataset_path(dataset_name)
        return create_response("get_dataset_path_info", "success", path, None)
    except Exception as e:
        return create_response("get_dataset_path_info", "error", None, str(e))
