# Fast sob tier for the foils studies

Study `tests/fixtures/engine_studies/fastsim_foilspfbpz_sob.json` uses the knobs, derive and target overlay of
`fastsim_foilspfbpz_stops` (see [fast-stopping-tier.md](fast-stopping-tier.md)). It is evaluated by the fastsimkit
task `beam_sob`, one 32-core CPU job per point. The objective is `sob`, the same approximate CE sensitivity
S/sqrt(B) that `foilspfbpz_ax` computes from its full simulation.

## What one point computes

1. **Stops.** The study's own beam sample (MuBeamCat Run1Baa muons at the Coll5_Out plane, with its generated
   events) goes through the DS field map and the foils. That gives target stops per generated event with no scale.
2. **Conversion electrons.** 104.97 MeV/c electrons, isotropic, from the mu- stops. They are carried through the
   foils, the IPA (with its end rings) and the OPA in the DS field to the tracker entry plane. This is the
   mu2e-fastsim CE chain, option-4 slab physics.
3. **Calorimeter acceptance.** A proxy stands in for the study's selection, visible calorimeter energy above
   10 MeV. The helix from the tracker entry must run at least 17.8 mm inside the crystals of the Offline crystal
   map. The disks' inner Al and carbon-fibre rings are crossed with the slab physics.
4. **Sensitivity.** The study's estimate on the selected electrons, reproduced exactly: 0.2 MeV resolution, DIO
   smeared with the measured entry-energy response, flat cosmics, the window that maximises S/sqrt(B).

Not modelled: tracker material, the calorimeter front and back plates and front-end electronics, foil supports
(also off in the full-simulation study), rails, service pipes and the degrader.

## Validation against the full-simulation boards (no new Geant4)

`graph.stand_in compare-sob` compares each fast point with the board's sob, unscaled. The G4 acceptance comes from
the same estimate run on each point's own EdepAna ntuple (`--g4-ce`); that estimate reproduces the board's sob on
all 66 points. Points: 65 (`foilsnopa_bb` is left out because its geometry removes the IPA), 200,000 CE per point.

| Quantity, G4 / fast | Mean | rms over points (expected) | chi2/ndf | Rank correlation |
|---|---|---|---|---|
| sob | 0.9920 +- 0.0006 (+- 0.0077 common, stops normalisation) | 0.46 % (0.77 %) | 0.40 | Spearman 0.993 +- 0.003, Kendall 0.945 +- 0.015 |
| stops per generated event | 1.0045 +- 0.0005 | 0.42 % | 0.39 | 0.998 |
| CE acceptance (selected / generated CE) | 0.9832 +- 0.0003 | 0.24 % (0.26 %) | 0.92 | 0.997 |
| signal per stop in the window (60 points with the same window) | 0.9875 +- 0.0004 | 0.29 % | 0.90 | 0.944 |

- **Windows.** The window is the same on 60 of the 65 points. On the other 5 the lower edge moves by one 0.2 MeV bin,
  where sob is flat.
- **Where the 0.8 % offset comes from.**
  - The calorimeter proxy keeps 1.7 % too many electrons. Against Geant4 on an option-4 CE sample it gives
    +1.6 %, traced to electrons that graze the first disk's inner edge and cross its back plate and front-end
    electronics.
  - The fast stops are 0.45 % low.
  - The boards use FTFP_BERT (EM option 0), and their selected electrons lose slightly less energy than in the
    option-4 fast chain: 0.8 % / 1.4 % fewer more than 1 / 2 MeV below the birth energy, worth +0.4 % on sob.
  - None of these is corrected.
- **Rank.** The offset is common to all points, so the rank correlation is what a BO campaign sees.

## Latency

One point through `graph.run` on UVA (bpzup05's x, 100,000 CE) takes 20.5 min from launch to result row:

- queue 3 min;
- the job itself 16.7 min on 32 CPU cores: input stream 43 s, stops 76 s, then the CE chain and calorimeter 858 s
  at 117 electrons/s.

The grid chain's critical path (mubeam -> mustops_ce -> ce_edep) has a median of 248 min per point, so the fast
tier is about 12x faster per BO iteration. The CE chain is host-bound on CPU; a device version of the CE chain will
shorten the job.

## Running it

As for the stopping tier (the kit server sources `$M2FS_SITE_ENV`):

```bash
export AUTORESEARCH_STUDY_PATH=$PWD/tests/fixtures/engine_studies M2FS_CODE=<umbrella> M2FS_SITE_ENV=<site.env>
PYTHONPATH= "$AUTORESEARCH_PYTHON" -m graph.check_study fastsim_foilspfbpz_sob
PYTHONPATH= "$AUTORESEARCH_PYTHON" -m graph.run --study fastsim_foilspfbpz_sob --config <name> --campaign <c> --x=<10 values>
python -m graph.stand_in compare-sob --g4 g4_points.json --fast fast.json --g4-ce refsob.json --exclude <nopa config>
```
