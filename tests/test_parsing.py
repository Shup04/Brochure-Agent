import unittest
from datetime import date
from unittest.mock import patch

from brochure_agent.models import FeatureRow
from brochure_agent.parsing import clean_feature_rows


class PropertyAgeTests(unittest.TestCase):
    def setUp(self):
        date_patch = patch("brochure_agent.parsing.date", wraps=date)
        self.mock_date = date_patch.start()
        self.addCleanup(date_patch.stop)
        self.mock_date.today.return_value = date(2030, 6, 15)

    def age_values(self, feature_rows=(), raw_text=""):
        return [
            value
            for row in clean_feature_rows(feature_rows, raw_text)
            if row.label == "Age"
            for value in row.values
        ]

    def test_age_uses_current_year_for_extracted_feature(self):
        for value in ("Year Built 2000", "Built 2000", "year built 2000"):
            with self.subTest(value=value):
                rows = [FeatureRow(label="Age", values=[value])]
                self.assertEqual(self.age_values(rows), ["30 Years"])

    def test_age_uses_current_year_for_raw_listing(self):
        self.assertEqual(self.age_values(raw_text="Year Built\n1983"), ["47 Years"])

    def test_age_updates_when_calendar_year_changes(self):
        self.mock_date.today.return_value = date(2030, 12, 31)
        self.assertEqual(self.age_values(raw_text="Year Built 2000"), ["30 Years"])
        self.mock_date.today.return_value = date(2031, 1, 1)
        self.assertEqual(self.age_values(raw_text="Year Built 2000"), ["31 Years"])

    def test_current_year_construction_has_zero_age(self):
        self.assertEqual(self.age_values(raw_text="Year Built 2030"), ["0 Years"])

    def test_future_construction_does_not_produce_negative_age(self):
        self.assertEqual(self.age_values(raw_text="Year Built 2031"), [])
        rows = [FeatureRow(label="Age", values=["Built 2031"])]
        self.assertEqual(self.age_values(rows), [])

    def test_missing_or_unrecognized_year_does_not_invent_age(self):
        for raw_text in ("", "Year Built Unknown"):
            with self.subTest(raw_text=raw_text):
                self.assertEqual(self.age_values(raw_text=raw_text), [])

    def test_extracted_year_keeps_precedence_over_raw_listing(self):
        rows = [FeatureRow(label="Age", values=["Built 2000"])]
        self.assertEqual(self.age_values(rows, "Year Built 1990"), ["30 Years"])

    def test_other_features_and_row_order_are_preserved(self):
        rows = [
            FeatureRow(label="Taxes", values=["$3,145 (2025)"]),
            FeatureRow(label="Age", values=["Built 2000"]),
            FeatureRow(label="Location", values=["Brocklehurst"]),
        ]
        self.assertEqual(
            clean_feature_rows(rows, ""),
            [
                FeatureRow(label="Location", values=["Brocklehurst"]),
                FeatureRow(label="Age", values=["30 Years"]),
                FeatureRow(label="Taxes", values=["$3,145 (2025)"]),
            ],
        )


if __name__ == "__main__":
    unittest.main()
