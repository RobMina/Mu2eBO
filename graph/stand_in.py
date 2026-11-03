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
  python -m graph.stand_in compare-sob --g4 DIR/g4_points.json --fast fast.json [--g4-ce refsob.json]
      [--kappa-rel-err E] [--json out]
      fast.json: {config: metrics of beamfastsim.cesob point}; the fast sob against the board's, unscaled, with
      its factors (stops per event, CE acceptance, signal per stop in the box, background) and rank correlations.

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
            exclude: Optional[List[str]] = None, kappa: Optional[float] = None) -> Dict[str, Any]:
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
    if kappa is not None:          # a scale measured upstream (e.g. muons per event of the G4 input), no board point
        kappa_err = 0.0
        held = list(range(len(names)))
    elif anchor is not None:
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
        "kappa_source": "given" if anchor is None and kappa_err == 0.0 else ("anchor" if anchor else "fit"),
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
            parts = [k for k in have if "sob.signal_rate" in g4[names[k]] and "sob.total_background" in g4[names[k]]]
            if len(parts) == len(have):
                S = np.array([g4[names[k]]["sob.signal_rate"] for k in have])
                B = np.array([g4[names[k]]["sob.total_background"] for k in have])
                W = np.array([g4[names[k]].get("sob.signal_box_high_mev", np.nan)
                              - g4[names[k]].get("sob.signal_box_low_mev", np.nan) for k in have])
                eff = np.log(S) - ls            # CE efficiency in the box per stop (log, up to a constant)
                out[obj]["var_log_box_eff"] = float(np.var(eff))
                out[obj]["var_half_log_bkg"] = float(np.var(0.5 * np.log(B)))
                out[obj]["cov_box_eff_half_log_bkg"] = float(np.cov(eff, -0.5 * np.log(B), ddof=0)[0, 1])
                out[obj]["box_width_mev"] = {"min": float(np.nanmin(W)), "median": float(np.nanmedian(W)),
                                             "max": float(np.nanmax(W))}
    return out


# sob of the foils studies: S/sqrt(B) with S = NPOT x BR x stops/POT x accepted per CE x fraction in the box
SOB_NPOT_BR = 1.0e18 * 1.0e-13 * 0.609
SOB_UPSTREAM_EFF = 0.01278168          # the study's fixed MuBeam events per POT (stops step)


def _rel_stats(ratio, rerr) -> Dict[str, float]:
    import numpy as np
    pull = (ratio - ratio.mean()) / rerr
    return {"mean": round(float(ratio.mean()), 5), "mean_err": round(float(ratio.std(ddof=1) / math.sqrt(len(ratio))), 5),
            "rms": round(float(ratio.std(ddof=1)), 5), "expected_rms_stat": round(float(np.sqrt(np.mean(rerr ** 2))), 5),
            "chi2_ndf_about_mean": round(float(np.sum(pull ** 2) / (len(ratio) - 1)), 3),
            "min": round(float(ratio.min()), 4), "max": round(float(ratio.max()), 4)}


def compare_sob(g4: Dict[str, Any], fast: Dict[str, Any], exclude: Optional[List[str]] = None,
                g4_sob_noise: float = 0.0021, kappa_rel_err: float = 0.0,
                g4_ce: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fast sob (beamfastsim.cesob point metrics) against the boards' sob, unscaled, and its factors: stops per
    generated event, CE acceptance (calorimeter selection per generated CE), signal per stop in the box, background.
    g4_sob_noise: the study's statistical noise on sob (absolute); kappa_rel_err: common relative error of the fast
    stops normalisation (muons per event of the start table). g4_ce: {config: metrics of the same estimate on the
    point's own EdepAna ntuple (cesob refsob)}; its ce_acceptance (weighted selected / generated, the count sob uses)
    replaces the board's ce_edep.selected_per_gen_event, which counts selected events differently."""
    import numpy as np
    names = sorted(c for c in g4 if c in fast and "sob.sensitivity" in g4[c] and "sensitivity" in fast[c]
                   and c not in (exclude or []) and (g4_ce is None or c in g4_ce))
    if len(names) < 3:
        raise ValueError(f"only {len(names)} points have both a full-simulation and a fast sob")
    gv = lambda key: np.array([g4[c][key] for c in names], float)
    fv = lambda key: np.array([fast[c][key] for c in names], float)
    G, F = gv("sob.sensitivity"), fv("sensitivity")
    Ge = np.full(len(names), g4_sob_noise)
    stop_rel = fv("stop_frac_err") / fv("stop_frac")
    Fe = F * np.sqrt((fv("sensitivity_err") / F) ** 2 + stop_rel ** 2)
    ratio = G / F
    rerr = ratio * np.sqrt((Ge / G) ** 2 + (Fe / F) ** 2)
    out: Dict[str, Any] = {"points": len(names), "sob": _rel_stats(ratio, rerr),
                           "g4_acceptance": "refsob" if g4_ce else "board ce_edep.selected_per_gen_event"}
    out["sob"]["common_scale_err"] = kappa_rel_err
    lf, lg = np.log(F), np.log(G)
    A = np.vstack([lf - lf.mean(), np.ones_like(lf)]).T
    out["sob"]["slope_log"] = round(float(np.linalg.lstsq(A, lg - lg.mean(), rcond=None)[0][0]), 4)
    out["sob"]["spearman"] = jackknife(spearman, F, G)
    out["sob"]["kendall"] = jackknife(kendall, F, G)
    out["sob"]["g4_range"] = [float(G.min()), float(G.max())]
    out["sob"]["fast_range"] = [float(F.min()), float(F.max())]
    # factors
    Gs, Fs = gv("stops.stops_per_gen_event"), fv("stops_per_gen_event")
    Gsn = gv("stops.n_gen_events")
    Ga = np.array([g4_ce[c]["ce_acceptance"] for c in names], float) if g4_ce else gv("ce_edep.selected_per_gen_event")
    Fa = fv("ce_acceptance")
    Gan = gv("ce_edep.n_gen_events")
    GS, GB = gv("sob.signal_rate"), gv("sob.total_background")
    Gbox = GS / (SOB_NPOT_BR * Gs * SOB_UPSTREAM_EFF)
    Fbox, FB = fv("box_eff_per_stop"), fv("total_background")
    ga_rel = np.sqrt((1 - Ga) / (Ga * Gan))
    factors = {   # name: (G4, fast, relative error of the ratio)
        "stops_per_gen_event": (Gs, Fs, np.hypot(np.sqrt((1 - Gs) / (Gs * Gsn)), stop_rel)),
        "ce_acceptance": (Ga, Fa, np.hypot(ga_rel, fv("ce_acceptance_err") / Fa)),
        "box_eff_per_stop": (Gbox, Fbox, np.hypot(ga_rel, fv("box_eff_per_stop_err") / Fbox)),
        "background": (GB, FB, fv("total_background_err") / FB),
    }
    gl, gh = gv("sob.signal_box_low_mev"), gv("sob.signal_box_high_mev")
    fl, fh = fv("signal_box_low_mev"), fv("signal_box_high_mev")
    same = (np.abs(gl - fl) < 1e-6) & (np.abs(gh - fh) < 1e-6)
    out["factors"] = {}
    for k, (g, f, rel) in factors.items():
        # box-dependent factors on the points whose two windows coincide (a one-bin window move is a step in them)
        m = same if k in ("box_eff_per_stop", "background") else np.ones(len(names), bool)
        out["factors"][k] = _rel_stats(g[m] / f[m], g[m] / f[m] * rel[m])
        out["factors"][k]["points"] = int(m.sum())
        out["factors"][k]["spearman"] = jackknife(spearman, f, g)
    out["box"] = {"low_diff_mev": {"mean": round(float(np.mean(gl - fl)), 3), "rms": round(float(np.std(gl - fl)), 3)},
                  "high_diff_mev": {"mean": round(float(np.mean(gh - fh)), 3), "rms": round(float(np.std(gh - fh)), 3)},
                  "same_box": int(same.sum()),
                  "g4_width_median": float(np.median(gh - gl)), "fast_width_median": float(np.median(fh - fl))}
    out["per_point"] = {c: {"g4": float(G[k]), "fast": float(F[k]), "fast_err": float(Fe[k]), "ratio": float(ratio[k]),
                            "ratio_err": float(rerr[k]), "g4_box": [float(gl[k]), float(gh[k])],
                            "fast_box": [float(fl[k]), float(fh[k])],
                            **{f"{f}_ratio": float(factors[f][0][k] / factors[f][1][k]) for f in factors}}
                        for k, c in enumerate(names)}
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
    c.add_argument("--kappa", type=float, default=None,
                   help="fixed scale (G4 stops per event / fast stops per muon) from outside the board, e.g. muons per event")
    c.add_argument("--exclude", action="append", default=[], help="config left out (repeatable)")
    c.add_argument("--json", type=Path)
    o = sp.add_parser("compare-sob")
    o.add_argument("--g4", required=True, type=Path)
    o.add_argument("--fast", required=True, type=Path, help="collect output of beamfastsim.cesob point runs")
    o.add_argument("--exclude", action="append", default=[], help="config left out (repeatable)")
    o.add_argument("--g4-noise", dest="g4_noise", type=float, default=0.0021, help="statistical noise of G4 sob")
    o.add_argument("--kappa-rel-err", dest="kappa_rel_err", type=float, default=0.0,
                   help="common relative error of the fast stops normalisation")
    o.add_argument("--g4-ce", dest="g4_ce", type=Path, default=None,
                   help="JSON {config: refsob metrics on the point's own EdepAna ntuple} for the G4 acceptance")
    o.add_argument("--json", type=Path)
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
    if args.cmd == "compare-sob":
        res = compare_sob(json.loads(args.g4.read_text()), json.loads(args.fast.read_text()), args.exclude,
                          args.g4_noise, args.kappa_rel_err,
                          json.loads(args.g4_ce.read_text()) if args.g4_ce else None)
        print(json.dumps({k: v for k, v in res.items() if k != "per_point"}, indent=1))
        if args.json:
            args.json.write_text(json.dumps(res, indent=1, sort_keys=True))
        return 0
    res = compare(json.loads(args.g4.read_text()), json.loads(args.fast.read_text()), args.anchor, args.exclude, args.kappa)
    text = json.dumps({k: v for k, v in res.items() if k != "per_point"}, indent=1)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
