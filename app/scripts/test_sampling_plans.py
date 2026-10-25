import copy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest

from sampling_plans import (ConflictError, StaleSourceError, export_plan, get_source,
                            get_state, list_sources, mutate, phase_audit, uniform_frames)


class SamplingPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'studio.project.json'
        self.walk = self.root / 'walk.json'
        self.config = {'version': 1, 'id': 'fixture', 'sources': [], 'sampling': {
            'store': 'saved/plans.json', 'sources': [{'id': 'walk', 'name': '测试行走',
                'type': '100style', 'path': 'walk.json'}], 'libraries': ['library/index.json']}}
        self.data = {'names': ['Hips', 'RightWrist', 'LeftWrist'], 'parents': [-1, 0, 0],
            'frameIds': list(range(571, 651)), 'dt': 1 / 60,
            'inPlace': [[[0, 10, 0], [-1, 7, index / 10], [1, 7, -index / 10]]
                for index in range(80)], 'license': 'CC BY 4.0', 'credit': 'fixture'}
        self.walk.write_text(json.dumps(self.data))
        self.library = {'version': 1, 'profiles': {'long': {'name': '长腿', 'family': 'biped'},
            'short': {'name': '短腿', 'family': 'biped'}, 'native': {'name': '犬型', 'family': 'quadruped'}},
            'sources': {'fixture': {'name': 'fixture pack', 'license': 'CC0'}}, 'clips': [
                {'id': 'library-walk', 'name': '库行走', 'family': 'biped', 'pack': 'fixture',
                 'loop': True, 'duration': 1, 'file': 'walk.json'}]}
        self.clip = {'id': 'library-walk', 'names': ['root', 'hand_r'], 'parents': [-1, 0],
            'root': 0, 'duration': 1, 'loop': True, 'times': [0, .25, .5, .75, 1],
            'profiles': {'long': {'positions': [
                [[index * 10, 4, index * 20], [index * 10 + 1, 6, index * 20 + 2]]
                for index in range(5)], 'rootPath': [[index * 10, index * 20] for index in range(5)]},
                'short': {'positions': [[[0, 2, 0], [1, 3, 2]] for _ in range(5)]}},
            'sourceSha256': 'original-binary-hash'}
        (self.root / 'library').mkdir()
        (self.root / 'library/index.json').write_text(json.dumps(self.library))
        (self.root / 'library/walk.json').write_text(json.dumps(self.clip))
        self.save_config()

    def tearDown(self):
        self.temp.cleanup()

    def save_config(self):
        self.project.write_text(json.dumps(self.config))

    def create(self, **values):
        state = get_state(self.project)
        return mutate(self.project, 'create', {'name': '八相位', 'sourceId': 'walk',
            'frames': list(range(0, 80, 10)), 'timing': 'source', **values}, state['revision'])

    def test_source_adapter_retains_exact_captured_poses_and_ids(self):
        source = get_source(self.project, 'walk')
        self.assertEqual(source['profileId'], 'original')
        self.assertEqual(source['frameCount'], 80)
        self.assertEqual(source['selectableFrameCount'], 80)
        self.assertFalse(source['includesEndpoint'])
        self.assertEqual(source['positions'], self.data['inPlace'])
        self.assertEqual(source['frameIds'][26], 597)
        self.assertEqual(source['provenance']['metadata']['license'], 'CC BY 4.0')
        self.assertAlmostEqual(source['durationMs'], 1333.3333333)
        with self.assertRaisesRegex(ValueError, 'original profile'):
            get_source(self.project, 'walk', 'short')

    def test_registry_summary_is_cheap_and_profiles_have_names(self):
        # Listing does not read clip arrays; a broken clip only fails when opened.
        (self.root / 'library/walk.json').write_text('not JSON')
        sources = list_sources(self.project)
        self.assertEqual(len(sources), 2)
        clip = sources[1]
        self.assertEqual(clip['profiles'], [{'id': 'long', 'name': '长腿'}, {'id': 'short', 'name': '短腿'}])
        self.assertNotIn('positions', clip)
        with self.assertRaises(ValueError):
            get_source(self.project, clip['id'])

    def test_library_endpoint_exclusion_and_in_place_root_removal(self):
        source = get_source(self.project, 'library-walk', 'long')
        self.assertEqual(source['frameCount'], 5)
        self.assertEqual(source['selectableFrameCount'], 4)
        self.assertTrue(source['includesEndpoint'])
        self.assertEqual(source['timesMs'], [0, 250, 500, 750, 1000])
        self.assertEqual(source['positions'], [[[0, 4, 0], [1, 6, 2]]] * 5)
        self.assertEqual(source['provenance']['metadata']['license'], 'CC0')
        self.assertIn('registry', source['provenance'])
        short = get_source(self.project, 'library-walk', 'short')
        self.assertEqual(short['positions'], self.clip['profiles']['short']['positions'])
        with self.assertRaisesRegex(ValueError, 'selectable'):
            self.create(sourceId='library-walk', frames=[0, 4])

    def test_crud_selection_and_independent_duplicate(self):
        self.assertEqual(get_state(self.project)['revision'], 0)
        first = self.create()
        original = first['plans'][0]
        self.assertEqual(first['selectedPlanId'], None)
        self.assertEqual(original['durationsMs'], [167, 166, 167, 167, 166, 167, 167, 166])
        selected = mutate(self.project, 'select', {'id': original['id']}, 1)
        self.assertEqual(selected['selectedPlanId'], original['id'])
        cloned = mutate(self.project, 'duplicate', {'id': original['id']}, 2)
        duplicate = cloned['plans'][1]
        self.assertNotEqual(duplicate['id'], original['id'])
        changed = mutate(self.project, 'update', {'id': duplicate['id'], 'frames': [5, 25, 45, 65],
            'timing': 'custom', 'durationsMs': [100, 200, 300, 400], 'camera': {'direction': 'NE', 'degrees': 45}}, 3)
        self.assertEqual(changed['plans'][0], cloned['plans'][0])
        self.assertEqual(changed['plans'][1]['createdAt'], duplicate['createdAt'])
        self.assertEqual(changed['plans'][1]['frames'], [5, 25, 45, 65])
        deleted = mutate(self.project, 'delete', {'id': original['id']}, 4)
        self.assertEqual(deleted['selectedPlanId'], None)
        self.assertEqual(len(get_state(self.project)['plans']), 1)
        disk = json.loads((self.root / 'saved/plans.json').read_text())
        self.assertEqual(disk['revision'], 5)
        self.assertNotIn('stale', disk['plans'][0])

    def test_four_eight_twenty_four_even_timings_preserve_cycle(self):
        for count in (4, 8, 24):
            frames = [i * 80 // count for i in range(count)]
            state = self.create(frames=frames, timing='even')
            self.assertEqual(sum(state['plans'][-1]['durationsMs']), 1333)
            self.assertEqual(len(state['plans'][-1]['durationsMs']), count)
            self.assertTrue(all(ms > 0 for ms in state['plans'][-1]['durationsMs']))

    def test_rotated_source_order_preserves_wrapped_intervals(self):
        state = self.create(frames=[60, 70, 0, 10, 20, 30, 40, 50])
        plan = state['plans'][0]
        self.assertEqual(sum(plan['durationsMs']), 1333)
        self.assertEqual(plan['durationsMs'][1], 166)
        one = self.create(frames=[20])
        self.assertEqual(one['plans'][-1]['durationsMs'], [1333])

    def test_export_contains_only_selected_poses_and_provenance(self):
        state = self.create(frames=[0, 20, 40, 60])
        plan = state['plans'][0]
        with self.assertRaisesRegex(ValueError, 'Select'):
            export_plan(self.project)
        mutate(self.project, 'select', {'id': plan['id']}, state['revision'])
        contract = export_plan(self.project)
        self.assertEqual(contract['kind'], 'character-motion-sampling-plan')
        self.assertEqual(contract['samples'][1]['sourceFrameId'], 591)
        self.assertEqual(contract['samples'][1]['positions'], self.data['inPlace'][20])
        self.assertNotIn('positions', contract['source'])
        self.assertEqual(contract['playbackDurationMs'], 1333)
        self.assertFalse(contract['generationConstraints']['mirrored'])
        self.assertFalse(contract['generationConstraints']['interpolated'])
        self.assertEqual(contract['source']['provenance']['sha256'], plan['sourceFingerprint'])

    def test_stale_source_blocks_selection_update_duplicate_and_export(self):
        state = self.create()
        pid = state['plans'][0]['id']
        self.data['inPlace'][0][0][1] += 1
        self.walk.write_text(json.dumps(self.data))
        self.assertTrue(get_state(self.project)['plans'][0]['stale'])
        for operation in ('select', 'update', 'duplicate'):
            with self.assertRaises(StaleSourceError):
                mutate(self.project, operation, {'id': pid}, 1)
        with self.assertRaises(StaleSourceError):
            export_plan(self.project, pid)
        # Deleting an obsolete plan remains possible.
        self.assertEqual(mutate(self.project, 'delete', {'id': pid}, 1)['plans'], [])

    def test_optimistic_conflict_does_not_overwrite(self):
        self.create()
        before = (self.root / 'saved/plans.json').read_bytes()
        with self.assertRaises(ConflictError):
            mutate(self.project, 'create', {'sourceId': 'walk'}, 0)
        self.assertEqual((self.root / 'saved/plans.json').read_bytes(), before)
        outcomes = []
        def save():
            try:
                mutate(self.project, 'create', {'sourceId': 'walk'}, 1)
                outcomes.append('ok')
            except ConflictError:
                outcomes.append('conflict')
        threads = [threading.Thread(target=save) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ['ok', 'conflict'])
        self.assertEqual(get_state(self.project)['revision'], 2)

    def test_immutable_base_ids_and_invalid_inputs(self):
        for operation in ('update', 'delete', 'duplicate', 'select'):
            with self.assertRaisesRegex(ValueError, 'immutable base'):
                mutate(self.project, operation, {'id': 'base:walk'}, 0)
        for bad in ([], [0, 0], [80], [-1], [True], [float('nan')], [float('inf')]):
            with self.assertRaises(ValueError):
                self.create(frames=bad)
        for values in ({'timing': 'custom', 'durationsMs': [1]},
                       {'timing': 'custom', 'durationsMs': [0] * 8},
                       {'camera': {'direction': 'E', 'degrees': float('nan')}},
                       {'camera': {'direction': 'fake'}}, {'loop': 1}):
            with self.assertRaises(ValueError):
                self.create(**values)
        state = self.create()
        with self.assertRaisesRegex(ValueError, 'change source'):
            mutate(self.project, 'update', {'id': state['plans'][0]['id'], 'sourceId': 'library-walk'}, 1)

    def test_all_configured_paths_stay_inside_project_including_symlinks(self):
        for area, change in [('store', '../escape.json'), ('sources', '../escape.json'), ('libraries', '../escape.json')]:
            original = copy.deepcopy(self.config)
            if area == 'store':
                self.config['sampling']['store'] = change
            elif area == 'sources':
                self.config['sampling']['sources'][0]['path'] = change
            else:
                self.config['sampling']['libraries'] = [change]
            self.save_config()
            with self.assertRaisesRegex(ValueError, 'outside'):
                list_sources(self.project)
            self.config = original
        self.save_config()
        with tempfile.TemporaryDirectory() as outside:
            (self.root / 'escape').symlink_to(outside, target_is_directory=True)
            self.config['sampling']['store'] = 'escape/plans.json'
            self.save_config()
            with self.assertRaisesRegex(ValueError, 'outside'):
                get_state(self.project)
        self.config['sampling']['store'] = 'walk.json'
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'overwrite a source'):
            get_state(self.project)

    def test_exact_half_cycle_presets_for_eighty_and_eighty_one_intervals(self):
        duration = 1.333333
        self.clip['times'] = [round(i * duration / 81, 6) for i in range(82)]
        self.clip['duration'] = duration
        self.library['clips'][0]['duration'] = duration
        self.clip['profiles'] = {'long': {
            'positions': [[[0, 4, 0], [1, 6, i / 10]] for i in range(82)]}}
        (self.root / 'library/walk.json').write_text(json.dumps(self.clip))
        (self.root / 'library/index.json').write_text(json.dumps(self.library))
        for source_id in ('walk', 'library-walk'):
            source = get_source(self.project, source_id)
            self.assertEqual(source['selectableFrameCount'], 80 if source_id == 'walk' else 81)
            for count in (4, 8, 16, 24):
                for offset in (0, .123):
                    frames = uniform_frames(source, count, offset)
                    audit = phase_audit(source, frames)
                    self.assertTrue(audit['paired'])
                    self.assertTrue(audit['oneForwardCycle'])
                    self.assertLess(audit['maxHalfCycleError'], 1e-12)
                    state = self.create(sourceId=source_id, frames=frames, timing='even')
                    plan = state['plans'][-1]
                    self.assertTrue(plan['phaseAudit']['paired'])
                    self.assertEqual(sum(plan['durationsMs']), 1333)
                    contract = export_plan(self.project, plan['id'])
                    self.assertTrue(contract['phaseAudit']['paired'])
                    self.assertEqual(len(contract['samples']), count)
                    for sample in contract['samples']:
                        self.assertAlmostEqual(sum(c['weight'] for c in sample['contributors']), 1)
                        self.assertTrue(all(0 <= c['sourceIndex'] < source['frameCount'] for c in sample['contributors']))
            if source_id == 'library-walk':
                self.assertTrue(any(type(frame) is float for frame in uniform_frames(source, 8)))

    def test_fractional_export_records_actual_native_contributors(self):
        state = self.create(frames=[.5, 20.5, 40.5, 60.5], timing='even')
        contract = export_plan(self.project, state['plans'][0]['id'])
        first = contract['samples'][0]
        self.assertEqual(first['sourceFrameId'], None)
        self.assertEqual(first['sourceFrameIds'], [571, 572])
        self.assertEqual(first['contributors'], [
            {'sourceIndex': 0, 'sourceFrameId': 571, 'weight': .5},
            {'sourceIndex': 1, 'sourceFrameId': 572, 'weight': .5}])
        self.assertEqual(first['positions'][1], [-1, 7, .05])
        self.assertTrue(contract['generationConstraints']['interpolated'])
        self.assertTrue(contract['phaseAudit']['paired'])
        wrapped = self.create(frames=[79.5], timing='even')
        sample = export_plan(self.project, wrapped['plans'][-1]['id'])['samples'][0]
        self.assertEqual(sample['sourceFrameIds'], [650, 571])
        self.assertLess(sample['sourceTimeMs'], contract['source']['durationMs'])

    def test_hand_selected_unsymmetric_plan_reports_phase_error_without_pose_changes(self):
        source = get_source(self.project, 'walk')
        original = copy.deepcopy(source['positions'])
        audit = phase_audit(source, [0, 12, 20, 31, 40, 50, 60, 70])
        self.assertFalse(audit['paired'])
        self.assertGreater(audit['maxHalfCycleErrorMs'], 0)
        self.assertGreater(audit['anatomicalHalfCycleAudit']['maximumJointDifference'], 0)
        self.assertFalse(audit['anatomicalHalfCycleAudit']['posesModified'])
        self.assertEqual(source['positions'], original)
        # Matching opposite slots alone is insufficient for scrambled playback.
        scrambled = phase_audit(source, [0, 20, 10, 30, 40, 60, 50, 70])
        self.assertFalse(scrambled['paired'])
        self.assertFalse(scrambled['oneForwardCycle'])

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, str(Path(__file__).with_name('sampling_plans.py')),
            *arguments, '--project', str(self.project)], capture_output=True, text=True, check=False)

    def test_cli_unselected_export_refuses_without_creating_file(self):
        self.create()
        output = self.root / 'contract.json'
        result = self.run_cli('export', '--out', str(output))
        self.assertEqual(result.returncode, 2)
        self.assertIn('Select a saved', result.stderr)
        self.assertFalse(output.exists())
        inspected = self.run_cli('inspect')
        self.assertEqual(inspected.returncode, 0)
        self.assertEqual(json.loads(inspected.stdout)['selectedPlanId'], None)
        listed = self.run_cli('sources')
        self.assertEqual(len(json.loads(listed.stdout)), 2)

    def test_cli_selected_fractional_export_has_real_contributors_and_refuses_overwrite(self):
        state = self.create(frames=[.5, 20.5, 40.5, 60.5], timing='even')
        plan = state['plans'][0]
        mutate(self.project, 'select', {'id': plan['id']}, state['revision'])
        output = self.root / 'contract.json'
        result = self.run_cli('export', '--out', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = json.loads(output.read_text())
        self.assertEqual(contract['samples'][0]['sourceFrameIds'], [571, 572])
        self.assertEqual([c['weight'] for c in contract['samples'][0]['contributors']], [.5, .5])
        self.assertEqual(contract['plan']['durationsMs'], [s['durationMs'] for s in contract['samples']])
        before = output.read_bytes()
        self.assertEqual(self.run_cli('export', '--out', str(output)).returncode, 2)
        self.assertEqual(output.read_bytes(), before)
        self.data['credit'] = 'changed'
        self.walk.write_text(json.dumps(self.data))
        stale_output = self.root / 'stale.json'
        self.assertEqual(self.run_cli('export', '--plan', plan['id'], '--out', str(stale_output)).returncode, 2)
        self.assertFalse(stale_output.exists())

    def test_fractional_end_boundary_and_huge_numeric_inputs_are_safe(self):
        source = get_source(self.project, 'walk')
        near_end = uniform_frames(source, 4, 1 - 1e-13)
        self.assertLess(near_end[0], 80)
        self.assertTrue(phase_audit(source, near_end)['paired'])
        state = self.create(frames=near_end, timing='even')
        exported = export_plan(self.project, state['plans'][0]['id'])
        self.assertEqual(exported['samples'][0]['sourceFrameIds'], [650, 571])
        for frames in ([80], [-1e-15], [10 ** 1000], [float('nan')]):
            with self.assertRaises(ValueError):
                self.create(frames=frames)

    def test_phase_pairing_and_custom_playback_symmetry_are_reported_separately(self):
        state = self.create(frames=[0, 20, 40, 60], timing='custom', durationsMs=[100, 200, 300, 400])
        audit = state['plans'][0]['phaseAudit']
        self.assertTrue(audit['paired'])
        self.assertEqual(audit['playbackTimingAudit']['halfDurationsMs'], [300, 700])
        self.assertEqual(audit['playbackTimingAudit']['maximumOppositeHoldDifferenceMs'], 200)

    def test_malformed_hierarchy_and_explicit_store_configuration(self):
        self.data['parents'] = [-1, 2, 1]
        self.walk.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            get_source(self.project, 'walk')
        self.data['parents'] = [-1, 2, 99]
        self.walk.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, 'parent'):
            get_source(self.project, 'walk')
        del self.config['sampling']['store']
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'explicit store'):
            get_state(self.project)

    def test_create_rejects_source_changed_after_viewer_preview(self):
        before = get_source(self.project, 'walk')['fingerprint']
        self.data['credit'] = 'changed'
        self.walk.write_text(json.dumps(self.data))
        with self.assertRaises(StaleSourceError):
            self.create(sourceFingerprint=before)
        self.assertEqual(get_state(self.project)['revision'], 0)

    def test_nonloop_endpoint_is_selectable_with_positive_final_hold(self):
        self.library['clips'][0]['loop'] = False
        (self.root / 'library/index.json').write_text(json.dumps(self.library))
        source = get_source(self.project, 'library-walk')
        self.assertEqual(source['selectableFrameCount'], 5)
        state = self.create(sourceId='library-walk', frames=[0, 2, 4], loop=False)
        self.assertEqual(state['plans'][0]['durationsMs'], [500, 500, 250])
        with self.assertRaisesRegex(ValueError, 'chronological'):
            self.create(sourceId='library-walk', frames=[4, 0], loop=False)


if __name__ == '__main__':
    unittest.main()
