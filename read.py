import duckdb
from manage import get_dataset_path, create_response

# Install and load the Lance extension for DuckDB
duckdb.execute("INSTALL lance FROM community;")
duckdb.execute("LOAD lance;")

def read_dataset(dataset_name):
    try:
        dataset_path = get_dataset_path(dataset_name)
        df = duckdb.query(f"SELECT * FROM '{dataset_path}'").to_df()
        # Convert datetime columns to ISO strings for JSON serialization
        for col in df.select_dtypes(include=['datetime64', 'datetime64[ns]']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')  # ISO format
        data = df.to_dict('records')
        return create_response("read_dataset", "success", data, None)
    except Exception as e:
        return create_response("read_dataset", "error", None, str(e))

def query_dataset(dataset_name, sql_query):
    try:
        dataset_path = get_dataset_path(dataset_name)
        # Replace a placeholder with the actual path
        sql_query = sql_query.replace("{dataset}", f"'{dataset_path}'")
        df = duckdb.query(sql_query).to_df()
        # Convert datetime columns to ISO strings for JSON serialization
        for col in df.select_dtypes(include=['datetime64', 'datetime64[ns]']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')  # ISO format
        data = df.to_dict('records')
        return create_response("query_dataset", "success", data, None)
    except Exception as e:
        return create_response("query_dataset", "error", None, str(e))

def get_record(dataset_name, record_id):
    try:
        dataset_path = get_dataset_path(dataset_name)
        df = duckdb.query(f"SELECT * FROM '{dataset_path}' WHERE id = '{record_id}'").to_df()
        if df.empty:
            raise ValueError(f"Record with ID {record_id} not found")
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
        query = f"SELECT * FROM '{dataset_path}'"
        if filters:
            query += f" {filters}"
        query += f" LIMIT {limit} OFFSET {offset}"
        df = duckdb.query(query).to_df()
        # Convert datetime columns to ISO strings for JSON serialization
        for col in df.select_dtypes(include=['datetime64', 'datetime64[ns]']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')  # ISO format
        data = {
            "records": df.to_dict('records'),
            "total": len(df)  # Note: This is the count of returned records, not total in dataset
        }
        return create_response("list_records", "success", data, None)
    except Exception as e:
        return create_response("list_records", "error", None, str(e))

def count_records(dataset_name, filters=None):
    try:
        dataset_path = get_dataset_path(dataset_name)
        query = f"SELECT COUNT(*) as count FROM '{dataset_path}'"
        if filters:
            query += f" {filters}"
        df = duckdb.query(query).to_df()
        count = int(df['count'][0])
        return create_response("count_records", "success", count, None)
    except Exception as e:
        return create_response("count_records", "error", None, str(e))