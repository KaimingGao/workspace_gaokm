/** Monaco loader for strategy spec (R2：可编辑草稿；默认只读兼容旧调用). */

const MONACO_BASE =
  "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs";

let _loading = null;
let _editor = null;

function loadMonaco() {
  if (window.monaco) return Promise.resolve(window.monaco);
  if (_loading) return _loading;
  _loading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${MONACO_BASE}/loader.js`;
    script.async = true;
    script.onload = () => {
      try {
        window.require.config({ paths: { vs: MONACO_BASE } });
        window.require(["vs/editor/editor.main"], () => {
          resolve(window.monaco);
        });
      } catch (err) {
        reject(err);
      }
    };
    script.onerror = () => reject(new Error("Monaco loader failed"));
    document.head.appendChild(script);
  });
  return _loading;
}

/**
 * @param {HTMLElement} host
 * @param {string} value
 * @param {HTMLTextAreaElement} [fallbackTa]
 * @param {{ readOnly?: boolean }} [opts] readOnly===false 可编辑；默认只读
 */
export async function mountJsonEditor(host, value, fallbackTa, opts = {}) {
  const isReadOnly = opts.readOnly !== false;
  if (!host) {
    if (fallbackTa) {
      fallbackTa.value = value || "";
      fallbackTa.readOnly = isReadOnly;
    }
    return null;
  }
  const text = value || "";
  try {
    const monaco = await loadMonaco();
    if (_editor) {
      _editor.setValue(text);
      _editor.updateOptions({ readOnly: isReadOnly });
      return _editor;
    }
    host.innerHTML = "";
    host.hidden = false;
    if (fallbackTa) fallbackTa.hidden = true;
    const isDark =
      document.documentElement.getAttribute("data-theme") === "dark";
    _editor = monaco.editor.create(host, {
      value: text,
      language: "json",
      readOnly: isReadOnly,
      minimap: { enabled: false },
      fontSize: 12,
      lineNumbers: "on",
      scrollBeyondLastLine: false,
      wordWrap: "on",
      automaticLayout: true,
      theme: isDark ? "vs-dark" : "vs",
      tabSize: 2,
    });
    return _editor;
  } catch (_) {
    host.hidden = true;
    if (fallbackTa) {
      fallbackTa.hidden = false;
      fallbackTa.value = text;
      fallbackTa.readOnly = isReadOnly;
    }
    return null;
  }
}

/** @deprecated use mountJsonEditor */
export async function mountReadonlyJsonEditor(host, value, fallbackTa) {
  return mountJsonEditor(host, value, fallbackTa, { readOnly: true });
}

export function setJsonEditorValue(value) {
  if (_editor) {
    _editor.setValue(value || "");
    return;
  }
  const ta = document.getElementById("quant-signal-config");
  if (ta) ta.value = value || "";
}

/** @deprecated use setJsonEditorValue */
export function setReadonlyJsonValue(value) {
  setJsonEditorValue(value);
}

export function getJsonEditorValue() {
  if (_editor) return _editor.getValue();
  const ta = document.getElementById("quant-signal-config");
  return ta ? ta.value : "";
}

export function setJsonEditorReadOnly(flag) {
  if (_editor) {
    _editor.updateOptions({ readOnly: !!flag });
    return;
  }
  const ta = document.getElementById("quant-signal-config");
  if (ta) ta.readOnly = !!flag;
}
