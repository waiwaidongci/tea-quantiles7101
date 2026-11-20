"""含水率配对研究：确定性最大配对数 + 最小总时差 + 中位数精确上下界。

问题
----
两册杯签遗失的化验读数（初测册、复测册）各有采样时间；只允许把采样时间差
不超过 ``tolerance_minutes`` 的初测与复测配成一份，且每份读数最多使用一次。
配对目标按字典序：
    1. 配对数最多；
    2. 在配对数最多的前提下，总时差最小。
然后对所有取得该 (最大份数, 最小总时差) 的配对，计算
``复测含水率 - 初测含水率`` 的中位数的下界与上界，并为每个界给出一个
达到它的配对明细。偶数份取中间两项的平均（内部用“两倍中位数”整数存储）。

算法（精确、确定性、多项式时间，全程最小费用指派）
-------------------------------------------------
* 一次带权重的最小费用指派（Hungarian）完成三级字典序：
  配对数 > 总时差 > 计数奖励。权重取整数且量级分层（见 ``_Tier``），
  因此“份数优先、其次时差、最后计数”严格成立。
* 记所选 k 条边的差值升序为 v_1<=...<=v_k。
  奇数 k=2h-1：中位数 = v_h。
      下界 = 最小的 T，使某最优配对里“至少 h 条边 delta<=T”；
      上界 = -min_{u=-delta}(u_h)，即在取负的差值上复用下界过程。
  偶数 k=2m：两倍中位数 = v_m + v_{m+1}。
      下界 = 最小 a+b，使某最优配对里“至少 m 条边 delta<=a 且
            至少 m+1 条边 delta<=b”（a<=b）；
      上界 = -min_{u=-delta}(u_m+u_{m+1})，同样在取负差值上复用下界过程。
  计数条件用“主计数 > 次计数”的两级奖励在同一次指派中同时最优化；
  对阈值单调，因此可二分。可行性判断与同成本配法的枚举都严格限制在
  “份数 k、总时差 total”的最优集合内。
* 最终再以“边序号越小越优先”作为最末级 tie-break，保证同一冻结快照重跑
  得到完全相同的配对明细；scipy 的 Hungarian 结果是确定性的。

单位
----
含水率内部换算为 10^-6 百分点（micro）整数；差值、时差、两倍中位数均为
整数，不经过二进制浮点，重跑可逐字节复现。
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import numpy as np
from scipy.optimize import linear_sum_assignment

MICRO = 1_000_000
MAX_READINGS_PER_BOOKLET = 40
MAX_TOLERANCE_MINUTES = 1440
MAX_SAMPLE_MINUTE = 10_079


class PairingError(ValueError):
    """输入不满足配对研究的前提。"""


def _micro(value):
    """把 JSON 里的含水率数值转成 10^-6 百分点整数。"""
    try:
        text = value if isinstance(value, str) else str(value)
        dec = Decimal(text)
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - 防御
        raise PairingError(f'含水率不是合法数值：{value!r}') from exc
    if not dec.is_finite():
        raise PairingError(f'含水率必须是有限数值：{value!r}')
    scaled = dec * MICRO
    if scaled != scaled.to_integral_value():
        raise PairingError('含水率最多支持 6 位小数')
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))


def _reading(row, label):
    if not isinstance(row, dict):
        raise PairingError(f'{label}读数格式不正确')
    for key in ('source_key', 'sampled_at', 'moisture'):
        if key not in row:
            raise PairingError(f'{label}读数缺少字段 {key}')
    raw_time = row['sampled_at']
    if isinstance(raw_time, bool):
        raise PairingError(f'{label}读数 {row.get("source_key")} 的采样时间必须是整数分钟')
    try:
        minute = int(raw_time)
    except (TypeError, ValueError) as exc:
        raise PairingError(f'{label}读数 {row.get("source_key")} 的采样时间必须是整数分钟') from exc
    if minute != raw_time:
        raise PairingError(f'{label}读数 {row.get("source_key")} 的采样时间必须是整数分钟')
    if not 0 <= minute <= MAX_SAMPLE_MINUTE:
        raise PairingError(f'{label}读数 {row.get("source_key")} 的采样时间超出 0–{MAX_SAMPLE_MINUTE} 分钟')
    key = str(row['source_key'])
    if not key:
        raise PairingError(f'{label}读数缺少源记录编号')
    return {'source_key': key, 'sampled_at': minute, 'moisture_micro': _micro(row['moisture'])}


def prepare(snapshot, tolerance_minutes):
    """校验并归一化冻结快照，返回 (initial, retest, tolerance)。"""
    if not isinstance(snapshot, dict):
        raise PairingError('冻结快照格式不正确')
    if isinstance(tolerance_minutes, bool) or not isinstance(tolerance_minutes, int):
        raise PairingError('允许时差必须是整数分钟')
    if not 1 <= tolerance_minutes <= MAX_TOLERANCE_MINUTES:
        raise PairingError(f'允许时差必须在 1–{MAX_TOLERANCE_MINUTES} 分钟之间')
    initial_raw = snapshot.get('initial')
    retest_raw = snapshot.get('retest')
    if not isinstance(initial_raw, list) or not isinstance(retest_raw, list):
        raise PairingError('冻结快照必须同时包含初测册与复测册读数')
    if not initial_raw:
        raise PairingError('初测册没有读数，无法配对')
    if not retest_raw:
        raise PairingError('复测册没有读数，无法配对')
    if len(initial_raw) > MAX_READINGS_PER_BOOKLET:
        raise PairingError(f'初测册有 {len(initial_raw)} 份，超过每册 {MAX_READINGS_PER_BOOKLET} 份上限')
    if len(retest_raw) > MAX_READINGS_PER_BOOKLET:
        raise PairingError(f'复测册有 {len(retest_raw)} 份，超过每册 {MAX_READINGS_PER_BOOKLET} 份上限')
    initial = [_reading(row, '初测册') for row in initial_raw]
    retest = [_reading(row, '复测册') for row in retest_raw]
    if len({row['source_key'] for row in initial}) != len(initial):
        raise PairingError('初测册存在重复的源记录编号')
    if len({row['source_key'] for row in retest}) != len(retest):
        raise PairingError('复测册存在重复的源记录编号')
    # 排序固定，保证同一快照每次得到同一套边序号与明细顺序。
    initial.sort(key=lambda row: (row['sampled_at'], row['source_key']))
    retest.sort(key=lambda row: (row['sampled_at'], row['source_key']))
    return initial, retest, tolerance_minutes


def build_edges(initial, retest, tolerance):
    """可行边：时差不超过允许时差。delta 为复测减初测（micro）。"""
    edges = []
    for i, a in enumerate(initial):
        for j, b in enumerate(retest):
            diff = abs(a['sampled_at'] - b['sampled_at'])
            if diff <= tolerance:
                edges.append((i, j, diff, b['moisture_micro'] - a['moisture_micro']))
    return edges


class _Tier:
    """三级字典序权重：配对数 > 总时差 > 计数奖励 > 边序号 tie-break。"""

    def __init__(self, n_initial, n_retest, tolerance, n_edges, primary=None, secondary=None):
        k = min(n_initial, n_retest)
        self.tie_scale = 1
        self.tie_max = n_edges                      # 边序号奖励 <= n_edges
        if secondary is not None:
            self.second = k * self.tie_max + 1
        else:
            self.second = 0
        if primary is not None:
            self.first = k * (self.second + self.tie_max) + 1
        else:
            self.first = 0
        self.bonus_max = (self.first + self.second) + self.tie_max
        self.time = k * self.bonus_max + 1
        self.reward = self.time * (k * tolerance + 1) + self.bonus_max + 1
        self.penalty = self.reward * (n_initial + n_retest + 2) + self.time * k * tolerance + 1

    def weight(self, edge_index, n_edges, time_diff, delta, primary, secondary):
        value = -self.reward + self.time * time_diff
        if primary is not None and primary(delta):
            value -= self.first
        if secondary is not None and secondary(delta):
            value -= self.second
        value -= (n_edges - 1 - edge_index)  # 边序号越小越优先（tie-break）
        return value


def _assign(n_initial, n_retest, edges, tolerance, primary=None, secondary=None, key=None):
    """一次最小费用指派，返回 (配对数, 总时差, 边序号列表)。

    primary / secondary 是作用于“比较键”的谓词，分别获得第一/第二级计数
    奖励。键默认是 delta 本身；求上界时传入 key=lambda d: -d。
    """
    key = key or (lambda d: d)
    n_edges = len(edges)
    tiers = _Tier(n_initial, n_retest, tolerance, n_edges, primary, secondary)
    size = n_initial + n_retest
    cost = np.full((size, size), tiers.penalty, dtype=np.int64)
    cost[n_initial:, :n_retest] = 0
    cost[:n_initial, n_retest:] = 0
    cost[n_initial:, n_retest:] = 0
    edge_index = {}
    for e, (i, j, w, d) in enumerate(edges):
        cost[i, j] = tiers.weight(e, n_edges, w, key(d), primary, secondary)
        edge_index[(i, j)] = e
    rows, cols = linear_sum_assignment(cost)
    selected = sorted(edge_index[(int(i), int(j))]
                      for i, j in zip(rows, cols)
                      if i < n_initial and j < n_retest and (int(i), int(j)) in edge_index)
    total = sum(edges[e][2] for e in selected)
    return len(selected), int(total), selected


def max_cardinality_min_cost(n_initial, n_retest, edges, tolerance):
    """字典序 (最大配对数, 最小总时差) 的最优配对。"""
    if not edges:
        return 0, 0, []
    return _assign(n_initial, n_retest, edges, tolerance)


def _min_h_smallest(keys, edges, n_initial, n_retest, tolerance, k, total, h, key=None):
    """同成本配法里第 h 小的最小可能值（keys[e] 为排序键，通常是 delta 或 -delta）。

    对候选阈值 T 二分：是否存在最优配对，使至少 h 条边的 key<=T。计数对 T
    单调，所以最小可行 T 就是所有同成本配法里 v_h 的下界；若 T 落在键值上
    （候选只取键值），最小可行 T 恰好等于某个配对的第 h 小键值。
    """
    key = key or (lambda d: d)
    candidates = sorted(set(keys))
    lo, hi = 0, len(candidates) - 1
    found = None
    while lo <= hi:
        mid = (lo + hi) // 2
        threshold = candidates[mid]
        kk, tt, selected = _assign(
            n_initial, n_retest, edges, tolerance,
            primary=lambda d, t=threshold: d <= t, key=key)
        if kk != k or tt != total:
            raise PairingError('中位数界搜索偏离了冻结的份数或总时差')
        if sum(1 for e in selected if keys[e] <= threshold) >= h:
            found = (threshold, selected)
            hi = mid - 1
        else:
            lo = mid + 1
    if found is None:
        raise PairingError('中位数下界二分为空，数据可能已变化')
    return found


def _min_even_pair_sum(keys, edges, n_initial, n_retest, tolerance, k, total, key=None):
    """同成本配法里 keys 的第 m 与第 m+1 小之和的最小值。

    对候选 a 逐个试：若某最优配对里至少 m 条 key<=a、至少 m+1 条 key<=a，
    则 (a,a) 可行；否则二分最小 b>=a 让第二级计数达标。取可行 a+b 的最小。
    """
    m = k // 2
    key = key or (lambda d: d)
    candidates = sorted(set(keys))
    best = None
    for idx, a in enumerate(candidates):
        if best is not None and 2 * a >= best[0]:
            break                      # b>=a ⇒ a+b>=2a，不可能更小
        kk, tt, selected = _assign(
            n_initial, n_retest, edges, tolerance,
            primary=lambda d, t=a: d <= t, key=key)
        if kk != k or tt != total:
            raise PairingError('中位数界搜索偏离了冻结的份数或总时差')
        count_a = sum(1 for e in selected if keys[e] <= a)
        if count_a < m:
            continue
        if count_a >= m + 1:
            b = a
        else:
            lo, hi = idx, len(candidates) - 1
            b = None
            while lo <= hi:
                mid = (lo + hi) // 2
                probe = candidates[mid]
                kk2, tt2, sel2 = _assign(
                    n_initial, n_retest, edges, tolerance,
                    primary=lambda d, t=a: d <= t,
                    secondary=lambda d, t=probe: d <= t, key=key)
                if (kk2 == k and tt2 == total
                        and sum(1 for e in sel2 if keys[e] <= a) >= m
                        and sum(1 for e in sel2 if keys[e] <= probe) >= m + 1):
                    b = probe
                    hi = mid - 1
                else:
                    lo = mid + 1
            if b is None:
                continue
        if best is None or a + b < best[0]:
            best = (a + b, a, b)
    if best is None:
        raise PairingError('中位数下界枚举为空，数据可能已变化')
    return best


def _even_detail(keys, edges, n_initial, n_retest, tolerance, k, total, a, b, key=None):
    """在 (a, b) 两级计数约束下取一份规范配对明细。"""
    kk, tt, selected = _assign(
        n_initial, n_retest, edges, tolerance,
        primary=lambda d, t=a: d <= t,
        secondary=lambda d, t=b: d <= t, key=key)
    m = k // 2
    if kk != k or tt != total:
        raise PairingError('中位数界明细偏离了冻结的份数或总时差')
    if (sum(1 for e in selected if keys[e] <= a) < m
            or sum(1 for e in selected if keys[e] <= b) < m + 1):
        raise PairingError('中位数界明细不满足阈值条件')
    return selected


def _bounds(n_initial, n_retest, edges, tolerance, k, total):
    """返回 (下界两倍中位数, 下界明细, 上界两倍中位数, 上界明细)。

    deltas 用于下界；neg 用于上界（max med(x) = -min med(-x)）。因配对集合
    完全不依赖差值符号，上界就是对取负后的同一个下界问题求解再还原。
    """
    deltas = [edge[3] for edge in edges]
    neg = [-d for d in deltas]
    if k % 2 == 1:
        h = (k + 1) // 2
        low_key, low_selected = _min_h_smallest(
            deltas, edges, n_initial, n_retest, tolerance, k, total, h)
        neg_key, high_selected = _min_h_smallest(
            neg, edges, n_initial, n_retest, tolerance, k, total, h,
            key=lambda d: -d)
        low_twice, high_twice = 2 * low_key, -2 * neg_key
    else:
        low_sum, low_a, low_b = _min_even_pair_sum(
            deltas, edges, n_initial, n_retest, tolerance, k, total)
        neg_sum, neg_a, neg_b = _min_even_pair_sum(
            neg, edges, n_initial, n_retest, tolerance, k, total, key=lambda d: -d)
        low_twice, high_twice = low_sum, -neg_sum
        low_selected = _even_detail(
            deltas, edges, n_initial, n_retest, tolerance, k, total, low_a, low_b)
        high_selected = _even_detail(
            neg, edges, n_initial, n_retest, tolerance, k, total, neg_a, neg_b,
            key=lambda d: -d)
    if low_twice > high_twice:
        raise PairingError('中位数下界大于上界，结果不可用')
    return low_twice, low_selected, high_twice, high_selected


def _twice_median_of(selected, edges):
    values = sorted(edges[e][3] for e in selected)
    k = len(values)
    if k == 0:
        return None
    return 2 * values[k // 2] if k % 2 else values[k // 2 - 1] + values[k // 2]


def _format_scaled(value_micro, denominator):
    """value_micro / denominator 的十进制字符串（去尾零）。"""
    if value_micro is None:
        return None
    sign = '-' if value_micro < 0 else ''
    value = abs(value_micro)
    whole, rest = divmod(value, denominator)
    text = format(Decimal(whole) + Decimal(rest) / Decimal(denominator), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return sign + text


def _format_twice(twice_micro):
    """两倍中位数（分母 2）→ 含水率差值百分点字符串。"""
    return _format_scaled(twice_micro, 2 * MICRO)


def _format_delta(delta_micro):
    return _format_scaled(delta_micro, MICRO)


def _detail(selected, edges, initial, retest):
    rows = []
    for e in selected:
        i, j, diff, delta = edges[e]
        rows.append({
            'initial_key': initial[i]['source_key'],
            'initial_time': initial[i]['sampled_at'],
            'retest_key': retest[j]['source_key'],
            'retest_time': retest[j]['sampled_at'],
            'time_diff': diff,
            'delta': _format_delta(delta),
        })
    rows.sort(key=lambda row: (
        row['initial_time'], row['initial_key'], row['retest_time'], row['retest_key']))
    return rows


def analyze(snapshot, tolerance_minutes):
    """完整配对研究，返回可冻结、可复核的结果字典。"""
    initial, retest, tolerance = prepare(snapshot, tolerance_minutes)
    n_initial, n_retest = len(initial), len(retest)
    edges = build_edges(initial, retest, tolerance)
    k, total, _ = max_cardinality_min_cost(n_initial, n_retest, edges, tolerance)

    base = {
        'tolerance_minutes': tolerance,
        'initial_count': n_initial,
        'retest_count': n_retest,
        'feasible_pairs': len(edges),
        'cardinality': k,
        'total_time_diff': total,
        'unit': '百分点',
        'method': '先配足最多份数，再使总时差最小；同成本配法全部纳入中位数上下界',
    }
    if k == 0:
        nearest = min(
            (abs(a['sampled_at'] - b['sampled_at']) for a in initial for b in retest),
            default=None,
        )
        base.update({
            'bound_lower': None,
            'bound_upper': None,
            'verified': True,
            'checks': ['最大配对数=0'],
            'reason': (
                f'允许时差 {tolerance} 分钟内没有任何初测—复测读数可配对'
                + (f'；两册最近采样时间相差 {nearest} 分钟' if nearest is not None else '')
                + '。可增大允许时差，或核对两册的采样时间。'
            ),
        })
        return base

    low_twice, low_selected, high_twice, high_selected = _bounds(
        n_initial, n_retest, edges, tolerance, k, total)

    checks = []
    for label, twice, selected in (('下界', low_twice, low_selected),
                                   ('上界', high_twice, high_selected)):
        if len(selected) != k:
            raise PairingError(f'{label}配对明细份数不是最大份数 {k}')
        if sum(edges[e][2] for e in selected) != total:
            raise PairingError(f'{label}配对明细的总时差不是最小总时差 {total}')
        if _twice_median_of(selected, edges) != twice:
            raise PairingError(f'{label}配对明细的中位数与求解结果不一致')
        checks.append(f'{label}明细核对：配对数 {k}、总时差 {total}、两倍中位数 {twice}')
    checks.append('下界不大于上界')
    checks.append(f'同成本配法均满足配对数 {k} 与总时差 {total}')

    base.update({
        'bound_lower': {
            'twice_median_micro': low_twice,
            'median': _format_twice(low_twice),
            'pairing': _detail(low_selected, edges, initial, retest),
        },
        'bound_upper': {
            'twice_median_micro': high_twice,
            'median': _format_twice(high_twice),
            'pairing': _detail(high_selected, edges, initial, retest),
        },
        'verified': True,
        'checks': checks,
        'reason': '',
        'interpretation': (
            f'{n_initial} 份初测与 {n_retest} 份复测在 {tolerance} 分钟允许时差内最多配 {k} 份，'
            f'最小总时差 {total} 分钟；所有同成本配法的“复测−初测”含水率差值中位数'
            f'介于 {_format_twice(low_twice)} 与 {_format_twice(high_twice)} 百分点之间。'
        ),
    })
    return base
