"""配对研究算法的独立 oracle：小规模穷举。

穷举逐条枚举所有匹配，取“份数最大、总时差最小”的全部配法，再算两倍中位数
的最小/最大值。用来核对生产算法在同一小例上的结果。
"""
import itertools


def normalize(initial, retest):
    initial = sorted(initial, key=lambda r: (r['sampled_at'], r['source_key']))
    retest = sorted(retest, key=lambda r: (r['sampled_at'], r['source_key']))
    return initial, retest


def _matchings(n1, n2):
    """枚举初测每份读数的去向（配某个复测或弃配），返回 (i,j) 列表集合。"""
    def rec(i, used, cur):
        if i == n1:
            yield tuple(cur)
            return
        yield from rec(i + 1, used, cur)
        for j in range(n2):
            if j not in used:
                cur.append((i, j))
                yield from rec(i + 1, used | {j}, cur)
                cur.pop()
    yield from rec(0, frozenset(), [])


def exhaustive(initial, retest, tolerance, micro=1_000_000):
    """返回 dict，含最大份数、最小总时差、两倍中位数下界/上界与全部最优配法。"""
    initial, retest = normalize(initial, retest)
    n1, n2 = len(initial), len(retest)
    best_k = -1
    records = []
    for matching in _matchings(n1, n2):
        cost = 0
        feasible = True
        for i, j in matching:
            diff = abs(initial[i]['sampled_at'] - retest[j]['sampled_at'])
            if diff > tolerance:
                feasible = False
                break
            cost += diff
        if not feasible:
            continue
        k = len(matching)
        if k > best_k:
            best_k = k
            records = []
        if k == best_k:
            records.append((cost, matching))
    if best_k <= 0:
        return {'cardinality': 0, 'pairings': []}
    best_cost = min(cost for cost, _ in records)
    optimal = [m for cost, m in records if cost == best_cost]
    twice_values = []
    for matching in optimal:
        deltas = sorted(
            int(round((float(retest[j]['moisture']) - float(initial[i]['moisture'])) * micro))
            for i, j in matching)
        k = len(deltas)
        twice = 2 * deltas[k // 2] if k % 2 else deltas[k // 2 - 1] + deltas[k // 2]
        twice_values.append(twice)
    return {
        'cardinality': best_k,
        'total_time_diff': best_cost,
        'twice_min': min(twice_values),
        'twice_max': max(twice_values),
        'optimal_pairings': optimal,
    }


def format_twice(twice_micro, micro=1_000_000):
    sign = '-' if twice_micro < 0 else ''
    value = abs(twice_micro)
    whole, rest = divmod(value, 2 * micro)
    text = f'{whole}' + (f'.{rest:07d}'.rstrip('0') if rest else '')
    return sign + text
