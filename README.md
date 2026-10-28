# Defence Test Data Analyzer — Telemetry Graph Studio

A focused, **fully offline** program for defence telemetry:

1. **Upload tabs**
   - **Single File** — upload one CSV → generate its professional graph.
   - **Multiple Files — Combine** — upload two or more CSVs → they are combined into one chronological dataset → one merged graph.
2. **Interval-sampled graphs** — graph data for **all selected attributes** is sampled at an
   interval of timestamps (default **5** → points at timestamp indices **#1, #6, #11, #16, …**).
3. **Points on every graph** — every sampled timestamp renders as a **visible point** with its
   **timestamp + attribute value** on hover, plus an interval sample table listing each point.
4. **Any number of attributes** — each attribute gets its own elegant per-attribute chart
   (small multiples), or view everything together in **Overlay** (shared / per-series independent
   value axes, chosen automatically by scale spread).
5. **All Timestamps tab (third tab)** — a **full-resolution** graph with **every timestamp on the
   X-axis** and the **selected attribute values on the Y-axis** (no interval sampling). It reuses
   either the **single-file pick from Tab 1** or the **combined set from Tab 2**, whichever source
   the user chooses, and plots every timestamp of that source.

Python stdlib backend (`server.py`, zero dependencies) + offline Chart.js 4 frontend (`index.html`).

---

## Quick Start

```bash
# From the project folder
python3 server.py
```

Open in a browser:

```
http://127.0.0.1:8001
```

*(Different port: `PORT=9000 python3 server.py`)*

---

## How the graph logic works

- CSVs are parsed with support for **FastLog** headers and defence datetime formats:
  - `6:6:2026 12:30:49:694` → `D:M:YYYY HH:MM:SS:ms`
  - `25-02-2026 10:02:24:254` → `DD-MM-YYYY HH:MM:SS:ms`
  - ISO / standard fallbacks
- Numeric columns are auto-detected (≥ 60 % numeric sample) and offered as attribute chips.
- Rows of all selected files are sorted by timestamp.
- Sampling: for interval **N**, graph points are taken at 1-based timestamp indices
  **1, 1+N, 1+2N, …** (so N = 5 → `#1, #6, #11, …`).
- Every point carries **{timestamp, source file, value of each selected attribute}**
  — surfacing as chart points, rich tooltips, and an interval sample table.

## Tabs

| Tab | What it does |
| :--- | :--- |
| **Single File** | Upload exactly one CSV, pick attributes + interval, generate its graph. |
| **Multiple Files — Combine** | Upload many CSVs (all default-selected in the checkbox list); press Generate to combine and graph them together. |
| **All Timestamps** | Same session, **no sampling** — every timestamp of the chosen source (a single file like Tab 1, or the combined set like Tab 2) is plotted: timestamps on X, attribute values on Y. Point dots and animation adapt automatically to dataset density. |

Both tabs also provide: per-attribute statistic cards (Min / Max / Mean / Count),
a **Points · per attribute** ↔ **Overlay** view switch, and the collapsible interval sample table.

## Key Endpoints

| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/` | `GET` | Serves the Telemetry Graph Studio frontend |
| `/api/health` | `GET` | Server status + time |
| `/api/upload` | `POST` | Multipart CSV upload (`X-Session-ID` header) |
| `/api/files?session=` | `GET` | Uploaded files + union of numeric columns |
| `/api/graph` | `POST` | Graph data `{session_id, file_ids, attributes, interval}` — add `"full": true` for every-timestamp data |
| `/api/preview?session=&file_id=` | `GET` | First rows of a parsed file |
| `/api/reset` | `POST` | Clears a session |

---

## Troubleshooting

- **Port busy**: run `lsof -ti :8000 | xargs kill -9` (or use `PORT=9000`).
- **Chart.js missing**: confirm `static/js/chart.min.js` and `static/js/chartjs-adapter-date-fns.min.js` exist.
- **Files up to 100 MB** are accepted per upload request.
# defence-test-graph
