import unittest
from unittest.mock import patch

from traffic_core.fps import FpsCounter


class FpsCounterTests(unittest.TestCase):
    @patch("traffic_core.fps.time.monotonic", side_effect=[10.0, 10.5, 11.0])
    def test_reports_frames_over_elapsed_time(self, _monotonic):
        counter = FpsCounter(stream_id=0)

        self.assertEqual(counter.get_fps(), 0.0)
        self.assertEqual(counter.get_fps(), 0.0)
        self.assertEqual(counter.get_fps(), 3.0)


if __name__ == "__main__":
    unittest.main()
