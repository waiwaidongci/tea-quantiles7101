"""穷举核对：小例上生产算法与独立 oracle 必须逐例一致。

覆盖：
* 最大份数与最小总时差；
* 两倍中位数上下界（奇数取第 h 小，偶数取中间两项之和）；
* 特别挑出“同成本配法产生不同中位数”的小例，确认上下界确实不同，
  且每个界都带一份达到它的配对明细。
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.services import pairing
from tests import oracle


def _snapshot(initial, retest):
    return {'initial': initial, 'retest': retest}


def _check(initial, retest, tolerance):
    expected = oracle.exhaustive(initial, retest, tolerance)
    got = pairing.analyze(_snapshot(initial, retest), tolerance)
    assert got['cardinality'] == expected['cardinality']
    if expected['cardinality'] == 0:
        assert got['bound_lower'] is None and got['bound_upper'] is None
        assert got['reason']
        return got
    assert got['total_time_diff'] == expected['total_time_diff']
    assert got['verified'] is True
    assert got['bound_lower']['twice_median_micro'] == expected['twice_min']
    assert got['bound_upper']['twice_median_micro'] == expected['twice_max']
    # 每个界的明细必须自身是最大份数、最小总时差，且中位数等于该界
    for bound in ('bound_lower', 'bound_upper'):
        detail = got[bound]['pairing']
        assert len(detail) == expected['cardinality']
        assert sum(row['time_diff'] for row in detail) == expected['total_time_diff']
        deltas = sorted(round(float(row['delta']) * 1_000_000) for row in detail)
        k = len(deltas)
        twice = 2 * deltas[k // 2] if k % 2 else deltas[k // 2 - 1] + deltas[k // 2]
        assert twice == got[bound]['twice_median_micro']
    return got


def test_single_pair_is_exact():
    got = _check(
        [{'source_key': 'I1', 'sampled_at': 0, 'moisture': '1.0'}],
        [{'source_key': 'R1', 'sampled_at': 0, 'moisture': '1.25'}],
        10)
    assert got['bound_lower']['median'] == '0.25'
    assert got['bound_upper']['median'] == '0.25'


def test_no_pairing_reports_reason():
    got = _check(
        [{'source_key': 'I1', 'sampled_at': 0, 'moisture': '1.0'}],
        [{'source_key': 'R1', 'sampled_at': 1000, 'moisture': '2.0'}],
        30)
    assert got['cardinality'] == 0
    assert '没有任何' in got['reason']


def test_even_cardinality_uses_middle_average():
    # 4 份读数、唯一最优配法，差值两两不同，两倍中位数应为中间两项之和
    initial = [
        {'source_key': 'i1', 'sampled_at': 0, 'moisture': '5.000'},
        {'source_key': 'i2', 'sampled_at': 10, 'moisture': '5.000'},
        {'source_key': 'i3', 'sampled_at': 20, 'moisture': '5.000'},
        {'source_key': 'i4', 'sampled_at': 30, 'moisture': '5.000'},
    ]
    retest = [
        {'source_key': 'r1', 'sampled_at': 0, 'moisture': '5.100'},
        {'source_key': 'r2', 'sampled_at': 10, 'moisture': '5.200'},
        {'source_key': 'r3', 'sampled_at': 20, 'moisture': '5.400'},
        {'source_key': 'r4', 'sampled_at': 30, 'moisture': '5.800'},
    ]
    got = _check(initial, retest, 1)
    assert got['cardinality'] == 4
    # 差值 0.1,0.2,0.4,0.8 -> 中位数 (0.2+0.4)/2 = 0.3
    assert got['bound_lower']['median'] == '0.3'
    assert got['bound_upper']['median'] == '0.3'


def test_same_cost_gives_different_medians():
    """2 份配成 2 对：两条同成本（总时差 6）的最优配法。

    配法 A：(I1,R0) 时差 4、差值 0.2-0.4=-0.2；(I0,R1) 时差 2、差值 2.9-2.2=0.7。
            中间两项平均 = (-0.2+0.7)/2 = 0.25；
    配法 B：(I1,R2) 时差 4、差值 2.0-0.4=1.6；(I0,R1) 时差 2、差值 0.7。
            中位数 = (0.7+1.6)/2 = 1.15。
    两者总时差同为 6，所以复测减初测的中位数不唯一：下界 0.25、上界 1.15。
    """
    initial = [
        {'source_key': 'I0', 'sampled_at': 15, 'moisture': '2.2'},
        {'source_key': 'I1', 'sampled_at': 10, 'moisture': '0.4'},
    ]
    retest = [
        {'source_key': 'R0', 'sampled_at': 6, 'moisture': '0.2'},
        {'source_key': 'R1', 'sampled_at': 13, 'moisture': '2.9'},
        {'source_key': 'R2', 'sampled_at': 6, 'moisture': '2.0'},
    ]
    got = _check(initial, retest, 8)
    expected = oracle.exhaustive(initial, retest, 8)
    assert expected['cardinality'] == 2
    assert expected['total_time_diff'] == 6
    assert expected['twice_min'] != expected['twice_max'], '该例必须产生同成本不同中位数'
    assert got['bound_lower']['median'] == '0.25'
    assert got['bound_upper']['median'] == '1.15'
    assert got['bound_lower']['pairing'] != got['bound_upper']['pairing']


@pytest.mark.parametrize('trial', range(120))
def test_random_small_cases(trial):
    rng = random.Random(trial)
    n1 = rng.randint(1, 4)
    n2 = rng.randint(1, 4)
    tolerance = rng.choice([2, 3, 5, 10, 30, 120])
    initial = [
        {'source_key': f'I{i}', 'sampled_at': rng.randint(0, 40),
         'moisture': str(round(rng.randint(0, 3000) / 1000, 3))}
        for i in range(n1)
    ]
    retest = [
        {'source_key': f'R{j}', 'sampled_at': rng.randint(0, 40),
         'moisture': str(round(rng.randint(0, 3000) / 1000, 3))}
        for j in range(n2)
    ]
    _check(initial, retest, tolerance)


def test_deterministic_rerun_and_dense_cap():
    """40×40 满载、允许时差覆盖全部边，重跑两次结果与明细逐字节一致。"""
    rng = random.Random(7)
    initial = [
        {'source_key': f'I{i:02d}', 'sampled_at': rng.randint(0, 600),
         'moisture': f'{rng.uniform(3, 7):.3f}'}
        for i in range(40)
    ]
    retest = [
        {'source_key': f'R{j:02d}', 'sampled_at': rng.randint(0, 600),
         'moisture': f'{rng.uniform(3, 7):.3f}'}
        for j in range(40)
    ]
    first = pairing.analyze(_snapshot(initial, retest), 1440)
    second = pairing.analyze(_snapshot(initial, retest), 1440)
    assert first == second
    assert first['cardinality'] == 40
    assert first['verified'] is True


def test_rejects_bad_inputs():
    with pytest.raises(pairing.PairingError):
        pairing.analyze(_snapshot([], [{'source_key': 'r', 'sampled_at': 0, 'moisture': '1'}]), 10)
    with pytest.raises(pairing.PairingError):
        pairing.analyze(_snapshot(
            [{'source_key': 'i', 'sampled_at': 0, 'moisture': '1'}],
            [{'source_key': 'r', 'sampled_at': 0, 'moisture': '1'}]), 0)
    with pytest.raises(pairing.PairingError):
        pairing.analyze(_snapshot(
            [{'source_key': 'i', 'sampled_at': 0, 'moisture': '1'}],
            [{'source_key': 'r', 'sampled_at': -1, 'moisture': '1'}]), 10)
    with pytest.raises(pairing.PairingError):
        pairing.analyze(_snapshot(
            [{'source_key': str(i), 'sampled_at': i, 'moisture': '1'} for i in range(41)],
            [{'source_key': 'r', 'sampled_at': 0, 'moisture': '1'}]), 10)
