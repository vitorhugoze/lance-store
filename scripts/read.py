import duckdb
import os
from manage import get_dataset_path, create_response, check_dataset_exists

# Install and load the LanceDB extension from DuckDB's core extensions: https://duckdb.org/docs/stable/core_extensions/lance
duckdb.execute("INSTALL lance FROM community;")
duckdb.execute("LOAD lance;")

def sanitize_sql_filters(filters):
    """
    Sanitize SQL filters to prevent SQL injection by checking for dangerous keywords.
    """
    if not filters:
        return filters
    
    dangerous_keywords = [
        'DROP', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'EXEC', 'EXECUTE',
        'UNION', 'SELECT', '--', '/*', '*/', ';', 'TRUNCATE', 'MERGE', 'BULK'
    ]
    
    upper_filters = filters.upper()
    for keyword in dangerous_keywords:
        if keyword in upper_filters:
            raise ValueError(f"Potentially dangerous SQL keyword '{keyword}' found in filters")
    
    return filters

def read_dataset(dataset_name):
    try:
        dataset_path = get_dataset_path(dataset_name)

        #Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        #If exists on metadata but not on disk, return empty list
        if not os.path.exists(dataset_path):
            return create_response("read_dataset", "success", [], None)
        
        df = duckdb.query(f"SELECT * FROM __lance_scan('{dataset_path}')").to_df()
        # Convert datetime columns to ISO strings for JSON serialization
        for col in df.select_dtypes(include=['datetime64', 'datetime64[ns]']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')  # ISO format
        data = df.to_dict('records')
        return create_response("read_dataset", "success", data, None)
    except Exception as e:
        return create_response("read_dataset", "error", None, str(e))

def get_record(dataset_name, record_id):
    try:
        dataset_path = get_dataset_path(dataset_name)

        #Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        #Returns the same error as if the record is not found
        if not os.path.exists(dataset_path):
            return create_response("get_record", "error", None, f"Record with ID '{record_id}' not found")
        df = duckdb.query(f"SELECT * FROM __lance_scan('{dataset_path}') WHERE _id = '{record_id}'").to_df()
        if df.empty:
            return create_response("get_record", "error", None, f"Record with ID '{record_id}' not found")
        # Convert datetime columns to ISO strings for JSON serialization
        for col in df.select_dtypes(include=['datetime64', 'datetime64[ns]']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')  # ISO format
        data = df.to_dict('records')[0]
        return create_response("get_record", "success", data, None)
    except Exception as e:
        return create_response("get_record", "error", None, str(e))

def list_records(dataset_name, limit=100, offset=0, filters=None):
    try:
        dataset_path = get_dataset_path(dataset_name)

        #Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        #If exists on metadata but not on disk, return empty list
        if not os.path.exists(dataset_path):
            return create_response("list_records", "success", {"records": [], "total": 0}, None)
        if filters:
            filters = sanitize_sql_filters(filters)
        query = f"SELECT * FROM __lance_scan('{dataset_path}')"
        if filters:
            query += f" {filters}"
        query += f" LIMIT {limit} OFFSET {offset}"
        df = duckdb.query(query).to_df()
        # Convert datetime columns to ISO strings for JSON serialization
        for col in df.select_dtypes(include=['datetime64', 'datetime64[ns]']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')  # ISO format
        data = {
            "records": df.to_dict('records'),
            "total": len(df)
        }
        return create_response("list_records", "success", data, None)
    except Exception as e:
        return create_response("list_records", "error", None, str(e))

def count_records(dataset_name, filters=None):
    try:
        dataset_path = get_dataset_path(dataset_name)

        #Check if dataset exists on metadata, if not raises an error
        check_dataset_exists(dataset_name)

        #If exists on metadata but not on disk, return count as 0
        if not os.path.exists(dataset_path):
            return create_response("count_records", "success", 0, None)
        if filters:
            filters = sanitize_sql_filters(filters)
        query = f"SELECT COUNT(*) as count FROM __lance_scan('{dataset_path}')"
        if filters:
            query += f" {filters}"
        df = duckdb.query(query).to_df()
        count = int(df['count'][0])
        return create_response("count_records", "success", count, None)
    except Exception as e:
        return create_response("count_records", "error", None, str(e))
