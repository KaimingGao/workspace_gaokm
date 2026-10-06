"""时间序列切分（P7.1）。

从 research/split.py 下沉到 core 层，消除 core → research 反向依赖。
"""


from dataclasses import dataclass
from typing import List


@dataclass
class TimeSeriesSplit:
    train_end: int
    valid_end: int
    test_end: int

    @property
    def train_slice(self) -> slice:
        return slice(0, self.train_end)

    @property
    def valid_slice(self) -> slice:
        return slice(self.train_end, self.valid_end)

    @property
    def test_slice(self) -> slice:
        return slice(self.valid_end, self.test_end)


def time_series_split(
    bar_count: int,
    *,
    train_ratio: float = 0.6,
    valid_ratio: float = 0.2,
    min_bars: int = 30,
) -> TimeSeriesSplit:
    n = max(0, int(bar_count or 0))
    if n < min_bars:
        raise ValueError(f"bar_count 不足: {n} < {min_bars}")

    train_ratio = max(0.1, min(float(train_ratio), 0.8))
    valid_ratio = max(0.05, min(float(valid_ratio), 0.3))
    if train_ratio + valid_ratio >= 0.95:
        valid_ratio = 0.2
        train_ratio = 0.6

    train_end = max(min_bars // 2, int(n * train_ratio))
    valid_end = max(train_end + 5, int(n * (train_ratio + valid_ratio)))
    valid_end = min(valid_end, n - 5) if n > 10 else n
    if valid_end <= train_end:
        valid_end = min(n, train_end + max(5, n // 10))

    return TimeSeriesSplit(train_end=train_end, valid_end=valid_end, test_end=n)


def slice_bars(bars: List[dict], sl: slice) -> List[dict]:
    return list(bars[sl])


@dataclass
class WalkForwardFold:
    """索引切分：train=[0, train_end)，test=[test_start, test_end)。"""

    fold: int
    train_end: int
    test_start: int
    test_end: int


def rolling_walk_forward_slices(
    bar_count: int,
    *,
    n_splits: int = 3,
    min_train: int = 40,
    min_test: int = 12,
) -> List[WalkForwardFold]:
    """扩展窗 Walk-forward：固定末段测试池，均分为 n_splits 折；训练集为测试之前全部历史。"""
    n = max(0, int(bar_count or 0))
    n_splits = max(1, min(int(n_splits or 3), 8))
    min_train = max(10, int(min_train or 40))
    min_test = max(5, int(min_test or 12))
    if n < min_train + min_test:
        return []

    test_total = max(min_test * n_splits, int(n * 0.3))
    test_total = min(test_total, n - min_train)
    test_size = max(min_test, test_total // n_splits)
    # 重新对齐测试池长度
    test_total = test_size * n_splits
    if test_total > n - min_train:
        n_splits = max(1, (n - min_train) // min_test)
        test_size = max(min_test, (n - min_train) // n_splits)
        test_total = test_size * n_splits

    folds: List[WalkForwardFold] = []
    for i in range(n_splits):
        test_start = n - test_total + i * test_size
        test_end = n if i == n_splits - 1 else test_start + test_size
        train_end = test_start
        if train_end < min_train or test_end <= test_start:
            continue
        folds.append(
            WalkForwardFold(
                fold=i + 1,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    return folds
