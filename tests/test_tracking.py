import unittest

from traffic_core.tracking import TrackKey, TrackingRegistry


class TrackingRegistryTests(unittest.TestCase):
    def test_uses_source_and_object_as_identity(self):
        registry = TrackingRegistry()
        first = registry.ensure(TrackKey(0, 7))
        second = registry.ensure(TrackKey(1, 7))

        self.assertIsNot(first, second)
        self.assertEqual(len(registry), 2)

    def test_ensure_preserves_existing_state(self):
        registry = TrackingRegistry()
        key = TrackKey(0, 7)
        original = registry.ensure(key)
        original.update_raw_plate("30A12345")

        current = registry.ensure(key)

        self.assertIs(current, original)
        self.assertEqual(current.raw_plate, "30A12345")

    def test_remove_is_idempotent(self):
        registry = TrackingRegistry()
        key = TrackKey(0, 7)
        registry.ensure(key)

        self.assertIsNotNone(registry.remove(key))
        self.assertIsNone(registry.remove(key))

    def test_observe_updates_last_seen_without_resetting_state(self):
        registry = TrackingRegistry()
        state = registry.ensure(TrackKey(0, 7))
        state.reentry_armed = True

        state.observe(123.5)

        self.assertEqual(state.last_seen_at, 123.5)
        self.assertTrue(state.reentry_armed)

    def test_reentry_transition_must_be_rearmed_after_it_is_consumed(self):
        registry = TrackingRegistry()
        state = registry.ensure(TrackKey(0, 7))
        state.outside_since_capture = True
        state.reentry_armed = True

        self.assertTrue(state.begin_reentry())
        self.assertFalse(state.begin_reentry())
        self.assertFalse(state.outside_since_capture)
        self.assertFalse(state.reentry_armed)


if __name__ == "__main__":
    unittest.main()
