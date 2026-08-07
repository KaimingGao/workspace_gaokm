"""组装 Web HTML：对话工作台 / 研究分页全页壳。"""

from __future__ import annotations

import os
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
    except Exception:
        return ASSET_V

_PAGE_TITLES = {
    "dashboard": "仪表盘",
    "quant": "研究枢纽",
    "watching": "数据中心",
    "paper": "模拟账户（已并入 /follow）",
    "strategy": "策略中心",
    "replay": "历史回测",
    "follow": "交易执行",
    "platform": "平台",
}

_PANEL_CLASS = {
    "dashboard": "quant-panel",
    "quant": "quant-panel",
    "watching": "quant-panel",
    "paper": "quant-panel",
    "strategy": "quant-panel",
    "replay": "quant-panel",
    "follow": "quant-panel",
    "platform": "quant-panel",
}


@lru_cache(maxsize=16)
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _partial(name: str) -> str:
    return _read(os.path.join(PARTIALS, name))


def _template(name: str) -> str:
    return _read(os.path.join(TEMPLATES, name))


def _panel(name: str) -> str:
    return _partial(f"{name}_panel.html")


def _with_tool_panels(html: str) -> str:
    return (
        html.replace("{{EVALS_PANEL}}", _partial("evals_panel.html"))
        .replace("{{USAGE_PANEL}}", _partial("usage_panel.html"))
    )


def _side_nav(active: str) -> str:
    """左侧五业务模块；AI 不占侧栏（顶栏 / ⌘K）。"""

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
            item("strategy", "/strategy", "策略中心", "策略卡 · 晋升 · 改限额", "btn-strategy"),
            item("watching", "/watching", "数据中心", "观察 · 建仓入口", "btn-watching"),
            item("follow", "/follow", "交易执行", "纸面调仓 · 做T验证", "btn-follow"),
            item("replay", "/replay", "历史回测", "历史验证 · 归因", "btn-replay"),
            item("quant", "/quant", "研究枢纽", "因子 · 横截面 · 日报", "btn-quant"),
            item("platform", "/platform", "平台", "态势 · 偏好 · 调度", "btn-settings"),
        ]
    )
    return f"""    <aside class="side-nav" aria-label="主导航">
      <div class="side-nav-brand">
        <a class="logo" href="/dashboard" title="QuantLab">
          <span class="logo-mark" aria-hidden="true">Q</span>
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


def _topbar(active: str) -> str:
    if active == "chat":
        reset = (
            '<button type="button" id="btn-reset" class="icon-btn" title="新会话" aria-label="新会话">\n'
            '          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">\n'
            '            <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>\n'
            "          </svg>\n"
            "        </button>"
        )
    else:
        reset = (
            '<a href="/watching" class="icon-btn" title="返回数据中心" aria-label="返回数据中心">\n'
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
        <button type="button" class="dialog-btn" id="btn-ai-open" title="AI 助手（⌘K）">AI 助手</button>
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


def render_chat_html() -> str:
    """全屏对话工作台（/chat）：自然语言命令的展开视图。"""
    clear_html_cache()
    dialogs = _partial("chat_dialogs.html")
    return _inject_asset_v(
        _apply_chrome(
            _template("chat.html")
            .replace("{{PANEL_QUANT}}", _panel("quant"))
            .replace("{{PANEL_WATCHING}}", _panel("watching"))
            .replace("{{PANEL_STRATEGY}}", _panel("strategy"))
            .replace("{{PANEL_REPLAY}}", _panel("replay"))
            .replace("{{PANEL_FOLLOW}}", _panel("follow"))
            .replace("{{PANEL_PAPER}}", _panel("paper"))
            .replace("{{PANEL_EVALS}}", _partial("evals_panel.html"))
            .replace("{{PANEL_USAGE}}", _partial("usage_panel.html"))
            .replace("{{PANEL_PLATFORM}}", _partial("platform_panel.html"))
            .replace("{{DIALOGS}}", dialogs),
            "chat",
        )
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
    extras = _with_tool_panels(_partial("shared_dialogs.html"))
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
