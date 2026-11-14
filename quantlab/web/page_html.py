"""组装 Web HTML：研究分页全页壳 + AI 抽屉。"""

import os
import re
from functools import lru_cache

from web.asset_version import ASSET_V

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PARTIALS = os.path.join(STATIC_DIR, "partials")
TEMPLATES = os.path.join(STATIC_DIR, "templates")


def _current_asset_v() -> str:
    """每次注入时重读，避免 run_web 长驻进程拿不到 bump。"""
    try:
        import importlib

        import web.asset_version as av

        importlib.reload(av)
        return str(av.ASSET_V)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return ASSET_V

_PAGE_TITLES = {
    "dashboard": "仪表盘",
    "data": "数据中心",
    "quant": "研究枢纽",
    "watching": "观察台",
    "paper": "模拟账户（已并入 /follow）",
    "strategy": "市场先验",
    "replay": "历史回测",
    "follow": "交易执行",
    "platform": "平台",
    "yhat_l": "模型中心 · Arm·L",
    "yhat_t": "模型中心 · Arm·T",
}

_PANEL_CLASS = {
    "dashboard": "quant-panel",
    "data": "quant-panel",
    "quant": "quant-panel",
    "watching": "quant-panel",
    "paper": "quant-panel",
    "strategy": "quant-panel",
    "replay": "quant-panel",
    "follow": "quant-panel",
    "platform": "quant-panel",
    "yhat_l": "quant-panel",
    "yhat_t": "quant-panel",
}

@lru_cache(maxsize=16)

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _partial(name: str) -> str:
    return _read(os.path.join(PARTIALS, name))


def _template(name: str) -> str:
    return _read(os.path.join(TEMPLATES, name))


_YHAT_T_ONLY = re.compile(r"<!--yhat-only:T-->.*?<!--/yhat-only:T-->", re.DOTALL)


# Arm·L 默认窗跟研究枢纽。共享模板里的 900/60 留给 Arm·T。
_L_LOOKBACK = {"oo": 600, "tc": 120, "oo_rank": 700, "tc_rank": 700}
_L_HOLDOUT = 20


def _apply_l_hub_windows(html: str) -> str:
    parts = re.split(
        r'(<section\b[^>]*\bdata-head-section="(?:oo|tc|oo_rank|tc_rank)")',
        html,
    )
    if len(parts) < 3:
        return html
    out = [parts[0]]
    i = 1
    while i < len(parts):
        tag = parts[i]
        rest = parts[i + 1] if i + 1 < len(parts) else ""
        m = re.search(r'data-head-section="(oo|tc|oo_rank|tc_rank)"', tag)
        lb = _L_LOOKBACK.get(m.group(1) if m else "")
        if lb is not None:
            rest = re.sub(
                r'(data-param="lookback"[^>]*\bvalue=")[0-9]+',
                rf"\g<1>{lb}",
                rest,
                count=1,
            )
            rest = re.sub(
                r'(data-param="holdout_trading_days"[^>]*\bvalue=")[0-9]+',
                rf"\g<1>{_L_HOLDOUT}",
                rest,
                count=1,
            )
        out.append(tag)
        out.append(rest)
        i += 2
    return "".join(out)


def _yhat_factory_panel(factory: str) -> str:
    """共享工厂模板；factory=L|T。L 页剥掉序列 / TF 超参 / 对侧臂。"""
    fac = "L" if str(factory).upper() == "L" else "T"
    if fac == "L":
        arms_rail = (
            '<span class="quant-path-step quant-pro-path-step is-active" data-state="busy" '
            'title="LightGBM / LambdaRank · 全量因子">'
            '<span class="quant-pro-path-num">Arm·L</span> LightGBM</span>'
        )
        fills = {
            "{{FACTORY}}": "L",
            "{{EYEBROW}}": "LightGBM Factory",
            "{{PAGE_H2}}": "模型中心 · Arm·L",
            "{{HELP_BLURB}}": (
                "本页只训 <strong>Arm·L（LightGBM / LambdaRank）</strong>。"
                "四头各自拟合与评测；绝对指标，无页内 vs T 硬闸；跨厂对照留给研究枢纽。不写 live。"
            ),
            "{{ARMS_TITLE}}": "本厂模型臂",
            "{{ARMS_RAIL}}": arms_rail,
            "open[T]→open[T+1] · SmoothL1 · Scheme LT": "open[T]→open[T+1] · LightGBM",
            "price[τ]→close[T] · SmoothL1 · Scheme LT": "price[τ]→close[T] · LightGBM",
            "CS rank · ListNet · Gate=Δcs_rank_ic vs Arm·L": "CS rank · LambdaRank",
            "Gate · Arm·L": "绝对指标",
            "Gate · Δcs_rank_ic": "绝对指标",
        }
    else:
        arms_rail = (
            '<span class="quant-path-step quant-pro-path-step is-active" data-state="busy" '
            'title="双支路晚融合：序列塔与因子支拼接后出标量">'
            '<span class="quant-pro-path-num">Arm·T</span> 双支路晚融合</span>'
        )
        fills = {
            "{{FACTORY}}": "T",
            "{{EYEBROW}}": "Transformer Factory",
            "{{PAGE_H2}}": "模型中心 · Arm·T",
            "{{HELP_BLURB}}": (
                "本页只训 <strong>Arm·T（SeqFactorTransformer · 双支路晚融合）</strong>。"
                "四头各自拟合与评测；绝对指标，无页内 vs L 硬闸；跨厂对照留给研究枢纽。不写 live。"
            ),
            "{{ARMS_TITLE}}": "本厂模型臂",
            "{{ARMS_RAIL}}": arms_rail,
            "Scheme LT": "Arm·T",
        }
    html = _partial("yhat_factory_panel.html")
    for k, v in fills.items():
        html = html.replace(k, v)
    if fac == "L":
        html = _YHAT_T_ONLY.sub("", html)
        html = _apply_l_hub_windows(html)
    return html


def _panel(name: str) -> str:
    if name == "yhat_l":
        return _yhat_factory_panel("L")
    if name == "yhat_t":
        return _yhat_factory_panel("T")
    return _partial(f"{name}_panel.html")


def _side_nav(active: str) -> str:
    """左侧导航（仪表盘 + 七业务）；AI 不占侧栏（顶栏 / ⌘K）。"""

    def item(page: str, href: str, label: str, sub: str, eid: str = "") -> str:
        is_on = active == page
        cls = "side-nav-item active" if is_on else "side-nav-item"
        id_attr = f' id="{eid}"' if eid else ""
        aria = ' aria-current="page"' if is_on else ""
        return (
            f'<a href="{href}" class="{cls}"{id_attr}{aria} title="{sub}">'
            f'<span class="side-nav-label">{label}</span>'
            f'<span class="side-nav-sub">{sub}</span></a>'
        )

    items = "\n          ".join(
        [
            item("dashboard", "/dashboard", "仪表盘", "全局概览 · KPI · 净值曲线", "btn-dashboard"),
            item("watching", "/watching", "观察台", "名单 · 建仓入口", "btn-watching"),
            item("strategy", "/market-prior", "市场先验", "M prior · Regime", "btn-strategy"),
            item("follow", "/follow", "交易执行", "调仓 · 做T执行", "btn-follow"),
            item("replay", "/replay", "历史回测", "调仓回测 · 做T回测", "btn-replay"),
            item("data", "/data", "数据中心", "日K · 5m · 物化", "btn-data"),
            item("quant", "/quant", "研究枢纽", "因子 · 横截面 · 日报", "btn-quant"),
            item("platform", "/platform", "平台", "调度 · 日更", "btn-settings"),
        ]
    )
    return f"""    <aside class="side-nav" aria-label="主导航">
      <div class="side-nav-brand">
        <a class="logo" href="/dashboard" title="QuantLab">
          <span class="logo-text">QuantLab</span>
        </a>
      </div>
      <nav class="side-nav-list" aria-label="功能模块">
          {items}
      </nav>
      <div class="side-nav-foot">
        <button type="button" class="side-nav-collapse" id="btn-side-collapse" title="折叠侧栏" aria-label="折叠侧栏">«</button>
      </div>
    </aside>"""


def _topbar(_active: str) -> str:
    reset = (
        '<a href="/watching" class="icon-btn" title="返回观察台" aria-label="返回观察台">\n'
        '          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">\n'
        '            <path d="M15 18l-6-6 6-6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>\n'
        "          </svg>\n"
        "        </a>"
    )

    return f"""    <header class="topbar" role="banner">
      <div class="topbar-left">
        {reset}
        <span class="env-badge" title="仅限模拟账户 · 不涉及真实交易">研究 · 模拟账户</span>
        <span class="risk-lamp risk-lamp-ok" id="risk-lamp" title="全局风控（纸面）" aria-label="风控正常">●</span>
        <span class="market-clocks" id="market-clocks" title="市场时钟">
          <span data-clock="cn">沪深 --:--:--</span>
        </span>
      </div>
      <div class="topbar-right">
        <span id="status" class="status" role="status">检测中…</span>
        <button type="button" class="icon-btn topbar-ai-btn" id="btn-ai-open" title="AI（⌘K）" aria-label="打开 AI（⌘K）">AI</button>
        <button type="button" class="icon-btn" id="btn-theme-toggle" title="切换深浅色" aria-label="切换深浅色">◐</button>
      </div>
    </header>
    <div id="api-degrade-banner" class="api-degrade-banner" hidden aria-hidden="true" role="alert">
      <span class="api-degrade-banner-text">后端暂时不可用 · 显示上次成功数据（若有）</span>
    </div>"""


def _nav(active: str) -> str:
    return _side_nav(active) + "\n" + _topbar(active)


def _inject_asset_v(html: str) -> str:
    return html.replace("{{ASSET_V}}", _current_asset_v())


def _apply_chrome(html: str, active: str) -> str:
    drawer = _partial("ai_drawer.html")
    palette = _partial("command_palette.html")
    return (
        html.replace("{{SIDE_NAV}}", _side_nav(active))
        .replace("{{TOPBAR}}", _topbar(active))
        .replace("{{NAV}}", _nav(active))
        .replace("{{AI_DRAWER}}", drawer + "\n" + palette)
    )


def render_tool_html(page: str) -> str:
    if page not in _PAGE_TITLES:
        raise ValueError(f"unknown page: {page}")
    clear_html_cache()
    panel_class = _PANEL_CLASS[page]
    content = (
        f'    <main class="page-main">\n'
        f'      <div class="page-panel {panel_class}" id="{page}-page">\n'
        f"        {_panel(page)}\n"
        f"      </div>\n"
        f"    </main>\n"
    )
    extras = _partial("shared_dialogs.html")
    return _inject_asset_v(
        _apply_chrome(
            _template("tool.html")
            .replace("{{TITLE}}", f"QuantLab · {_PAGE_TITLES[page]}")
            .replace("{{PAGE}}", page)
            .replace("{{CONTENT}}", content)
            .replace("{{EXTRA_DIALOGS}}", extras),
            page,
        )
    )


def clear_html_cache() -> None:
    _read.cache_clear()
