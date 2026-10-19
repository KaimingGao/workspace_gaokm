"""进程池收结果：缺湖原样抛出，池坏了改串行。"""

from __future__ import annotations

import unittest
from concurrent.futures import Future


def _raise_required(code: str) -> None:
    from core.research.materialize.consumer import MaterializeRequired

    raise MaterializeRequired(
        {
            "error": "请先日线物化",
            "stock_code": str(code),
            "families": ["oo_rank"],
        }
    )


class ConsumeProcessBatchTests(unittest.TestCase):
    def test_worker_error_retries_serially(self) -> None:
        from core.research.materialize.pool import consume_process_batch

        calls = []

        def _submit(i: int) -> Future:
            fut: Future = Future()
            fut.set_exception(RuntimeError("worker"))
            return fut

        def _run(i: int) -> str:
            calls.append(i)
            return "ok"

        results = [None]
        broke = consume_process_batch(
            start=0,
            end=1,
            n_workers=1,
            results=results,
            submit=_submit,
            run_one=_run,
        )
        self.assertFalse(broke)
        self.assertEqual(results, ["ok"])
        self.assertEqual(calls, [0])

    def test_materialize_required_propagates(self) -> None:
        from core.research.materialize.consumer import MaterializeRequired
        from core.research.materialize.pool import consume_process_batch

        def _submit(i: int) -> Future:
            fut: Future = Future()
            fut.set_exception(
                MaterializeRequired(
                    {"error": "请先日线物化", "stock_code": "000001", "families": ["oo"]}
                )
            )
            return fut

        with self.assertRaises(MaterializeRequired) as ctx:
            consume_process_batch(
                start=0,
                end=1,
                n_workers=1,
                results=[None],
                submit=_submit,
                run_one=lambda i: None,
            )
        self.assertEqual(ctx.exception.payload.get("stock_code"), "000001")

    def test_broken_pool_finishes_batch_serially(self) -> None:
        from concurrent.futures.process import BrokenProcessPool

        from core.research.materialize.pool import consume_process_batch

        submitted = []
        ran = []

        def _submit(i: int) -> Future:
            submitted.append(i)
            fut: Future = Future()
            if i == 0:
                fut.set_result("a")
            else:
                fut.set_exception(BrokenProcessPool("boom"))
            return fut

        def _run(i: int) -> str:
            ran.append(i)
            return f"s{i}"

        results = [None, None, None, None]
        broke = consume_process_batch(
            start=0,
            end=4,
            n_workers=2,
            results=results,
            submit=_submit,
            run_one=_run,
        )
        self.assertTrue(broke)
        self.assertTrue(all(item is not None for item in results))
        self.assertTrue(set(submitted) <= {0, 1})
        self.assertIn(2, ran)
        self.assertIn(3, ran)

    def test_finished_empty_result_is_not_rerun(self) -> None:
        from concurrent.futures.process import BrokenProcessPool

        from core.research.materialize.pool import consume_process_batch

        ran = []

        def _submit(i: int) -> Future:
            fut: Future = Future()
            if i == 0:
                fut.set_result(None)
            else:
                fut.set_exception(BrokenProcessPool("boom"))
            return fut

        def _run(i: int) -> str:
            ran.append(i)
            return f"s{i}"

        results: list = [None, None]
        consume_process_batch(
            start=0,
            end=2,
            n_workers=2,
            results=results,
            submit=_submit,
            run_one=_run,
        )
        self.assertNotIn(0, ran)
        self.assertEqual(results[0], None)
        self.assertEqual(results[1], "s1")

    def test_child_process_materialize_required_reaches_parent(self) -> None:
        from concurrent.futures import ProcessPoolExecutor

        from core.research.materialize.consumer import MaterializeRequired
        from core.research.materialize.pool import consume_process_batch

        results = [None]
        with ProcessPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(MaterializeRequired) as ctx:
                consume_process_batch(
                    start=0,
                    end=1,
                    n_workers=1,
                    results=results,
                    submit=lambda i: pool.submit(_raise_required, "000001"),
                    run_one=lambda i: _raise_required("000001"),
                )
        self.assertEqual(ctx.exception.payload.get("stock_code"), "000001")
        self.assertEqual(ctx.exception.payload.get("families"), ["oo_rank"])
