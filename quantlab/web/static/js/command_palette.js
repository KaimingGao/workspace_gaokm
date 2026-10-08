/** Command Palette · W3.1 · ⌘⇧K / Ctrl+Shift+K */

const COMMANDS = [
  { id: "nav-watching", label: "前往 · 数据中心", hint: "/watching", href: "/watching", keywords: "home 观察 名单 建仓 首页" },
  { id: "nav-strategy", label: "前往 · 策略中心", hint: "/strategy", href: "/strategy", keywords: "策略 舆情 prior 人审" },
  { id: "nav-follow", label: "前往 · 交易执行", hint: "/follow", href: "/follow", keywords: "模拟 持仓 调仓 做T" },
  { id: "nav-replay", label: "前往 · 历史回测", hint: "/replay", href: "/replay", keywords: "回测 回溯 绩效 grid 做T" },
  { id: "nav-quant", label: "前往 · 研究枢纽", hint: "/quant", href: "/quant", keywords: "研究 因子 ic ols 运维 日报 hub" },
  { id: "nav-platform", label: "前往 · 平台", hint: "/platform", href: "/platform", keywords: "调度 日更 平台 系统设置" },
  {
    id: "ai-open",
    label: "打开 AI",
    hint: "⌘K",
    keywords: "ai 助手 命令 模型 llm",
    run: () => {
      if (typeof window.__quantlabOpenAi === "function") window.__quantlabOpenAi();
    },
  },
  {
    id: "theme-toggle",
    label: "切换深浅色",
    hint: "theme",
    keywords: "theme dark light 主题",
    run: () => document.getElementById("btn-theme-toggle")?.click(),
  },
];

function closePalette() {
  const root = document.getElementById("command-palette");
  if (!root) return;
  root.hidden = true;
  root.setAttribute("aria-hidden", "true");
}

function openPalette() {
  const root = document.getElementById("command-palette");
  const input = document.getElementById("command-palette-input");
  if (!root) return;
  root.hidden = false;
  root.setAttribute("aria-hidden", "false");
  if (input) {
    input.value = "";
    renderList("");
    setTimeout(() => input.focus(), 20);
  }
}

function filterCommands(q) {
  const s = String(q || "")
    .trim()
    .toLowerCase();
  if (!s) return COMMANDS.slice();
  return COMMANDS.filter((c) => {
    const blob = `${c.label} ${c.hint || ""} ${c.keywords || ""} ${c.id}`.toLowerCase();
    return blob.includes(s);
  });
}

function runCommand(cmd) {
  closePalette();
  if (!cmd) return;
  if (typeof cmd.run === "function") {
    cmd.run();
    return;
  }
  if (cmd.href) {
    if (cmd.href.startsWith("#")) {
      location.hash = cmd.href;
      return;
    }
    const [path, hash] = cmd.href.split("#");
    if (path && path !== location.pathname + (location.search || "")) {
      window.location.href = cmd.href;
    } else if (hash) {
      location.hash = hash;
      document.getElementById(hash)?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      window.location.href = cmd.href;
    }
  }
}

let _active = 0;
let _visible = [];

function renderList(q) {
  const list = document.getElementById("command-palette-list");
  if (!list) return;
  _visible = filterCommands(q);
  _active = 0;
  if (!_visible.length) {
    list.innerHTML = `<li class="command-palette-empty">无匹配命令</li>`;
    return;
  }
  list.innerHTML = _visible
    .map(
      (c, i) =>
        `<li class="command-palette-item${i === 0 ? " is-active" : ""}" role="option" data-idx="${i}">` +
        `<span class="command-palette-item-label">${c.label}</span>` +
        `<span class="command-palette-item-hint">${c.hint || ""}</span></li>`
    )
    .join("");
}

function moveActive(delta) {
  if (!_visible.length) return;
  _active = (_active + delta + _visible.length) % _visible.length;
  const list = document.getElementById("command-palette-list");
  if (!list) return;
  list.querySelectorAll(".command-palette-item").forEach((el, i) => {
    el.classList.toggle("is-active", i === _active);
  });
  list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
}

export function initCommandPalette() {
  const root = document.getElementById("command-palette");
  if (!root) return;
  const input = document.getElementById("command-palette-input");
  const list = document.getElementById("command-palette-list");

  document.getElementById("command-palette-backdrop")?.addEventListener("click", closePalette);

  document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.shiftKey && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (root.hidden) openPalette();
      else closePalette();
      return;
    }
    if (e.key === "Escape" && !root.hidden) {
      e.preventDefault();
      closePalette();
    }
  });

  input?.addEventListener("input", () => renderList(input.value));
  input?.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveActive(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveActive(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      runCommand(_visible[_active]);
    }
  });

  list?.addEventListener("click", (e) => {
    const item = e.target.closest && e.target.closest("[data-idx]");
    if (!item) return;
    const idx = Number(item.getAttribute("data-idx"));
    runCommand(_visible[idx]);
  });

  window.__quantlabOpenCommandPalette = openPalette;
  window.__quantlabCloseCommandPalette = closePalette;
  renderList("");
}
