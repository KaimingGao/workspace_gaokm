"""框架整改：ports adapter、paper job 落盘、paper_cycle 拆分。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestPortsAdapters(unittest.TestCase):
    def tearDown(self) -> None:
        from core.ports.adapters import clear_adapters

        clear_adapters()

    def test_set_adapter_bypasses_skills(self):
        from core.ports import market
        from core.ports.adapters import set_adapter

        set_adapter("query_quote", lambda code: {"success": True, "stock_code": code, "price_raw": 1.23})
        from core.ports.adapters import mark_bound

        mark_bound()
        q = market.query_quote("600519")
        self.assertTrue(q["success"])
        self.assertEqual(q["price_raw"], 1.23)

    def test_ensure_bound_registers_defaults(self):
        from core.ports.adapters import clear_adapters, get_adapter
        from skills.ports_bind import bind_market_adapters

        clear_adapters()
        bind_market_adapters(force=True)
        self.assertIsNotNone(get_adapter("query_quote"))
        self.assertIsNotNone(get_adapter("fetch_daily_bars"))
        self.assertIsNotNone(get_adapter("fetch_a_spot"))
        self.assertIsNotNone(get_adapter("batch_query_quotes"))
        self.assertIsNotNone(get_adapter("build_signal_pool"))
        self.assertIsNotNone(get_adapter("search_stocks"))
        self.assertIsNotNone(get_adapter("load_disk_spot"))
        self.assertIsNotNone(get_adapter("fetch_minute_bars"))
        self.assertIsNotNone(get_adapter("fetch_cn_financial_series"))

    def test_core_has_no_hard_skills_imports(self):
        """O2：core 内仅 adapters 可 lazy import skills.ports_bind。"""
        import os
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "core"
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root.parent)
            text = path.read_text(encoding="utf-8")
            if "from skills." in text or "import skills." in text:
                if path.name == "adapters.py" and "skills.ports_bind" in text:
                    continue
                offenders.append(str(rel))
        self.assertEqual(offenders, [])

    def test_core_business_reads_via_data_service(self):
        """DS-E5：业务模块不得直 import ports.query_quote / fetch_daily_bars。

        允许：core/ports/*、core/data/ports.py（适配器）。
        """
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "core"
        allow = {
            Path("ports/market.py"),
            Path("ports/__init__.py"),
            Path("data/ports.py"),
        }
        patterns = [
            re.compile(r"from\s+core\.ports\.market\s+import\s+[^\n]*\bquery_quote\b"),
            re.compile(r"from\s+core\.ports\.market\s+import\s+[^\n]*\bfetch_daily_bars\b"),
            re.compile(r"from\s+core\.ports\.market\s+import\s+[^\n]*\bbatch_query_quotes\b"),
            re.compile(r"from\s+core\.ports\.market\s+import\s+[^\n]*\bfetch_index_bars\b"),
            re.compile(r"from\s+core\.ports\.market\s+import\s+[^\n]*\bfetch_a_spot\b"),
            re.compile(r"from\s+core\.ports\.market\s+import\s+[^\n]*\bbuild_news\b"),
        ]
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root)
            if rel in allow:
                continue
            text = path.read_text(encoding="utf-8")
            for pat in patterns:
                if pat.search(text):
                    offenders.append(f"{rel}: {pat.pattern}")
                    break
        self.assertEqual(offenders, [])

    def test_business_scores_via_signal_service(self):
        """SS-E3：services / web / quant/services / skills 不得直 import score_stock / rank_*。

        允许：core/signal 实现层（service · score_stock · cross_section · cluster_rank · batch）。
        """
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        scan_dirs = [
            root / "services",
            root / "web",
            root / "quant" / "services",
            root / "skills",
            root / "core",
            root / "scripts",
            root / "research",
        ]
        allow_core = {
            Path("signal/service.py"),
            Path("signal/score_stock.py"),
            Path("signal/cross_section.py"),
            Path("signal/cross_section_batch.py"),
            Path("signal/cluster_rank.py"),
            Path("signal_service.py"),
        }
        patterns = [
            re.compile(r"from\s+core\.signal\.score_stock\s+import\s+[^\n]*\bscore_stock\b"),
            re.compile(
                r"from\s+core\.signal\.cross_section\s+import\s+[^\n]*\brank_cross_section\b"
            ),
            re.compile(
                r"from\s+core\.signal\.cluster_rank\s+import\s+[^\n]*\brank_cluster_pools\b"
            ),
        ]
        offenders = []
        for base in scan_dirs:
            if not base.is_dir():
                continue
            for path in base.rglob("*.py"):
                rel = path.relative_to(root)
                if base.name == "core" or str(rel).startswith("core/"):
                    core_rel = path.relative_to(root / "core")
                    if core_rel in allow_core:
                        continue
                    # core 内其它模块也不得直调打分出口（须经 SignalService）
                text = path.read_text(encoding="utf-8")
                for pat in patterns:
                    if pat.search(text):
                        offenders.append(str(rel))
                        break
        self.assertEqual(offenders, [])

    def test_quant_services_has_no_skills_imports(self):
        """H2：quant/services 不得直接 import skills（经 ports / services 门面）。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "quant" / "services"
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root.parent.parent)
            text = path.read_text(encoding="utf-8")
            if "from skills." in text or "import skills." in text:
                offenders.append(str(rel))
        self.assertEqual(offenders, [])

    def test_core_has_no_quant_imports(self):
        """M1：core 不得 import quant（依赖方向 core ← quant 禁止）。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "core"
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root.parent)
            text = path.read_text(encoding="utf-8")
            if "from quant." in text or "import quant" in text:
                offenders.append(str(rel))
        self.assertEqual(offenders, [])

    def test_core_has_no_services_imports(self):
        """FH3：core 不得 import services（公式 DTO 在 score_view）。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "core"
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root.parent)
            text = path.read_text(encoding="utf-8")
            if "from services" in text or "import services" in text:
                offenders.append(str(rel))
        self.assertEqual(offenders, [])

    def test_skill_quote_uses_data_service(self):
        from unittest.mock import patch
        from skills.quote.engine import QuoteEngine

        with patch(
            "core.data.facade.get_quote",
            return_value={"success": True, "stock_code": "600519", "data_source": "tencent_quote"},
        ) as mock_gq:
            out = QuoteEngine().query({"stock_code": "茅台"})
        self.assertTrue(out.get("success"))
        mock_gq.assert_called_once()


class TestPaperJobPersist(unittest.TestCase):
    def test_running_becomes_failed_after_reload(self):
        from core.job_progress import JobProgress

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            j1 = JobProgress(name="paper", persist_path=path)
            jid = j1.start(kind="paper_buy", total=3, message="run")
            self.assertTrue(j1.is_running())
            # 模拟新进程加载同一文件
            j2 = JobProgress(name="paper", persist_path=path)
            snap = j2.get()
            self.assertEqual(snap["id"], jid)
            self.assertEqual(snap["status"], "failed")
            self.assertIn("重启", snap.get("error") or "")

    def test_done_survives_reload(self):
        from core.job_progress import JobProgress

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            j1 = JobProgress(name="paper", persist_path=path)
            j1.start(kind="paper_buy", total=1)
            j1.finish(result={"ok": True, "observation_pool_count": 2})
            j2 = JobProgress(name="paper", persist_path=path)
            snap = j2.get()
            self.assertEqual(snap["status"], "done")
            self.assertEqual((snap.get("result") or {}).get("observation_pool_count"), 2)


class TestPaperCycleExport(unittest.TestCase):
    def test_reexport_from_paper(self):
        from core import paper
        from core import paper_cycle

        self.assertIs(paper.run_daily_cycle, paper_cycle.run_daily_cycle)

    def test_run_daily_cycle_callable_with_mocks(self):
        from core.paper.cycle import run_daily_cycle

        paper = {
            "cash": 100000,
            "holdings": [],
            "rules": {},
            "operation_log": [],
            "snapshots": [],
        }
        with patch("core.paper.cycle.run_signal_scan", return_value=[]), patch(
            "core.paper.cycle.simulate_sells", return_value=[]
        ), patch("core.paper.cycle.simulate_buys", return_value=[]), patch(
            "core.paper.cycle.mark_to_market",
            return_value={"cash": 100000, "equity": 100000, "position_count": 0},
        ), patch("core.paper.cycle.append_snapshot"), patch(
            "core.paper.cycle.summarize_data_quality", create=True
        ):
            # summarize is imported inside function; patch data_service instead
            with patch(
                "core.data.facade.summarize_data_quality",
                return_value={"levels": {}, "fallback_count": 0, "count": 0},
            ), patch(
                "core.strategy_monitor.assess_strategy_health",
                return_value={"ok": True, "alerts": [], "level": "ok"},
            ), patch(
                "core.risk.check_account_risk",
                return_value={"ok": True, "blocks": [], "warnings": []},
            ):
                out = run_daily_cycle(paper, simulate_buy=False, strategy="short")
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("observation_pool_count"), 0)


class TestPaperMixinImportGuard(unittest.TestCase):
    """拆 mixin 后防漏 import（曾导致评分空 / 日线 500 / append_snapshot NameError）。"""

    # 若函数体用到这些名字，模块顶层必须 import / 绑定
    _CRITICAL = frozenset(
        {
            "os",
            "threading",
            "copy",
            "logging",
            "append_snapshot",
            "append_operation_log",
            "run_signal_scan",
            "run_daily_cycle",
            "load_paper",
            "save_paper",
            "mark_to_market",
            "init_from_example",
            "manual_buy",
            "manual_sell",
            "_now_iso",
            "_build_score_formula",
            "paper_job",
            "PAPER_PATH",
        }
    )

    def test_paper_mixins_bind_critical_names(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "services"
        files = [
            root / "paper_account.py",
            root / "paper_jobs.py",
            root / "paper_trades.py",
        ]
        missing_by_file = {}
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            bound: set[str] = set()
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        bound.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            bound.add(t.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    bound.add(node.target.id)
                elif isinstance(node, ast.FunctionDef):
                    bound.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    bound.add(node.name)
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            bound.add(item.name)

            used: set[str] = set()

            class _LoadVisitor(ast.NodeVisitor):
                def visit_Name(self, n: ast.Name) -> None:
                    if isinstance(n.ctx, ast.Load) and n.id in TestPaperMixinImportGuard._CRITICAL:
                        used.add(n.id)

                def visit_Attribute(self, n: ast.Attribute) -> None:
                    # os.path.isfile → needs os
                    if isinstance(n.value, ast.Name) and n.value.id in TestPaperMixinImportGuard._CRITICAL:
                        used.add(n.value.id)
                    self.generic_visit(n)

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            _LoadVisitor().visit(item)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _LoadVisitor().visit(node)

            # nested imports inside functions also bind for that use — collect them
            class _InnerImport(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.names: set[str] = set()

                def visit_Import(self, n: ast.Import) -> None:
                    for a in n.names:
                        self.names.add(a.asname or a.name.split(".")[0])

                def visit_ImportFrom(self, n: ast.ImportFrom) -> None:
                    for a in n.names:
                        self.names.add(a.asname or a.name.split(".")[0])

            inner = _InnerImport()
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            inner.visit(item)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inner.visit(node)

            available = bound | inner.names
            missing = sorted(used - available)
            if missing:
                missing_by_file[path.name] = missing

        self.assertEqual(missing_by_file, {})


if __name__ == "__main__":
    unittest.main()
