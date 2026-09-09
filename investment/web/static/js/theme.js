/** 主题：浅色默认 · 深色可选；侧栏折叠 · 沪深时钟 */

const THEME_KEY = "investment_theme";
const SIDE_KEY = "investment_side_collapsed";
const DENSITY_KEY = "investment_density";

function currentTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  return t === "dark" ? "dark" : "light";
}

export function setTheme(theme) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (_) {
    /* ignore */
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", next === "dark" ? "#1E1E1E" : "#eef2f7");
}

export function toggleTheme() {
  setTheme(currentTheme() === "dark" ? "light" : "dark");
}

function tickClocks() {
  const el = document.getElementById("market-clocks");
  if (!el) return;
  const now = new Date();
  const cn = now.toLocaleTimeString("zh-CN", {
    hour12: false,
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const span = el.querySelector('[data-clock="cn"]');
  if (span) span.textContent = `沪深 ${cn}`;
  else el.textContent = `沪深 ${cn}`;
}

function setSideCollapsed(collapsed) {
  const shell = document.querySelector(".app-shell");
  if (!shell) return;
  shell.classList.toggle("side-collapsed", !!collapsed);
  try {
    localStorage.setItem(SIDE_KEY, collapsed ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
  const btn = document.getElementById("btn-side-collapse");
  if (btn) btn.textContent = collapsed ? "»" : "«";
}

function currentDensity() {
  const d = document.documentElement.getAttribute("data-density");
  return d === "compact" ? "compact" : "comfortable";
}

export function setDensity(mode) {
  const next = mode === "compact" ? "compact" : "comfortable";
  if (next === "comfortable") {
    document.documentElement.removeAttribute("data-density");
  } else {
    document.documentElement.setAttribute("data-density", "compact");
  }
  try {
    localStorage.setItem(DENSITY_KEY, next);
  } catch (_) {
    /* ignore */
  }
  const btn = document.getElementById("btn-density");
  if (btn) {
    btn.textContent = next === "compact" ? "舒适" : "密度";
    btn.title = next === "compact" ? "切换为舒适密度" : "切换为紧凑密度";
  }
}

export function toggleDensity() {
  setDensity(currentDensity() === "compact" ? "comfortable" : "compact");
}

export function initTheme() {
  // head 内联脚本已设 data-theme；此处同步 meta / 按钮
  setTheme(currentTheme());

  let density = "comfortable";
  try {
    density = localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
  } catch (_) {
    density = "comfortable";
  }
  setDensity(density);

  const toggle = document.getElementById("btn-theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", (e) => {
      e.preventDefault();
      toggleTheme();
    });
  }

  const densityBtn = document.getElementById("btn-density");
  if (densityBtn) {
    densityBtn.addEventListener("click", (e) => {
      e.preventDefault();
      toggleDensity();
    });
  }

  let collapsed = false;
  try {
    collapsed = localStorage.getItem(SIDE_KEY) === "1";
  } catch (_) {
    collapsed = false;
  }
  setSideCollapsed(collapsed);
  const collapseBtn = document.getElementById("btn-side-collapse");
  if (collapseBtn) {
    collapseBtn.addEventListener("click", (e) => {
      e.preventDefault();
      const shell = document.querySelector(".app-shell");
      setSideCollapsed(!(shell && shell.classList.contains("side-collapsed")));
    });
  }

  tickClocks();
  window.setInterval(tickClocks, 1000);

  // 轻量健康：非对话页也刷新顶栏 status
  const statusEl = document.getElementById("status");
  if (statusEl && (document.body.dataset.page || "") !== "chat") {
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => {
        const ok = d.ok !== false && d.success !== false;
        statusEl.textContent = ok ? "系统就绪" : d.error || "异常";
        statusEl.className = ok ? "status ok" : "status bad";
      })
      .catch(() => {
        statusEl.textContent = "健康检查失败";
        statusEl.className = "status bad";
      });
  }

  // /platform 高亮侧栏「平台」
  try {
    const u = new URL(window.location.href);
    if (u.pathname.indexOf("/platform") === 0) {
      document.querySelectorAll(".side-nav-item").forEach((a) => a.classList.remove("active"));
      const s = document.getElementById("btn-settings");
      if (s) {
        s.classList.add("active");
        s.setAttribute("aria-current", "page");
      }
    }
  } catch (_) {
    /* ignore */
  }
}
