"""Тесты парсера сводной таблицы ТО (без server2 — временный xlsx)."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from dialog.sto_to_summary_table import (
    clear_sto_to_table_cache,
    get_labor_and_cost_from_table,
    lookup_sto_to_regulation,
)


class StoToSummaryTableTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_sto_to_table_cache()

    def tearDown(self) -> None:
        clear_sto_to_table_cache()

    def _write_sample_xlsx(self, path: Path) -> None:
        df = pd.DataFrame(
            [
                {
                    "Марка": "Chery",
                    "Модель": "Tiggo 7 Pro",
                    "№ ТО": "ТО-2",
                    "Пробег от, км": 10000,
                    "Пробег до, км": 20000,
                    "Время, мин": 90,
                    "Стоимость, руб": 18500,
                },
                {
                    "Марка": "Chery",
                    "Модель": "Tiggo 7 Pro",
                    "№ ТО": "ТО-3",
                    "Пробег от, км": 20001,
                    "Пробег до, км": 30000,
                    "Время, мин": 150,
                    "Стоимость, руб": 21000,
                },
            ]
        )
        df.to_excel(path, index=False)

    def _write_server2_layout_xlsx(self, path: Path) -> None:
        """Как на server2: пустая строка 0, шапка в строке 1."""
        rows = [
            [None, None, None, None, None, "последняя правка"],
            ["марка ам", "модель", "двигатель коробка", "Наименование", "Итого", None],
            ["Chery", "TIGGO 4", "1.5 MT", "5 т.км", 12398.84, None],
            ["Chery", "TIGGO 4", "1.5 MT", "10 т.км", 17773.45, None],
            ["Chery", "TIGGO 4", "1.5 MT", "20 т.км", 17640.77, None],
        ]
        pd.DataFrame(rows).to_excel(path, index=False, header=False)

    def test_lookup_by_mileage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test_to.xlsx"
            self._write_sample_xlsx(path)
            r = lookup_sto_to_regulation("Chery", "Tiggo 7 Pro", 15000, path=path)
            self.assertTrue(r.found)
            self.assertEqual(r.to_label, "ТО-2")
            self.assertEqual(r.duration_min, 150)
            self.assertEqual(r.price_total, 18500.0)

    def test_lookup_server2_header_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "server2_layout.xlsx"
            self._write_server2_layout_xlsx(path)
            r = lookup_sto_to_regulation("Chery", "TIGGO 4", 12000, path=path)
            self.assertTrue(r.found, r)
            self.assertEqual(r.duration_min, 150)
            self.assertAlmostEqual(r.price_total, 17773.45, places=0)
            self.assertIn("10", r.to_label)

    def test_collect_all_specs_for_mileage_interval(self) -> None:
        from dialog.sto_to_summary_table import (
            _collect_sto_to_candidates,
            next_sto_to_disambiguation_field,
        )

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "multi_spec.xlsx"
            rows = [
                [None] * 6,
                ["марка ам", "модель", "двигатель коробка", "Наименование", "Итого", None],
                ["Chery", "TIGGO 4", "1.5 MT", "10 т.км", 100.0, None],
                ["Chery", "TIGGO 4", "1.5 CVT", "10 т.км", 200.0, None],
                ["Chery", "TIGGO 4", "2.0 MT", "10 т.км", 300.0, None],
            ]
            pd.DataFrame(rows).to_excel(path, index=False, header=False)
            candidates, _, cols = _collect_sto_to_candidates(
                "Chery", "TIGGO 4", 12000, path=path,
            )
            self.assertEqual(len(candidates), 3)
            field = next_sto_to_disambiguation_field(
                candidates, cols.get("engine_gearbox_col"), {},
            )
            self.assertEqual(field, "transmission")

    def test_lookup_missing_file(self) -> None:
        r = lookup_sto_to_regulation("Chery", "Tiggo 7", 1000, path=Path("/nonexistent/file.xlsx"))
        self.assertFalse(r.found)

    def test_get_labor_and_cost_fallback(self) -> None:
        duration, price, r = get_labor_and_cost_from_table("Peugeot", "308", 50000)
        self.assertFalse(r.found)
        self.assertEqual(duration, 150)
        self.assertEqual(price, 0.0)


if __name__ == "__main__":
    unittest.main()
