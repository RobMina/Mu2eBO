"""Bounded native handshake: fake clients only, never UVA/EAF endpoints."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'core'), str(ROOT / 'graph'), str(ROOT)]
import contract as ct
import kit_registry
import scheduler
import study as st
import check_study as cs
from point_dir import PointDir
from study_graph import geometry_file_ref, preflight_basis, render_geometry
from tests.engine_fixtures import TmpCase, Kits, write_study

FIXTURE = ROOT / 'tests/fixtures/engine_studies/fastsim_beam_geometry.json'
LEGACY = {'params': ['task', 'muons', 'seed', 'repeat', 'background'],
          'metrics': ['stop_frac'], 'accepts_lists': False}
DESCRIBE = dict(LEGACY, capabilities_schema_version=1,
               requested_metrics_supported=True, tasks={'beam_point': {
                   'defaults': {'background': 0},
                   'metrics': {'stop_frac': {'availability': 'available', 'unit': '1', 'reason': ''}},
                   'metric_rules': [], 'metric_patterns': [], 'configs': {}}})


class Client:
    def __init__(self, describe=None, ok=True):
        self.description = copy.deepcopy(DESCRIBE if describe is None else describe)
        self.ok, self.calls = ok, []
        self.started, self.server_version, self.campaign = True, 'fake-v1', 'test'
        self.tools = frozenset((*ct.REQUIRED_TOOLS, 'describe', 'check'))
        self.on_check = lambda args: None
        self.on_submit = lambda args: None

    def call(self, tool, args, **kw):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == 'describe':
            return self.description
        if tool == 'check':
            self.on_check(args)
            return {'ok': self.ok, 'message': 'available' if self.ok else 'metric unavailable for resolved config'}
        if tool == 'submit':
            self.on_submit(args)
            return {'handle': args['name']}
        if tool == 'status':
            return {'state': 'completed', 'message': '', 'poll_ms': 0, 'progress': None}
        if tool == 'results':
            return {'metrics': {'stop_frac': .5}, 'files': [], 'metadata': {}}
        raise AssertionError(tool)

    def start(self):
        self.started = True

    def close(self):
        pass


class Parsers(unittest.TestCase):
    def test_file_extensions_round_trip_and_unknown_top_level_ignored(self):
        ref = {'name': 'geom', 'kind': 'geom', 'uri': 'file:///geom',
               'sha256': 'a' * 64, 'metadata': {'format': 'offline_simpleconfig', 'v': [1, None, True]}}
        result = ct.parse_results({'metrics': {}, 'files': [dict(ref, ignored=7)], 'metadata': {}}, 'fake')
        self.assertEqual(result.files, (ref,))
        self.assertIsNot(result.files[0]['metadata'], ref['metadata'])
        self.assertEqual(ct.parse_file_ref({k: ref[k] for k in ('name', 'kind', 'uri')}, 'k', 'r'),
                         {k: ref[k] for k in ('name', 'kind', 'uri')})

    def test_invalid_extensions(self):
        ref = {'name': 'geom', 'kind': 'geom', 'uri': 'file:///geom'}
        for key, values in {'sha256': ['A'*64, 'a'*63, '', None, 4],
                            'metadata': [[], None, {'x': float('nan')}, {'x': float('inf')},
                                         {1: 'x'}, {'x': (1,)}, {'x': object()}]}.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ct.ContractError):
                    ct.parse_file_ref(dict(ref, **{key: value}), 'k', 'r')

    def test_describe_extension_and_legacy(self):
        old = ct.parse_describe(LEGACY, 'k')
        self.assertEqual(old, ct.Describe(tuple(LEGACY['params']), ('stop_frac',), False))
        new = ct.parse_describe(DESCRIBE, 'k')
        self.assertEqual(new.tasks, DESCRIBE['tasks'])
        self.assertTrue(new.requested_metrics_supported)
        self.assertEqual(ct.parse_describe(dict(LEGACY, tasks=['old-extension']), 'k'), old)

    def test_unknown_or_malformed_capability_version_refused(self):
        for version in (2, None, True, '1'):
            with self.subTest(version=version), self.assertRaises(ct.ContractError):
                ct.parse_describe(dict(DESCRIBE, capabilities_schema_version=version), 'k')
        for extra in ({'requested_metrics_supported': 1}, {'tasks': []}):
            with self.assertRaises(ct.ContractError):
                ct.parse_describe(dict(DESCRIBE, **extra), 'k')


class Handshake(TmpCase):
    def study(self, endpoint='fastsimkit_eaf', mutate=None):
        doc = json.loads(FIXTURE.read_text())
        doc['evaluate'][0]['kit'] = endpoint
        if mutate:
            mutate(doc)
        return st.load_study_file(write_study(doc, self.tmp / endpoint))

    def kit(self, endpoint='fastsimkit_eaf', **kw):
        client = Client(**kw)
        kit = ct.NativeKit(kit_registry.NATIVE[endpoint], client, pause=lambda _: None)
        return kit, client

    def run_step(self, study, kit, state=None, files=None):
        return scheduler.run_steps(study, config='c', state_dir=state or self.tmp / 'state',
                                   env={'deltaZ': 22}, files=files or {'geom': {'name':'geom', 'kind':'geom',
                                   'uri':'file:///unused'}}, kits=Kits(kit),
                                   workflow=lambda step: f'test/c/{step}', log=lambda _: None)

    def test_both_endpoints_check_then_forward_metrics_before_submit_record(self):
        for endpoint in ('fastsimkit', 'fastsimkit_eaf'):
            with self.subTest(endpoint=endpoint):
                study, (kit, client) = self.study(endpoint), self.kit(endpoint)
                state = self.tmp / endpoint / 'state'
                pd = PointDir(state)
                client.on_check = lambda _: self.assertFalse(pd.path('beam_submit.json').exists())
                client.on_submit = lambda _: self.assertTrue(pd.path('beam_submit.json').exists())
                state.mkdir(parents=True)
                ref = render_geometry(study, state / 'geom.txt', [22])
                self.assertTrue(self.run_step(study, kit, state, {'geom': ref})['beam'].ok)
                payloads = {tool: args for tool, args in client.calls}
                self.assertEqual(payloads['check']['requested_metrics'], ['stop_frac'])
                self.assertEqual(payloads['submit'], payloads['check'])
                self.assertEqual(payloads['describe'], {})
                self.assertEqual(payloads['submit']['files'], [ref])
                self.assertNotIn('geom', payloads['submit']['params'])

    def test_refusal_before_submit_record_or_handle_both_endpoints(self):
        for endpoint in ('fastsimkit', 'fastsimkit_eaf'):
            with self.subTest(endpoint=endpoint):
                kit, client = self.kit(endpoint, ok=False)
                state = self.tmp / endpoint / 'state'
                out = self.run_step(self.study(endpoint), kit, state)['beam']
                self.assertFalse(out.ok)
                self.assertIn('metric unavailable', out.message)
                self.assertFalse(PointDir(state).path('beam_submit.json').exists())
                self.assertIsNone(PointDir(state).handle('beam'))
                self.assertNotIn('submit', [t for t, _ in client.calls])

    def test_legacy_describe_or_no_describe_keeps_old_payload(self):
        for has_describe in (True, False):
            kit, client = self.kit(describe=LEGACY)
            if not has_describe:
                client.tools -= {'describe'}
            state = self.tmp / str(has_describe)
            self.assertTrue(self.run_step(self.study(), kit, state)['beam'].ok)
            self.assertNotIn('check', [t for t, _ in client.calls])
            args = next(a for t, a in client.calls if t == 'submit')
            self.assertEqual(set(args), {'name', 'params', 'files', 'inputs'})

    def test_advertised_admission_without_check_fails_before_record(self):
        kit, client = self.kit()
        client.tools -= {'check'}
        out = self.run_step(self.study(), kit)['beam']
        self.assertFalse(out.ok)
        self.assertIn('requires check', out.message)
        self.assertFalse((self.tmp / 'state/beam_submit.json').exists())
        self.assertEqual([t for t, _ in client.calls], ['describe'])

    def test_explicit_opt_out_keeps_legacy_payload(self):
        kit, client = self.kit(describe=dict(DESCRIBE, requested_metrics_supported=False))
        self.assertTrue(self.run_step(self.study(), kit)['beam'].ok)
        self.assertNotIn('check', [t for t, _ in client.calls])
        self.assertNotIn('requested_metrics', next(a for t, a in client.calls if t == 'submit'))

    def test_description_refreshed_after_server_generation_changes(self):
        kit, client = self.kit()
        self.assertEqual(kit.describe().capabilities_schema_version, 1)
        client.server_version = 'fake-v2'
        client.description['capabilities_schema_version'] = 2
        with self.assertRaises(ct.ContractError):
            kit.describe()

    def test_resume_existing_handle_never_checks_or_submits(self):
        study, (kit, client) = self.study(), self.kit(ok=False)
        state = self.tmp / 'state'; state.mkdir()
        pd = PointDir(state)
        pd.write_handle('beam', 'c.beam')
        pd.write_submit('beam', kit.name, kit.version)
        self.assertTrue(self.run_step(study, kit, state)['beam'].ok)
        self.assertEqual([t for t, _ in client.calls], ['status', 'results'])
        client.calls.clear()
        self.assertTrue(self.run_step(study, kit, state)['beam'].ok)
        self.assertEqual(client.calls, [])

    def test_start_and_version_do_not_describe_or_read_geometry(self):
        kit, client = self.kit()
        kit.start(); self.assertEqual(kit.version, 'fake-v1')
        self.assertEqual(client.calls, [])

    def test_static_unknown_and_unavailable_and_dynamic_deferred(self):
        study = self.study()
        desc = copy.deepcopy(DESCRIBE)
        desc['tasks']['beam_point']['metrics']['stop_frac']['availability'] = 'unavailable'
        kit, _ = self.kit(describe=desc)
        self.assertIn('unavailable', kit.step_problems(study, study.steps[0])[0])
        desc['tasks']['beam_point']['metrics'] = {}
        kit, _ = self.kit(describe=desc)
        self.assertIn('no metric', kit.step_problems(study, study.steps[0])[0])
        dynamic = replace(study.steps[0], params={'task': 'deltaZ'})
        self.assertEqual(kit.step_problems(study, dynamic), [])

    def test_conditional_rules_are_checked_with_resolved_params(self):
        study = self.study(mutate=lambda d: d['evaluate'][0]['params'].update(background='deltaZ'))
        desc = copy.deepcopy(DESCRIBE)
        desc['tasks']['beam_point']['metric_rules'] = [
            {'when': {'background': [22]}, 'unavailable': ['stop_frac'], 'conditional': [], 'reason': 'test'}]
        kit, client = self.kit(describe=desc, ok=False)
        self.assertEqual(kit.step_problems(study, study.steps[0]), [])
        self.assertFalse(self.run_step(study, kit)['beam'].ok)
        self.assertEqual(next(a for t, a in client.calls if t == 'check')['params']['background'], 22)

    def test_static_known_configuration_rule_and_pattern(self):
        study = self.study()
        desc = copy.deepcopy(DESCRIBE)
        desc['tasks']['beam_point']['metric_rules'] = [
            {'when': {'repeat': [1]}, 'unavailable': ['stop_frac'], 'conditional': [], 'reason': 'fixture'}]
        kit, client = self.kit(describe=desc)
        self.assertIn('unavailable', kit.step_problems(study, study.steps[0])[0])
        self.assertEqual(kit.step_problems(study, replace(study.steps[0], params={'repeat':'deltaZ'})), [])
        desc['tasks']['beam_point']['metrics'] = {}
        desc['tasks']['beam_point']['metric_rules'] = []
        desc['tasks']['beam_point']['metric_patterns'] = [
            {'pattern':'stop_frac', 'when':{'repeat':[0]}, 'status':'conditional', 'reason':'fixture'}]
        kit, _ = self.kit(describe=desc)
        self.assertIn('no metric', kit.step_problems(study, study.steps[0])[0])
        self.assertEqual(kit.step_problems(study, replace(study.steps[0], params={'repeat':'deltaZ'})), [])

    def test_extra_and_upstream_reads_are_requested(self):
        def mutate(d):
            other = copy.deepcopy(d['evaluate'][0]); other.update(step='down', params_from={'muons':'beam.count'})
            del other['fixed']['muons']
            d['evaluate'].append(other)
            d['extra_metrics'] = [{'name':'timing','metric':'beam.timing','fmt':'{:.2f}'},
                                  {'name':'downstream','metric':'down.stop_frac','fmt':'{:.4f}'}]
        study = self.study(mutate=mutate)
        kit, client = self.kit()
        scheduler.check_step_submission(kit, study, study.steps[0], 'c.beam', {}, [], [], 'w')
        self.assertEqual(client.calls[-1][1]['requested_metrics'], ['count', 'stop_frac', 'timing'])

    def test_render_hash_changes_metadata_and_measure_basis_stay_stable(self):
        study = self.study(); basis = study.measure_basis_sha
        refs = []
        for x in (20, 24):
            path = self.tmp / f'geom{x}.txt'
            refs.append(render_geometry(study, path, [x]))
            self.assertEqual(refs[-1]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIn('#include "Offline/Mu2eG4/geom/geom_common.txt"', path.read_text())
        self.assertNotEqual(refs[0]['sha256'], refs[1]['sha256'])
        self.assertEqual(refs[0]['metadata'], refs[1]['metadata'])
        self.assertEqual(basis, study.measure_basis_sha)
        changed = self.study(mutate=lambda d: d['derive']['consts'].update(extra=1))
        self.assertNotEqual(geometry_file_ref(changed, path)['metadata'], refs[0]['metadata'])
        other_measure = self.study(mutate=lambda d: d['evaluate'][0]['fixed'].update(seed=9))
        self.assertEqual(geometry_file_ref(other_measure, path)['metadata'], refs[0]['metadata'])

    def test_preflight_identity_includes_metadata(self):
        study = self.study(); path = self.tmp / 'geom'; path.write_bytes(b'x')
        ref = geometry_file_ref(study, path)
        pre = {'kit':'fastsimkit', 'params':{}}
        a = preflight_basis(pre, {}, {}, [ref])
        ref['metadata']['schema_version'] = 2
        self.assertNotEqual(a, preflight_basis(pre, {}, {}, [ref]))

    def test_check_study_resolved_hook_skips_dependencies_and_never_submits(self):
        def mutate(d):
            other = copy.deepcopy(d['evaluate'][0])
            other.update(step='down', params_from={'muons': 'beam.stop_frac'})
            del other['fixed']['muons']
            d['evaluate'].append(other)
            d['extra_metrics'] = [{'name': 'downstream', 'metric': 'down.stop_frac', 'fmt': '{:.4f}'}]
        for endpoint in ('fastsimkit', 'fastsimkit_eaf'):
            with self.subTest(endpoint=endpoint):
                study = self.study(endpoint, mutate=mutate)
                kit, client = self.kit(endpoint)
                path = self.tmp / 'geom.txt'
                path.write_bytes(study.geom.render([22]).encode('utf-8'))
                files = {'geom': geometry_file_ref(study, path)}
                problems, notes = cs.check_resolved_submissions(
                    study, Kits(kit), {'deltaZ': 22}, files, 'check')
                self.assertEqual(problems, [])
                self.assertIn('unresolved', '; '.join(notes))
                self.assertEqual([t for t, _ in client.calls], ['describe', 'check'])
                self.assertEqual(client.calls[-1][1]['files'], [files['geom']])
                client.ok = False
                problems, _ = cs.check_resolved_submissions(
                    study, Kits(kit), {'deltaZ': 22}, files, 'check')
                self.assertIn('submission refused', problems[0])

    @unittest.skipUnless(importlib.util.find_spec('langgraph'), 'langgraph not installed')
    def test_check_study_checks_rendered_independent_steps_only(self):
        def mutate(d):
            other = copy.deepcopy(d['evaluate'][0]); other.update(step='down', params_from={'muons':'beam.stop_frac'})
            del other['fixed']['muons']; d['evaluate'].append(other)
            d['extra_metrics'] = [{'name':'downstream','metric':'down.stop_frac','fmt':'{:.4f}'}]
        study = self.study(mutate=mutate); kit, client = self.kit()
        with mock.patch.object(cs.paths, 'GRID_DATA_ROOT', self.tmp / 'grid'):
            out = cs.check_geometry(study, Kits(kit), x=[22], config='check', executor='grid')
        self.assertEqual(out.status, 'passed', out.problems)
        self.assertIn('unresolved', out.note)
        checks = [args for t, args in client.calls if t == 'check']
        self.assertEqual(len(checks), 1)
        self.assertIn('sha256', checks[0]['files'][0])
        self.assertNotIn('submit', [t for t, _ in client.calls])


if __name__ == '__main__':
    unittest.main()
