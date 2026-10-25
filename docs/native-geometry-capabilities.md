# Native geometry and requested-metric admission (schema 1)

This is a technical handshake, not a validation of fast simulation physics.
It uses the existing contract engine and scheduler. No measurement-basis,
scoring, leaderboard, or adapter-version definition changes.

## Geometry identity

Every `offline_simpleconfig` render now supplies a FileRef with the existing
`name`, `uri`, and `kind` plus:

```json
{
  "sha256": "<64 lowercase hex digits: SHA-256 of the exact UTF-8 file bytes>",
  "metadata": {
    "format": "offline_simpleconfig",
    "schema_version": 1,
    "renderer": "offline_simpleconfig",
    "renderer_sha256": "<64 lowercase hex digits: definition identity>"
  }
}
```

`renderer_sha256` hashes exactly `{"geom": study.measure_basis["geom"],
"derive": study.measure_basis["derive"]}` with Python `json.dumps` using
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`,
`allow_nan=False`, encoded as UTF-8. These are the loaded geometry and derive
*definitions*, not the evaluated point, the whole study, or
`measure_basis_sha`. Different points normally have different file digests
and the same renderer digest. Changing an unrelated step setting does not
change the renderer digest. The existing study measurement identity is
untouched.

FileRef parsing preserves only the two agreed optional extensions, validates
lowercase SHA-256 and JSON-safe object metadata (finite numbers, string keys),
and ignores other top-level extensions. The old three-field FileRef remains
valid. Preflight-verdict reuse includes declared SHA and metadata identity as
well as actual local file content, avoiding reuse after a metadata change.

## Bounded fastsim input policy

The generic BO renderer is not a fastsim geometry interpreter. The native
fastsim admission check accepts only its strict numeric stopping-target overlay
whitelist and the base line:

```text
#include "Offline/Mu2eG4/geom/geom_common.txt"
```

Arbitrary other/nested includes and non-whitelisted geometry assignments are
not admitted by fastsim. BO does not resolve or upload include trees. The
receiving kit verifies the file digest and format and applies its bounded
parser before dispatch. Other BO kits can continue to use their existing,
more general Offline geometry. A successful BO render alone is not fastsim
admission.

`tests/fixtures/engine_studies/fastsim_beam_geometry.json` varies only the
numeric `stoppingTarget.deltaZ` overlay and uses `files: ["geom"]`. Its fixed
settings are `task: beam_point`, `muons`, `seed`, `repeat`; there is deliberately
no competing `fixed.geom`. The fixture names `fastsimkit_eaf`; replacing only
its kit with `fastsimkit` selects UVA. Endpoint availability and inputs still
belong to the kit. This fixture is not a production physics recommendation.

## Capability negotiation and requested metrics

`describe()` still takes no arguments and retains `params`, `metrics`, and
`accepts_lists`. Optional `capabilities_schema_version: 1`,
`requested_metrics_supported: true`, and `tasks` opt into richer admission.
Task descriptors retain `metrics` (name -> `availability`, `unit`, `reason`),
`metric_rules`, `metric_patterns`, defaults, and configs as JSON objects.
A metric's availability is `available`, `conditional`, or `unavailable`.
An advertised unknown schema version is refused. Without a version, old
unstructured `tasks` extensions are ignored as before.

Static launch checks reject task-local unknown metrics, known unavailable metrics
and unsupported rendered geometry. They apply declarative equality/membership
rules only to defaults/fixed/settings values whose parameters are not dynamically
mapped. Pattern names are checked against their declared configuration predicates;
unresolved predicates defer to the server. **Resolved admission still belongs to
the server with the exact parameters and files.** The old flat metric list is not
authoritative for opt-in task/config/pattern names.

Before a fresh submission, the scheduler resolves point mappings, settings,
fixed values, upstream metric parameters and input FileRefs as before, then
calls the optional `check_submission` hook **before writing `<step>_submit.json`**.
For an opted-in native kit, the hook calls `check` with the old four fields plus
`requested_metrics`: sorted names read by objectives, extra metrics, and all
other steps' `params_from` references. A negative verdict raises
`ContractError`, writes neither submit record nor handle, and never calls
`submit`. The approved metric list is held by deterministic handle and forwarded
as `requested_metrics` on submit, so the kit can enforce it again at admission
and results. An advertised metric-admission capability requires a `check` tool.

Legacy kits receive no new check call or submit field; adapter method signatures
are unchanged. Existing handles are polled without the new admission hook,
and completed records are adopted unchanged. `start` and `version` do not
invoke capability checks or consume geometry/input artifacts. Describe caching
is refreshed when the native client's server generation/version changes.

`check_study` uses the same hook after derive/render/preflight, on independent
steps only, without submitting or writing submit records. Steps whose
`params_from` or `files_from` dependencies are unresolved have an explicit skip
note; no upstream values/files are fabricated. This tests one selected point,
not all corners of the search space.

## Local tests

`tests/test_native_handshake.py` uses fake native clients for both endpoint
registry entries, not actual endpoint jobs. It covers extension round trips,
legacy payloads, admission refusal ordering, metric propagation, resume,
renderer/content identities, and the resolved `check_study` hook. Its full
LangGraph wiring test requires the optional installed `langgraph` runtime;
the hook and renderer unit tests do not. No dependencies are installed by tests.

For the actual BO/fastsim interface without network or physics, set
`FASTSIM_CODE_ROOT=<fastsim umbrella>` and run
`python3 -m unittest tests.test_fastsim_handshake_integration`. Its six tests
use the real native contract, renderer and FastsimKit with fake endpoint dispatch;
they cover both routes, result FileRef forwarding and explicit omission of
unavailable NaN placeholders. Opted-in fastsim requests retain only finite metrics,
with absent optional quantities explained in `metadata.unavailable_metrics`;
requested missing/nonfinite/sentinel values still fail. This satisfies the existing
BO finite-metric parser without weakening its rules.

Verification: 269 local tests passed plus one LangGraph skip in the dependency-light
selection. The isolated GPVM `ana 2.8.0` suite passed 380 tests, no skips, including
MCP/LangGraph and the cross-package tests. One earlier scheduler cancellation test
failed intermittently before the final passing run. No endpoint/physics jobs or
package installs were used. The geometry fixture needed the engine fixture-name
expectation updated. The kit implementation remains a bounded target-overlay
handshake, not full geometry or runtime snapshot certification.
