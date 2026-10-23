---
type: external
title: fastsimkit — the Mu2e JAX fast chain as a native kit
description: 'Native contract kit (kits.toml) for the JAX CRV fast sim/reco of RobMina/mu2e-fastsim on a compute endpoint, one Slurm job per point; UVA Rivanna/Afton over ssh today; smoke task reco_bench passes end to end (2026-10-21); kit host must be on the UVA network'
status: active
status_note: 'four tasks pass live (reco_bench, response_bench, v4_response, chain_point); veto objectives in progress'
timestamp: '2026-10-22'
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
- **Params:** step `fixed` keys `task`, `events`, `seed`, `rows`, `sipms`,
  `shards`, `files_per_job`, `shard_offset`, `max_events`, `wall_minutes`;
  `describe` lists tasks and metrics. Tasks:
  - `reco_bench` (`events`, `seed`): synthetic CRV reco chain throughput;
    `events_per_s`, `first_call_s`, `events`.
  - `response_bench` (`rows`, `sipms`, `seed`): photon and SiPM throughput
    with the real CRVConditions table 3200_0 and photon map;
    `photon_rows_per_s`, `photons_per_row`, `sipms_per_s`,
    `charges_per_photon`.
  - `chain_point` (`shards`, `files_per_job`, `shard_offset`, `max_events`,
    `seed`, `wall_minutes`): one point of the whole fast chain as `shards`
    one-GPU Slurm jobs (whole GPU nodes queue for hours, single GPUs start in
    minutes). Each job's Mu2e-internal inputs (tier-1b library, CSVs, GDML,
    its gen tables) are streamed from `FASTSIMKIT_FNAL_HOST` through the kit
    host into the job's memory (md5-checked, never on disk); the kit refuses
    results without each job's deletion record. Metrics: `accepted_events`,
    `generated_events`, `accepted_per_gpu_hour` (charged and `_chain`),
    `accepted_per_est_su`, `input_wait_s_mean`, `campaign_s`,
    `clusters_per_generated_{EX,T1,T2,M}`. At most 2 file streams per job and
    6 overall: one ssh connection per host carries 10 channels (sshd
    MaxSessions), and 4 streams x 2 jobs failed (point kitcp02).
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
- **`chain_point` through `graph.run` (2026-10-22):** study
  `tests/fixtures/engine_studies/fastsim_chain_point.json` (4 sub-jobs x 20
  gen files), config fcp01, UVA jobs 21773784-87: one leaderboard row,
  **177,345 accepted events for 66 SU (2,690 per SU, MAM)**, 0.67 M accepted
  per A100-hour of chain time, input streaming 76 % of the charged time
  (485-735 s per sub-job against ~240 s of chain). The 20 gen shards were
  reused with different seeds: more shards are needed for production points.
- **`chain_point` on crygen_v1 through `graph.run` (2026-10-23):** study
  `tests/fixtures/engine_studies/fastsim_chain_point_crygen.json`
  (`gen_set` 1: 94 x 1 M CRY-only gen tables; 4 sub-jobs x 6 distinct files,
  wall 60 min), config fcg01, code from a clean clone of the public umbrella
  `66bd1aa` (mu2e-fastsim-core `6692a00`), UVA jobs 21852275-78: one row in
  `leaderboard_fastsim_chain_point_crygen.tsv`, **1,067,794 accepted events
  of 24 M generated for 57 SU (MAM; ~18,700 per SU, 7x fcp01)**, 13-14 min
  per sub-job, campaign 18 min, input wait 100-155 s. The job-side input
  deadline now scales with the bytes the point streams (here 1,946 s; it was
  a fixed 30 min, which 4 x 20 crygen files overran), and the kit's `check`
  refuses a point whose input budget plus estimated chain time does not fit
  in `wall_minutes`.
- **Interpreter on a laptop:** a single-point `graph.run` needs only `mcp`
  2.x and `langgraph` (the picker's torch/botorch are `closed_loop` only).
  The conda env used had them added by pip; with `mcp` 2.3 the suite still
  fails 32 tests that pass under `ana 2.8.0` (mcp 2.3 hides a tool's error
  text, `activate.sh` wants /cvmfs, no botorch), none in the
  `fastsimkit` path.
- **Data rule:** the image and the repo hold no Mu2e-internal data; tasks
  that need the CRVConditions tables fetch them per job into node tmpfs and
  delete them (copy-compute-delete).

## On Fermilab EAF (`fastsimkit_eaf`, 2026-10-22)
- **Entry `[fastsimkit_eaf]`** in `kits.toml`: the same kit with
  `FASTSIMKIT_ENDPOINT=eaf` (`mu2efastsim.endpoints.eaf`, mu2e-fastsim-core
  `90bc604`). The kit host must be on the FNAL network (EAF answers nothing
  else) with `EAF_TOKEN_FILE` naming a mode-600 JupyterHub API token. Jobs
  run in the user's EAF server on one A100 MIG slice (1g.10gb, 2g.20gb or
  4g.40gb), reading inputs in place from /exp.
- **Tasks only EAF runs:** `chain` (the whole fast chain on held-out G4
  shards against the stock chain) and `regression` (unit tests, V4 photons
  and SiPM, reco reproducibility, the chain on one shard; `regression_pass`
  1/0). They read Mu2e-internal inputs in place, so the kit refuses them on
  an endpoint that does not mount FNAL storage (UVA).
- **One slice per user:** points queue on the endpoint and run in order;
  `submit` can take minutes while the server starts (timeout 1800 s), and
  concurrent submits are serialized (two simultaneous server starts got HTTP
  500 from the hub). The server stops itself `EAF_LINGER_S` (300 s here)
  after the last result.
- **Study `fastsim_eaf_regression`** (tests/fixtures/engine_studies; zero
  knobs; steps `regress` and `chain`). `graph.run --study
  fastsim_eaf_regression --config eafreg02 --campaign eafreg` from
  mu2egpvm02 (ana 2.8.0, data root under the user's crv_fastsim area):
  both steps on 1g.10gb, launched 46 s apart and run back to back,
  completed in 18 min; one row, `events_with_steps_per_s` 28.4,
  `max_abs_cluster_rate_rel` 0.027 against the stock chain,
  `regression_pass` 0, from two parts: one SiPM time-quantile cell (the
  known noise of that check on a small sample) and one unit test,
  `crv/tests/test_oneapi_batch.py::test_chain_digis_bucketed_default_equals_unbucketed`
  ("volt": bucketed and unbucketed waveform synthesis differ on the EAF GPU;
  it passes on CPU). A run the evening before, with the same code otherwise,
  gave `regression_pass` 1. A first point (eafreg01, before the
  serialization) failed with the HTTP 500 above.

## Veto tier C4 on EAF (`veto_c4`, 2026-10-23)
- Task `veto_c4` (mu2e-fastsim-core `8a0f90c`; `crvfastsim.veto` in crv main `16102c3`): a CRV veto card from the
  point's params (`selection`, `dt_lo`, `dt_hi`, `min_pe`, `min_layers`, `gate_start_lo`, `gate_end_hi`, `pmin`,
  `pmax`) applied to a cached Run 1A selection (`run1a_dev`: S. Grant's `dev` cut set, 10,279 CE-like cosmic tracks);
  metrics `inefficiency`, `vetoed_frac` (+ Jeffreys interval), counts. CPU only; `fastsimkit_eaf` only.
- Study `fastsim_veto_c4_run1a` (knobs `dt_hi`, `min_pe`; objective inefficiency). `graph.closed_loop --study
  fastsim_veto_c4_run1a --q 2 --max-evals 6 --picker qlnei --name-prefix vc4a` from mu2egpvm02 (needs
  `AUTORESEARCH_SURROKIT` at pin `26929f7`): 6/6 ok. Inefficiency 0.752 (67 ns), 0.689 (93 ns), 0.646 above ~150 ns;
  `min_pe` 0-60 is inert (coincidences >= 266 PE). Single objective by design until the deadtime objective exists.
- The `fastsimkit-eaf` branch was folded into `fastsimkit` (2026-10-23); both kit entries live here.

## Cross-links
- Contract: [contract-engine](/drivers/contract-engine.md) ("toykit", the
  reference native kit)
- Source: `mu2e-fastsim-core: mu2efastsim/endpoints/{__init__,slurm_ssh,kit}.py`,
  `tests/test_endpoints.py`, `uva/README.md`
- Endpoint record (validation, costs): ai-config wiki
  `mu2e/wiki/sim/uva-hpc-endpoint.md` (RobMina/ai-config-mu2e)

## Open questions / TODO
- Input streaming dominates a short `chain_point` job (the FNAL leg runs at
  ~2.4 MB/s, the library is 1 GB): use many files per job.
- Veto inefficiency and deadtime objectives (needs the full-detector
  configuration of the fast chain).
- Unattended use from FNAL: Globus Compute endpoint at UVA, or a relay.
- B200 needs an image with cuBLAS >= 13.2 before production use.
