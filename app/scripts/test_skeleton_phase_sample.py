"""Sampler geometry and cycle-accounting tests, not visual character approval."""
import copy
import unittest
import skeleton_phase_sample as tool


class SamplingTests(unittest.TestCase):
    def fixture(self):
        return {
            'names': ['Hips', 'RightShoulder', 'RightWrist', 'RightAnkle'],
            'parents': [-1, 0, 1, 0], 'frameIds': [571, 572, 573, 574], 'dt': .1,
            'inPlace': [
                [[0, 0, 0], [0, 4, 0], [0, 2, z], [0, -4, -z]] for z in [0, 1, 0, -1]
            ]
        }

    def test_uniform_phases_exclude_duplicate_endpoint(self):
        self.assertEqual(tool.uniform_indices(80, 8), [0, 10, 20, 30, 40, 50, 60, 70])
        self.assertEqual(tool.uniform_indices(81, 8, True), [0, 10, 20, 30, 40, 50, 60, 70])
        self.assertAlmostEqual(tool.uniform_indices(5, 8)[-1], 4.375)
        for args in [(0, 8), (80, 0), (1, 8, True), (True, 8)]:
            with self.assertRaises(ValueError): tool.uniform_indices(*args)

    def test_exact_1333_millisecond_cycle_with_even_holds(self):
        durations = tool.allocate_ms(1333, 8)
        self.assertEqual(durations, [167, 166, 167, 167, 166, 167, 166, 167])
        self.assertEqual(sum(durations), 1333)
        self.assertLessEqual(max(durations) - min(durations), 1)
        with self.assertRaises(ValueError): tool.allocate_ms(7, 8)

    def test_wrapped_seam_is_part_of_maximum_step(self):
        stats = tool.audit_trajectory([[0, 0], [1, 0], [5, 0]])
        self.assertEqual(stats['stepDistancesIncludingWrap'], [1, 4, 5])
        self.assertEqual(stats['wrappedSeamStep'], 5)
        self.assertEqual(stats['maximumInternalStep'], 4)
        self.assertEqual(stats['maximumStep'], 5)
        self.assertEqual(stats['largestTransition'], [2, 0])

    def test_relative_trajectory_ignores_common_root_translation(self):
        relative = [[0, 2, 1], [1, 2, 0], [0, 2, -1]]
        root = [[100, 0, 30], [120, 4, -10], [-30, -3, 70]]
        translated = [[v + shift for v, shift in zip(p, r)] for p, r in zip(relative, root)]
        self.assertEqual(tool.audit_trajectory(translated, root), tool.audit_trajectory(relative))

    def test_fractional_sampling_records_wrapped_contributors(self):
        report = tool.build_report(self.fixture(), 8)
        self.assertEqual([s['sourceIndices'] for s in report['samples'][-2:]], [[3], [3, 0]])
        last = report['samples'][-1]
        self.assertEqual(last['sourceFrameIds'], [574, 571])
        self.assertEqual(last['interpolationWeight'], .5)
        self.assertTrue(last['wrappedInterpolation'])
        self.assertEqual(last['inPlace'][2], [0, 2, -.5])
        self.assertEqual(report['totalMs'], 400)

    def test_included_endpoint_never_wraps_and_is_not_duplicated(self):
        data = self.fixture()
        data['frameIds'].append(575)
        data['inPlace'].append(copy.deepcopy(data['inPlace'][0]))
        report = tool.build_report(data, 8, True)
        self.assertEqual(report['totalMs'], 400)
        self.assertEqual(report['samples'][-1]['sourceIndices'], [3, 4])
        self.assertFalse(any(s['wrappedInterpolation'] for s in report['samples']))
        self.assertEqual(report['declaredEndpointErrors']['maximumHipsRelativeJointDistance'], 0)
        self.assertNotIn(1, [s['phase'] for s in report['samples']])

    def test_report_keeps_anatomy_and_actual_source_poses(self):
        source = self.fixture()
        report = tool.build_report(source, 4)
        self.assertEqual(report['samples'][1]['inPlace'], source['inPlace'][1])
        self.assertEqual(report['samples'][1]['sourceFrameIds'], [572])
        self.assertEqual(report['sampledTrajectories']['RightWrist@RightShoulder']['strictE2d']['positions'], [[0, 2], [1, 2], [0, 2], [-1, 2]])
        self.assertEqual(report['sampledTrajectories']['RightWrist@Hips']['source3d']['wrappedSeamStep'], 1)

    def test_nonfinite_or_mismatched_source_is_rejected(self):
        source = self.fixture()
        source['inPlace'][0][0][0] = float('nan')
        with self.assertRaises(ValueError): tool.build_report(source, 8)
        source = self.fixture(); source['frameIds'].pop()
        with self.assertRaises(ValueError): tool.build_report(source, 8)
        with self.assertRaises(ValueError): tool.audit_trajectory([[0, 1]], [[0, 1], [1, 0]])


if __name__ == '__main__':
    unittest.main()
