import os
import unittest
from unittest.mock import patch

from traffic_core.watchdog import StallWatchdog


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class StallWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.watchdog = StallWatchdog(
            [0, 1], stall_seconds=60, startup_seconds=120, clock=self.clock
        )

    def test_source_without_first_frame_gets_startup_grace(self):
        self.clock.now += 119
        self.assertEqual(self.watchdog.stalled(), [])
        self.clock.now += 2
        self.assertEqual([item[0] for item in self.watchdog.stalled()], [0, 1])
        self.assertFalse(self.watchdog.stalled()[0][2])

    def test_source_silent_after_frames_is_stalled(self):
        self.watchdog.mark(0)
        self.watchdog.mark(1)
        self.clock.now += 30
        self.watchdog.mark(1)
        self.clock.now += 31

        stalled = self.watchdog.stalled()

        self.assertEqual([(source_id, ever) for source_id, _, ever in stalled], [(0, True)])
        self.assertAlmostEqual(stalled[0][1], 61)

    def test_forgotten_source_is_ignored(self):
        self.watchdog.forget(0)
        self.watchdog.mark(1)
        self.clock.now += 500
        self.assertEqual([item[0] for item in self.watchdog.stalled()], [1])

    def test_limits_come_from_environment(self):
        with patch.dict(
            os.environ,
            {"MODA_SOURCE_STALL_SECONDS": "45", "MODA_SOURCE_STARTUP_SECONDS": "90"},
        ):
            watchdog = StallWatchdog.from_environment([0])
        self.assertEqual((watchdog.stall_seconds, watchdog.startup_seconds), (45, 90))

    def test_rejects_non_positive_limits(self):
        with patch.dict(os.environ, {"MODA_SOURCE_STALL_SECONDS": "0"}):
            with self.assertRaises(ValueError):
                StallWatchdog.from_environment([0])


if __name__ == "__main__":
    unittest.main()
