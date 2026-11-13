"""ak_worker.map：BrokenProcessPool 重建并重试剩余项。"""

from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from unittest import TestCase
from unittest.mock import MagicMock

from adapters.market.ak_worker import AkPool


class TestAkWorkerMap(TestCase):
    def test_broken_pool_retries_remaining_once(self) -> None:
        pool = AkPool(max_workers=2)
        wave = {"n": 0}
        first = [Future(), Future(), Future()]
        first[0].set_result("A")
        first[1].set_exception(BrokenProcessPool("boom"))
        # first[2] never waited — pool already broken

        second = [Future(), Future()]
        second[0].set_result("B")
        second[1].set_result("C")

        def ensure():
            wave["n"] += 1
            ex = MagicMock()
            if wave["n"] == 1:
                idx = {"i": 0}

                def submit(fn, item, **kw):
                    i = idx["i"]
                    idx["i"] += 1
                    return first[i]

                ex.submit.side_effect = submit
            else:
                idx = {"i": 0}

                def submit2(fn, item, **kw):
                    i = idx["i"]
                    idx["i"] += 1
                    return second[i]

                ex.submit.side_effect = submit2
            return ex

        pool._ensure = ensure  # type: ignore[method-assign]
        pool._reset = lambda: None  # type: ignore[method-assign]

        out = pool.map(lambda x: x, ["a", "b", "c"])
        self.assertEqual(out, ["A", "B", "C"])
        self.assertEqual(wave["n"], 2)

    def test_second_break_fills_none(self) -> None:
        """重建后再崩：剩余填 None，不无限重试。"""
        pool = AkPool(max_workers=2)
        wave = {"n": 0}

        def ensure():
            wave["n"] += 1
            ex = MagicMock()
            futs = [Future(), Future()]
            futs[0].set_exception(BrokenProcessPool("boom"))
            # second future unused after first raises
            idx = {"i": 0}

            def submit(fn, item, **kw):
                i = idx["i"]
                idx["i"] += 1
                return futs[min(i, len(futs) - 1)]

            ex.submit.side_effect = submit
            return ex

        pool._ensure = ensure  # type: ignore[method-assign]
        pool._reset = lambda: None  # type: ignore[method-assign]

        out = pool.map(lambda x: x, ["a", "b"])
        self.assertEqual(out, [None, None])
        self.assertEqual(wave["n"], 2)

    def test_submit_break_drains_partial_then_retries_rest(self) -> None:
        """submit 中途 BrokenProcessPool：收割已提交结果，再重试剩余。"""
        pool = AkPool(max_workers=2)
        wave = {"n": 0}
        kept = Future()
        kept.set_result("A")

        def ensure():
            wave["n"] += 1
            ex = MagicMock()
            if wave["n"] == 1:
                calls = {"n": 0}

                def submit(fn, item, **kw):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return kept
                    raise BrokenProcessPool("submit boom")

                ex.submit.side_effect = submit
            else:
                rest = [Future(), Future()]
                rest[0].set_result("B")
                rest[1].set_result("C")
                idx = {"i": 0}

                def submit2(fn, item, **kw):
                    i = idx["i"]
                    idx["i"] += 1
                    return rest[i]

                ex.submit.side_effect = submit2
            return ex

        pool._ensure = ensure  # type: ignore[method-assign]
        pool._reset = lambda: None  # type: ignore[method-assign]

        out = pool.map(lambda x: x, ["a", "b", "c"])
        self.assertEqual(out, ["A", "B", "C"])
        self.assertEqual(wave["n"], 2)

    def test_submit_break_without_recreate_keeps_partial(self) -> None:
        """allow_recreate=False 时 submit 崩：已成功项保留，剩余 None。"""
        pool = AkPool(max_workers=2)
        kept = Future()
        kept.set_result("A")
        calls = {"n": 0}

        def ensure():
            ex = MagicMock()

            def submit(fn, item, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    return kept
                raise BrokenProcessPool("submit boom")

            ex.submit.side_effect = submit
            return ex

        pool._ensure = ensure  # type: ignore[method-assign]
        pool._reset = lambda: None  # type: ignore[method-assign]

        out = pool._map_once(
            lambda x: x,
            ["a", "b", "c"],
            timeout=None,
            allow_recreate=False,
        )
        self.assertEqual(out, ["A", None, None])
