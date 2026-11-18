"""求车接报与对账结算的纯函数核心。

不依赖 numpy / scipy 或数据库，便于在最小环境下单测；持久化与并发控制
放在 services/requests.py 与 routes/requests.py。

业务口径（与 BUSINESS.md 保持一致）：
- 一条求车编号可产生多次扫码；需求归到“首次扫码”的站点与时段。
- 同一求车编号借成只计一次；未借成但报缺车也计一单。
- 成功单按借成站点与时段，与批次观测（站点时段）对账，对不上先不结算。
- 接报与结算并发时按同一截止点取数；晚到记录留给下一版；结算重试不重复出结果。
"""
from __future__ import annotations

import math
import random

# 结果取值（result）
BORROWED = "borrowed"          # 借成
SHORTAGE = "shortage"          # 未借成但报缺车
RESULT_VALUES = (BORROWED, SHORTAGE)

# 分组后每单的结算状态
PENDING = "pending"            # 尚未进入任何结算
SETTLED = "settled"            # 已结算
HELD = "held"                  # 借成但与批次观测对不上，暂挂不结算

# 结算批次状态
RUNNING = "running"
DONE = "done"

# 一个时段的时长（秒）。站点观测 period 为半小时序号。
PERIOD_SECONDS = 1800


class ValidationError(ValueError):
    """接报数据不合法，可恢复的校验错误。"""


def _finite_number(value, field):
    if isinstance(value, bool):
        raise ValidationError(f"{field} 必须是有限数值")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError(f"{field} 必须是有限数值")
    if not math.isfinite(number):
        raise ValidationError(f"{field} 必须是有限数值")
    return number


def normalize_record(row, index, known_stations=None):
    """校验并归一化单条接报记录，返回可持久化的 dict。

    入站字段：request_id 求车编号、scan_time 扫码时刻（秒级时间戳）、
    station 站点、result 结果（borrowed / shortage）、可选 seq 扫码序号。
    """
    if not isinstance(row, dict):
        raise ValidationError(f"第 {index + 1} 行必须是对象")

    request_id = row.get("request_id")
    if request_id is None or str(request_id).strip() == "":
        raise ValidationError(f"第 {index + 1} 行缺少求车编号")
    request_id = str(request_id).strip()
    if len(request_id) > 80:
        raise ValidationError(f"第 {index + 1} 行求车编号过长（≤80）")

    station = row.get("station")
    if station is None or str(station).strip() == "":
        raise ValidationError(f"第 {index + 1} 行缺少站点")
    station = str(station).strip()
    if len(station) > 120:
        raise ValidationError(f"第 {index + 1} 行站点名称过长（≤120）")
    if known_stations is not None and station not in known_stations:
        raise ValidationError(f"第 {index + 1} 行站点 {station} 不在观测站点内")

    scan_time = _finite_number(row.get("scan_time"), f"第 {index + 1} 行扫码时刻")
    if scan_time < 0:
        raise ValidationError(f"第 {index + 1} 行扫码时刻不能为负")

    result = row.get("result")
    result = str(result).strip().lower() if result is not None else ""
    if result not in RESULT_VALUES:
        raise ValidationError(
            f"第 {index + 1} 行结果必须是 {BORROWED}（借成）或 {SHORTAGE}（缺车）"
        )

    seq = row.get("seq", 0)
    if seq is None or str(seq) == "":
        seq = 0
    seq = int(_finite_number(seq, f"第 {index + 1} 行扫码序号"))
    if seq < 0:
        raise ValidationError(f"第 {index + 1} 行扫码序号不能为负")

    return {
        "request_id": request_id,
        "scan_time": scan_time,
        "station": station,
        "result": result,
        "seq": seq,
    }


def normalize_batch(rows, known_stations=None):
    """校验整批接报记录。"""
    if not isinstance(rows, list) or not rows:
        raise ValidationError("接报记录至少需要一条")
    if len(rows) > 20000:
        raise ValidationError("单次接报不超过 20,000 条")
    return [normalize_record(r, i, known_stations) for i, r in enumerate(rows)]


def period_of(scan_time, origin=0.0):
    """扫码时刻落到的半小时时段序号（相对窗口起点 origin 向下取整）。"""
    return int(math.floor((scan_time - origin) / PERIOD_SECONDS))


def group_requests(events, cutoff, origin=0.0):
    """把同一求车编号的多次扫码归并成一单。

    只取 scan_time <= cutoff 的记录；晚于截止点的记录原样返回，留给下一版。
    origin 为观测窗口起点（秒），扫码时刻减去它后再折算相对半小时时段，
    以便与批次观测的 period 对齐。
    每个求车编号：
      - 首次扫码 = 按 (scan_time, seq) 最小的那条，决定归属站点/时段；
      - 借成只计一次（组内出现任意 borrowed 即视为借成单）。
    返回 (groups, late_events)。
    """
    late, by_id = [], {}
    for event in events:
        if event["scan_time"] > cutoff:
            late.append(event)
            continue
        bucket = by_id.setdefault(event["request_id"], [])
        bucket.append(event)

    groups = []
    for request_id, scans in by_id.items():
        scans.sort(key=lambda e: (e["scan_time"], e["seq"]))
        first = scans[0]
        any_borrowed = any(e["result"] == BORROWED for e in scans)
        # 借成单取最早一次借成扫码作为借成站点/时段依据；缺车单没有借成依据。
        borrowed_scan = next(
            (e for e in scans if e["result"] == BORROWED), None
        )
        groups.append({
            "request_id": request_id,
            "first_station": first["station"],
            "first_period": period_of(first["scan_time"], origin),
            "first_scan_time": first["scan_time"],
            "borrowed": any_borrowed,
            "borrow_station": borrowed_scan["station"] if borrowed_scan else None,
            "borrow_period": period_of(borrowed_scan["scan_time"], origin) if borrowed_scan else None,
            "scan_count": len(scans),
        })
    groups.sort(key=lambda g: (g["first_scan_time"], g["request_id"]))
    return groups, late


def observation_index(observations):
    """把批次观测建成 (station, period) -> 行 的索引，并校验唯一性。"""
    index = {}
    for row in observations:
        key = (str(row["station"]), int(row["period"]))
        if key in index:
            raise ValidationError("批次观测存在重复的站点时段，无法对账")
        index[key] = row
    return index


def settle_groups(groups, obs_index):
    """按对账口径给每个分组打结算状态，并汇总。

    借成单：借成站点+时段必须能在批次观测中对上，否则 held（先不结算）。
    缺车单：不要求与观测对账，直接可结算（报缺车即计一单）。
    """
    items, settled_count, held_count = [], 0, 0
    borrowed_settled = shortage_settled = 0
    for g in groups:
        if g["borrowed"]:
            matched = (g["borrow_station"], g["borrow_period"]) in obs_index
            status = SETTLED if matched else HELD
            reason = "" if matched else "借成站点时段与批次观测对不上，暂挂不结算"
        else:
            status = SETTLED
            matched = True
            reason = "未借成但报缺车，计一单"
        if status == SETTLED:
            settled_count += 1
            if g["borrowed"]:
                borrowed_settled += 1
            else:
                shortage_settled += 1
        else:
            held_count += 1
        items.append({**g, "status": status, "matched": matched, "reason": reason})
    summary = {
        "groups": len(groups),
        "settled": settled_count,
        "held": held_count,
        "borrowed_settled": borrowed_settled,
        "shortage_settled": shortage_settled,
    }
    return items, summary


# --- 研究页：借成量与求车量的偏相关（控制通勤指数） -----------------------

def _ols_residual(x, control):
    """含截距 OLS 残差化（纯 Python）。x、control 等长数值序列。"""
    n = len(x)
    sx = sum(x)
    sc = sum(control)
    scc = sum(c * c for c in control)
    sxc = sum(a * b for a, b in zip(x, control))
    denom = n * scc - sc * sc
    if abs(denom) < 1e-12:
        raise ValidationError("控制变量无变异，无法残差化")
    # 回归 x 对 [1, control]
    beta = (n * sxc - sx * sc) / denom
    alpha = (sx - beta * sc) / n
    return [a - (alpha + beta * b) for a, b in zip(x, control)]


def _pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 1e-14 or vb <= 1e-14:
        raise ValidationError("残差无变异，无法计算相关")
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def _t_sf_two_sided(t, df):
    """双侧 t 检验 p 值（正则化不完全 beta，纯 Python，避免依赖 scipy）。"""
    x = df / (df + t * t)
    # 正则化不完全 beta 函数 I_x(df/2, 1/2)
    if x <= 0:
        return 1.0
    if x >= 1:
        return 0.0
    a = df / 2.0
    b = 0.5
    # 连分式（Lentz）求不完全 beta
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta) / a

    def betacf():
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c = 1.0
        d = 1.0 - qab * x / qap
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        h = d
        for m in range(1, 201):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d
            if abs(d) < 1e-30:
                d = 1e-30
            c = 1.0 + aa / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d
            if abs(d) < 1e-30:
                d = 1e-30
            c = 1.0 + aa / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            delta = d * c
            h *= delta
            if abs(delta - 1.0) < 1e-10:
                break
        return h

    return max(0.0, min(1.0, front * betacf()))


def partial_corr_p(borrowed, requests_total, commute):
    """控制通勤后，借成量与求车量的偏相关及双侧 p 值。"""
    n = len(borrowed)
    if n < 8:
        raise ValidationError("至少需要 8 个时段才能估计偏相关")
    rb = _ols_residual(borrowed, commute)
    rr = _ols_residual(requests_total, commute)
    r = _pearson(rb, rr)
    # 与 business.py 一致：自由度 n-3 的双侧 t 近似
    df = n - 3
    if abs(abs(r) - 1.0) < 1e-12:
        p = 0.0
    else:
        t = r * math.sqrt(df / max(1e-15, 1.0 - r * r))
        p = _t_sf_two_sided(abs(t), df)
    return r, p, n


def build_research(settled_items, observations, diff_threshold=1):
    """按站点聚合借成量/求车量，对照观测做偏相关，并标出差异时段。

    输入 settled_items：仅已结算分组（含 first_station/first_period/borrowed）。
    求车量按“首次扫码站点+时段”计；借成量在同一归属单元内计（每单一计）。
    观测提供 commute 通勤指数作为控制变量。
    """
    obs_index = observation_index(observations)

    # 需求归属单元 (station, first_period)
    demand = {}
    for it in settled_items:
        key = (it["first_station"], it["first_period"])
        cell = demand.setdefault(key, {"requests": 0, "borrowed": 0})
        cell["requests"] += 1
        if it["borrowed"]:
            cell["borrowed"] += 1

    stations = sorted({k[0] for k in demand} | {str(r["station"]) for r in observations})
    station_rows, diff_periods = [], []
    pooled = {"borrowed": [], "requests": [], "commute": []}

    for station in stations:
        periods = sorted({
            p for (s, p) in demand if s == station
        } | {
            int(r["period"]) for r in observations if str(r["station"]) == station
        })
        b_list, r_list, c_list, missing_obs = [], [], [], 0
        for p in periods:
            cell = demand.get((station, p), {"requests": 0, "borrowed": 0})
            obs = obs_index.get((station, p))
            if obs is None:
                # 观测缺失则无法控制通勤，该时段不进偏相关，但仍标差异
                missing_obs += 1
                commute_val = None
            else:
                commute_val = float(obs["commute"])
                b_list.append(cell["borrowed"])
                r_list.append(cell["requests"])
                c_list.append(commute_val)
                pooled["borrowed"].append(cell["borrowed"])
                pooled["requests"].append(cell["requests"])
                pooled["commute"].append(commute_val)
            gap = cell["requests"] - cell["borrowed"]
            if gap >= diff_threshold:
                diff_periods.append({
                    "station": station,
                    "period": p,
                    "requests": cell["requests"],
                    "borrowed": cell["borrowed"],
                    "gap": gap,
                    "has_observation": obs is not None,
                })
        station_requests = sum(
            cell["requests"] for (s, _), cell in demand.items() if s == station
        )
        station_borrowed = sum(
            cell["borrowed"] for (s, _), cell in demand.items() if s == station
        )
        row = {
            "station": station,
            "periods": len(periods),
            "requests": station_requests,
            "borrowed": station_borrowed,
            "usable_pairs": len(b_list),
            "missing_observations": missing_obs,
        }
        try:
            r, p, n = partial_corr_p(b_list, r_list, c_list)
            row.update({"partial": round(r, 5), "raw": round(_pearson(b_list, r_list), 5),
                        "p": float(f"{p:.6g}"), "pairs": n})
        except ValidationError:
            row.update({"partial": None, "raw": None, "p": None, "pairs": len(b_list)})
        station_rows.append(row)

    overall = {}
    try:
        r, p, n = partial_corr_p(pooled["borrowed"], pooled["requests"], pooled["commute"])
        overall = {"partial": round(r, 5),
                   "raw": round(_pearson(pooled["borrowed"], pooled["requests"]), 5),
                   "p": float(f"{p:.6g}"), "pairs": n}
    except ValidationError:
        overall = {"partial": None, "raw": None, "p": None, "pairs": len(pooled["borrowed"])}

    diff_periods.sort(key=lambda d: (-d["gap"], d["station"], d["period"]))
    return {
        "stations": station_rows,
        "overall": overall,
        "diff_periods": diff_periods[:100],
        "diff_threshold": diff_threshold,
        "method": "求车量按首次扫码站点时段归组，借成量每单计一次；将借成量、求车量分别对通勤指数做含截距 OLS 残差化，残差 Pearson r 为偏相关，p 值用自由度 n−3 的双侧 t 近似。",
    }


# --- 确定性演示数据（固定种子，可复现） -----------------------------------

def seed_events(stations=("科技园", "江湾口", "书院路"), periods=96, seed=20261118):
    """生成模拟求车扫码记录。

    与观测的半小时窗口对齐：base_time 为窗口起点，scan_time 落在各 period 内，
    因此借成单的借成站点时段默认能与观测对上；个别单故意落到观测外以演示暂挂。
    返回 (events, base_time)。
    """
    rng = random.Random(seed)
    base_time = 1_763_000_000.0  # 固定窗口起点（秒），演示用
    events = []
    req_seq = 0
    for station in stations:
        for period in range(periods):
            # 通勤高峰求车更密
            peak = 1.4 + 1.1 * math.sin((period % 48 - 12) * math.pi / 24) ** 2
            n_requests = 1 + int(rng.random() * peak * 1.4)
            for _ in range(n_requests):
                req_seq += 1
                request_id = f"R{req_seq:05d}"
                first_offset = period * PERIOD_SECONDS + rng.randint(60, PERIOD_SECONDS - 240)
                # 约 18% 缺车；其余借成
                shortage = rng.random() < 0.18
                if shortage:
                    events.append({
                        "request_id": request_id,
                        "scan_time": round(base_time + first_offset, 1),
                        "station": station, "result": SHORTAGE, "seq": 0,
                    })
                    # 缺车单约一半会再扫一次（重复扫码，仍只计一单）
                    if rng.random() < 0.5:
                        events.append({
                            "request_id": request_id,
                            "scan_time": round(base_time + first_offset + 180, 1),
                            "station": station, "result": SHORTAGE, "seq": 1,
                        })
                else:
                    # 借成：先一次扫码（可能在另一站），随后借成
                    if rng.random() < 0.3:
                        other = stations[(stations.index(station) + 1) % len(stations)]
                        events.append({
                            "request_id": request_id,
                            "scan_time": round(base_time + first_offset, 1),
                            "station": other, "result": SHORTAGE, "seq": 0,
                        })
                        borrow_offset = first_offset + 240
                        b_station, b_period = station, period
                    else:
                        borrow_offset = first_offset
                        b_station, b_period = station, period
                    # 少数借成单故意落到观测窗口外，演示对不上暂挂
                    if rng.random() < 0.03:
                        b_period = periods + 5
                    events.append({
                        "request_id": request_id,
                        "scan_time": round(base_time + b_period * PERIOD_SECONDS
                                           + (borrow_offset % PERIOD_SECONDS), 1),
                        "station": b_station, "result": BORROWED,
                        "seq": 0 if borrow_offset == first_offset else 1,
                    })
    events.sort(key=lambda e: (e["scan_time"], e["request_id"], e["seq"]))
    return events, base_time
