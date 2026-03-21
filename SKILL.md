---
name: lance-store
description: "Persist and retrieve structured data using the Lance columnar format. Use when you need to store, query, or analyze data across sessions — such as saving skill outputs, tracking conversation context, storing research data, or building knowledge bases. Configure via LANCE_DATA_PATH environment variable to set the data storage location. Triggers on: 'store this data', 'save to lance', 'persist information', 'remember this', 'store for later', 'query my data', 'analyze stored data', 'lance store'."
---

# Lance Store

A persistent data store using the Lance columnar format for fast ML data access.

## Quick Start

```bash
# Set data path (persist this in your OpenClaw config)
export LANCE_DATA_PATH=/path/to/data

# Create a dataset
python3 scripts/command.py create-dataset <name> <field1> <field2> ...

# Append data
python3 scripts/command.py append-to-dataset <name> <value1> <value2> ...

# Read data
python3 scripts/command.py list-datasets-info
```

## Configuration

**Environment Variable:** `LANCE_DATA_PATH`

- Default: `.` (current directory)
- Set before running any command
- Persist in OpenClaw config: `openclaw config set 'env.LANCE_DATA_PATH' "/custom/path"`

## Critical Behavior: Data Type Strictness

⚠️ **Lance is strict about data types — they CANNOT change after the first record**

When you append the first record to a dataset, Lance infers the data type for each field. **All subsequent records MUST use the same types.**

**Example — this FAILS:**

```
# First record: age as STRING
append-to-dataset users "John" "25" "john@test.com"

# Second record: age as INTEGER (will FAIL!)
append-to-dataset users "Jane" 30 "jane@test.com"
# Error: `age` should have type large_string but type was int64
```

**Correct approach — maintain consistent types:**

```
# First record: age as STRING
append-to-dataset users "John" "25" "john@test.com"

# Second record: age as STRING
append-to-dataset users "Jane" "30" "jane@test.com"
```

### Why This Matters

Unlike traditional databases that may coerce types, Lance rejects type mismatches. If you store numbers as strings initially, you must always pass strings. Plan your schema carefully.

## Initialization Workflow

When starting a session, **always initialize by listing existing datasets first**:

```bash
# This command returns ALL datasets with their structure
python3 scripts/command.py list-datasets-info
```

Example output:

```json
{
    "skill": "lance",
    "operation": "list_datasets_info",
    "status": "success",
    "data": [
        {
            "dataset_name": "users",
            "path": "/data/users",
            "fields": ["name", "age", "email"],
            "field_types": {
                "id": "large_string",
                "updated_at": "timestamp[us]",
                "name": "large_string",
                "age": "large_string",
                "email": "large_string"
            },
            "record_count": 2,
            "columns": ["id", "updated_at", "name", "age", "email"],
            "last_updated": "2026-03-21T17:57:44.595628"
        }
    ],
    "error": null
}
```

### Understanding `field_types`

| State        | Meaning                                                       |
| ------------ | ------------------------------------------------------------- |
| `{}` (empty) | Dataset exists but no records yet — **types not yet defined** |
| populated    | Types are locked — appends must match                         |

**Important:** If `field_types` is empty, the first append will define types. Be deliberate about the first record's types.

## Commands Reference

### Create Dataset

```bash
python3 scripts/command.py create-dataset <name> <field1> <field2> ...
```

Creates a metadata entry. Fields have no types until first append.

### Append Record

```bash
python3 scripts/command.py append-to-dataset <name> <value1> <value2> ...
```

Appends one record. Types are inferred from first record.

### Batch Append

```bash
python3 scripts/command.py batch-append-to-dataset <name> '<json-array>'
```

Example: `batch-append-to-dataset users '[["Alice", "22", "alice@test.com"], ["Bob", "35", "bob@test.com"]]'`

### Update Record

```bash
python3 scripts/command.py update-dataset-record <name> <record_id> <value1> <value2> ...
```

Updates fields for a specific record by ID.

### Delete Record

```bash
python3 scripts/command.py delete-dataset-record <name> <record_id>
```

### List All Datasets

```bash
python3 scripts/command.py list-datasets
```

### Get Dataset Info

```bash
python3 scripts/command.py get-dataset-info <name>
```

Returns schema, field types (if data exists), and record count.

### List All Datasets with Full Info

```bash
python3 scripts/command.py list-datasets-info
```

**Recommended for initialization.** Returns all datasets with complete metadata.

### Get Dataset Path

```bash
python3 scripts/command.py get-dataset-path-info <name>
```

### Backup Dataset

```bash
python3 scripts/command.py backup-dataset <name> <backup_path>
```

### Count Records

```bash
python3 scripts/command.py count-records <name>
```

## Response Format

All commands return JSON:

```json
{
  "skill": "lance",
  "operation": "<operation_name>",
  "status": "success|error",
  "data": <result_data_or_null>,
  "error": <error_message_or_null>
}
```

## Internal Fields

Every dataset automatically includes:

- `id` — UUID for each record
- `updated_at` — timestamp of last insert/update

These are managed automatically — when appending, only provide your defined fields.

## Data Type Inference

Lance infers types from the first record:

| Python Type    | Lance Type     |
| -------------- | -------------- |
| `"string"`     | `large_string` |
| `25` (int)     | `int64`        |
| `25.5` (float) | `float64`      |
| `True`/`False` | `bool`         |

**CLI caveat:** When passing via command line, all values are strings. To ensure integer types, initialize with actual integers in a script rather than CLI.

## Tips

1. **Initialize at session start:** Run `list-datasets-info` to understand what data already exists
2. **Plan your schema:** First record determines types for the entire dataset
3. **Use batch append for related data:** Ensures consistent types across records
4. **Backup before major operations:** Use `backup-dataset` for safety
5. **Store metadata separately:** Consider a separate dataset for schema documentation if types aren't obvious from context

## Requirements

Install dependencies:

```bash
pip install -r requirements.txt
```

Required packages:

- `pylance` — Lance columnar format
- `pandas` — Data manipulation
- `duckdb` — SQL queries on Lance data
- `argparse` — CLI (stdlib)
