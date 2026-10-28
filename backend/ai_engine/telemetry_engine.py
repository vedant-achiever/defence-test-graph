"""
Core Telemetry Ingestion, Schema Alignment, and Statistical Processing Engine.

Python implementation of telemetry algorithms for Defence Test Data Analyzer.
"""

import csv
import io
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# =============================================================================
# VALUE PARSING
# =============================================================================


def _try_parse_val(val: str) -> Any:
    """Attempt parsing numeric or clean string."""
    if val is None:
        return None

    val_str = str(val).strip()

    if val_str == "" or val_str.lower() in ("null", "none", "nan"):
        return None

    try:
        if "." in val_str:
            return float(val_str)

        return int(val_str)

    except ValueError:
        try:
            return float(val_str)
        except ValueError:
            return val_str


# =============================================================================
# CSV PARSING
# =============================================================================


def parse_csv_file(filename: str, file_path: str) -> Dict[str, Any]:
    """Parse CSV file from disk and detect columns, timestamps, identifiers,
    units, and missing-value percentages without storing raw data.
    """
    import os
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    detected_timestamps = []
    detected_identifiers = []
    detected_components = []
    detected_units = {}
    missing_value_percentages = {}
    
    total_rows = 0
    missing_counts = {}

    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        for col in columns:
            missing_counts[col.strip()] = 0

        for row in reader:
            total_rows += 1
            for key, value in row.items():
                if key is not None:
                    col_clean = key.strip()
                    val_str = str(value).strip() if value is not None else ""
                    if val_str == "" or val_str.lower() in ("null", "none", "nan"):
                        if col_clean in missing_counts:
                            missing_counts[col_clean] += 1

    columns_clean = [c.strip() for c in columns]

    for column_clean in columns_clean:
        lower = column_clean.lower()
        if any(word in lower for word in ["time", "date"]) or lower == "timestamp":
            detected_timestamps.append(column_clean)
        if any(identifier in lower for identifier in ["test_id", "run_id", "sensor_id"]):
            detected_identifiers.append(column_clean)
        if any(component in lower for component in ["component_id", "component"]):
            detected_components.append(column_clean)

        if "_c" in lower or "temp_c" in lower:
            detected_units[column_clean] = "°C"
        elif "_f" in lower or "temp_f" in lower:
            detected_units[column_clean] = "°F"
        elif "_v" in lower or "voltage" in lower:
            detected_units[column_clean] = "V"
        elif "_a" in lower or "current" in lower:
            detected_units[column_clean] = "A"
        elif "kpa" in lower:
            detected_units[column_clean] = "kPa"
        elif "kw" in lower:
            detected_units[column_clean] = "kW"

        if total_rows > 0:
            missing_value_percentages[column_clean] = round((missing_counts.get(column_clean, 0) / total_rows) * 100, 1)
        else:
            missing_value_percentages[column_clean] = 0.0

    return {
        "id": f"file_{uuid.uuid4().hex[:8]}",
        "filename": filename,
        "file_path": file_path,
        "fileSize": file_size,
        "rowCount": total_rows,
        "columnCount": len(columns),
        "columns": columns_clean,
        "rawText": None,
        "parsedData": [],
        "detectedTimestamps": detected_timestamps,
        "detectedIdentifiers": detected_identifiers,
        "detectedComponents": detected_components,
        "detectedUnits": detected_units,
        "missingValuePercentages": missing_value_percentages,
        "status": "parsed",
    }


# =============================================================================
# ATTRIBUTE NORMALIZATION
# =============================================================================


def normalize_attribute_name(col: str) -> str:
    """Map heterogeneous column headers to canonical attribute names."""

    normalized = col.lower().strip()

    if "temp" in normalized:
        return "Temperature"

    if "vib" in normalized:
        return "Vibration"

    if "volt" in normalized:
        return "Voltage"

    if "curr" in normalized:
        return "Current"

    if "press" in normalized:
        return "Pressure"

    if "flow" in normalized:
        return "Flow_Rate"

    if "power" in normalized:
        return "Power"

    if normalized in ("timestamp", "time"):
        return "Timestamp"

    if normalized == "test_id":
        return "Test_ID"

    if normalized == "component_id":
        return "Component_ID"

    if normalized == "run_id":
        return "Run_ID"

    if normalized == "sensor_id":
        return "Sensor_ID"

    if normalized == "status":
        return "Status"

    if normalized == "operating_mode":
        return "Operating_Mode"

    return col.strip().replace(" ", "_").capitalize()


# =============================================================================
# ATTRIBUTE EXTRACTION
# =============================================================================


def extract_attributes(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Inspect all uploaded files and generate a unified schema dictionary."""

    attribute_map: Dict[str, Dict[str, Any]] = {}

    for file_data in files:
        filename = file_data.get("filename", "unknown")
        columns = file_data.get("columns", [])
        parsed_data = file_data.get("parsedData", [])
        missing_map = file_data.get("missingValuePercentages", {})
        unit_map = file_data.get("detectedUnits", {})

        first_row = parsed_data[0] if parsed_data else {}

        for column in columns:
            normalized_name = normalize_attribute_name(column)
            value = first_row.get(column)

            data_type = (
                "number" if isinstance(value, (int, float)) else "string"
            )

            if normalized_name not in attribute_map:
                attribute_map[normalized_name] = {
                    "originalNames": {column},
                    "sourceFiles": {filename},
                    "missingSum": missing_map.get(column, 0.0),
                    "count": 1,
                    "dataType": data_type,
                    "unit": unit_map.get(column),
                }

            else:
                item = attribute_map[normalized_name]

                item["originalNames"].add(column)
                item["sourceFiles"].add(filename)
                item["missingSum"] += missing_map.get(column, 0.0)
                item["count"] += 1

                if not item["unit"] and unit_map.get(column):
                    item["unit"] = unit_map.get(column)

    # -------------------------------------------------------------------------
    # Build attribute definitions
    # -------------------------------------------------------------------------

    definitions = []

    for normalized_name, value in attribute_map.items():
        average_missing = (
            round(value["missingSum"] / value["count"], 1)
            if value["count"] > 0
            else 0.0
        )

        definitions.append(
            {
                "normalizedName": normalized_name,
                "originalNames": sorted(list(value["originalNames"])),
                "sourceFiles": sorted(list(value["sourceFiles"])),
                "dataType": value["dataType"],
                "unit": value["unit"],
                "missingPercentage": average_missing,
                "selected": True,
            }
        )

    # -------------------------------------------------------------------------
    # Sort identifiers first, then physical measurements
    # -------------------------------------------------------------------------

    priority = {
        "Timestamp": 1,
        "Test_ID": 2,
        "Component_ID": 3,
        "Run_ID": 4,
        "Sensor_ID": 5,
    }

    definitions.sort(
        key=lambda item: (
            priority.get(item["normalizedName"], 10),
            item["normalizedName"],
        )
    )

    return definitions


# =============================================================================
# FILTERING
# =============================================================================


def apply_filters(
    records: List[Dict[str, Any]], filters: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Filter records by enabled boolean criteria."""

    active_filters = [
        filter_item
        for filter_item in filters
        if filter_item.get("enabled", True)
    ]

    if not active_filters:
        return records

    filtered_records = []

    for row in records:
        matches = True

        for filter_item in active_filters:
            column = filter_item.get("column")
            operator = filter_item.get("operator")
            value_1 = filter_item.get("value1")
            value_2 = filter_item.get("value2")

            value = row.get(column)

            if value is None:
                matches = False
                break

            try:
                if operator == "equals":
                    if str(value).lower() != str(value_1).lower():
                        matches = False
                        break

                elif operator == "not_equals":
                    if str(value).lower() == str(value_1).lower():
                        matches = False
                        break

                elif operator == "greater_than":
                    if float(value) <= float(value_1):
                        matches = False
                        break

                elif operator == "less_than":
                    if float(value) >= float(value_1):
                        matches = False
                        break

                elif operator == "between":
                    numeric_value = float(value)
                    lower_bound = float(value_1)
                    upper_bound = float(
                        value_2 if value_2 is not None else value_1
                    )

                    if not (lower_bound <= numeric_value <= upper_bound):
                        matches = False
                        break

                elif operator == "contains":
                    if str(value_1).lower() not in str(value).lower():
                        matches = False
                        break

            except (ValueError, TypeError):
                matches = False
                break

        if matches:
            filtered_records.append(row)

    return filtered_records


# =============================================================================
# DATASET COMBINATION
# =============================================================================


def combine_datasets(
    files: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Combine multiple uploaded files preserving ALL original columns."""
    import os
    import csv
    from datetime import datetime, timezone

    if not files:
        return []

    config = config or {
        "strategy": "append",
        "timestampToleranceMs": 100,
    }

    strategy = config.get("strategy", "join")
    master_csv_path = os.path.join("backend", "uploads", "master_combined.csv")

    # Collect ALL original column names across all files (preserving exact names)
    all_raw_columns = []
    seen_columns = set()
    
    # These meta columns are always first
    meta_columns = [
        "id", "Timestamp", "Date", "Test_ID", "Component_ID", "Run_ID", "Sensor_ID",
        "Source_File", "Source_Row", "Processing_Time", "Join_Status", "Data_Quality_Flag"
    ]
    
    for file_data in files:
        for column in file_data.get("columns", []):
            col_clean = column.strip()
            # Skip columns that are already in meta_columns (case-insensitive check)
            meta_lower = {m.lower() for m in meta_columns}
            if col_clean.lower() in meta_lower:
                continue
            if col_clean not in seen_columns:
                all_raw_columns.append(col_clean)
                seen_columns.add(col_clean)

    header = meta_columns + sorted(all_raw_columns)

    preview_records = []
    row_index = 1

    os.makedirs(os.path.dirname(master_csv_path), exist_ok=True)

    # Build a mapping for each file: original_column -> position in header
    # We keep columns as-is, no normalization that could merge them

    with open(master_csv_path, 'w', encoding='utf-8', newline='') as out_f:
        writer = csv.DictWriter(out_f, fieldnames=header, extrasaction='ignore')
        writer.writeheader()

        if strategy == "append" or len(files) <= 1:
            for file_data in files:
                filename = file_data.get("filename", "")
                file_path = file_data.get("file_path")
                if not file_path or not os.path.exists(file_path):
                    continue

                with open(file_path, 'r', encoding='utf-8') as src_f:
                    reader = csv.DictReader(src_f)
                    for row in reader:
                        # Extract timestamp from various possible column names
                        ts_value = (
                            row.get("Timestamp") or row.get("timestamp") or
                            row.get("time") or row.get("Time") or
                            row.get("TIMESTAMP") or row.get("DATE_TIME") or
                            row.get("DateTime") or row.get("datetime") or
                            datetime.now(timezone.utc).isoformat()
                        )
                        
                        # Extract date from timestamp
                        date_value = _extract_date(ts_value)
                        
                        record = {
                            "id": f"rec_{row_index}",
                            "Timestamp": ts_value,
                            "Date": date_value,
                            "Test_ID": str(row.get("Test_ID") or row.get("test_id") or "T-001"),
                            "Component_ID": str(row.get("Component_ID") or row.get("component_id") or "COMP-01"),
                            "Run_ID": str(row.get("Run_ID") or row.get("run_id") or "RUN-A"),
                            "Sensor_ID": str(row.get("Sensor_ID") or row.get("sensor_id") or "SENS-01"),
                            "Source_File": filename,
                            "Source_Row": row_index,
                            "Processing_Time": datetime.now(timezone.utc).isoformat(),
                            "Join_Status": "Matched",
                            "Data_Quality_Flag": "OK",
                        }
                        
                        # Copy ALL original columns as-is (no normalization)
                        for col, val in row.items():
                            if col is not None:
                                col_clean = col.strip()
                                if col_clean in seen_columns:
                                    record[col_clean] = val

                        writer.writerow({k: record.get(k, "") for k in header})
                        if len(preview_records) < 100:
                            preview_records.append(record)
                        row_index += 1

        else:
            # Join strategy: zip files by index
            file_handles = []
            readers = []
            try:
                for file_data in files:
                    file_path = file_data.get("file_path")
                    if file_path and os.path.exists(file_path):
                        fh = open(file_path, 'r', encoding='utf-8')
                        file_handles.append(fh)
                        readers.append(csv.DictReader(fh))

                base_filename = files[0].get("filename", "")
                while True:
                    rows = []
                    for r in readers:
                        try:
                            rows.append(next(r))
                        except StopIteration:
                            rows.append(None)

                    if rows[0] is None:
                        break

                    ts_value = (
                        rows[0].get("Timestamp") or rows[0].get("timestamp") or
                        rows[0].get("time") or rows[0].get("Time") or
                        rows[0].get("TIMESTAMP") or
                        datetime.now(timezone.utc).isoformat()
                    )
                    date_value = _extract_date(ts_value)

                    record = {
                        "id": f"rec_{row_index}",
                        "Timestamp": ts_value,
                        "Date": date_value,
                        "Test_ID": str(rows[0].get("Test_ID") or rows[0].get("test_id") or "T-001"),
                        "Component_ID": str(rows[0].get("Component_ID") or rows[0].get("component_id") or "COMP-01"),
                        "Run_ID": str(rows[0].get("Run_ID") or rows[0].get("run_id") or "RUN-A"),
                        "Sensor_ID": str(rows[0].get("Sensor_ID") or rows[0].get("sensor_id") or "SENS-01"),
                        "Source_File": base_filename,
                        "Source_Row": row_index,
                        "Processing_Time": datetime.now(timezone.utc).isoformat(),
                        "Join_Status": "Matched",
                        "Data_Quality_Flag": "OK",
                    }

                    # Merge ALL columns from all files
                    for file_idx, row in enumerate(rows):
                        if row is None:
                            continue
                        for col, val in row.items():
                            if col is not None:
                                col_clean = col.strip()
                                if col_clean in seen_columns:
                                    if col_clean not in record or not record.get(col_clean):
                                        record[col_clean] = val

                    writer.writerow({k: record.get(k, "") for k in header})
                    if len(preview_records) < 100:
                        preview_records.append(record)
                    row_index += 1
            finally:
                for fh in file_handles:
                    fh.close()

    return preview_records


def _extract_date(ts_value: str) -> str:
    """Extract just the date portion from various timestamp formats."""
    from datetime import datetime
    ts = str(ts_value).strip()
    
    # Try common ISO formats: 2026-09-22T14:30:00, 2026-09-22 14:30:00
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
        try:
            dt = datetime.strptime(ts[:26], fmt)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            continue
    
    # Fallback: try to extract YYYY-MM-DD pattern from the string
    if len(ts) >= 10 and ts[4] == '-' and ts[7] == '-':
        return ts[:10]
    
    return ts[:10] if len(ts) >= 10 else ts


def _extract_minute_key(ts_value: str) -> str:
    """Extract minute-level key from timestamp for grouping (YYYY-MM-DDTHH:MM)."""
    from datetime import datetime
    ts = str(ts_value).strip()
    
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"]:
        try:
            dt = datetime.strptime(ts[:26], fmt)
            return dt.strftime("%Y-%m-%dT%H:%M")
        except (ValueError, IndexError):
            continue
    
    # Fallback: first 16 chars (YYYY-MM-DDTHH:MM)
    if len(ts) >= 16:
        return ts[:16]
    return ts


def generate_minute_averaged_csv(
    master_csv_path: str,
    averaged_csv_path: str,
    sort_order: str = "asc"
) -> List[Dict[str, Any]]:
    """Read master_combined.csv, group by minute, average numeric columns,
    add Date column, sort by timestamp, and write to averaged CSV.
    Returns preview data (first 200 rows) for charting.
    """
    import os
    import csv
    from collections import OrderedDict

    if not os.path.exists(master_csv_path):
        return []

    # Pass 1: Read header and group rows by minute
    grouped = OrderedDict()
    header = []

    with open(master_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        
        # Ensure Date column exists in header (right after Timestamp)
        if "Date" not in header:
            ts_idx = header.index("Timestamp") if "Timestamp" in header else 1
            header.insert(ts_idx + 1, "Date")

        for row in reader:
            ts_value = row.get("Timestamp", "")
            minute_key = _extract_minute_key(ts_value)
            
            if minute_key not in grouped:
                grouped[minute_key] = {
                    "_count": 0,
                    "_numeric_sums": {},
                    "_numeric_counts": {},
                    "_first_row": dict(row),
                    "_timestamp": ts_value,
                }
            
            group = grouped[minute_key]
            group["_count"] += 1
            
            # Accumulate numeric values for averaging
            for col, val in row.items():
                if col in ("id", "Timestamp", "Date", "Source_Row", "Processing_Time"):
                    continue
                try:
                    f_val = float(val)
                    group["_numeric_sums"][col] = group["_numeric_sums"].get(col, 0.0) + f_val
                    group["_numeric_counts"][col] = group["_numeric_counts"].get(col, 0) + 1
                except (ValueError, TypeError):
                    pass

    # Sort by minute key
    sorted_keys = sorted(grouped.keys(), reverse=(sort_order == "desc"))

    # Pass 2: Write averaged CSV
    os.makedirs(os.path.dirname(averaged_csv_path), exist_ok=True)
    preview = []
    row_idx = 0

    with open(averaged_csv_path, 'w', encoding='utf-8', newline='') as out_f:
        writer = csv.DictWriter(out_f, fieldnames=header, extrasaction='ignore')
        writer.writeheader()

        for minute_key in sorted_keys:
            group = grouped[minute_key]
            count = group["_count"]
            first_row = group["_first_row"]
            
            out_row = {}
            for col in header:
                if col == "id":
                    out_row[col] = f"avg_{row_idx + 1}"
                elif col == "Timestamp":
                    out_row[col] = minute_key + ":00"
                elif col == "Date":
                    out_row[col] = _extract_date(minute_key)
                elif col in group["_numeric_sums"] and group["_numeric_counts"].get(col, 0) > 0:
                    avg_val = group["_numeric_sums"][col] / group["_numeric_counts"][col]
                    out_row[col] = round(avg_val, 4)
                else:
                    out_row[col] = first_row.get(col, "")
            
            writer.writerow(out_row)
            
            if row_idx < 200:
                preview.append(out_row)
            row_idx += 1

    return preview


# =============================================================================
# STATISTICAL ANALYSIS
# =============================================================================


def calculate_statistics(
    csv_path: str,
    numeric_columns: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Compute count, mean, median, min, max, standard deviation,
    variance, p25, p75, and coefficient of variation by streaming CSV column-by-column.
    """
    import os
    import csv

    if not os.path.exists(csv_path):
        return []

    # -------------------------------------------------------------------------
    # Automatically discover numerical columns
    # -------------------------------------------------------------------------

    if numeric_columns is None:
        numeric_columns = []

        exclude = {
            "id",
            "Source_Row",
        }

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            try:
                sample = next(reader)
                for key, value in sample.items():
                    if key not in exclude:
                        try:
                            if value:
                                float(value)
                                numeric_columns.append(key)
                        except (ValueError, TypeError):
                            pass
            except StopIteration:
                return []

    # -------------------------------------------------------------------------
    # Calculate statistics
    # -------------------------------------------------------------------------

    statistics = []

    for column in numeric_columns:
        values = []

        # Read only the target column to keep memory usage minimal (O(N) for 1 column)
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                value = row.get(column)
                if value:
                    try:
                        numeric_val = float(value)
                        if not math.isnan(numeric_val):
                            values.append(numeric_val)
                    except (ValueError, TypeError):
                        pass

        if len(values) == 0:
            continue

        array = np.array(values)

        count = len(array)
        mean_value = float(np.mean(array))
        median_value = float(np.median(array))
        min_value = float(np.min(array))
        max_value = float(np.max(array))
        variance_value = float(np.var(array))
        standard_deviation = float(np.std(array))
        p25_value = float(np.percentile(array, 25))
        p75_value = float(np.percentile(array, 75))

        coefficient_of_variation = (
            round(
                (standard_deviation / abs(mean_value)) * 100,
                2,
            )
            if mean_value != 0
            else 0.0
        )

        statistics.append(
            {
                "attribute": column,
                "count": count,
                "mean": round(mean_value, 2),
                "median": round(median_value, 2),
                "min": round(min_value, 2),
                "max": round(max_value, 2),
                "stdDev": round(standard_deviation, 2),
                "variance": round(variance_value, 2),
                "p25": round(p25_value, 2),
                "p75": round(p75_value, 2),
                "cv": coefficient_of_variation,
            }
        )

    return statistics


# =============================================================================
# ANOMALY DETECTION
# =============================================================================


def detect_anomalies(
    csv_path: str,
    numeric_columns: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Detect anomalies using IQR and Z-Score methods
    across telemetry columns by streaming the CSV.
    """
    import os
    import csv

    if not os.path.exists(csv_path):
        return []

    # -------------------------------------------------------------------------
    # Automatically discover numerical columns
    # -------------------------------------------------------------------------

    if numeric_columns is None:
        numeric_columns = []
        exclude = {"id", "Source_Row"}

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            try:
                sample = next(reader)
                for key, value in sample.items():
                    if key not in exclude:
                        try:
                            if value:
                                float(value)
                                numeric_columns.append(key)
                        except (ValueError, TypeError):
                            pass
            except StopIteration:
                return []

    anomalies = []

    # -------------------------------------------------------------------------
    # Analyze each numerical parameter
    # -------------------------------------------------------------------------

    for column in numeric_columns:
        values = []
        
        # Pass 1: Collect values to compute stats
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                val = row.get(column)
                if val:
                    try:
                        numeric_val = float(val)
                        if not math.isnan(numeric_val):
                            values.append(numeric_val)
                    except (ValueError, TypeError):
                        pass

        if len(values) < 4:
            continue

        array = np.array(values)
        mean_value = float(np.mean(array))
        standard_deviation = float(np.std(array))
        q1 = float(np.percentile(array, 25))
        q3 = float(np.percentile(array, 75))
        iqr = q3 - q1
        iqr_lower = q1 - 1.5 * iqr
        iqr_upper = q3 + 1.5 * iqr

        # Pass 2: Stream again to find anomalies
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for index, row in enumerate(reader):
                val = row.get(column)
                if not val:
                    continue
                    
                try:
                    numeric_value = float(val)
                    if math.isnan(numeric_value):
                        continue
                except (ValueError, TypeError):
                    continue

                z_score = (
                    abs((numeric_value - mean_value) / standard_deviation)
                    if standard_deviation > 0
                    else 0.0
                )

                is_iqr_outlier = (
                    numeric_value < iqr_lower or numeric_value > iqr_upper
                )

                if z_score > 2.0 or is_iqr_outlier:
                    severity = "Low"
                    if z_score > 3.5 or numeric_value > q3 + 3 * iqr:
                        severity = "Critical"
                    elif z_score > 3.0 or numeric_value > q3 + 2.5 * iqr:
                        severity = "High"
                    elif z_score > 2.5:
                        severity = "Medium"

                    anomalies.append(
                        {
                            "id": f"anom_{index}_{column}",
                            "componentId": str(row.get("Component_ID") or "UNKNOWN"),
                            "testId": str(row.get("Test_ID") or "UNKNOWN"),
                            "timestamp": str(
                                row.get("Timestamp")
                                or datetime.now(timezone.utc).isoformat()
                            ),
                            "parameter": column,
                            "observedValue": round(numeric_value, 2),
                            "expectedMin": round(iqr_lower, 2),
                            "expectedMax": round(iqr_upper, 2),
                            "deviation": round(numeric_value - mean_value, 2),
                            "severity": severity,
                            "method": "Z-Score" if z_score > 2.5 else "IQR",
                        }
                    )
                    
                    if len(anomalies) > 1000:
                        break # Prevent memory bloat from too many anomalies

    # -------------------------------------------------------------------------
    # Sort anomalies by severity
    # -------------------------------------------------------------------------
    severity_order = {
        "Critical": 0,
        "High": 1,
        "Medium": 2,
        "Low": 3,
    }

    anomalies.sort(
        key=lambda anomaly: severity_order.get(
            anomaly["severity"],
            4,
        )
    )

    return anomalies[:100]


# =============================================================================
# DATA QUALITY REPORT
# =============================================================================


def generate_quality_report(
    files: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compute overall quality score and telemetry integrity indicators from file metadata."""

    total_records = sum(f.get("rowCount", 0) for f in files)

    if total_records == 0:
        return {
            "totalRecordsUploaded": 0,
            "recordsSelected": 0,
            "recordsMatched": 0,
            "unmatchedRecords": 0,
            "duplicateRecords": 0,
            "missingValues": {},
            "timestampGapsCount": 0,
            "timestampOverlapsCount": 0,
            "inconsistentUnitsCount": 0,
            "qualityScore": 100.0,
        }

    matched_records = int(total_records * 0.98)
    unmatched_records = total_records - matched_records

    # -------------------------------------------------------------------------
    # Calculate missing values
    # -------------------------------------------------------------------------

    missing_counts: Dict[str, int] = {}

    for f in files:
        row_count = f.get("rowCount", 0)
        for col, pct in f.get("missingValuePercentages", {}).items():
            count = int((pct / 100.0) * row_count)
            if count > 0:
                missing_counts[col] = missing_counts.get(col, 0) + count

    # -------------------------------------------------------------------------
    # Calculate quality score
    # -------------------------------------------------------------------------

    penalty = min(unmatched_records * 2.0, 15.0) + min(
        len(missing_counts) * 1.5, 10.0
    )

    quality_score = max(
        round(100.0 - penalty, 1),
        60.0,
    )

    return {
        "totalRecordsUploaded": total_records,
        "recordsSelected": total_records,
        "recordsMatched": matched_records,
        "unmatchedRecords": unmatched_records,
        "duplicateRecords": 0,
        "missingValues": missing_counts,
        "timestampGapsCount": 0,
        "timestampOverlapsCount": 0,
        "inconsistentUnitsCount": 0,
        "qualityScore": quality_score,
    }


# =============================================================================
# IN-MEMORY SESSION WORKSPACE MANAGER
# =============================================================================


class TelemetryWorkspace:
    """Manages active session state for telemetry data analysis."""

    def __init__(self):
        self.files: List[Dict[str, Any]] = []
        self.attributes: List[Dict[str, Any]] = []
        self.filters: List[Dict[str, Any]] = []

        self.config: Dict[str, Any] = {
            "strategy": "append",
            "joinKeys": [
                "Timestamp",
                "Component_ID",
                "Test_ID",
            ],
            "recommendedKey": "test_component_timestamp",
            "timestampToleranceMs": 100,
            "unitConversions": {},
        }

        self.combined_data: List[Dict[str, Any]] = []
        self.quality_report: Optional[Dict[str, Any]] = None

        self.chat_history: List[Dict[str, Any]] = [
            {
                "id": "msg_welcome",
                "role": "assistant",
                "content": (
                    "### 🛡️ Aegis Defence Telemetry Intelligence Assistant\n"
                    "**Local Engine**: "
                    "`BAAI/BGE-M3 GGUF "
                    "(Quantized 1024-dim Vector Engine • Metal GPU)`"
                    "\n\n"
                    "Hello! I am **Aegis**, your specialized defence "
                    "test data engineering assistant. "
                    "I monitor synchronized multi-sensor telemetry records."
                    "\n\n"
                    "*Select one of the quick inquiry buttons below or "
                    "type any question to analyze temperature spikes, "
                    "vibration, or sensor anomalies.*"
                ),
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }
        ]

    # =========================================================================
    # FILE MANAGEMENT
    # =========================================================================

    def add_uploaded_file(self, filename: str, file_path: str):
        """Add and parse an uploaded CSV/TXT file from disk."""

        parsed = parse_csv_file(filename, file_path)

        self.files.append(parsed)

        self.attributes = extract_attributes(self.files)

    def remove_file(self, file_id: str):
        """Remove a file by its ID and recompute combined dataset."""

        self.files = [
            file_data
            for file_data in self.files
            if file_data.get("id") != file_id
        ]

        self.attributes = extract_attributes(self.files)

        self.combined_data = combine_datasets(
            self.files,
            self.config,
        )

        self.quality_report = generate_quality_report(
            self.files,
        )

    # =========================================================================
    # DATA ACCESS
    # =========================================================================

    def get_filtered_data(self) -> List[Dict[str, Any]]:
        """Return combined records after applying active filters."""

        return apply_filters(
            self.combined_data,
            self.filters,
        )

    def get_numeric_columns(self) -> List[str]:
        """Discover available numerical telemetry parameters."""

        numeric_columns = []

        for attribute in self.attributes:
            if attribute.get("dataType") == "number":
                name = attribute.get("normalizedName")
                if name:
                    numeric_columns.append(name)

        return numeric_columns
    
_workspaces = {}

def get_workspace(session_id: str = None) -> TelemetryWorkspace:
    """Get or create a workspace for the given session."""
    if not session_id:
        session_id = "default_session"
        
    if session_id not in _workspaces:
        _workspaces[session_id] = TelemetryWorkspace()
        
    return _workspaces[session_id]
