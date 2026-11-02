"""Validate a fast stand-in on points that already have full-simulation rows.
Read only on the point records; writes only under --out.

  python -m graph.stand_in extract --grid <GRID_DATA_ROOT> --board <board.tsv> --out DIR
      per point: the step metrics the board's objectives come from (stops,
      ce_edep, sob, flash) -> DIR/g4_points.json, and the point's stopping
      target as a bounded overlay on geom_common.txt -> DIR/overlays/<config>.txt
  python -m graph.stand_in collect --runs RUNDIR --out fast.json
      RUNDIR/<config>/metrics.json (one beam_point output per point) -> {config: metrics}
  python -m graph.stand_in compare --g4 DIR/g4_points.json --fast fast.json [--anchor CONFIG] [--json out]
      fast.json: {config: {"stop_frac": f, "stop_frac_err": e, ...}}; reports
      the stop-rate agreement (scale fixed on --anchor only), rank correlations
      and how much of each objective the stopping rate explains.

The overlay keeps only the target keys a fast beam stage models
(TARGET_KEYS) and hasTSdA; everything else in the rendered geometry (supports,
rails, service pipes, materials, VD positions) is reported, not carried.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from point_dir import PointDir  # noqa: E402

BASE = "Offline/Mu2eG4/geom/geom_common.txt"
TARGET_KEYS = ("stoppingTarget.z0InMu2e", "stoppingTarget.deltaZ", "stoppingTarget.radii",
               "stoppingTarget.halfThicknesses", "stoppingTarget.holeRadii", "stoppingTarget.holeRadius",
               "stoppingTarget.zVars", "protonabsorber.distFromTargetEnd")
SWITCH_KEYS = ("hasTSdA",)
DECL = re.compile(r"^\s*(double|bool|int|string|vector<\s*\w+\s*>)\s+([A-Za-z][\w.]*)\s*=\s*([^;]*);", re.M)
STEP_METRICS = {
    "stops": ("stops_per_pot", "stops_per_gen_event", "n_events", "n_gen_events"),
    "ce_edep": ("selected_per_gen_event", "n_events_selected", "n_gen_events"),
    "sob": ("sensitivity", "signal_rate", "total_background", "signal_fwhm_mev",
            "signal_box_low_mev", "signal_box_high_mev"),
    "flash": ("avg_trk_edep_per_gen_event_mev", "n_gen_events"),
}


def declarations(text: str) -> Dict[str, tuple]:
    """{key: (type, raw value)} of a SimpleConfig file's own lines, last one wins."""
    clean = re.sub(r"//[^\n]*", "", text)
    return {m.group(2): (re.sub(r"\s+", "", m.group(1)), m.group(3).strip()) for m in DECL.finditer(clean)}


def target_overlay(text: str) -> Dict[str, Any]:
    """The bounded target overlay of a rendered geometry, and the keys left out."""
    decl = declarations(text)
    keep = [k for k in TARGET_KEYS if k in decl]
    if "stoppingTarget.holeRadii" in decl and "stoppingTarget.holeRadius" in keep:
        keep.remove("stoppingTarget.holeRadius")     # shadowed by the per-foil vector
    lines = [f'#include "{BASE}"']
    lines += [f"{decl[k][0]} {k} = {decl[k][1]};" for k in keep]
    for k in SWITCH_KEYS:
        if k in decl:
            lines.append(f"{decl[k][0]} {k} = {decl[k][1]};")
    dropped = sorted(k for k in decl if k not in keep and k not in SWITCH_KEYS)
    return {"text": "\n".join(lines) + "\n", "kept": keep + [k for k in SWITCH_KEYS if k in decl],
            "dropped": dropped}


def extract(grid: Path, configs: List[str], out: Path) -> Dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "overlays").mkdir(exist_ok=True)
    points: Dict[str, Any] = {}
    for config in configs:
        pd = PointDir.of(grid, config)
        summary = pd._json("summary.json")
        point = pd.point()
        if summary is None or point is None:
            continue
        steps = summary.get("steps", {})
        rec: Dict[str, Any] = {"study": point.get("study"), "x": point.get("x")}
        for step, names in STEP_METRICS.items():
            for name in names:
                if isinstance(steps.get(step, {}).get(name), (int, float)):
                    rec[f"{step}.{name}"] = steps[step][name]
        geom = pd.path("geom.txt")
        if geom.exists():
            ov = target_overlay(geom.read_text())
            (out / "overlays" / f"{config}.txt").write_text(ov["text"])
            rec["overlay_kept"], rec["overlay_dropped"] = ov["kept"], ov["dropped"]
        points[config] = rec
    (out / "g4_points.json").write_text(json.dumps(points, indent=1, sort_keys=True))
    return points


def collect(runs: Path) -> Dict[str, Any]:
    """{config: metrics} of every RUNDIR/<config>/metrics.json."""
    return {p.parent.name: json.loads(p.read_text()) for p in sorted(Path(runs).glob("*/metrics.json"))}


def board_configs(board: Path) -> List[str]:
    rows = Path(board).read_text().splitlines()[1:]
    return [r.split("\t", 1)[0] for r in rows if r.strip()]


# -- statistics (numpy only) -------------------------------------------------------------------------------------

def _ranks(a):
    import numpy as np
    a = np.asarray(a, float)
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a))
    r[order] = np.arange(len(a))
    for v in np.unique(a):                       # average ties
        m = a == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman(a, b) -> float:
    import numpy as np
    return float(np.corrcoef(_ranks(a), _ranks(b))[0, 1])


def kendall(a, b) -> float:
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    s = np.sign(a[:, None] - a[None, :]) * np.sign(b[:, None] - b[None, :])
    n = len(a)
    return float(s[np.triu_indices(n, 1)].sum() / (n * (n - 1) / 2))


def jackknife(fn, a, b) -> Dict[str, float]:
    """Point estimate and its leave-one-point-out jackknife standard error."""
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    loo = np.array([fn(np.delete(a, i), np.delete(b, i)) for i in range(n)])
    se = math.sqrt((n - 1) / n * np.sum((loo - loo.mean()) ** 2))
    return {"value": round(fn(a, b), 4), "err": round(float(se), 4)}


def compare(g4: Dict[str, Any], fast: Dict[str, Any], anchor: Optional[str],
            exclude: Optional[List[str]] = None) -> Dict[str, Any]:
    import numpy as np
    names = sorted(c for c in g4 if c in fast and "stops.stops_per_gen_event" in g4[c]
                   and c not in (exclude or []))
    if len(names) < 3:
        raise ValueError(f"only {len(names)} points have both a full-simulation and a fast result")
    G = np.array([g4[c]["stops.stops_per_gen_event"] for c in names])
    Gn = np.array([g4[c]["stops.n_gen_events"] for c in names])
    Ge = np.sqrt(G * (1 - G) / Gn)
    F = np.array([fast[c]["stop_frac"] for c in names])
    Fe = np.array([fast[c]["stop_frac_err"] for c in names])
    out: Dict[str, Any] = {"points": len(names)}
    # Scale: G4 stops per generated event = kappa x fast stops per DS-entrance muon (an upstream normalisation).
    if anchor is not None:
        if anchor not in names:
            raise ValueError(f"anchor {anchor!r} has no full-simulation and fast pair")
        i = names.index(anchor)
        kappa = G[i] / F[i]
        kappa_err = kappa * math.hypot(Ge[i] / G[i], Fe[i] / F[i])
        held = [j for j in range(len(names)) if j != i]
    else:
        w = 1 / ((Ge / F) ** 2 + (G * Fe / F ** 2) ** 2)
        kappa = float(np.sum(w * G / F) / np.sum(w))
        kappa_err = float(1 / math.sqrt(np.sum(w)))
        held = list(range(len(names)))
    ratio = G / (kappa * F)
    rerr = ratio * np.sqrt((Ge / G) ** 2 + (Fe / F) ** 2)
    h = np.array(held)
    pull = (ratio[h] - 1) / rerr[h]
    out["stops"] = {
        "kappa": float(kappa), "kappa_err": float(kappa_err), "anchor": anchor,
        "held_out_points": len(h),
        "ratio_mean": round(float(ratio[h].mean()), 5),
        "ratio_mean_err": round(float(ratio[h].std(ddof=1) / math.sqrt(len(h))), 5),
        "ratio_rms": round(float(ratio[h].std(ddof=1)), 5),
        "expected_rms_stat": round(float(np.sqrt(np.mean(rerr[h] ** 2))), 5),
        "chi2_ndf": round(float(np.sum(pull ** 2) / len(h)), 3),
        "max_abs_pull": round(float(np.max(np.abs(pull))), 2),
        "slope_log": None,
        "spearman": jackknife(spearman, F, G), "kendall": jackknife(kendall, F, G),
        "g4_range": [float(G.min()), float(G.max())],
    }
    lf, lg = np.log(F), np.log(G)
    A = np.vstack([lf - lf.mean(), np.ones_like(lf)]).T
    slope = np.linalg.lstsq(A, lg - lg.mean(), rcond=None)[0][0]
    out["stops"]["slope_log"] = round(float(slope), 4)     # 1 = the fast stage tracks relative changes exactly
    out["per_point"] = {c: {"g4": float(G[k]), "g4_err": float(Ge[k]), "fast": float(F[k]),
                            "fast_err": float(Fe[k]), "ratio": float(ratio[k]), "ratio_err": float(rerr[k])}
                        for k, c in enumerate(names)}
    for obj, key in (("sob", "sob.sensitivity"), ("flash_edep", "flash.avg_trk_edep_per_gen_event_mev")):
        have = [k for k, c in enumerate(names) if key in g4[names[k]]]
        if len(have) < 3:
            continue
        Y = np.array([g4[names[k]][key] for k in have])
        out[obj] = {"points": len(have),
                    "fast_stop_frac": {"spearman": jackknife(spearman, F[have], Y),
                                       "kendall": jackknife(kendall, F[have], Y)},
                    "g4_stops": {"spearman": jackknife(spearman, G[have], Y),
                                 "kendall": jackknife(kendall, G[have], Y)}}
        if obj == "sob":
            # sob = S/sqrt(B), S proportional to stops x (CE efficiency in the box): split the log variance
            ly, ls = np.log(Y), np.log(G[have])
            rest = ly - ls
            out[obj]["var_log_sob"] = float(np.var(ly))
            out[obj]["var_log_stops"] = float(np.var(ls))
            out[obj]["var_log_rest"] = float(np.var(rest))
            out[obj]["cov_log_stops_rest"] = float(np.cov(ls, rest, ddof=0)[0, 1])
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("extract")
    e.add_argument("--grid", required=True, type=Path)
    e.add_argument("--board", required=True, type=Path, action="append")
    e.add_argument("--out", required=True, type=Path)
    k = sp.add_parser("collect")
    k.add_argument("--runs", required=True, type=Path)
    k.add_argument("--out", required=True, type=Path)
    c = sp.add_parser("compare")
    c.add_argument("--g4", required=True, type=Path)
    c.add_argument("--fast", required=True, type=Path)
    c.add_argument("--anchor", default=None, help="config whose pair fixes the scale (held out of the agreement)")
    c.add_argument("--exclude", action="append", default=[], help="config left out (repeatable)")
    c.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "extract":
        configs = [x for b in args.board for x in board_configs(b)]
        pts = extract(args.grid, configs, args.out)
        print(f"{len(pts)} points -> {args.out}")
        return 0
    if args.cmd == "collect":
        got = collect(args.runs)
        args.out.write_text(json.dumps(got, indent=1, sort_keys=True))
        print(f"{len(got)} points -> {args.out}")
        return 0
    res = compare(json.loads(args.g4.read_text()), json.loads(args.fast.read_text()), args.anchor, args.exclude)
    text = json.dumps({k: v for k, v in res.items() if k != "per_point"}, indent=1)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
