import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import requests

# Set up logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================================================
# 1. Configuration Constants
# =====================================================================

# Standardized default URL without typo prefix
OLLAMA_BASE_URL = os.environ.get(
    "OLLAMA_BASE_URL", "http://172.16.122.51:11434"
).rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:latest")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "3"))

# Cache for pseudo-embedding generation
_embedding_cache: Dict[str, np.ndarray] = {}


# =====================================================================
# 2. Low-Level API & Connectivity Tools
# =====================================================================


def call_ollama_api(prompt: str, model: Optional[str] = None) -> Optional[str]:
    """Call the Ollama generate endpoint to produce an LLM response."""
    model_name = model or OLLAMA_MODEL
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 4000,
        },
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=OLLAMA_TIMEOUT,
        )

        if response.status_code == 200:
            result = response.json()
            return result.get("response", "")

        logger.error(f"Ollama API error: {response.status_code} - {response.text}")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Request error calling Ollama API: {e}")
        return None
    except Exception as e:
        logger.error(f"Error calling Ollama API: {e}")
        return None


def get_model_status() -> Dict[str, Any]:
    """Check connectivity to the Ollama server and verify model availability."""
    status_info: Dict[str, Any] = {
        "status": "unknown",
        "ollama_url": OLLAMA_BASE_URL,
        "model_name": OLLAMA_MODEL,
        "error": None,
    }

    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        if resp.status_code == 200:
            tags = resp.json()
            available_models = [
                m.get("name", "") for m in tags.get("models", [])
            ]
            status_info["status"] = "ready"
            status_info["available_models"] = available_models
            status_info["model_available"] = any(
                OLLAMA_MODEL in m for m in available_models
            )
        else:
            status_info["status"] = "api_error"
            status_info["error"] = f"HTTP {resp.status_code}"

    except requests.exceptions.RequestException as e:
        status_info["status"] = "unreachable"
        status_info["error"] = str(e)

    return status_info


# =====================================================================
# 3. Vector & Fallback Search Utilities
# =====================================================================


def compute_embedding(text: str) -> np.ndarray:
    """Compute a deterministic pseudo-embedding vector using hash-seeded random generation."""
    clean_text = text.strip() if text else "empty"

    if clean_text in _embedding_cache:
        return _embedding_cache[clean_text]

    np.random.seed(abs(hash(clean_text)) % (2**32))
    vec = np.random.randn(1024).astype(np.float32)
    normalized_vec = vec / np.linalg.norm(vec)

    _embedding_cache[clean_text] = normalized_vec
    return normalized_vec


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Compute cosine similarity score between two normalized vectors."""
    dot = np.dot(vec1, vec2)
    norm = np.linalg.norm(vec1) * np.linalg.norm(vec2)
    return float(dot / norm) if norm > 0 else 0.0


def rank_items_by_relevance(
    query: str,
    items: List[Dict[str, Any]],
    text_fn,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """Rank item dictionaries by pseudo-semantic similarity to the query string."""
    if not items:
        return []

    candidate_items = items[:max_items]
    query_vec = compute_embedding(query)
    ranked = []

    for item in candidate_items:
        text = text_fn(item)
        item_vec = compute_embedding(text)
        sim = cosine_similarity(query_vec, item_vec)
        ranked.append({**item, "_similarity_score": round(sim, 4)})

    ranked.sort(key=lambda x: x["_similarity_score"], reverse=True)
    return ranked


# =====================================================================
# 4. Context Extraction Helpers
# =====================================================================


def _build_telemetry_summary(context_data: List[Dict[str, Any]]) -> str:
    """Build a structured text summary from raw telemetry records for prompt context."""
    if not context_data:
        return "No telemetry data is currently loaded."

    components = sorted(
        {
            str(r.get("Component_ID"))
            for r in context_data
            if r.get("Component_ID")
        }
    )
    tests = sorted(
        {str(r.get("Test_ID")) for r in context_data if r.get("Test_ID")}
    )
    files = sorted(
        {
            str(r.get("Source_File"))
            for r in context_data
            if r.get("Source_File")
        }
    )

    def _stats(keys: List[str]) -> Optional[Dict[str, float]]:
        vals = []
        for r in context_data:
            for k in keys:
                v = r.get(k)
                if v is not None and isinstance(v, (int, float)):
                    vals.append(float(v))
                    break
        if not vals:
            return None
        return {
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "avg": round(sum(vals) / len(vals), 2),
            "count": len(vals),
        }

    temp_s = _stats(["Temperature", "Temperature_C", "Temp"])
    vib_s = _stats(["Vibration", "Vib_Level", "Vib"])
    volt_s = _stats(["Voltage", "Voltage_V", "Volt"])
    press_s = _stats(["Pressure", "Pressure_kPa", "Pressure_PSI"])
    curr_s = _stats(["Current_A", "Current"])
    power_s = _stats(["Power_kW", "Power"])

    critical_count = sum(
        1
        for r in context_data
        if str(r.get("Status", "")).upper() in ("CRITICAL", "FAILURE", "FAIL")
    )
    warning_count = sum(
        1
        for r in context_data
        if str(r.get("Status", "")).upper() in ("WARNING", "ELEVATED")
    )

    lines = [
        f"Records: {len(context_data)} | Files: {len(files)} | "
        f"Components: {', '.join(components)} | Tests: {', '.join(tests)}",
    ]

    if temp_s:
        lines.append(
            f"Temperature(°C): min={temp_s['min']}, max={temp_s['max']}, avg={temp_s['avg']}"
        )
    if vib_s:
        lines.append(
            f"Vibration(g): min={vib_s['min']}, max={vib_s['max']}, avg={vib_s['avg']}"
        )
    if volt_s:
        lines.append(
            f"Voltage(V): min={volt_s['min']}, max={volt_s['max']}, avg={volt_s['avg']}"
        )
    if press_s:
        lines.append(
            f"Pressure(kPa): min={press_s['min']}, max={press_s['max']}, avg={press_s['avg']}"
        )
    if curr_s:
        lines.append(
            f"Current(A): min={curr_s['min']}, max={curr_s['max']}, avg={curr_s['avg']}"
        )
    if power_s:
        lines.append(
            f"Power(kW): min={power_s['min']}, max={power_s['max']}, avg={power_s['avg']}"
        )
    if critical_count or warning_count:
        lines.append(
            f"Anomalies: {critical_count} Critical, {warning_count} Warning"
        )

    # Attach sample rows for grounding
    sample_rows = context_data[:5]
    if sample_rows:
        lines.append("Sample records:")
        for r in sample_rows:
            parts = []
            for k in [
                "Test_ID",
                "Component_ID",
                "Timestamp",
                "Temperature",
                "Vibration",
                "Voltage",
                "Pressure",
                "Current_A",
                "Power_kW",
                "Status",
            ]:
                v = r.get(k)
                if v is not None:
                    parts.append(f"{k}={v}")
            lines.append("  " + ", ".join(parts))

    return "\n".join(lines)


# =====================================================================
# 5. Engineering Report Generation
# =====================================================================


def _fallback_static_analysis(
    prompt: str,
    dataset_summary: Dict[str, Any],
    statistical_summary: List[Dict[str, Any]],
    anomalies_summary: List[Dict[str, Any]],
    total_records: int,
    critical_count: int,
    high_count: int,
) -> str:
    """Generate a static markdown fallback report when the API is unavailable."""
    report_lines = [
        "### 🛡️ Defence Engineering Telemetry Analysis Report",
        "**Engine**: Ollama API (Offline — Fallback Mode)",
        f'**Query Focus**: *"{prompt}"*',
        "",
        "#### 1. Executive Telemetry Overview",
        f"- **Dataset Volume**: {total_records} records with {len(anomalies_summary)} anomalies "
        f"({critical_count} Critical, {high_count} High).",
        "",
        "#### 2. Priority Telemetry Indicators",
    ]

    for stat in statistical_summary[:4]:
        report_lines.append(
            f"- **{stat.get('attribute')}**: "
            f"Mean: `{stat.get('mean')}`, Max: `{stat.get('max')}`, "
            f"Min: `{stat.get('min')}`, StdDev: `{stat.get('stdDev')}`, "
            f"CV: `{stat.get('cv')}%`"
        )

    report_lines.append("\n#### 3. Traceable Anomaly Detections")
    if anomalies_summary:
        report_lines.append(
            "| Test ID | Component ID | Parameter | Observed | Expected Range | Deviation | Severity |"
        )
        report_lines.append(
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        )
        for a in anomalies_summary[:6]:
            exp_range = (
                f"[{a.get('expectedMin', '—')} - {a.get('expectedMax', '—')}]"
            )
            report_lines.append(
                f"| `{a.get('testId', 'N/A')}` | `{a.get('componentId', 'N/A')}` | "
                f"**{a.get('parameter', 'N/A')}** | `{a.get('observedValue', 'N/A')}` | "
                f"{exp_range} | `{a.get('deviation', 'N/A')}` | **{a.get('severity', 'Low')}** |"
            )
    else:
        report_lines.append("No anomalies detected.")

    report_lines.extend(
        [
            "",
            "#### 4. Engineering Recommendations",
            "1. Schedule non-destructive testing (NDT) on components with critical deviations.",
            "2. Verify sensor baseline calibration where CV exceeds normal operating envelopes.",
            "3. Correlate peak load durations against secondary test logs.",
            "",
            "> ⚠️ *This is a fallback report. Ollama API was unreachable for full AI analysis.*",
        ]
    )

    return "\n".join(report_lines)


def synthesize_engineering_analysis(
    prompt: str,
    dataset_summary: Dict[str, Any],
    statistical_summary: List[Dict[str, Any]],
    anomalies_summary: List[Dict[str, Any]],
) -> str:
    """Generate an engineering telemetry report using Ollama, or fallback if offline."""
    total_records = dataset_summary.get("totalRecords", 0)
    critical_count = sum(
        1 for a in anomalies_summary if a.get("severity") == "Critical"
    )
    high_count = sum(
        1 for a in anomalies_summary if a.get("severity") == "High"
    )

    # Build statistical context
    stats_text = ""
    for s in statistical_summary[:8]:
        stats_text += (
            f"  - {s.get('attribute')}: mean={s.get('mean')}, max={s.get('max')}, "
            f"min={s.get('min')}, stdDev={s.get('stdDev')}, CV={s.get('cv')}%\n"
        )

    # Build anomalies context
    anomalies_text = ""
    for a in anomalies_summary[:10]:
        anomalies_text += (
            f"  - Test {a.get('testId')}, Component {a.get('componentId')}, "
            f"Parameter {a.get('parameter')}: observed={a.get('observedValue')}, "
            f"expected=[{a.get('expectedMin')} - {a.get('expectedMax')}], "
            f"deviation={a.get('deviation')}, severity={a.get('severity')}, "
            f"method={a.get('method')}\n"
        )

    llm_prompt = f"""You are Aegis, a defence test data engineering AI assistant.
Generate a detailed engineering telemetry analysis report based on the data below.

USER QUERY: {prompt}

DATASET OVERVIEW:
- Total records: {total_records}
- Total anomalies: {len(anomalies_summary)} ({critical_count} Critical, {high_count} High)
- Dataset summary: {dataset_summary}

STATISTICAL SUMMARY:
{stats_text if stats_text else '  No statistical data available.'}

ANOMALY DETECTIONS:
{anomalies_text if anomalies_text else '  No anomalies detected.'}

FORMAT YOUR RESPONSE AS A STRUCTURED MARKDOWN REPORT WITH:
1. Executive Telemetry Overview
2. Priority Telemetry Indicators (reference the statistics)
3. Traceable Anomaly Detections (use a markdown table with columns: Test ID, Component ID, Parameter, Observed, Expected Range, Deviation, Severity)
4. Engineering Recommendations & Operational Mitigations

Start with: ### 🛡️ Defence Engineering Telemetry Analysis Report
Include: **Engine**: Ollama llama3.1 API
Include: **Query Focus**: "{prompt}"
"""

    result = call_ollama_api(llm_prompt)
    if result:
        return result

    logger.warning("Ollama API unreachable — generating fallback static report.")
    return _fallback_static_analysis(
        prompt,
        dataset_summary,
        statistical_summary,
        anomalies_summary,
        total_records,
        critical_count,
        high_count,
    )


# =====================================================================
# 6. Conversational Interface (Chat)
# =====================================================================


def synthesize_chat_response(
    messages: List[Dict[str, str]],
    context_data: List[Dict[str, Any]],
) -> str:
    """Synthesize a interactive chat response using Ollama API or smart fallback."""
    last_query = (
        messages[-1].get("content", "").strip() if messages else "Summary"
    )
    telemetry_summary = _build_telemetry_summary(context_data)

    # Build prompt history
    history_text = ""
    recent = [m for m in messages if m.get("role") in ("user", "assistant")]
    for m in recent[-6:]:
        role_label = "User" if m["role"] == "user" else "Aegis"
        history_text += f"{role_label}: {m['content']}\n"

    llm_prompt = f"""You are Aegis, an AI assistant for defence test data engineering.
You help engineers analyze telemetry data from multi-sensor test runs.
Answer the user's question directly and concisely.
If the question is conversational (greetings, how are you, etc.), respond naturally and briefly.
If the question is about telemetry data, use ONLY the data provided below — never invent numbers.
Keep answers short (2-4 sentences for simple questions, more detail only when explicitly asked).

TELEMETRY DATA:
{telemetry_summary}

CONVERSATION HISTORY:
{history_text}

User: {last_query}
Aegis:"""

    result = call_ollama_api(llm_prompt)
    if result:
        return result

    # =========================================================================
    # SMART FALLBACK — Analyze data locally without LLM
    # =========================================================================
    logger.warning("Ollama API unreachable — using smart local analysis fallback.")

    query_lower = last_query.lower()

    # --- Conversational greetings ---
    greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "howdy", "sup"]
    if any(g == query_lower or query_lower.startswith(g + " ") or query_lower.startswith(g + ",") for g in greetings):
        record_info = f" I currently have **{len(context_data)} telemetry records** loaded and ready for analysis." if context_data else ""
        return (
            f"Hello! 👋 I'm **Aegis**, your defence telemetry intelligence assistant.{record_info}\n\n"
            f"Try asking me about:\n"
            f"- `statistics` — View statistical summary of all parameters\n"
            f"- `anomalies` — Detect outliers in your data\n"
            f"- `components` — See component breakdown\n"
            f"- `summary` — Get a full dataset overview"
        )

    if not context_data:
        return (
            "📭 No telemetry records are currently loaded in the workspace.\n\n"
            "Please **upload CSV files** first using the Files tab, then run **Combination** to populate the dataset."
        )

    # --- Build local statistics ---
    import math
    numeric_cols = {}
    string_cols = {}
    for record in context_data:
        for k, v in record.items():
            if k in ("id", "Source_Row", "Processing_Time", "Join_Status", "_similarity_score"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v):
                numeric_cols.setdefault(k, []).append(float(v))
            elif isinstance(v, str) and v:
                string_cols.setdefault(k, set()).add(v)

    total = len(context_data)

    # --- Statistics query ---
    if any(w in query_lower for w in ["statistic", "stats", "mean", "average", "min", "max", "summary", "overview"]):
        lines = [f"### 📊 Telemetry Statistical Summary\n**Total Records**: {total}\n"]
        lines.append("| Parameter | Count | Mean | Min | Max | Std Dev |")
        lines.append("|-----------|-------|------|-----|-----|---------|")
        for col, vals in sorted(numeric_cols.items()):
            arr = np.array(vals)
            lines.append(
                f"| `{col}` | {len(arr)} | {np.mean(arr):.2f} | {np.min(arr):.2f} | {np.max(arr):.2f} | {np.std(arr):.2f} |"
            )
        if string_cols:
            lines.append(f"\n**Categorical Fields**: {', '.join(f'`{k}` ({len(v)} unique)' for k, v in sorted(string_cols.items()) if len(v) <= 20)}")
        return "\n".join(lines)

    # --- Anomaly query ---
    if any(w in query_lower for w in ["anomal", "outlier", "spike", "abnormal", "deviation"]):
        anomalies_found = []
        for col, vals in numeric_cols.items():
            if len(vals) < 4:
                continue
            arr = np.array(vals)
            q1, q3 = float(np.percentile(arr, 25)), float(np.percentile(arr, 75))
            iqr = q3 - q1
            upper = q3 + 1.5 * iqr
            lower = q1 - 1.5 * iqr
            outlier_count = int(np.sum((arr > upper) | (arr < lower)))
            if outlier_count > 0:
                anomalies_found.append((col, outlier_count, f"{lower:.2f}", f"{upper:.2f}"))

        if not anomalies_found:
            return "✅ **No anomalies detected.** All numeric parameters are within expected IQR bounds across the loaded dataset."

        lines = [f"### ⚠️ Anomaly Detection Report\n**Records Scanned**: {total}\n"]
        lines.append("| Parameter | Outlier Count | Expected Range |")
        lines.append("|-----------|--------------|----------------|")
        for col, count, lo, hi in sorted(anomalies_found, key=lambda x: -x[1]):
            lines.append(f"| `{col}` | **{count}** | {lo} – {hi} |")
        lines.append(f"\n**Total anomalous readings**: {sum(x[1] for x in anomalies_found)}")
        return "\n".join(lines)

    # --- Component query ---
    if any(w in query_lower for w in ["component", "comp", "part", "module"]):
        comp_map = {}
        for r in context_data:
            cid = r.get("Component_ID") or r.get("component_id") or "Unknown"
            comp_map.setdefault(cid, 0)
            comp_map[cid] += 1
        lines = [f"### 🔧 Component Breakdown\n**Total Records**: {total}\n"]
        lines.append("| Component ID | Records | Share |")
        lines.append("|-------------|---------|-------|")
        for cid, count in sorted(comp_map.items(), key=lambda x: -x[1]):
            pct = (count / total * 100) if total > 0 else 0
            lines.append(f"| `{cid}` | {count} | {pct:.1f}% |")
        return "\n".join(lines)

    # --- Test query ---
    if any(w in query_lower for w in ["test", "run", "experiment"]):
        test_map = {}
        for r in context_data:
            tid = r.get("Test_ID") or r.get("test_id") or "Unknown"
            test_map.setdefault(tid, 0)
            test_map[tid] += 1
        lines = [f"### 🧪 Test Run Summary\n"]
        for tid, count in sorted(test_map.items()):
            lines.append(f"- **{tid}**: {count} records")
        return "\n".join(lines)

    # --- Specific parameter query ---
    for col, vals in numeric_cols.items():
        if col.lower() in query_lower or col.lower().replace("_", " ") in query_lower:
            arr = np.array(vals)
            return (
                f"### 📈 `{col}` Parameter Analysis\n\n"
                f"| Metric | Value |\n|--------|-------|\n"
                f"| Count | {len(arr)} |\n"
                f"| Mean | {np.mean(arr):.4f} |\n"
                f"| Median | {np.median(arr):.4f} |\n"
                f"| Min | {np.min(arr):.4f} |\n"
                f"| Max | {np.max(arr):.4f} |\n"
                f"| Std Dev | {np.std(arr):.4f} |\n"
                f"| Range | {np.max(arr) - np.min(arr):.4f} |"
            )

    # --- Generic fallback with useful info ---
    param_list = ", ".join(f"`{k}`" for k in sorted(numeric_cols.keys())[:10])
    cat_list = ", ".join(f"`{k}`" for k in sorted(string_cols.keys())[:8])
    return (
        f"### 🛡️ Aegis Local Analysis Engine\n\n"
        f"**Dataset**: {total} records loaded\n"
        f"**Numeric Parameters**: {param_list}\n"
        f"**Categorical Fields**: {cat_list}\n\n"
        f"I'm running in **offline mode** (Ollama unavailable). I can still help with:\n\n"
        f"- Type `statistics` or `summary` for a full statistical report\n"
        f"- Type `anomalies` to detect outliers\n"
        f"- Type `components` to see component breakdown\n"
        f"- Type `tests` to see test run summary\n"
        f"- Type any parameter name (e.g. `temperature`) for detailed analysis\n\n"
        f"*Connect Ollama for natural language conversations.*"
    )