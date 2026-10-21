---
type: external
title: fastsimkit — the Mu2e JAX fast chain as a native kit
description: 'Native contract kit (kits.toml) for the JAX CRV fast sim/reco of RobMina/mu2e-fastsim on a compute endpoint, one Slurm job per point; UVA Rivanna/Afton over ssh today; smoke task reco_bench passes end to end (2026-10-21); kit host must be on the UVA network'
status: active
status_note: 'three tasks pass live (reco_bench, response_bench, v4_response); veto objectives in progress'
timestamp: '2026-10-21'
---

# fastsimkit — the Mu2e JAX fast chain as a native kit

## Summary
`fastsimkit` is the first Mu2eBO kit that evaluates a point on an HPC
endpoint instead of FermiGrid. It is a native kit: an MCP stdio server,
`mu2efastsim.endpoints.kit`, from
[RobMina/mu2e-fastsim-core](https://github.com/RobMina/mu2e-fastsim-core)
(public), with one `kits.toml` entry. One point is one batch job: the kit
renders a job body from a task template and the point's params, submits it
through an endpoint adapter (`mu2efastsim.endpoints.slurm_ssh`: `sbatch`,
`squeue`/`sacct`, `scancel` and a tar fetch over `ssh`), and on completion
reads `out/metrics.json`. The job runs the `mu2e-fastsim` Apptainer image
on one GPU.

## Key facts
- **Endpoint today:** UVA Rivanna/Afton, allocation `ldmxuva`, partition
  `gpu`, A100 (B200 also validated). The kit host must reach
  `login.hpc.virginia.edu` by ssh with a key: UVA answers only the UVA
  network or the UVA Anywhere VPN, so FermiGrid hosts and the AI node
  cannot drive it yet (needs a Globus Compute endpoint or a UVA-side relay).
- **Settings:** `$M2FS_CODE` (umbrella checkout, for `PYTHONPATH`),
  `FASTSIMKIT_ENDPOINT=uva`, `FASTSIMKIT_STATE_DIR` (job records, fetched
  outputs; `${DATA_ROOT}/fastsimkit`), `FASTSIMKIT_MAX_SU_PER_JOB` (the kit
  refuses a job whose estimated charge is larger; UVA measured ~60 SU per
  A100-hour, ~140 per B200-hour), `FASTSIMKIT_SIF` (image path on the site).
- **Params:** step `fixed` keys `task`, `events`, `seed`, `rows`, `sipms`;
  `describe` lists tasks and metrics. Tasks:
  - `reco_bench` (`events`, `seed`): synthetic CRV reco chain throughput;
    `events_per_s`, `first_call_s`, `events`.
  - `response_bench` (`rows`, `sipms`, `seed`): photon and SiPM throughput
    with the real CRVConditions table 3200_0 and photon map;
    `photon_rows_per_s`, `photons_per_row`, `sipms_per_s`,
    `charges_per_photon`.
  - `v4_response` (`events`, `seed`): V4 photon and SiPM checks (JAX vs
    numpy ports, real tables, synthetic muon CrvSteps); `v4_photons_pass`,
    `v4_sipm_pass` (1/0), `v4_*_max_abs_pe_rel`, `v4_*_jax_s`.
- **Copy-compute-delete:** `response_bench` and `v4_response` fetch the
  Mu2e-internal tables per job from the CVMFS Stratum-1 into node tmpfs
  (mode 700) and delete them in an EXIT trap that writes `out/cleanup.txt`;
  `results` refuses a table task without that record.
- **Idempotent submit:** a handle maps to one job; the same name with the
  same rendered script and resources returns it, a different one is refused
  (the toykit rule). Job records live in the state dir, so a restarted
  server finds its jobs; a final Slurm state is cached.
- **States:** Slurm `PENDING`/`RUNNING`/... -> `working`; `COMPLETED` with
  exit 0 -> `completed`; non-zero exit, `TIMEOUT`, `OUT_OF_MEMORY`,
  `NODE_FAIL`, ... -> `failed`; `CANCELLED*` -> `cancelled`.
- **Smoke study:** `tests/fixtures/engine_studies/fastsim_bench.json`
  (zero knobs, one step). Kit and study load through `kit_config` and
  `study.load_study_file` (`tests/test_kit_config.py`).
- **Live test (2026-10-21):** point `kitlive02.bench`, UVA job 21718574
  (A100-80, 3 SU): check -> submit -> working -> completed -> results,
  `events_per_s` 20,568; `kitlive03.resp` (job 21721089, 2 SU): photons
  394,427 rows/s, SiPM 46,625/s; `kitlive04.v4` (job 21721090, 6 SU): both
  V4 checks pass, max |PE deviation| 0.0012, deletion records present.
  Driven from the kit's Python class on the laptop.
- **`graph.run` end to end (2026-10-21):** `python -m graph.run --study
  fastsim_bench --config fsb02 --campaign fsbtest` on the laptop (UVA
  network), with `AUTORESEARCH_DATA_ROOT` on the laptop,
  `AUTORESEARCH_STUDY_PATH=tests/fixtures/engine_studies` and `M2FS_CODE`
  set: launch check, kit start over MCP, submit (UVA job 21722982,
  A100-40), poll, results, score, and one row in
  `leaderboard_fastsim_bench.tsv` (`events_per_s` 19,625, `first_call_s`
  64.1). The first try (fsb01) failed in 22 s: Mu2eBO sends every `number`
  param as a float, and `--max-events 200000.0` is not an int; the kit now
  renders integral floats as ints (mu2e-fastsim-core `51c91a2`).
- **Interpreter on a laptop:** a single-point `graph.run` needs only `mcp`
  2.x and `langgraph` (the picker's torch/botorch are `closed_loop` only).
  The conda env used had them added by pip; with `mcp` 2.3 the suite still
  fails 32 tests that pass under `ana 2.8.0` (mcp 2.3 hides a tool's error
  text, `activate.sh` wants /cvmfs, no botorch), none in the
  `fastsimkit` path.
- **Data rule:** the image and the repo hold no Mu2e-internal data; tasks
  that need the CRVConditions tables fetch them per job into node tmpfs and
  delete them (copy-compute-delete).

## Cross-links
- Contract: [contract-engine](/drivers/contract-engine.md) ("toykit", the
  reference native kit)
- Source: `mu2e-fastsim-core: mu2efastsim/endpoints/{__init__,slurm_ssh,kit}.py`,
  `tests/test_endpoints.py`, `uva/README.md`
- Endpoint record (validation, costs): ai-config wiki
  `mu2e/wiki/sim/uva-hpc-endpoint.md` (RobMina/ai-config-mu2e)

## Open questions / TODO
- Veto inefficiency and deadtime objectives (needs the full-detector
  configuration of the fast chain).
- Unattended use from FNAL: Globus Compute endpoint at UVA, or a relay.
- B200 needs an image with cuBLAS >= 13.2 before production use.
