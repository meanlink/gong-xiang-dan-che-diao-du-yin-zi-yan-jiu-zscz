"""站点补车需求：控制通勤时段后的偏相关与滞后关系。"""
import math
import random
import numpy as np
from scipy import stats


def metadata():
    return {"name": "共享单车调度因子研究", "short": "cycle-factors", "tagline": "区分时段共变与提前信号，再决定补车观察点。",
            "client": "泊轮出行 · 城西 12 人调度研究组，维护 3 个试点站点", "workflow": "调度员按昨日借车曲线补车，研究员在表格里直接相关所有变量。", "pains": ["通勤高峰制造虚假相关", "提前量没有统一定义", "跨站点拼接导致错位配对"], "upstream": "站点半小时观测 BL_station_observation.csv", "downstream": "运力研究员选择下一轮调度试点的提前观测因子",
            "entities": ["站点时段观测", "因子研究", "调度研究简报"], "dataset_name": "城西三站 · 288 条半小时观测",
            "columns": [{"key": "station", "label": "站点", "type": "string"}, {"key": "period", "label": "时段序号", "type": "number"}, {"key": "commute", "label": "通勤指数", "type": "number"}, {"key": "arrivals", "label": "附近到达客流", "type": "number"}, {"key": "demand", "label": "借车需求", "type": "number"}],
            "parameters": [{"key": "lag", "label": "需求滞后（半小时）", "type": "select", "default": "1", "options": [{"value": str(i), "label": f"{i} 个时段"} for i in range(4)]}, {"key": "station", "label": "分析站点", "type": "select", "default": "全部", "options": [{"value": n, "label": n} for n in ["全部", "科技园", "江湾口", "书院路"]]}],
            "workbench_title": "调度先行因子研究", "action_label": "计算偏相关与提前量", "unit": "人次", "accent": "oklch(49% 0.12 125)", "style": "transit",
            "seed_story": ["07:30 导入 BL_station_observation.csv，共 288 条观测。", "07:45 核对科技园、江湾口、书院路各 96 个时段。", "08:00 研究员选择滞后 1 个半小时。", "08:05 执行分析，比较原始相关与控制通勤后的偏相关。", "08:20 查看各站点配对数量，确认无跨站点错配。", "08:40 改为滞后 0，观察同步与提前关系变化。", "09:00 导出因子简报，安排一周观察性试点。"],
            "method": "按站点、period 排序，客流 X(t) 与需求 Y(t+lag) 配对；将 X、Y 分别对目标时段通勤指数做含截距 OLS 残差化，残差 Pearson r 为偏相关；p 值用自由度 n−3 的双侧 t 近似。",
            "limitations": ["观察相关不证明因果", "时序自相关可能低估 p 值", "通勤指数仅控制一个已观测混杂因素"]}


def seed_records():
    rng = random.Random(1003)
    rows = []
    for j, name in enumerate(["科技园", "江湾口", "书院路"]):
        arrival_history = []
        for t in range(96):
            peak = 1.4 + 1.1 * math.sin((t % 48 - 12) * math.pi / 24) ** 2
            arrivals = max(0, 23 + 20 * peak + rng.gauss(0, 12))
            previous = arrival_history[-1] if arrival_history else arrivals
            demand = max(0, 5 + 9 * peak + (.4 - .07 * j) * previous + rng.gauss(0, 4))
            rows.append({"station": name, "period": t, "commute": round(peak, 4), "arrivals": round(arrivals, 2), "demand": round(demand, 2)})
            arrival_history.append(arrivals)
    return rows


def analyze(records, params):
    try:
        lag_number = float(params.get("lag", 1))
        lag = int(lag_number)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("滞后须为 0–3 的整数")
    if lag_number != lag or lag not in range(4):
        raise ValueError("滞后须为 0–3 的整数")
    selection = str(params.get("station", "全部"))
    groups = {}
    for row in records:
        try:
            station = str(row["station"])
            if selection != "全部" and station != selection:
                continue
            vals = [float(row[k]) for k in ("period", "commute", "arrivals", "demand")]
            if not all(math.isfinite(x) for x in vals) or vals[2] < 0 or vals[3] < 0:
                raise ValueError()
            groups.setdefault(station, []).append(vals)
        except (KeyError, ValueError, TypeError):
            raise ValueError("观测须含站点、时段、通勤指数及非负客流需求")
    paired = []
    rows, warnings = [], []
    for station, values in sorted(groups.items()):
        values.sort(key=lambda x: x[0])
        by_period = {v[0]: v for v in values}
        if len(by_period) != len(values):
            raise ValueError("同站同一时段不得重复")
        local = [(v[2], by_period[v[0] + lag][3], by_period[v[0] + lag][1]) for v in values if v[0] + lag in by_period]
        if len(local) < 8:
            raise ValueError("每个选定站点至少需要 8 对可匹配时段")
        paired.extend(local)
        x, y, control = np.array(local).T
        design = np.column_stack([np.ones(len(x)), control])
        rx, ry = x - design @ np.linalg.lstsq(design, x, rcond=None)[0], y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
        if min(float(np.std(rx)), float(np.std(ry))) < 1e-10:
            raise ValueError("残差无变异，无法计算偏相关")
        partial = float(np.corrcoef(rx, ry)[0, 1])
        original = float(np.corrcoef(x, y)[0, 1])
        t = partial * math.sqrt((len(local) - 3) / max(1e-15, 1 - partial ** 2))
        p = float(2 * stats.t.sf(abs(t), len(local) - 3))
        rows.append({"station": station, "pairs": len(local), "raw": round(original, 5), "partial": round(partial, 5), "p": float(f"{p:.6g}")})
    if not paired:
        raise ValueError("没有符合所选站点的数据")
    top = max(rows, key=lambda r: abs(r["partial"]))
    warnings.append("连续时段可能相关，p 值仅作探索性参考；不得解释为因果。")
    return {"summary": f"在 {lag * 30} 分钟提前量下配对 {len(paired)} 条观测，{top['station']} 的偏相关最强（r={top['partial']:.3f}）。",
            "metrics": [{"label": "有效配对", "value": len(paired), "unit": "对"}, {"label": "提前量", "value": lag * 30, "unit": "分钟"}, {"label": "最强偏相关", "value": top["partial"]}, {"label": "站点数", "value": len(rows)}],
            "table": {"columns": [{"key": "station", "label": "站点"}, {"key": "pairs", "label": "有效配对"}, {"key": "raw", "label": "原始相关 r"}, {"key": "partial", "label": "偏相关 r"}, {"key": "p", "label": "参考 p 值"}], "rows": rows},
            "chart": {"type": "bar", "labels": [r["station"] for r in rows], "series": [{"name": "原始相关", "values": [r["raw"] for r in rows]}, {"name": "控制通勤后", "values": [r["partial"] for r in rows]}]},
            "findings": [f"{r['station']}：原始 r={r['raw']:.3f}，控制通勤后 r={r['partial']:.3f}；{r['pairs']} 对。" for r in rows], "method": metadata()["method"], "warnings": warnings}
