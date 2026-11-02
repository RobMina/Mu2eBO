# Fast stopping tier for the foils studies

Study `tests/fixtures/engine_studies/fastsim_foilspfbpz_stops.json`: the knobs and derive of `foilspfbpz_ax`,
evaluated by fastsimkit task `beam_point`: DS-entrance MuBeamCat muons through the DS field map and the foils,
counted when they stop in a foil. One point is one one-GPU job. The objective is `stop_frac`, the stopping factor
of sob. It is **not** sob.

## What the study renders

A bounded target overlay on `Offline/Mu2eG4/geom/geom_common.txt`, the only base fastsimkit admits:
`stoppingTarget.{z0InMu2e, deltaZ, radii, halfThicknesses, holeRadii, zVars}`,
`protonabsorber.distFromTargetEnd` and `hasTSdA = false`, with the same expressions as `foilspfbpz_ax`.
`tests/test_fast_stopping_tier.py` checks that, at the same x, these lines equal the target lines of the
`foilspfbpz_ax` render. The G4 study also sets keys the fast stage does not model: foil supports off, rails
shortened, service pipes off, degrader parked, COL5 material, VD position and the absolute IPA pin (the fast stage
places the IPA with `distFromTargetEnd`, which gives the same position). The scalar `holeRadius` poison pill is
left out, because the per-foil `holeRadii` shadows it.

## Validation against the full-simulation boards (no new Geant4)

`graph.stand_in extract` reads the points' records, and `beam_point` (the same code on CPU, one seed, the 105,350
muons of the reference table) evaluates each point's overlay. `graph.stand_in compare` then compares the fast stop
fraction (per DS-entrance muon) with the board's `stops_per_gen_event` (target stops per generated mubeam event).
Points: 65 G4 rows (`foilspfbpz_ax` upstream board 62, `foilspf_nominal` 1, `foilsflash_ax` 1, `foilsflash_bb` 1).
`foilsnopa_bb` is left out because its G4 geometry removes the IPA. G4 stops per event range from 0.057 to 0.130,
fast stop fractions from 0.36 to 0.80.

The two quantities differ by a normalisation k. The fast table starts at the DS entrance (about one muon per
event). The boards' MuBeamCat sample starts at TS3: the resampled primaries of the stopped muons start near z = 0,
x = 1 m in the Mu2e frame. So k is the transmission from TS3 to the DS entrance times the muons per event. It is a
**one-point calibration of the absolute level**: fixed on the nominal target alone, with the other 64 points held
out.

| Quantity | Value |
|---|---|
| k from the nominal point (calibration) | 0.16314 +- 0.00077 |
| k fitted to all 65 (diagnostic) | 0.16248 +- 0.00007 |
| G4 / (k x fast) on 64 held-out points, mean | 0.9960 +- 0.0004 (common scale error 0.0047 from the anchor) |
| same, rms over points / expected from statistics | 0.0030 / 0.0034 |
| chi2/ndf (anchor scale / fitted scale) | 2.41 / 0.80 (max pull 3.5 / 2.1) |
| slope of log G4 vs log fast | 0.9985 |
| Spearman / Kendall rank correlation | 0.9991 +- 0.0005 / 0.988 +- 0.006 (jackknife) |

The fast stage follows the geometry dependence of the stopping rate at the 0.3 % level, consistent with the
statistics. The offset of the anchored ratio (0.4 %) is the anchor's own statistical error.

**Stops alone do not rank sob.** Across the same 65 points, Spearman(sob, G4 stops) = 0.25 +- 0.13 (fast stops
0.25 +- 0.13), and flash_edep is uncorrelated with the stops (0.10 +- 0.13). In log sob = log stops + rest, the
variance of log stops (0.025) and of the rest (0.019) are each larger than the variance of log sob (0.004). The rest
is the CE efficiency in the signal box and the box width, and it is anticorrelated with the stops: designs that stop
more muons lose CE resolution. A fast sob needs the CE side too: electrons from the stop positions through the
foils and the DS field, giving the momentum spectrum and so the box efficiency and width.

## Latency

`graph.latency` over the same point records: a `foilspfbpz_ax` point takes a median 248 min from claim to row
(range 188-1225). Its `mubeam` step takes 97 min (70-858) and `mustops_ce` 103 min. Through `graph.run` on UVA (one
A100), a fast point took 4.4 and 5.4 min from launch to row. That includes 0.1-2.3 min of queue, a 2.7-3.1 min job
(about 1.6 min of compilation) and 0.6-0.8 min of input streaming. Both GPU results equal the CPU results for the
same seed.

## Running it

The kit server sources the site file named by `M2FS_SITE_ENV` (see `kits.toml`). Then:

```bash
export AUTORESEARCH_STUDY_PATH=$PWD/tests/fixtures/engine_studies M2FS_CODE=<umbrella> M2FS_SITE_ENV=<site.env>
PYTHONPATH= "$AUTORESEARCH_PYTHON" -m graph.check_study fastsim_foilspfbpz_stops
PYTHONPATH= "$AUTORESEARCH_PYTHON" -m graph.run --study fastsim_foilspfbpz_stops --config <name> --campaign <c> --x=<10 values>
```
