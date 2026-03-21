import lance
import os
import shutil
import json

def get_data_path():
    return os.getenv('LANCE_DATA_PATH', '.')

def create_response(operation, status, data=None, error=None):
    response = {
        "skill": "lance",
        "operation": operation,
        "status": status,
        "data": data,
        "error": error
    }

    return json.dumps(response)

def check_dataset_exists(dataset_name):
    try:
        metadata_path = os.path.join(get_data_path(), "metadata.lance")
        metadata_ds = lance.dataset(metadata_path)
        metadata_df = metadata_ds.to_table().to_pandas()
        matching_rows = metadata_df[metadata_df['dataset_name'] == dataset_name]
        if matching_rows.empty:
            raise ValueError(f"Dataset {dataset_name} does not exist in metadata.")
        fields_data = matching_rows.iloc[0]['fields']
        if isinstance(fields_data, str):
            return json.loads(fields_data)
        else:
            # Assume it's a list or array, convert to list
            return list(fields_data)
    except Exception as e:
        raise ValueError(f"Error checking dataset metadata: {str(e)}")

def backup_dataset(dataset_name, backup_path):
    try:
        check_dataset_exists(dataset_name)

        # Ensure backup directory exists
        os.makedirs(backup_path, exist_ok=True)

        # Copy the dataset directory
        source_path = os.path.join(get_data_path(), dataset_name)
        dest_path = os.path.join(backup_path, os.path.basename(dataset_name))

        if os.path.exists(source_path):
            shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
            return create_response("backup_dataset", "success", None, None)
        else:
            raise ValueError(f"Dataset directory {source_path} not found")
    except Exception as e:
        return create_response("backup_dataset", "error", None, str(e))

def list_datasets():
    try:
        metadata_path = os.path.join(get_data_path(), "metadata.lance")
        metadata_ds = lance.dataset(metadata_path)
        metadata_df = metadata_ds.to_table().to_pandas()
        datasets = metadata_df['dataset_name'].unique().tolist()
        return create_response("list_datasets", "success", datasets, None)
    except Exception as e:
        return create_response("list_datasets", "error", None, str(e))

def get_dataset_path(dataset_name):
    return os.path.join(get_data_path(), dataset_name)

def get_dataset_info(dataset_name):
    try:
        fields = check_dataset_exists(dataset_name)

        # Try to load the dataset
        dataset_path = get_dataset_path(dataset_name)
        try:
            ds = lance.dataset(dataset_path)
            df = ds.to_table().to_pandas()
            record_count = len(df)
            columns = df.columns.tolist()
            schema = ds.schema
            field_types = {field.name: str(field.type) for field in schema}
            last_updated = df['updated_at'].max().isoformat() if 'updated_at' in df.columns and len(df) > 0 else None
        except Exception:
            # Dataset file doesn't exist, return metadata info
            record_count = 0
            columns = ['id', 'updated_at'] + fields
            field_types = {}
            last_updated = None

        info = {
            "dataset_name": dataset_name,
            "path": dataset_path,
            "fields": fields,
            "field_types": field_types,
            "record_count": record_count,
            "columns": columns,
            "last_updated": last_updated
        }
        return create_response("get_dataset_info", "success", info, None)
    except Exception as e:
        return create_response("get_dataset_info", "error", None, str(e))

def list_datasets_info():
    try:
        datasets = list_datasets()  # This returns JSON, need to parse
        # Since list_datasets returns JSON, parse it
        import json
        parsed = json.loads(datasets)
        if parsed['status'] == 'error':
            return datasets
        dataset_names = parsed['data']
        infos = []
        for name in dataset_names:
            info_response = get_dataset_info(name)
            info_parsed = json.loads(info_response)
            if info_parsed['status'] == 'success':
                infos.append(info_parsed['data'])
            else:
                # If one fails, perhaps continue or return error
                pass
        return create_response("list_datasets_info", "success", infos, None)
    except Exception as e:
        return create_response("list_datasets_info", "error", None, str(e))

def get_dataset_path_info(dataset_name):
    try:
        path = get_dataset_path(dataset_name)
        return create_response("get_dataset_path_info", "success", path, None)
    except Exception as e:
        return create_response("get_dataset_path_info", "error", None, str(e))