"""Work metrics devices send with their heartbeats (ADR-025): counters and timing histograms.

A snapshot is {"window_s": 300, "counters": {name: int}, "timings": {name: {"n", "sum", "max",
"buckets"}}}. Buckets count samples at or below each bound in BOUNDS_MS (the last bucket is
everything above 1024 ms), so snapshots from many heartbeats merge by adding, and the portal reads
approximate percentiles back out. Nothing here is about content: counts and milliseconds only.
"""

from __future__ import annotations

from typing import Any

BOUNDS_MS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
BUCKETS = len(BOUNDS_MS) + 1
MAX_NAMES = 64


def empty() -> dict[str, Any]:
    return {"window_s": 0, "counters": {}, "timings": {}}


def clean(snapshot: Any) -> dict[str, Any] | None:
    """Accept only the documented shape, bounded, so a device cannot stuff the database."""
    if not isinstance(snapshot, dict):
        return None
    out = empty()
    window = snapshot.get("window_s")
    out["window_s"] = int(window) if isinstance(window, (int, float)) and 0 <= window <= 86400 else 0
    counters = snapshot.get("counters") or {}
    if isinstance(counters, dict):
        for name, value in list(counters.items())[:MAX_NAMES]:
            if isinstance(name, str) and len(name) <= 48 and isinstance(value, (int, float)) and value >= 0:
                out["counters"][name] = int(value)
    timings = snapshot.get("timings") or {}
    if isinstance(timings, dict):
        for name, t in list(timings.items())[:MAX_NAMES]:
            if not (isinstance(name, str) and len(name) <= 48 and isinstance(t, dict)):
                continue
            buckets = t.get("buckets")
            if not (isinstance(buckets, list) and len(buckets) == BUCKETS
                    and all(isinstance(b, (int, float)) and b >= 0 for b in buckets)):
                continue
            n, total, peak = t.get("n"), t.get("sum"), t.get("max")
            if not all(isinstance(v, (int, float)) and v >= 0 for v in (n, total, peak)):
                continue
            out["timings"][name] = {"n": int(n), "sum": float(total), "max": float(peak),
                                    "buckets": [int(b) for b in buckets]}
    return out


def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = {"window_s": a.get("window_s", 0) + b.get("window_s", 0),
           "counters": dict(a.get("counters", {})), "timings": {k: dict(v) for k, v in a.get("timings", {}).items()}}
    for name, value in b.get("counters", {}).items():
        out["counters"][name] = out["counters"].get(name, 0) + value
    for name, t in b.get("timings", {}).items():
        have = out["timings"].get(name)
        if have is None:
            out["timings"][name] = {"n": t["n"], "sum": t["sum"], "max": t["max"], "buckets": list(t["buckets"])}
            continue
        have["n"] += t["n"]
        have["sum"] += t["sum"]
        have["max"] = max(have["max"], t["max"])
        have["buckets"] = [x + y for x, y in zip(have["buckets"], t["buckets"], strict=True)]
    return out


def percentile(t: dict[str, Any], q: float) -> float:
    """The bucket bound at or below which a share q of the samples fall, capped at the observed max."""
    n = t["n"]
    if n <= 0:
        return 0.0
    want = q * n
    seen = 0
    for i, count in enumerate(t["buckets"]):
        seen += count
        if seen >= want:
            bound = BOUNDS_MS[i] if i < len(BOUNDS_MS) else t["max"]
            return float(min(bound, t["max"]))
    return float(t["max"])


def summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Rows for the portal: timings with runs, mean, p50, p95, max; counters as they are."""
    rows = []
    for name, t in sorted(snapshot.get("timings", {}).items()):
        if t["n"] <= 0:
            continue
        rows.append({"name": name, "n": t["n"], "mean": t["sum"] / t["n"],
                     "p50": percentile(t, 0.5), "p95": percentile(t, 0.95), "max": t["max"]})
    return {"window_s": snapshot.get("window_s", 0), "timings": rows,
            "counters": sorted(snapshot.get("counters", {}).items())}
