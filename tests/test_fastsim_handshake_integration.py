"""Opt-in BO/real-fastsim handshake; synthetic fixtures, never physics or network.

Run with FASTSIM_CODE_ROOT pointing at the fastsim umbrella checkout (core/,
crv/, beam/). Uses unittest and the BO/fastsim source trees; no pytest, GPU,
MCP server, endpoint credentials, LangGraph runtime, or dependency installation.
All job completion, fingerprints, and metric values below are synthetic test
fixtures. Generated job scripts are recorded, NEVER executed.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FASTSIM_CODE_ROOT = os.environ.get("FASTSIM_CODE_ROOT")
# Do not import either application's runtime when the integration is not opted in.
if FASTSIM_CODE_ROOT:
    fsroot = Path(FASTSIM_CODE_ROOT).expanduser().resolve()
    sys.path[:0] = [str(ROOT / "core"), str(ROOT / "graph"), str(ROOT),
                   *(str(fsroot / part) for part in ("core", "crv", "beam"))]
    import contract as ct
    import kit_registry
    import scheduler
    import study as st
    from point_dir import PointDir
    from study_graph import render_geometry
    from tests.engine_fixtures import Kits, write_study
    from mu2efastsim.endpoints.kit import FastsimKit
    from mu2efastsim.endpoints.chainpoint import FnalSource
    from beamfastsim import kit_uva


class SyntheticEAFEndpoint:
    """Runtime/dispatch double only: no hub, mount, shell, or physics execution."""
    name = "eaf"
    fnal_data = "/synthetic/not-mounted/crv_fastsim"

    def __init__(self):
        self.submissions = []

    def runtime(self):
        return {"a": "M2FS_CODE=/synthetic/code; M2FS_VENV=/synthetic/venv\n"
                     "A() { return 0; }\n", "t": "T() { return 0; }\n"}

    def submit(self, name, body, resources):
        self.submissions.append((name, body, resources))

    def status(self, handle):
        assert any(name == handle for name, _, _ in self.submissions), handle
        return SimpleNamespace(state="completed", site_state="SYNTHETIC_COMPLETED",
                               message="test fixture, no physics ran", job_id="synthetic")

    def _sh(self, *args, **kwargs):
        raise AssertionError("remote shell is forbidden in this integration test")

    def fetch(self, *args, **kwargs):
        raise AssertionError("results must come from the explicitly synthetic _results double")


class SyntheticSlurmEndpoint(SyntheticEAFEndpoint):
    name = "uva"
    fnal_data = None
    host = "synthetic.invalid"
    account = "synthetic"
    partition = "synthetic"
    workdir = "/synthetic/work"

    def stream_in(self, *args, **kwargs):
        raise AssertionError("no physics input transfer is permitted")


class InProcessNativeClient:
    """Keep the real tool payload/reply boundary; replace only MCP transport."""
    def __init__(self, kit):
        self.kit = kit
        self.calls, self.replies = [], []
        self.started = True
        self.server_version = "synthetic-handshake-v1"
        self.campaign = "synthetic-handshake"
        self.tools = frozenset((*ct.REQUIRED_TOOLS, "describe", "check"))

    def start(self):
        self.started = True

    def close(self):
        pass

    def call(self, tool, args, **kwargs):
        if tool not in self.tools:
            raise AssertionError(tool)
        self.calls.append((tool, copy.deepcopy(args)))
        reply = getattr(self.kit, tool)(**args)
        self.replies.append((tool, copy.deepcopy(reply)))
        return reply


@unittest.skipUnless(FASTSIM_CODE_ROOT, "set FASTSIM_CODE_ROOT to opt into the real fastsim source handshake")
class FastsimHandshakeIntegration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-bo-fastsim-")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        # Fail closed if a future admission/delegate implementation attempts I/O.
        self.patch("subprocess.Popen", side_effect=AssertionError("subprocess execution forbidden"))
        self.patch("socket.create_connection", side_effect=AssertionError("network forbidden"))

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def patch_object(self, obj, name, **kwargs):
        patcher = mock.patch.object(obj, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def study(self, endpoint="fastsimkit_eaf", mutate=None):
        doc = json.loads((ROOT / "tests/fixtures/engine_studies/fastsim_beam_geometry.json").read_text())
        doc["evaluate"][0]["kit"] = endpoint
        if mutate:
            mutate(doc)
        return st.load_study_file(write_study(doc, self.tmp / "studies"))

    def bridge(self, endpoint="fastsimkit_eaf"):
        ep = SyntheticEAFEndpoint() if endpoint == "fastsimkit_eaf" else SyntheticSlurmEndpoint()
        real = FastsimKit(endpoint=ep, state_dir=self.tmp / endpoint, sif="/synthetic/image.sif")
        client = InProcessNativeClient(real)
        native = ct.NativeKit(kit_registry.NATIVE[endpoint], client, pause=lambda _: None)
        return native, client, real, ep

    def synthetic_results(self, real):
        """Mock only raw extraction; real results() still checks capabilities/evidence.

        Geometry proof is an explicit local fixture, not evidence of a real job.
        No metrics.json or simulated physics artifact is written.
        """
        def extract(handle):
            artifact = real._request(handle)["geometry"]
            out = real.out_root / handle / "out"
            out.mkdir(parents=True, exist_ok=True)
            if artifact is not None:
                (out / "geometry.txt").write_bytes(artifact["text"].encode("ascii"))
                (out / "geometry-artifact.json").write_text(json.dumps(
                    {k: v for k, v in artifact.items() if k != "text"}))
            diagnostic = out / "synthetic-diagnostic.txt"
            diagnostic.write_text("SYNTHETIC handshake fixture. No physics job was executed.\n")
            return {"metrics": {"stop_frac": 0.25, "stops": 2},
                    "files": [{"name": diagnostic.name, "uri": diagnostic.as_uri(), "kind": "text"}],
                    "metadata": {"synthetic": True, "physics_executed": False}}
        return self.patch_object(real, "_results", side_effect=extract)

    def run_steps(self, study, native, refs, config="candidate", delta=22):
        state = self.tmp / (config + "-state")
        outcomes = scheduler.run_steps(
            study, config=config, state_dir=state, env={"deltaZ": delta}, files=refs,
            kits=Kits(native), workflow=lambda step: f"synthetic/{config}/{step}",
            sleep=lambda _: self.fail("synthetic jobs should already be completed"), log=lambda _: None)
        return outcomes, state

    def test_real_describe_schema_preserves_task_rules_patterns_and_configs(self):
        native, client, real, _ = self.bridge()
        description = native.describe()
        raw = real.describe()
        self.assertEqual(description.capabilities_schema_version, 1)
        self.assertTrue(description.requested_metrics_supported)
        self.assertEqual(description.tasks, raw["tasks"])
        self.assertEqual(description, ct.parse_describe(raw, native.name))
        self.assertIn("beam_point", description.tasks)
        self.assertIn("crv_response", description.tasks)
        self.assertTrue(description.tasks["beam_point"]["metric_rules"])
        self.assertTrue(description.tasks["chain_point"]["metric_patterns"])
        self.assertIn("configurations", description.tasks["chain_point"])
        self.assertEqual(client.calls, [("describe", {})])
        json.dumps(raw, allow_nan=False)

    def test_two_bo_renders_pass_actual_eaf_kit_with_shared_measure_and_renderer(self):
        study = self.study()
        native, client, real, ep = self.bridge()
        extraction = self.synthetic_results(real)
        basis = copy.deepcopy(study.measure_basis)
        measure_sha = study.measure_basis_sha
        refs = []
        for i, delta in enumerate((20, 24)):
            path = self.tmp / f"candidate{i}.txt"
            ref = render_geometry(study, path, [delta])
            refs.append(ref)
            outcomes, _ = self.run_steps(study, native, {"geom": ref}, config=f"point{i}", delta=delta)
            self.assertTrue(outcomes["beam"].ok, outcomes["beam"].message)
            self.assertEqual(ref["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            request = real._request(f"point{i}.beam")
            self.assertEqual(request["geometry"]["reference"], ref)
            self.assertEqual(request["geometry"]["text"], path.read_text())
            self.assertEqual(request["requested_metrics"], ["stop_frac"])
            self.assertIn('GEOM="$M2FS_GEOMETRY"', request["body"])
            calls = {tool: args for tool, args in client.calls
                     if args.get("name") == f"point{i}.beam"}
            self.assertEqual(calls["check"], calls["submit"])
            self.assertEqual(calls["submit"]["requested_metrics"], ["stop_frac"])
            self.assertEqual(calls["submit"]["files"], [ref])
            self.assertNotIn("geom", calls["submit"]["params"])
        self.assertNotEqual(refs[0]["sha256"], refs[1]["sha256"])
        self.assertEqual(refs[0]["metadata"], refs[1]["metadata"])
        self.assertEqual(study.measure_basis, basis)
        self.assertEqual(study.measure_basis_sha, measure_sha)
        renderer_bytes = json.dumps({k: basis[k] for k in ("geom", "derive")}, sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
        self.assertEqual(refs[0]["metadata"]["renderer_sha256"], hashlib.sha256(renderer_bytes).hexdigest())
        self.assertEqual(len(ep.submissions), 2)
        self.assertNotEqual(ep.submissions[0][1], ep.submissions[1][1])
        self.assertEqual(extraction.call_count, 2)
        self.assertEqual([t for t, _ in client.calls].count("status"), 2)

    def test_repeat_zero_warm_objective_refused_before_scheduler_write_submit(self):
        def warm(doc):
            doc["evaluate"][0]["fixed"]["repeat"] = 0
            doc["objectives"][0]["metric"] = "beam.muons_per_s_warm"
        for endpoint in ("fastsimkit_eaf", "fastsimkit"):
            with self.subTest(endpoint=endpoint):
                study = self.study(endpoint, warm)
                native, client, real, ep = self.bridge(endpoint)
                ref = render_geometry(study, self.tmp / f"{endpoint}-geom.txt", [22])
                with mock.patch.object(PointDir, "write_submit", autospec=True) as write_submit:
                    outcomes, state = self.run_steps(study, native, {"geom": ref}, config=endpoint)
                self.assertFalse(outcomes["beam"].ok)
                self.assertIn("muons_per_s_warm", outcomes["beam"].message)
                self.assertIn("unavailable", outcomes["beam"].message)
                write_submit.assert_not_called()
                self.assertFalse(PointDir(state).path("beam_submit.json").exists())
                self.assertIsNone(PointDir(state).handle("beam"))
                self.assertEqual([t for t, _ in client.calls], ["describe", "check"])
                self.assertEqual(client.calls[-1][1]["requested_metrics"], ["muons_per_s_warm"])
                self.assertEqual(ep.submissions, [])
                self.assertIsNone(real._request(f"{endpoint}.beam"))

    def test_actual_beam_delegate_binds_bo_geometry_with_synthetic_fingerprints(self):
        study = self.study("fastsimkit")
        native, client, real, ep = self.bridge("fastsimkit")
        # Exercise FnalSource.fingerprint and its parser with an injected runner;
        # only the returned fingerprint bytes are synthetic, never remotely read.
        synthetic_bytes = b"SYNTHETIC fingerprint fixture; not a muon table"
        fingerprint = {"md5": hashlib.md5(synthetic_bytes).hexdigest(),
                       "sha256": hashlib.sha256(synthetic_bytes).hexdigest(), "size": len(synthetic_bytes)}
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["synthetic-fingerprint"], 0, json.dumps(fingerprint).encode(), b""))
        source = FnalSource(host="synthetic.invalid", root="/synthetic/beam", runner=runner)
        delegate = kit_uva.BeamPointStream(real, source=source, code="/synthetic/code", background=False)
        real._delegates["beam_point"] = delegate
        prepare = self.patch_object(kit_uva, "prepare_code", return_value={
            "path": "/synthetic/code.snapshots/" + "e" * 32, "umbrella_commit": "a" * 40,
            "submodules": {"core": "b" * 40}, "requested_ref": "synthetic",
            "policy": {"name": "retained-readonly-git-v1", "modules": ["core"], "branch": ""}})
        image = self.patch_object(kit_uva, "resolve_image", return_value={
            "path": real.sif, "sha256": "c" * 64, "size": 1, "policy": "synthetic-test-only"})
        self.synthetic_results(real)
        ref = render_geometry(study, self.tmp / "slurm-geom.txt", [22])
        outcomes, _ = self.run_steps(study, native, {"geom": ref})
        self.assertTrue(outcomes["beam"].ok, outcomes["beam"].message)
        self.assertIs(real._delegate(handle="candidate.beam"), delegate)
        record = delegate._rec("candidate.beam")
        self.assertEqual(record["manifest"]["geometry"]["reference"], ref)
        self.assertIn('--geom "$M2FS_GEOMETRY"', record["body"])
        self.assertEqual(ep.submissions[0][1], record["body"])
        self.assertEqual(record["manifest"]["inputs"]["files"][0]["sha256"], fingerprint["sha256"])
        runner.assert_called_once()
        prepare.assert_called_once()
        image.assert_called_once()
        self.assertEqual([t for t, _ in client.calls], ["describe", "check", "submit", "status", "results"])

    def test_files_from_roundtrip_passes_only_verified_geom_not_diagnostics(self):
        def downstream(doc):
            step = copy.deepcopy(doc["evaluate"][0])
            step.update(step="down", files=[], files_from=["beam"])
            doc["evaluate"].append(step)
            doc["extra_metrics"] = [{"name": "downstream", "metric": "down.stop_frac", "fmt": "{:.4f}"}]
        study = self.study(mutate=downstream)
        native, client, real, _ = self.bridge()
        self.synthetic_results(real)
        ref = render_geometry(study, self.tmp / "upstream-geom.txt", [22])
        outcomes, _ = self.run_steps(study, native, {"geom": ref})
        for outcome in outcomes.values():
            self.assertTrue(outcome.ok, outcome.message)
        raw = next(reply for tool, reply in client.replies if tool == "results")
        parsed = ct.parse_results(raw, native.name)
        self.assertEqual(len(parsed.files), 1)
        propagated = parsed.files[0]
        self.assertEqual(propagated["kind"], "geom")
        self.assertEqual(propagated["sha256"], ref["sha256"])
        self.assertEqual(propagated["metadata"], ref["metadata"])
        self.assertNotEqual(propagated["uri"], ref["uri"])
        self.assertTrue(parsed.metadata["synthetic"])
        self.assertFalse(parsed.metadata["physics_executed"])
        self.assertEqual(len(parsed.metadata["diagnostic_files"]), 1)
        self.assertEqual(parsed.metadata["diagnostic_files"][0]["kind"], "text")
        for tool, args in client.calls:
            if tool in ("check", "submit") and args["name"] == "candidate.down":
                self.assertEqual(args["files"], [])
                self.assertEqual(args["inputs"], [propagated])
                self.assertEqual(args["requested_metrics"], ["stop_frac"])
        self.assertEqual(real._request("candidate.down")["geometry"]["reference"], propagated)

    def test_nonextracted_chain_type_metric_removes_nan_placeholders_explicitly(self):
        native, client, real, ep = self.bridge("fastsimkit")
        params = {"task": "chain_point", "shards": 1, "files_per_job": 1,
                  "shard_offset": 0, "max_events": 10, "seed": 1, "wall_minutes": 120,
                  "gen_set": 2, "run_config": 1}
        metric = "clusters_per_generated_type5"
        # Real ChainPoint.check validates route, parameters and resource budget.
        # The dispatcher alone is stubbed: no fan-out jobs or physics artifacts.
        dispatch = self.patch_object(real.chain_point, "submit", return_value={"handle": "synthetic.chain"})
        native.check_submission("synthetic.chain", params, [], [], [metric], "synthetic/chain")
        self.assertEqual(native.submit("synthetic.chain", params, [], [], "synthetic/chain"), "synthetic.chain")
        dispatch.assert_called_once()
        placeholders = {f"clusters_per_generated_{sector}": float("nan") for sector in ("EX", "T1", "T2", "M")}
        raw = {"metrics": {metric: 0.125, **placeholders}, "files": [],
               "metadata": {"synthetic": True, "physics_executed": False}}
        extraction = self.patch_object(real, "_results", side_effect=lambda _: copy.deepcopy(raw))
        with self.assertRaises(ValueError):
            json.dumps(raw, allow_nan=False)  # Producer placeholders are not JSON-safe.
        parsed = native.results("synthetic.chain", "synthetic/chain")
        self.assertEqual(parsed, ct.parse_results(client.replies[-1][1], native.name))
        self.assertEqual(parsed.metrics, {metric: 0.125})
        self.assertTrue(all(math.isfinite(v) for v in parsed.metrics.values()))
        self.assertEqual(parsed.metadata["requested_metrics"], [metric])
        for name in placeholders:
            self.assertNotIn(name, parsed.metrics)
            self.assertTrue(parsed.metadata["unavailable_metrics"][name])
            self.assertEqual(parsed.metadata["capabilities"]["metrics"][name]["availability"], "unavailable")
            self.assertTrue(math.isnan(raw["metrics"][name]))
        self.assertEqual(parsed.files, ())
        self.assertTrue(parsed.metadata["synthetic"])
        self.assertFalse(parsed.metadata["physics_executed"])
        json.dumps(client.replies[-1][1], allow_nan=False)
        extraction.assert_called_once_with("synthetic.chain")
        self.assertEqual(ep.submissions, [])
        # Requested missing/nonfinite dynamic output must still fail, not vanish.
        for metrics in (placeholders, {metric: float("nan"), **placeholders}):
            raw["metrics"] = metrics
            with self.subTest(metrics=metrics), self.assertRaisesRegex(ValueError, "missing|finite"):
                real.results("synthetic.chain")


if __name__ == "__main__":
    unittest.main()
