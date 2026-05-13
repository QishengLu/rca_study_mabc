"""mABC ops-lite data adapter.

mABC framework historically required a custom JSON column layout
(per-minute endpoint_stats / topology / label). The legacy convert_all.py
read these from RolloutRunner/data/ + an SQLite DB.

This adapter does the same conversion, but reads directly from an ops-lite
case directory (cases/<name>/{*.parquet, injection.json, causal_graph.json}).

Usage from agent_runner.py:

    from data_adapter import ensure_mabc_data_for_case
    mabc_case_dir = ensure_mabc_data_for_case(ops_lite_case_dir, case_name)
    # mabc_case_dir contains metric/, topology/, label/ subdirs
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

TZ_SHANGHAI = timezone(timedelta(hours=8))


def ensure_mabc_data_for_case(ops_lite_case_dir: str | Path, case_name: str,
                              output_root: str | Path | None = None) -> Path:
    """Convert ops-lite parquet → mABC JSON layout.

    Args:
        ops_lite_case_dir: path to cases/<name>/ in ops-lite
        case_name: short name (used for output dir)
        output_root: where to put converted JSON (default: <mabc_root>/data/cases)

    Returns:
        Path to the mabc case dir (containing metric/, topology/, label/)
    """
    import pandas as pd

    ops_dir = Path(ops_lite_case_dir).resolve()
    if not ops_dir.exists():
        raise FileNotFoundError(f"ops-lite case dir missing: {ops_dir}")

    if output_root is None:
        output_root = Path(__file__).resolve().parent / "data" / "cases"
    output_root = Path(output_root)
    case_out = output_root / case_name

    # Idempotent: skip if already converted
    if (case_out / "metric" / "endpoint_stats.json").exists() and \
       (case_out / "topology" / "endpoint_maps.json").exists() and \
       (case_out / "label" / "label.json").exists():
        return case_out

    (case_out / "metric").mkdir(parents=True, exist_ok=True)
    (case_out / "topology").mkdir(parents=True, exist_ok=True)
    (case_out / "label").mkdir(parents=True, exist_ok=True)

    # ---------- 1. Parse abnormal_traces.parquet ----------
    traces_path = ops_dir / "abnormal_traces.parquet"
    if not traces_path.exists():
        logger.warning(f"abnormal_traces.parquet missing: {traces_path}")
        # Write empty stubs so mABC won't crash
        _write_json(case_out / "metric" / "endpoint_stats.json", {})
        _write_json(case_out / "topology" / "endpoint_maps.json", {})
        _write_json(case_out / "label" / "label.json", [])
        return case_out

    df = pd.read_parquet(traces_path)

    # Column normalization: ops-lite uses lowercase OTel schema (`time`,
    # `service_name`, `duration`, `attr.status_code`); legacy used PascalCase.
    col_map = {
        "time": "Timestamp",
        "service_name": "ServiceName",
        "duration": "Duration",
        "attr.status_code": "StatusCode",
        "span_name": "SpanName",
        "parent_span_id": "ParentSpanId",
        "span_id": "SpanId",
    }
    for src, dst in col_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # Required columns sanity
    needed = ["Timestamp", "ServiceName", "Duration", "StatusCode"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        logger.warning(f"Trace parquet missing columns {missing}; case={case_name}")

    # Time → minute string (Shanghai TZ)
    if "Timestamp" in df.columns:
        ts = df["Timestamp"]
        if hasattr(ts, "dt"):
            if ts.dt.tz is None:
                ts = ts.dt.tz_localize("UTC")
            df["minute"] = ts.dt.tz_convert(TZ_SHANGHAI).dt.strftime("%Y-%m-%d %H:%M:00")
        else:
            df["minute"] = ""

    # Duration: ns → ms
    if "Duration" in df.columns:
        df["duration_ms"] = df["Duration"] / 1_000_000
    else:
        df["duration_ms"] = 0.0

    # is_error
    if "StatusCode" in df.columns:
        df["is_error"] = ~df["StatusCode"].isin(["Ok", "Unset"])
    else:
        df["is_error"] = False

    # ---------- endpoint_stats ----------
    stats: dict = defaultdict(lambda: defaultdict(dict))
    if "ServiceName" in df.columns and "minute" in df.columns:
        for (svc, minute), grp in df.groupby(["ServiceName", "minute"]):
            calls = len(grp)
            errors = int(grp["is_error"].sum())
            avg_dur = float(grp["duration_ms"].mean()) if calls else 0.0
            err_rate = (errors / calls * 100) if calls else 0.0
            stats[svc][minute] = {
                "calls": int(calls),
                "success_rate": round(100 - err_rate, 2),
                "error_rate": round(err_rate, 2),
                "average_duration": round(avg_dur, 2),
                "timeout_rate": 0.0,
            }
    _write_json(case_out / "metric" / "endpoint_stats.json", stats)

    # ---------- endpoint_maps (call graph) ----------
    topo: dict = defaultdict(lambda: defaultdict(set))
    if all(c in df.columns for c in ["SpanId", "ParentSpanId", "ServiceName", "minute"]):
        span_to_svc = dict(zip(df["SpanId"], df["ServiceName"]))
        span_to_minute = dict(zip(df["SpanId"], df["minute"]))
        for _, row in df.iterrows():
            parent = row.get("ParentSpanId")
            if parent and parent in span_to_svc:
                src = span_to_svc[parent]
                dst = row["ServiceName"]
                if src != dst:
                    topo[span_to_minute.get(row["SpanId"], "")][src].add(dst)
    # Convert sets to lists for JSON
    topo_clean = {m: {s: list(d) for s, d in v.items()} for m, v in topo.items()}
    _write_json(case_out / "topology" / "endpoint_maps.json", topo_clean)

    # ---------- label (alert service + timestamp) ----------
    # mABC expects nested dict format:
    # {
    #   "<timestamp>": {
    #     "<alert_service>": [["svc1", "svc2"], ...]   # causal chains
    #   }
    # }
    injection_path = ops_dir / "injection.json"
    causal_path = ops_dir / "causal_graph.json"
    alert_svc, alert_time = _extract_alert(injection_path, causal_path, df)
    chains = _extract_chains(causal_path)
    label_entry: dict = {}
    if alert_time and alert_svc:
        label_entry = {
            alert_time: {
                alert_svc: chains or [[alert_svc]],
            }
        }
    _write_json(case_out / "label" / "label.json", label_entry)

    logger.info(f"mabc data adapter: {case_name} ready at {case_out}")
    return case_out


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _extract_alert(injection_path: Path, causal_path: Path, df) -> tuple[str | None, str | None]:
    """Best-effort extraction of (alert_service, alert_timestamp) from injection.json."""
    alert_svc = None
    alert_time = None

    if injection_path.exists():
        try:
            with open(injection_path) as f:
                inj = json.load(f)
            # ops-lite injection.json typically: { "target_service": ..., "start_time": ... }
            alert_svc = inj.get("target_service") or inj.get("root_service")
            if isinstance(alert_svc, list) and alert_svc:
                alert_svc = alert_svc[0]
            alert_time = inj.get("start_time") or inj.get("inject_time")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to parse {injection_path}: {e}")

    if not alert_svc and causal_path.exists():
        try:
            with open(causal_path) as f:
                cg = json.load(f)
            rcs = cg.get("root_causes") or []
            if rcs:
                # root_cause might be "ts-order-service:http_500"
                rc = rcs[0]
                alert_svc = rc.split(":")[0] if ":" in rc else rc
        except Exception:  # noqa: BLE001
            pass

    if not alert_time and df is not None and "minute" in df.columns and len(df):
        try:
            alert_time = df["minute"].iloc[0]
        except Exception:
            pass

    return alert_svc, alert_time


def _extract_chains(causal_path: Path) -> list[list[str]]:
    """Extract propagation chains from causal_graph.json edges.

    mABC expects [["svc_root", "svc_intermediate", ...], ...].
    """
    if not causal_path.exists():
        return []
    try:
        cg = json.loads(causal_path.read_text())
        edges = cg.get("edges") or []
        nodes = cg.get("nodes") or []
        # Build a parent → children map from edges
        if not edges:
            return [[n.get("name") or n.get("service") or n.get("component", "?")
                     for n in nodes[:3]]] if nodes else []
        # edges may be [{from, to}] or [{source, target}] depending on schema
        chains: list[list[str]] = []
        for e in edges:
            src = e.get("from") or e.get("source") or ""
            dst = e.get("to") or e.get("target") or ""
            if src and dst:
                # strip ":<state>" suffix if present
                src_svc = src.split(":")[0] if ":" in src else src
                dst_svc = dst.split(":")[0] if ":" in dst else dst
                chains.append([src_svc, dst_svc])
        return chains
    except Exception:
        return []


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", help="ops-lite cases/<name>/ path")
    ap.add_argument("--case-name", default=None)
    args = ap.parse_args()
    name = args.case_name or Path(args.case_dir).name
    out = ensure_mabc_data_for_case(args.case_dir, name)
    print(out)
