"""Timing helpers used for identifier-free attribution."""
import unittest
from datetime import datetime

import _paths
_paths.ensure()
from ghost_audit.attribution import cadence_slot_times, circular_distance, fit_batch_offset, in_window
from ghost_audit.config import CADENCE_BASE_S, CADENCE_PERIOD_S, CADENCE_STEP_S


class Timing(unittest.TestCase):
    def test_circular_distance(self):
        self.assertEqual(circular_distance(59, 1), 2)
        self.assertEqual(circular_distance(10.5, 10), 0.5)
        self.assertEqual(circular_distance(0, 30), 30)

    def test_window(self):
        R = datetime(2026, 9, 1, 12, 0, 0)
        self.assertTrue(in_window(datetime(2026, 9, 1, 11, 0, 0), R, (-90, -30)))
        self.assertFalse(in_window(datetime(2026, 9, 1, 11, 45, 0), R, (-90, -30)))

    def test_cadence_slots_follow_the_formula(self):
        R = datetime(2026, 9, 1, 12, 0, 0)
        [t0, t1] = cadence_slot_times(100, R, [0, 1])
        self.assertEqual((R - t0).total_seconds(), CADENCE_BASE_S + 100)
        self.assertEqual((R - t1).total_seconds(), CADENCE_BASE_S + (100 + CADENCE_STEP_S) % CADENCE_PERIOD_S)

    def test_fit_recovers_batch_offset(self):
        R = datetime(2026, 9, 1, 12, 0, 0)
        true_A = 300
        info = []
        for i in range(5):
            [t] = cadence_slot_times(true_A, R, [i])
            info.append((None, R, (i, i + 20), [{"_t": t}]))
        A = fit_batch_offset(info)
        self.assertIsNotNone(A)
        for i in range(5):
            self.assertEqual(cadence_slot_times(A, R, [i]), cadence_slot_times(true_A, R, [i]))

    def test_fit_rejects_noise(self):
        R = datetime(2026, 9, 1, 12, 0, 0)
        info = [(None, R, (i, i + 20), []) for i in range(5)]
        self.assertIsNone(fit_batch_offset(info))


if __name__ == "__main__":
    unittest.main()
