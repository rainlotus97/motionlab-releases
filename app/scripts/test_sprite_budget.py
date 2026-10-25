import unittest
from sprite_budget import estimate


class SpriteBudgetTests(unittest.TestCase):
    def test_known_frame_totals(self):
        report = estimate(576, 768, 16)
        self.assertEqual(report['uniqueFramePixelsBytesPerGroup'] / 2**20, 27)
        self.assertEqual(report['allUniqueFramePixelsBytes'] / 2**20, 135)
        smaller = estimate(288, 384, 16)
        self.assertEqual(smaller['allUniqueFramePixelsBytes'] / 2**20, 33.75)

    def test_padding_unused_cells_and_residency(self):
        report = estimate(10, 20, 5, directions=5, actions=3, outfits=2,
                          resident_directions=2, resident_actions=2, padding=2)
        atlas = report['atlas']
        self.assertEqual((atlas['width'], atlas['height'], atlas['unusedCells']), (56, 48, 3))
        self.assertEqual(atlas['allBaseBytes'], 56 * 48 * 4 * 30)
        self.assertEqual(atlas['residentBaseBytes'], 56 * 48 * 4 * 4)

    def test_mips_and_cpu_are_separate(self):
        report = estimate(10, 20, 4, mipmaps=True, cpu_copies=2)
        base = report['atlas']['residentBaseBytes']
        self.assertEqual(report['residentTextureBytesApprox'], base + report['residentMipExtraBytesApprox'])
        self.assertEqual(report['residentCpuDecodedBytesEstimate'], base * 2)

    def test_page_limit(self):
        self.assertTrue(estimate(1024, 1024, 16)['atlas']['requiresPageSplit'])
        self.assertFalse(estimate(1024, 1024, 16, padding=0)['atlas']['requiresPageSplit'])

    def test_invalid_counts(self):
        for overrides in ({'resident_directions': 6}, {'padding': -1}, {'cpu_copies': -1},
                          {'resident_actions': 2}, {'resident_outfits': 2}, {'columns': 0}):
            with self.assertRaises(ValueError):
                estimate(288, 384, 16, **overrides)
        with self.assertRaises(ValueError):
            estimate(288, 384, 0)


if __name__ == '__main__':
    unittest.main()
