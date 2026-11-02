"""The fast stopping tier renders the same target as foilspfbpz_ax; the stand-in tools read point records.

Set FASTSIM_CODE_ROOT (the fastsim umbrella checkout) to also check that the
render passes fastsimkit's bounded geometry admission. Synthetic records only.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))
import study as st  # noqa: E402
from graph import latency, stand_in  # noqa: E402
from tests.engine_fixtures import ENGINE_STUDIES  # noqa: E402

FAST = ENGINE_STUDIES / "fastsim_foilspfbpz_stops.json"
G4 = ROOT / "mode_specs" / "foilspfbpz_ax.json"
X = [99.15, 57.44, 123.46, 0.0178, 0.1449, 0.0607, 0.297, 0.296, 0.502, 121.24]


class TestFastTierGeometry(unittest.TestCase):
    def setUp(self):
        self.fast, self.g4 = st.load_study_file(FAST), st.load_study_file(G4)

    def test_same_knobs_and_derive(self):
        a, b = json.loads(FAST.read_text()), json.loads(G4.read_text())
        self.assertEqual(a["knobs"], b["knobs"])
        self.assertEqual(a["derive"], b["derive"])

    def test_target_lines_equal_the_g4_studys(self):
        for x in (X, [(k["min"] + k["max"]) / 2 for k in json.loads(FAST.read_text())["knobs"]]):
            fast = stand_in.declarations(self.fast.geom.render(x))
            g4 = stand_in.target_overlay(self.g4.geom.render(x))
            self.assertEqual(stand_in.target_overlay(self.fast.geom.render(x))["text"], g4["text"])
            self.assertEqual(sorted(fast), sorted(g4["kept"]))
            self.assertEqual(fast["hasTSdA"], ("bool", "false"))
            self.assertNotIn("stoppingTarget.holeRadius", fast)

    @unittest.skipUnless(os.environ.get("FASTSIM_CODE_ROOT"), "needs FASTSIM_CODE_ROOT")
    def test_render_passes_fastsimkit_admission(self):
        sys.path.insert(0, str(Path(os.environ["FASTSIM_CODE_ROOT"]) / "core"))
        from mu2efastsim.endpoints import geometry
        _, keys = geometry.validate_text(self.fast.geom.render(X).encode("ascii"))
        self.assertIn("hasTSdA", keys)


class TestStandInTools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.grid = self.tmp / "grid"

    def point(self, config, spge, sob, t0=1000.0):
        state = self.grid / config / "state"
        state.mkdir(parents=True)
        (state / "point.json").write_text(json.dumps({"config": config, "study": "foilspfbpz_ax", "x": X}))
        (state / "summary.json").write_text(json.dumps({"steps": {
            "stops": {"stops_per_gen_event": spge, "n_gen_events": 3e6, "stops_per_pot": spge * 0.0128},
            "sob": {"sensitivity": sob}}}))
        (state / "geom.txt").write_text(st.load_study_file(G4).geom.render(X))
        for name, minutes in (("mubeam_cluster.txt", 1), ("mubeam_results.json", 91), ("evaluate_result.json", 200)):
            (state / name).write_text("{}")
            os.utime(state / name, (t0 + 60 * minutes, t0 + 60 * minutes))
        os.utime(state / "point.json", (t0, t0))

    def test_extract_and_compare(self):
        truth = {"a": 0.05, "b": 0.06, "c": 0.07, "d": 0.08}
        for i, (c, v) in enumerate(truth.items()):
            self.point(c, v, 1.0 + i)
        board = self.tmp / "board.tsv"
        board.write_text("config\tsob\n" + "".join(f"{c}\t1\n" for c in truth))
        pts = stand_in.extract(self.grid, stand_in.board_configs(board), self.tmp / "out")
        self.assertEqual(sorted(pts), sorted(truth))
        overlay = (self.tmp / "out" / "overlays" / "a.txt").read_text()
        self.assertTrue(overlay.startswith(f'#include "{stand_in.BASE}"'))
        self.assertIn("bool hasTSdA = false;", overlay)
        self.assertIn("stoppingTarget.holeRadius", pts["a"]["overlay_dropped"])
        fast = {c: {"stop_frac": v / 0.16, "stop_frac_err": 1e-3} for c, v in truth.items()}
        res = stand_in.compare(pts, fast, "a")
        self.assertAlmostEqual(res["stops"]["kappa"], 0.16)
        self.assertAlmostEqual(res["stops"]["ratio_mean"], 1.0, places=6)
        self.assertEqual(res["stops"]["spearman"]["value"], 1.0)
        self.assertEqual(res["sob"]["g4_stops"]["kendall"]["value"], 1.0)
        fixed = stand_in.compare(pts, fast, None, kappa=0.16)
        self.assertEqual(fixed["stops"]["kappa_source"], "given")
        self.assertEqual(fixed["stops"]["held_out_points"], 4)
        self.assertAlmostEqual(fixed["stops"]["ratio_mean"], 1.0, places=6)

    def test_latency_timeline(self):
        self.point("a", 0.05, 1.0)
        pts = latency.scan(self.grid, configs=["a"])
        self.assertEqual(pts[0]["steps"]["mubeam"]["wall_min"], 90.0)
        self.assertEqual(pts[0]["scored_min"], 200.0)
        self.assertEqual(latency.summarise(pts)["foilspfbpz_ax"]["points"], 1)


if __name__ == "__main__":
    unittest.main()
