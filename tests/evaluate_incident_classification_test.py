"""Offline receipt checks; never contact queue44 from these tests."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import evaluate_incident_classification as evaluator


class EvaluationReceiptTests(unittest.TestCase):
    def run_receipt(self, transport):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'receipt.json'
            with patch.object(evaluator, '_queue_transport', side_effect=transport) as mock:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = evaluator.evaluate(output, 'holdout')
            return code, json.loads(output.read_text()), mock.call_count

    def test_holdout_uses_four_requests_and_full_corpus_floors(self):
        code, receipt, calls = self.run_receipt(
            lambda _: dict(class_='repair'))  # Malformed model output must stay repair.
        self.assertEqual(code, 2)  # Two log labels intentionally differ from safety fallback.
        self.assertEqual(calls, 4)
        self.assertEqual(len(receipt['cases']), 5)
        self.assertEqual({r['split'] for r in receipt['cases']}, {'holdout'})
        self.assertTrue(receipt['guard_checks']['passed'])
        self.assertTrue(receipt['guard_checks']['checks']['incomplete_to_repair'])
        self.assertEqual(receipt['summary']['baseline_class_matches'], 3)
        self.assertEqual(receipt['summary']['baseline_missed_emergency'], 0)
        self.assertTrue(receipt['source_unchanged_during_run'])

    def test_unknown_stops_further_requests_without_erasing_baseline(self):
        def unknown(_):
            raise TimeoutError('isolated transport fixture')
        code, receipt, calls = self.run_receipt(unknown)
        self.assertEqual(code, 2)
        self.assertEqual(calls, 1)
        self.assertTrue(receipt['stopped_after_unknown'])
        self.assertEqual(receipt['summary']['model_requests_unknown'], 1)
        self.assertEqual(receipt['summary']['baseline_class_matches'], 3)
        self.assertEqual(len(receipt['cases']), 5)

    def test_existing_receipt_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'receipt.json'
            output.write_text('existing receipt')
            with patch.object(evaluator, '_queue_transport') as mock:
                with self.assertRaises(SystemExit):
                    evaluator.evaluate(output, 'holdout')
            mock.assert_not_called()
            self.assertEqual(output.read_text(), 'existing receipt')


if __name__ == '__main__':
    unittest.main()
