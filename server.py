#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Defence Test Data Analyzer — Offline Telemetry Graph Studio
Single-file, zero-dependency (Python stdlib only), completely offline server.

What it does:
  1. Accepts ONE CSV file (Single File tab) or MANY CSV files (Multiple Files
     tab) via multipart upload.
  2. Parses defence telemetry datetime formats (`6:6:2026 12:30:49:694`,
     `25-02-2026 10:02:24:254`, ISO), FastLog headers and generic CSVs.
  3. Keeps every uploaded file in an in-memory session; the Multiple Files tab
     combines them all chronologically.
  4. Returns graph data for ANY number of selected attributes, sampled at a
     fixed interval of timestamps (default 5  ->  timestamp indices
     1, 6, 11, 16, ...) carrying the attribute value at every sampled
     timestamp, so the graph shows a visible point with its timestamp and
     value at each interval.

Frontend: ./index.html  (Chart.js 4 + date adapter bundled locally in ./static)
Run:      python3 server.py   ->   http://127.0.0.1:8001
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

# =============================================================================
# CONFIGURATION
# =============================================================================

PORT = int(os.environ.get("PORT", "8001"))
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024          # 100 MB
MAX_INTERVAL = 5000

# Professional palette for attribute series (dark UI friendly)
SERIES_COLORS = [
    "#818cf8", "#2dd4bf", "#fbbf24", "#fb7185", "#38bdf8", "#c084fc",
    "#fb923c", "#4ade80", "#f472b6", "#60a5fa", "#5eead4", "#e879f9",
    "#f87171", "#a3e635",
]

SESSIONS: Dict[str, Dict[str, Any]] = {}
LOCK = threading.Lock()

# =============================================================================
# CSV & TELEMETRY PARSING
# =============================================================================

DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
)

RE_A = re.compile(
    r'^(\d{1,2}):(\d{1,2}):(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2}):(\d+)$')   # D:M:YYYY HH:MM:SS:ms
RE_B = re.compile(
    r'^(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2}):(\d+)$')   # DD-MM-YYYY HH:MM:SS:ms


def parse_defence_datetime(ts_str: Any) -> Optional[datetime]:
    """Parse defence datetime formats (with millisecond field) and ISO fallbacks."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    s = ts_str.strip()

    m = RE_A.match(s)
    if m:
        return datetime(
            int(m.group(3)), int(m.group(2)), int(m.group(1)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
            int(m.group(7)[:6].ljust(6, "0")[:6]))

    m = RE_B.match(s)
    if m:
        return datetime(
            int(m.group(3)), int(m.group(2)), int(m.group(1)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
            int(m.group(7)[:6].ljust(6, "0")[:6]))

    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def parse_csv_text(raw_text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """Parse a CSV tolerant of FastLog markers, blank meta lines and BOMs."""
    lines = [ln.rstrip("\r") for ln in raw_text.lstrip("\ufeff").split("\n")]

    header_line, data_start = 0, 1
    for i, line in enumerate(lines[:100]):
        if line.strip() == "FastLog":
            header_line, data_start = i + 1, i + 2
            break

    raw_headers = [h.strip() for h in lines[header_line].split(",")]
    headers, seen = [], set()
    for idx, h in enumerate(raw_headers):
        if not h:
            h = f"Column_{idx + 1}"
        base, n = h, 2
        while h in seen:
            h = f"{base}_{n}"
            n += 1
        seen.add(h)
        headers.append(h)

    records: List[Dict[str, str]] = []
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or set(line) == {","}:
            continue
        values = line.split(",")
        records.append({h: values[j].strip() if j < len(values) else ""
                        for j, h in enumerate(headers)})
    return headers, records


def is_numeric(val: Any) -> bool:
    if val is None or val == "" or str(val).strip() in ("N.A.", "NA", "nan", "None"):
        return False
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def detect_datetime_column(headers: List[str], records: List[Dict[str, str]]) -> str:
    for h in headers:
        for r in records[:10]:
            if parse_defence_datetime(r.get(h, "")):
                return h
    for h in headers:
        if "date" in h.lower() or "time" in h.lower():
            return h
    return headers[0] if headers else ""


def classify_numeric_columns(headers: List[str], dt_col: str,
                              records: List[Dict[str, str]]) -> List[str]:
    """A column is numeric if >=60% of a sample (up to 300 rows) parses as float."""
    sample = records[:300]
    num_cols = []
    for h in headers:
        if h == dt_col:
            continue
        hits = sum(1 for r in sample if is_numeric(r.get(h, "")))
        if sample and hits / len(sample) >= 0.6:
            num_cols.append(h)
    return num_cols

# =============================================================================
# SESSION MANAGEMENT
# =============================================================================


def get_session(session_id: str) -> Dict[str, Any]:
    if not session_id:
        session_id = uuid.uuid4().hex[:16]
    with LOCK:
        if session_id not in SESSIONS:
            SESSIONS[session_id] = {"files": {}, "order": [],
                                    "_sid": session_id, "created": time.time()}
            if len(SESSIONS) > 500:                     # opportunistic GC
                stale = [k for k, v in SESSIONS.items()
                         if time.time() - v["created"] > 86400]
                for k in stale:
                    SESSIONS.pop(k, None)
        return SESSIONS[session_id]


def files_payload(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for fid in session["order"]:
        f = session["files"][fid]
        out.append({
            "id": fid,
            "filename": f["filename"],
            "rows": len(f["rows"]),
            "cols": len(f["headers"]),
            "size": f["size"],
            "dt_column": f["dt_col"],
            "numeric_cols": f["numeric_cols"],
        })
    return out


def union_numeric(session: Dict[str, Any]) -> List[str]:
    cols = set()
    for fid in session["order"]:
        cols.update(session["files"][fid]["numeric_cols"])
    return sorted(cols)


def process_file(session: Dict[str, Any], filename: str, raw_text: str,
                 size: int) -> Tuple[Dict[str, Any], Optional[str]]:
    """Parse one CSV into the session. Returns (meta, error)."""
    try:
        headers, records = parse_csv_text(raw_text)
        if not records:
            return {"filename": filename, "error": "No data rows found"}, None

        dt_col = detect_datetime_column(headers, records)
        dts = [parse_defence_datetime(r.get(dt_col, "")) for r in records]
        numeric_cols = classify_numeric_columns(headers, dt_col, records)

        fid = f"file_{uuid.uuid4().hex[:8]}"
        safe = re.sub(r"[^A-Za-z0-9._\- ]", "_", filename) or "upload.csv"
        seq = len(session["order"]) + 1
        dest = UPLOAD_DIR / f"{session.get('_sid', 'sess')}_{seq:02d}_{safe}"
        try:
            dest.write_text(raw_text, encoding="utf-8", errors="replace")
        except OSError:
            pass

        session["files"][fid] = {
            "id": fid, "filename": filename, "size": size,
            "headers": headers, "rows": records, "dts": dts,
            "dt_col": dt_col or "", "numeric_cols": numeric_cols,
            "created": time.time(),
        }
        session["order"].append(fid)

        return {
            "id": fid, "filename": filename, "rows": len(records),
            "cols": len(headers), "size": size,
            "dt_column": dt_col, "numeric_cols": numeric_cols,
        }, None
    except Exception as exc:                                   # noqa: BLE001
        return {"filename": filename, "error": f"Parse failed: {exc}"}, exc

# =============================================================================
# GRAPH DATA  —  interval sampling (points on every Nth timestamp)
# =============================================================================


def fmt_full(dt: Optional[datetime]) -> str:
    return dt.strftime("%d %b %Y %H:%M:%S") if dt else ""


def build_graph(session: Dict[str, Any], file_ids: List[str],
                attributes: List[str], interval: Any,
                full: bool = False) -> Dict[str, Any]:
    if not session["order"]:
        return {"error": "No files uploaded yet."}

    file_ids = [f for f in (file_ids or session["order"]) if f in session["files"]]
    if not file_ids:
        return {"error": "Selected files not found in this session."}

    try:
        interval = max(1, min(int(interval or 5), MAX_INTERVAL))
    except (TypeError, ValueError):
        return {"error": "Interval must be a number."}

    # Full mode: every timestamp on the x-axis (no sampling) → step = 1
    step = 1 if full else interval

    numeric_union = set()
    for fid in file_ids:
        numeric_union.update(session["files"][fid]["numeric_cols"])
    if not numeric_union:
        return {"error": "No numeric attribute columns found in these files."}

    wanted = [a for a in (attributes or []) if a in numeric_union]
    if not wanted:
        return {"error": "No valid numeric attributes selected.",
                "available": sorted(numeric_union)}

    # ---- merge every selected file into one chronological row stream ----
    merged: List[Tuple[Optional[datetime], str, str, Dict[str, str]]] = []
    for fid in file_ids:
        f = session["files"][fid]
        col = f["dt_col"]
        for i, row in enumerate(f["rows"]):
            merged.append((f["dts"][i], f["filename"], col, row))
    merged.sort(key=lambda t: (t[0] is None, t[0] or datetime.min))
    total = len(merged)

    # ---- sample every Nth timestamp (1-based: 1, N+1, 2N+1, …) or all ----
    points: List[Dict[str, Any]] = []
    for i in range(0, total, step):
        dt, src, dt_col, row = merged[i]
        values = {}
        for a in wanted:
            raw = row.get(a, "")
            values[a] = round(float(raw), 6) if is_numeric(raw) else None
        ts_raw = row.get(dt_col, "") if dt_col else ""
        pts = {
            "i": i + 1,                       # 1-based timestamp index
            "ts_full": ts_raw or fmt_full(dt),
            "ts_short": (dt.strftime("%d %b %H:%M:%S") if dt else f"#{i + 1}"),
            "values": values,
        }
        if not full:
            pts["ts"] = datetime.strftime(dt, "%d %b %Y") if dt else ""
        if len(file_ids) > 1:
            pts["src"] = src                  # only present when combined
        points.append(pts)

    # ---- full-resolution statistics per attribute ----
    series = []
    for a in wanted:
        vals = []
        for _, _, _, row in merged:
            raw = row.get(a, "")
            if is_numeric(raw):
                vals.append(float(raw))
        stats = {"count": len(vals), "min": None, "max": None,
                 "mean": None, "std": None}
        if vals:
            n = len(vals)
            mean = sum(vals) / n
            stats.update({
                "min": min(vals), "max": max(vals), "mean": mean,
                "std": (sum((x - mean) ** 2 for x in vals) / n) ** 0.5 if n > 1 else 0.0,
            })
        series.append({
            "attribute": a,
            "color": SERIES_COLORS[len(series) % len(SERIES_COLORS)],
            **stats,
        })

    # ---- overlay scaling strategy ----
    spans = [s["max"] - s["min"] for s in series if s["max"] is not None]
    if len(spans) >= 2 and max(spans) > 0:
        ratio = max(spans) / max(min(spans), 1e-12)
        overlay_mode = "shared" if ratio <= 8 else "independent"
    else:
        overlay_mode = "shared"

    dts_valid = [t[0] for t in merged if t[0]]
    multi_day = len({d.date() for d in dts_valid}) > 1 if dts_valid else False

    return {
        "full": bool(full),
        "sampling": "all-timestamps" if full else "interval",
        "interval": interval,
        "total_rows": total,
        "sampled_points": len(points),
        "files": [{"id": fid, "filename": session["files"][fid]["filename"],
                   "rows": len(session["files"][fid]["rows"])} for fid in file_ids],
        "attributes": series,
        "points": points,
        "overlay_mode": overlay_mode,
        "multi_day": multi_day,
        "time_span": {
            "first": fmt_full(dts_valid[0]) if dts_valid else "",
            "last": fmt_full(dts_valid[-1]) if dts_valid else "",
        },
    }

# =============================================================================
# HTTP HANDLER
# =============================================================================


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DefenceGraphStudio/3.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    # ----------------------------------------------------------- helpers --
    def log_message(self, fmt, *args):                          # noqa: A003
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ------------------------------------------------------------ routes --
    def do_OPTIONS(self):                                       # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):                                           # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path.startswith("/api/"):
            session = get_session(query.get("session", ["default"])[0])

            if path == "/api/health":
                self.send_json({"status": "ok",
                                "server": "Defence Test Data Analyzer",
                                "version": "3.0-graph",
                                "now": datetime.now().strftime("%d %b %Y %H:%M:%S")})
                return

            if path == "/api/files":
                self.send_json({"files": files_payload(session),
                                "numeric_union": union_numeric(session),
                                "count": len(session["order"])})
                return

            if path == "/api/preview":
                fid = query.get("file_id", [""])[0]
                f = session["files"].get(fid)
                if not f:
                    self.send_json({"error": "File not found"}, 404)
                    return
                self.send_json({
                    "filename": f["filename"], "dt_column": f["dt_col"],
                    "headers": f["headers"],
                    "rows": [r for r in f["rows"][:8]],
                    "total_rows": len(f["rows"]),
                })
                return

            self.send_json({"error": "Unknown endpoint"}, 404)
            return

        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):                                          # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        content_type = self.headers.get("Content-Type", "")
        if path.startswith("/api/"):
            if path == "/api/upload":
                self._handle_upload(content_type)
                return
            body = self.read_body()
            session = get_session(body.get("session_id", ""))

            if path == "/api/graph":
                result = build_graph(session,
                                     body.get("file_ids") or [],
                                     body.get("attributes") or [],
                                     body.get("interval", 5),
                                     bool(body.get("full", False)))
                self.send_json(result, 200 if "error" not in result else 400)
                return

            if path == "/api/reset":
                sid = session.get("_sid", "")
                with LOCK:
                    SESSIONS.pop(sid, None)
                    SESSIONS.pop("default", None)
                self.send_json({"ok": True})
                return

            self.send_json({"error": "Unknown endpoint"}, 404)
            return
        self.send_json({"error": "Not found"}, 404)

    # ------------------------------------------------------------ upload --
    def _handle_upload(self, content_type: str) -> None:
        if "multipart/form-data" not in content_type:
            self.send_json({"error": "Upload must be multipart/form-data"}, 400)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "Upload too large (100 MB max)"}, 413)
            return
        body = self.rfile.read(length) if length else b""

        m = re.search(r'boundary="?([^";]+)"?', content_type)
        if not m:
            self.send_json({"error": "Malformed multipart body"}, 400)
            return
        boundary = m.group(1).encode()

        session_id = self.headers.get("X-Session-ID") or "default"
        session = get_session(session_id)

        results = []
        for part in body.split(b"--" + boundary):
            if b"filename=" not in part:
                continue
            fn_m = re.search(rb'filename="([^"]*)"', part)
            filename = fn_m.group(1).decode("utf-8", "replace") if fn_m else "upload.csv"
            filename = os.path.basename(filename.replace("\\", "/")) or "upload.csv"
            cut = part.find(b"\r\n\r\n")
            if cut == -1:
                continue
            raw = part[cut + 4:]
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            if not raw:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            meta, _err = process_file(session, filename, text, len(raw))
            results.append(meta)

        if not results:
            self.send_json({"error": "No file parts found in upload"}, 400)
            return
        ok = [r for r in results if "error" not in r]
        bad = [r for r in results if "error" in r]
        self.send_json({"accepted": ok, "failed": bad,
                        "total_files": len(session["order"]),
                        "numeric_union": union_numeric(session)})

# =============================================================================
# MAIN
# =============================================================================


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server() -> None:
    with Server(("", PORT), Handler) as httpd:
        print(f"""
╔══════════════════════════════════════════════════════════════════════════╗
║      Defence Test Data Analyzer — Telemetry Graph Studio  v3.0           ║
╠══════════════════════════════════════════════════════════════════════════╣
║   Open:  http://127.0.0.1:{PORT:<43}║
║   • Tab 1: single-file upload  →  interval-sampled graph                 ║
║   • Tab 2: multi-file upload   →  combined chronological graph           ║
║   • Graph points at every Nth timestamp (default 5 → #1,#6,#11,… )       ║
║   • Fully offline  •  no cloud  •  stdlib only                           ║
╚══════════════════════════════════════════════════════════════════════════╝
""")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down…")


if __name__ == "__main__":
    run_server()
