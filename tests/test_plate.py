import unittest

from traffic_core.plate import (
    find_best_match,
    is_valid_license_plate_no_hyphen,
    select_best_license_plate,
)


class PlateLogicTests(unittest.TestCase):
    def test_selects_each_character_by_confidence(self):
        plate, confidences, requires_search = select_best_license_plate(
            ["30A12345", "30A19345"],
            [
                [0.9, 0.9, 0.9, 0.9, 0.4, 0.9, 0.9, 0.9],
                [0.8, 0.8, 0.8, 0.8, 0.95, 0.8, 0.8, 0.8],
            ],
        )

        self.assertEqual(plate, "30A19345")
        self.assertEqual(confidences[4], 0.95)
        self.assertFalse(requires_search)

    def test_ignores_malformed_confidence_rows(self):
        result = select_best_license_plate(["30A12345"], [[0.9]])

        self.assertEqual(result, (None, None, False))

    def test_plate_validation_and_reference_matching(self):
        self.assertTrue(is_valid_license_plate_no_hyphen("30A12345"))
        self.assertFalse(is_valid_license_plate_no_hyphen("not-a-plate"))
        match, score = find_best_match("30A12346", ["30A12345", "29B99999"])
        self.assertEqual(match, "30A12345")
        self.assertGreater(score, 0.8)


if __name__ == "__main__":
    unittest.main()
