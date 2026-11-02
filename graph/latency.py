"""Latency of evaluated points from their point records: when each step was
submitted and when its results landed, per point and summarised per study
and step. Read only; nothing is submitted or written outside --json.
  python -m graph.latency --grid <GRID_DATA_ROOT> --study foilspfbpz_ax
  python -m graph.latency --grid <GRID_DATA_ROOT> --board <board.tsv> --json out.json
Times come from file modification times in <grid>/<config>/state/ (see
core/point_dir.py): point.json (claim), preflight_verdict.json, each step's
<step>_submit.json or <step>_cluster.txt (submitted) and <step>_results.json
(done, at the poll that saw it), evaluate_result.json (row scored). A step's
wall therefore includes queue, stragglers and the poll cadence.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from point_dir import PointDir  # noqa: E402


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _min(*xs: Optional[float]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return min(vals) if vals else None


def step_names(state: Path) -> List[str]:
    return sorted(p.name[:-len("_cluster.txt")] for p in state.glob("*_cluster.txt"))


def _job_shape(results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """njobs and events per job of a grid step, when its results record them."""
    if not isinstance(results, dict):
        return {}
    meta = results.get("metadata") or {}
    metrics = results.get("metrics") or {}
    params = results.get("params") or {}
    out = {}
    for key, src in (("njobs", metrics), ("njobs_ok", metrics), ("events_per_job", meta)):
        if isinstance(src.get(key), (int, float)):
            out[key] = src[key]
    for key in ("njobs", "events_per_job"):
        if key not in out and isinstance(params.get(key), (int, float)):
            out[key] = params[key]
    return out


def point_timeline(state: Path) -> Optional[Dict[str, Any]]:
    """One point's timeline in minutes from its claim; None without point.json."""
    pd = PointDir(state)
    point = pd.point()
    if point is None:
        return None
    t0 = _mtime(pd.path("point.json"))
    rel = lambda t: None if t is None or t0 is None else round((t - t0) / 60.0, 2)
    steps = {}
    for step in step_names(state):
        sub = _mtime(pd.path(f"{step}_submit.json"))
        handle = _mtime(pd.path(f"{step}_cluster.txt"))
        done = _mtime(pd.path(f"{step}_results.json"))
        start = _min(sub, handle)
        steps[step] = {
            "submitted_min": rel(start),
            "handle_min": rel(handle),
            "done_min": rel(done),
            "wall_min": None if done is None or start is None else round((done - start) / 60.0, 2),
            **_job_shape(pd.results(step)),
        }
    end = _mtime(pd.path("evaluate_result.json"))
    broken = pd.broken()
    return {
        "config": point.get("config", state.parent.name),
        "study": point.get("study"),
        "campaign": point.get("campaign"),
        "executor": point.get("executor"),
        "claimed_at": t0,
        "preflight_min": rel(_mtime(pd.path("preflight_verdict.json"))),
        "scored_min": rel(end),
        "broken": broken.text if broken else None,
        "steps": steps,
    }


def _stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": round(statistics.median(values), 1),
            "min": round(min(values), 1), "max": round(max(values), 1)}


def summarise(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per study and step: wall minutes (median, range) over scored points."""
    out: Dict[str, Any] = {}
    for p in points:
        if p["scored_min"] is None:
            continue
        s = out.setdefault(p["study"], {"points": 0, "total_min": [], "preflight_min": [], "steps": {}})
        s["points"] += 1
        s["total_min"].append(p["scored_min"])
        if p["preflight_min"] is not None:
            s["preflight_min"].append(p["preflight_min"])
        for step, rec in p["steps"].items():
            st = s["steps"].setdefault(step, {"wall_min": [], "submitted_min": [], "shape": {}})
            if rec["wall_min"] is not None:
                st["wall_min"].append(rec["wall_min"])
            if rec["submitted_min"] is not None:
                st["submitted_min"].append(rec["submitted_min"])
            for k in ("njobs", "events_per_job"):
                if k in rec:
                    st["shape"][k] = rec[k]
    for s in out.values():
        s["total_min"] = _stats(s["total_min"])
        s["preflight_min"] = _stats(s["preflight_min"])
        for st in s["steps"].values():
            st["wall_min"] = _stats(st["wall_min"])
            st["submitted_min"] = _stats(st["submitted_min"])
    return out


def board_configs(board: Path) -> List[str]:
    """Config names of a leaderboard TSV's rows."""
    rows = Path(board).read_text().splitlines()[1:]
    return [r.split("\t", 1)[0] for r in rows if r.strip()]


def scan(grid: Path, studies: Optional[List[str]] = None,
         configs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    points = []
    states = ([Path(grid) / c / "state" for c in configs] if configs
              else sorted(Path(grid).glob("*/state")))
    for state in states:
        try:
            p = point_timeline(state)
        except (OSError, ValueError):
            continue
        if p is not None and (not studies or p["study"] in studies):
            points.append(p)
    return points


def render(summary: Dict[str, Any]) -> str:
    lines = []
    for study, s in sorted(summary.items()):
        t = s["total_min"]
        lines.append(f"{study}: {s['points']} scored points, claim->row median {t.get('median')} min "
                     f"({t.get('min')}-{t.get('max')}), preflight median {s['preflight_min'].get('median')} min")
        for step, st in sorted(s["steps"].items(), key=lambda kv: kv[1]["submitted_min"].get("median") or 0):
            w = st["wall_min"]
            shape = " ".join(f"{k}={v:g}" for k, v in st["shape"].items())
            lines.append(f"  {step:16s} submitted at {st['submitted_min'].get('median')} min, wall median "
                         f"{w.get('median')} ({w.get('min')}-{w.get('max')}) min, n={w.get('n')} {shape}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--grid", required=True, type=Path, help="GRID_DATA_ROOT holding <config>/state/")
    ap.add_argument("--study", action="append", help="only points of this study (repeatable)")
    ap.add_argument("--board", type=Path, action="append",
                    help="only the configs of this leaderboard TSV's rows (repeatable)")
    ap.add_argument("--json", type=Path, help="write points and summary as JSON here")
    args = ap.parse_args(argv)
    configs = [c for b in args.board for c in board_configs(b)] if args.board else None
    points = scan(args.grid, args.study, configs)
    summary = summarise(points)
    print(render(summary))
    if args.json:
        args.json.write_text(json.dumps({"points": points, "summary": summary}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
