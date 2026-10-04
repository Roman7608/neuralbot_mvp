"""
Регрессия классификации входящих звонков ОП (OP_IN).

Запуск из корня проекта:
  python -m unittest call_analytics.test_classify_golden_op_in -v

Эталоны: call_analytics/fixtures/classify_golden_op_in.json
Справочник архетипов: docs/GOLDEN_OP_IN.md
"""
import json
import unittest
from pathlib import Path

from call_analytics.classify_by_transcript import classify_auto

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classify_golden_op_in.json"


class TestClassifyGoldenOpIn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_FIXTURE, encoding="utf-8") as f:
            cls._cases = json.load(f)

    def test_golden_op_in_cases_match_spec(self):
        for row in self._cases:
            with self.subTest(id=row["id"], archetype=row.get("archetype", "")):
                got = classify_auto(row["text"])
                self.assertEqual(
                    got[0],
                    row["expected_dept"],
                    msg=f"{row['id']}: department — {row.get('note', row.get('name', ''))}",
                )
                self.assertEqual(
                    got[1],
                    row["expected_call_type"],
                    msg=f"{row['id']}: call_type — {row.get('note', row.get('name', ''))}",
                )


if __name__ == "__main__":
    unittest.main()
