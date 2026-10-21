---
type: external
title: fastsimkit — the Mu2e JAX fast chain as a native kit
description: 'Native contract kit (kits.toml) for the JAX CRV fast sim/reco of RobMina/mu2e-fastsim on a compute endpoint, one Slurm job per point; UVA Rivanna/Afton over ssh today; smoke task reco_bench passes end to end (2026-10-21); kit host must be on the UVA network'
status: active
status_note: 'smoke task only; physics tasks (real-table response, veto objectives) in progress'
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
- **Params:** step `fixed` keys `task`, `events`, `seed`; `describe` lists
  tasks and metrics. Task `reco_bench`: the synthetic CRV reco chain
  benchmark, metrics `events_per_s`, `first_call_s`, `events`.
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
  `events_per_s` 20,568. Driven from the kit's Python class on the laptop,
  not yet through `graph.run`.
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
- Physics tasks: CRV response with the real tables (stage-in inside the
  job), then the veto inefficiency/deadtime objectives.
- Run `fastsim_bench` through `graph.run` from a UVA-network host.
- Unattended use from FNAL: Globus Compute endpoint at UVA, or a relay.
- B200 needs an image with cuBLAS >= 13.2 before production use.
