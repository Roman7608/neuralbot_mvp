"""
Регрессия по эталонным транскриптам (fixtures/classify_golden_cases.json).

Запуск из корня проекта:
  python -m unittest call_analytics.test_classify_golden -v

- expected_dept / expected_call_type — широкий слой (classify_auto).
- expected_sto_to_rubric_type (опционально) — узкая рубрика ТО (infer_sto_to_rubric_type): STO_TO_IN | STO_TO_OUT | null.
"""
import json
import unittest
from pathlib import Path

from call_analytics.classify_by_transcript import classify_auto
from call_analytics.sto_booking_dimensions import infer_sto_booking_dimensions
from call_analytics.sto_to_rubric import infer_sto_to_rubric_type

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classify_golden_cases.json"


class TestClassifyGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_FIXTURE, encoding="utf-8") as f:
            cls._cases = json.load(f)

    def test_golden_cases_match_spec(self):
        for row in self._cases:
            with self.subTest(id=row["id"], name=row.get("name", "")):
                if row.get("xfail_until_refactor"):
                    got = classify_auto(row["text"])
                    want = (row["expected_dept"], row["expected_call_type"])
                    if got == want:
                        self.fail(
                            f"{row['id']}: ожидался xfail, но уже совпало с целью — "
                            "уберите xfail_until_refactor в JSON"
                        )
                    continue
                got = classify_auto(row["text"])
                self.assertEqual(
                    got[0],
                    row["expected_dept"],
                    msg=f"department: {row.get('note', '')}",
                )
                self.assertEqual(
                    got[1],
                    row["expected_call_type"],
                    msg=f"call_type: {row.get('note', '')}",
                )
                if "expected_sto_to_rubric_type" in row or "expected_service_brand" in row:
                    dims = infer_sto_booking_dimensions(row["text"])
                    if "expected_sto_to_rubric_type" in row:
                        rub, reason = infer_sto_to_rubric_type(
                            row["text"],
                            got[1],
                            dims,
                            department=got[0],
                        )
                        self.assertEqual(
                            rub,
                            row["expected_sto_to_rubric_type"],
                            msg=f"узкая рубрика СТО_ТО (reason={reason})",
                        )
                    if "expected_service_brand" in row:
                        self.assertEqual(
                            dims.get("service_brand"),
                            row["expected_service_brand"],
                            msg=f"sto_service_brand: {row.get('note', '')}",
                        )
                    if "expected_work_type" in row:
                        self.assertEqual(
                            dims.get("work_type"),
                            row["expected_work_type"],
                            msg=f"work_type: {row.get('note', '')}",
                        )
                    if row.get("expected_sto_to_rubric_type") == "STO_TO_OUT":
                        self.assertEqual(
                            got[1],
                            "STO_OUT",
                            msg="узкая СТО_ТО_исх требует широкий STO_OUT",
                        )


if __name__ == "__main__":
    unittest.main()
