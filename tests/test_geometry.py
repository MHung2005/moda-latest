import unittest

from traffic_core.geometry import contains_point


class GeometryTests(unittest.TestCase):
    def test_point_inside_polygon(self):
        polygon = [(0, 0), (10, 0), (10, 10), (0, 10)]

        self.assertTrue(contains_point((5, 5), polygon))
        self.assertFalse(contains_point((20, 5), polygon))


if __name__ == "__main__":
    unittest.main()
