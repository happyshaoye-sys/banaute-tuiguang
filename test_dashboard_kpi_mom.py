import unittest

from core.analysis import calc_kpi_mom_percent


class DashboardKpiMomTests(unittest.TestCase):
    def test_calculates_all_six_dashboard_mom_percentages(self):
        current = {
            "GMV": 300,
            "本期花费": 60,
            "本期在推广计划": 3,
            "店铺ROI": 5,
            "在推广商品数量": 2,
            "加购件数": 30,
        }
        previous = {
            "GMV": 200,
            "本期花费": 50,
            "本期在推广计划": 2,
            "店铺ROI": 4,
            "在推广商品数量": 1,
            "加购件数": 20,
        }

        self.assertEqual(
            calc_kpi_mom_percent(current, previous),
            {
                "GMV": 50.0,
                "本期花费": 20.0,
                "本期在推广计划": 50.0,
                "店铺ROI": 25.0,
                "在推广商品数量": 100.0,
                "加购件数": 50.0,
            },
        )

    def test_uses_existing_zero_baseline_contract(self):
        current = {
            "GMV": 100,
            "本期花费": 20,
            "本期在推广计划": 1,
            "店铺ROI": 5,
            "在推广商品数量": 1,
            "加购件数": 10,
        }
        previous = {key: 0 for key in current}

        self.assertEqual(
            calc_kpi_mom_percent(current, previous),
            {key: 0.0 for key in current},
        )


if __name__ == "__main__":
    unittest.main()
