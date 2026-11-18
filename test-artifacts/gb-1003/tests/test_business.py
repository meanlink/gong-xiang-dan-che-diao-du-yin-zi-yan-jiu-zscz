"""求车接报、对账结算与借成/求车偏相关的业务单测。

仅依赖 Python 标准库即可运行（新口径全部在 requests_core 纯函数内）；
若环境装有 NumPy/SciPy，会顺带跑现有 business.analyze 的统计案例。

从批次根目录运行：
    python test-artifacts/gb-1003/tests/test_business.py
"""
import json
import math
import os
import random
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from src.services import requests_core as core  # noqa: E402

T = core.PERIOD_SECONDS
STATIONS = ("科技园", "江湾口", "书院路")


def observations(stations=STATIONS, periods=24):
    rows = []
    for s in stations:
        for p in range(periods):
            rows.append({"station": s, "period": p,
                         "commute": round(0.4 + 0.03 * p + 0.1 * (p % 3), 4),
                         "arrivals": 10 + p, "demand": 5})
    return rows


class NormalizeTests(unittest.TestCase):
    def test_valid_record(self):
        out = core.normalize_record(
            {"request_id": " R1 ", "scan_time": 100.0, "station": " 科技园 ",
             "result": "BORROWED", "seq": "2"}, 0)
        self.assertEqual(out["request_id"], "R1")
        self.assertEqual(out["station"], "科技园")
        self.assertEqual(out["result"], core.BORROWED)
        self.assertEqual(out["seq"], 2)

    def test_missing_and_bad_fields(self):
        bad = [
            {"scan_time": 1, "station": "A", "result": "borrowed"},          # 缺编号
            {"request_id": "R", "scan_time": 1, "station": "A", "result": "x"},  # 结果非法
            {"request_id": "R", "scan_time": float("nan"), "station": "A", "result": "borrowed"},
            {"request_id": "R", "scan_time": True, "station": "A", "result": "borrowed"},  # 布尔拒绝
            {"request_id": "R", "scan_time": -1, "station": "A", "result": "borrowed"},
            {"request_id": "R", "scan_time": 1, "station": "", "result": "borrowed"},
            {"request_id": "R", "scan_time": 1, "station": "A", "result": "shortage", "seq": -1},
        ]
        for i, row in enumerate(bad):
            with self.assertRaises(core.ValidationError, msg=f"row {i}"):
                core.normalize_record(row, i)

    def test_station_whitelist(self):
        with self.assertRaises(core.ValidationError):
            core.normalize_record(
                {"request_id": "R", "scan_time": 1, "station": "陌生站", "result": "borrowed"},
                0, known_stations=set(STATIONS))
        out = core.normalize_record(
            {"request_id": "R", "scan_time": 1, "station": "江湾口", "result": "borrowed"},
            0, known_stations=set(STATIONS))
        self.assertEqual(out["station"], "江湾口")

    def test_batch_bounds(self):
        with self.assertRaises(core.ValidationError):
            core.normalize_batch([])
        with self.assertRaises(core.ValidationError):
            core.normalize_batch("not-a-list")


class GroupingTests(unittest.TestCase):
    def test_first_scan_attribution_and_borrow_once(self):
        events = [
            {"request_id": "R1", "scan_time": T + 100, "station": "科技园", "result": core.SHORTAGE, "seq": 0},
            {"request_id": "R1", "scan_time": T + 200, "station": "江湾口", "result": core.BORROWED, "seq": 1},
            {"request_id": "R1", "scan_time": T + 300, "station": "江湾口", "result": core.BORROWED, "seq": 2},
        ]
        groups, late = core.group_requests(events, cutoff=10 * T)
        self.assertEqual(late, [])
        self.assertEqual(len(groups), 1)
        g = groups[0]
        # 需求归到首次扫码站点/时段
        self.assertEqual(g["first_station"], "科技园")
        self.assertEqual(g["first_period"], core.period_of(T + 100))
        # 借成只计一次
        self.assertTrue(g["borrowed"])
        self.assertEqual(g["borrow_station"], "江湾口")
        self.assertEqual(g["scan_count"], 3)

    def test_shortage_repeat_scan_is_one_order(self):
        events = [
            {"request_id": "R2", "scan_time": 2 * T + 5, "station": "书院路", "result": core.SHORTAGE, "seq": 0},
            {"request_id": "R2", "scan_time": 2 * T + 9, "station": "书院路", "result": core.SHORTAGE, "seq": 1},
        ]
        groups, _ = core.group_requests(events, cutoff=10 * T)
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["borrowed"])
        self.assertIsNone(groups[0]["borrow_station"])

    def test_cutoff_snapshot_and_late(self):
        events = [
            {"request_id": "R1", "scan_time": T, "station": "A", "result": core.BORROWED, "seq": 0},
            {"request_id": "R2", "scan_time": 5 * T, "station": "A", "result": core.BORROWED, "seq": 0},
        ]
        cutoff = 3 * T
        groups, late = core.group_requests(events, cutoff)
        self.assertEqual([g["request_id"] for g in groups], ["R1"])
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["request_id"], "R2")  # 晚到留给下一版
        # 同一截止点重复取数结果一致
        groups2, late2 = core.group_requests(events, cutoff)
        self.assertEqual(groups, groups2)
        self.assertEqual(late, late2)

    def test_period_boundary(self):
        self.assertEqual(core.period_of(T - 1), 0)
        self.assertEqual(core.period_of(T), 1)
        self.assertEqual(core.period_of(2 * T + 999), 2)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.idx = core.observation_index(observations())

    def test_borrowed_matched_settled(self):
        groups = [{"request_id": "R1", "first_station": "科技园", "first_period": 1,
                   "first_scan_time": T, "borrowed": True, "borrow_station": "科技园",
                   "borrow_period": 1, "scan_count": 1}]
        items, summary = core.settle_groups(groups, self.idx)
        self.assertEqual(items[0]["status"], core.SETTLED)
        self.assertTrue(items[0]["matched"])
        self.assertEqual(summary["settled"], 1)
        self.assertEqual(summary["borrowed_settled"], 1)

    def test_borrowed_unmatched_held_not_settled(self):
        groups = [{"request_id": "R9", "first_station": "科技园", "first_period": 1,
                   "first_scan_time": T, "borrowed": True, "borrow_station": "科技园",
                   "borrow_period": 999, "scan_count": 1}]  # 观测里没有该时段
        items, summary = core.settle_groups(groups, self.idx)
        self.assertEqual(items[0]["status"], core.HELD)
        self.assertFalse(items[0]["matched"])
        self.assertEqual(summary["settled"], 0)
        self.assertEqual(summary["held"], 1)

    def test_shortage_counts_one_order_without_observation_match(self):
        groups = [{"request_id": "R8", "first_station": "书院路", "first_period": 3,
                   "first_scan_time": 3 * T, "borrowed": False, "borrow_station": None,
                   "borrow_period": None, "scan_count": 2}]
        items, summary = core.settle_groups(groups, self.idx)
        self.assertEqual(items[0]["status"], core.SETTLED)
        self.assertEqual(summary["shortage_settled"], 1)

    def test_duplicate_observation_rejected(self):
        dup = observations() + [{"station": "科技园", "period": 0, "commute": 1, "arrivals": 1, "demand": 1}]
        with self.assertRaises(core.ValidationError):
            core.observation_index(dup)


class ResearchTests(unittest.TestCase):
    @staticmethod
    def _independent_partial(borrowed, requests_total, commute):
        def resid(x, z):
            n = len(x)
            sx, sz = sum(x), sum(z)
            szz = sum(v * v for v in z)
            sxz = sum(a * b for a, b in zip(x, z))
            beta = (n * sxz - sx * sz) / (n * szz - sz * sz)
            alpha = (sx - beta * sz) / n
            return [a - (alpha + beta * b) for a, b in zip(x, z)]

        def pear(a, b):
            n = len(a)
            ma, mb = sum(a) / n, sum(b) / n
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            va = sum((x - ma) ** 2 for x in a)
            vb = sum((y - mb) ** 2 for y in b)
            return cov / math.sqrt(va * vb)

        return pear(resid(borrowed, commute), resid(requests_total, commute))

    def _settled_payload(self, periods=40, station="科技园"):
        rng = random.Random(11)
        settled = []
        for p in range(periods):
            n_req = 2 + int(3 * abs(math.sin(p / 4)) + rng.random() * 2)
            n_borrow = max(0, n_req - (1 if rng.random() < 0.35 else 0))
            for k in range(n_req):
                settled.append({"request_id": f"{station}-{p}-{k}",
                                "first_station": station, "first_period": p,
                                "borrowed": k < n_borrow})
        return settled

    def test_aggregation_partial_and_diff(self):
        obs = observations(stations=("科技园",), periods=40)
        settled = self._settled_payload()
        res = core.build_research(settled, obs)
        # JSON 严格可序列化（无 NaN/Inf）
        json.dumps(res, allow_nan=False)

        row = next(s for s in res["stations"] if s["station"] == "科技园")
        # 求车量、借成量与逐单一致
        self.assertEqual(row["requests"], len(settled))
        self.assertEqual(row["borrowed"], sum(1 for s in settled if s["borrowed"]))

        # 独立复算偏相关
        demand = {}
        for it in settled:
            cell = demand.setdefault((it["first_station"], it["first_period"]),
                                     {"requests": 0, "borrowed": 0})
            cell["requests"] += 1
            cell["borrowed"] += 1 if it["borrowed"] else 0
        b = [demand[("科技园", p)]["borrowed"] for p in range(40)]
        rq = [demand[("科技园", p)]["requests"] for p in range(40)]
        cm = [o["commute"] for o in obs]
        expect = self._independent_partial(b, rq, cm)
        self.assertAlmostEqual(row["partial"], round(expect, 5), places=5)
        self.assertAlmostEqual(res["overall"]["partial"], round(expect, 5), places=5)
        # 差异时段：gap = 求车 - 借成，按缺口降序
        self.assertTrue(all(d["gap"] >= 1 for d in res["diff_periods"]))
        gaps = [d["gap"] for d in res["diff_periods"]]
        self.assertEqual(gaps, sorted(gaps, reverse=True))

    def test_insufficient_sample_raises(self):
        obs = observations(stations=("科技园",), periods=4)
        settled = self._settled_payload(periods=4)
        res = core.build_research(settled, obs)
        # 样本不足时不给伪造相关，给 None 而不是抛断整体
        self.assertIsNone(res["overall"]["partial"])

    def test_known_t_table(self):
        # 双侧 p 值与 t 表临界值对照
        self.assertAlmostEqual(core._t_sf_two_sided(2.228, 10), 0.05, places=3)
        self.assertAlmostEqual(core._t_sf_two_sided(2.086, 20), 0.05, places=3)
        self.assertAlmostEqual(core._t_sf_two_sided(2.0, 10), 0.0734, places=3)


class SeedTests(unittest.TestCase):
    def test_seed_deterministic_and_cutoff_aligned(self):
        e1, base1 = core.seed_events()
        e2, base2 = core.seed_events()
        self.assertEqual(e1, e2)  # 固定种子可复现
        self.assertEqual(base1, base2)
        self.assertTrue(e1)  # 非空
        ids = {e["request_id"] for e in e1}
        # 存在缺车单、存在借成单
        self.assertIn(core.SHORTAGE, {e["result"] for e in e1})
        self.assertIn(core.BORROWED, {e["result"] for e in e1})
        # 同一编号有多条扫码（重复扫码场景）
        self.assertTrue(any(sum(1 for e in e1 if e["request_id"] == i) > 1 for i in ids))
        # 所有事件时刻为非负有限数
        self.assertTrue(all(math.isfinite(e["scan_time"]) and e["scan_time"] >= 0 for e in e1))


# 现有 business.analyze（NumPy/SciPy）在装好依赖的环境（如 Docker）内运行
try:
    from src.services import business  # noqa: E402
    HAS_NUMPY = True
except Exception:  # pragma: no cover - 最小环境缺库时跳过
    HAS_NUMPY = False


@unittest.skipUnless(HAS_NUMPY, "环境未安装 NumPy/SciPy，跳过现有统计案例")
class ExistingBusinessTests(unittest.TestCase):
    def test_fixed_seed_and_param_change(self):
        rows = business.seed_records()
        self.assertEqual(len(rows), 288)
        self.assertEqual(rows, business.seed_records())  # 固定种子
        base = business.analyze(rows, {"lag": 1, "station": "全部"})
        shifted = business.analyze(rows, {"lag": 0, "station": "全部"})
        self.assertNotEqual(base["table"]["rows"], shifted["table"]["rows"])
        json.dumps(base, allow_nan=False)

    def test_empty_and_boundary_rejected(self):
        with self.assertRaises(ValueError):
            business.analyze([], {"lag": 1, "station": "全部"})
        with self.assertRaises(ValueError):
            business.analyze(business.seed_records(), {"lag": 4, "station": "全部"})
        with self.assertRaises(ValueError):
            business.analyze(business.seed_records(), {"lag": 0.5, "station": "全部"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
