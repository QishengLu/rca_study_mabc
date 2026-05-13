# mABC Data Adapter (ops-lite)

mABC originally required a custom JSON layout under `data/cases/<case_name>/`
(metric/, topology/, label/), produced by the legacy `convert_all.py` from
`/home/nn/SOTA-agents/RolloutRunner/data/` parquets.

For sota-rca-study with ops-lite, **`data_adapter.py`** replaces convert_all.py:

```python
from data_adapter import ensure_mabc_data_for_case

# At top of agent_runner.py, before running mABC:
case_name = Path(payload["data_dir"]).name
mabc_case_dir = ensure_mabc_data_for_case(payload["data_dir"], case_name)
# Now mABC can read mabc_case_dir/{metric, topology, label}/
```

Differences from legacy convert_all.py:
- Reads directly from ops-lite `cases/<name>/abnormal_traces.parquet` (no DB lookup)
- Handles both `time/service_name/duration/attr.status_code` (lowercase ops-lite
  OTel schema) and the legacy PascalCase `Timestamp/ServiceName/Duration/StatusCode`
- Idempotent: re-runs are no-ops if output JSON already exists
- Falls back gracefully on missing parquet (writes empty stubs so mABC won't crash)

## Patch agent_runner.py

In `frameworks/mabc/agent_runner.py`, after parsing the stdin payload:

```python
# OLD: extract case_name from question text (regex-heuristic)
# NEW: derive from data_dir + run adapter
from pathlib import Path
from data_adapter import ensure_mabc_data_for_case

data_dir = payload["data_dir"]
case_name = Path(data_dir).name
mabc_case_dir = ensure_mabc_data_for_case(data_dir, case_name)
# Pass mabc_case_dir to the mABC pipeline
```

Done as part of WP3.6 (mabc framework adaptation).
