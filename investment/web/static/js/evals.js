import {
  formatDailySteps,
  runDaily,
  postQuantCiEval,
  renderReadmeLinksHtml,
  attachReadmeLinkHandler,
  downloadJson,
} from "./shared.js";

/** Evals dialog. */
export function initEvals(ctx) {
  const evalsDialog = document.getElementById("evals-dialog");
  const evalsMeta = document.getElementById("evals-meta");
  const evalsSummary = document.getElementById("evals-summary");
  const evalsList = document.getElementById("evals-list");
  const evalsCase = document.getElementById("evals-case");
  const evalsMock = document.getElementById("evals-mock");
  const evalsPresets = document.getElementById("evals-presets");
  const evalsAgent = document.getElementById("evals-agent");
  const evalsRoutingMeta = document.getElementById("evals-routing-meta");
  const evalsRoutingTable = document.getElementById("evals-routing-table");
  const evalsRoutingWrap = document.getElementById("evals-routing-wrap");
  const evalsReadmeWrap = document.getElementById("evals-readme-wrap");
  const evalsReadmeMeta = document.getElementById("evals-readme-meta");
  const evalsReadmeTree = document.getElementById("evals-readme-tree");
  let evalsLastReport = null;
  let evalsPollTimer = null;

  async function loadEvalRouting() {
    if (!evalsRoutingTable) return;
    if (evalsRoutingMeta) evalsRoutingMeta.textContent = "加载路由对照…";
    try {
      const res = await fetch("/api/evals/routing");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const rows = (data.cases || []).filter((c) => c.has_routing_expect);
      if (evalsRoutingMeta) {
        evalsRoutingMeta.textContent = `路由 ${data.ok_count}/${data.with_expect} OK · 共 ${data.count} cases`;
      }
      const body = rows
        .map((c) => {
          const exp = c.routing_expect || {};
          const inf = c.inferred || {};
          const expectTxt = [
            exp.quant_task ? `quant=${exp.quant_task}` : "",
            exp.is_quant_question != null ? `is_quant=${exp.is_quant_question}` : "",
            exp.wants_position_stance != null ? `stance=${exp.wants_position_stance}` : "",
          ]
            .filter(Boolean)
            .join(" · ");
          const inferTxt = [
            inf.quant_task ? `quant=${inf.quant_task}` : "",
            inf.is_quant_question ? "is_quant" : "",
            inf.wants_position_stance ? "stance" : "",
          ]
            .filter(Boolean)
            .join(" · ");
          return `<tr class="${c.ok ? "ok" : "fail"}">
            <td><button type="button" class="text-btn evals-route-case" data-case-id="${c.id}">${c.id}</button></td>
            <td>${expectTxt || "—"}</td>
            <td>${inferTxt || "—"}</td>
            <td>${c.ok ? "OK" : (c.failures || [])[0] || "FAIL"}</td>
          </tr>`;
        })
        .join("");
      evalsRoutingTable.innerHTML = `<table>
        <thead><tr><th>case</th><th>expect</th><th>inferred</th><th>status</th></tr></thead>
        <tbody>${body || '<tr><td colspan="4">无 routing_expect</td></tr>'}</tbody>
      </table>`;
    } catch (err) {
      if (evalsRoutingMeta) evalsRoutingMeta.textContent = String(err.message || err);
      if (evalsRoutingTable) evalsRoutingTable.innerHTML = "";
    }
  }

  async function loadEvalReadme() {
    if (!evalsReadmeTree) return;
    if (evalsReadmeMeta) evalsReadmeMeta.textContent = "加载 README 覆盖…";
    try {
      const [checkRes, indexRes] = await Promise.all([
        fetch("/api/evals/readme"),
        fetch("/api/readme-index"),
      ]);
      const check = await checkRes.json();
      const index = indexRes.ok ? await indexRes.json() : { entries: check.entries || [] };
      if (evalsReadmeMeta) {
        evalsReadmeMeta.textContent = check.ok
          ? `README ${check.present_count}/${check.total_dirs} OK`
          : `README FAIL · ${(check.failures || []).slice(0, 2).join("；")}`;
      }
      evalsReadmeTree.innerHTML = renderReadmeLinksHtml(index, {
        summary: `覆盖 ${check.present_count}/${check.total_dirs}${check.ok ? "" : " · 存在缺失"}`,
      });
      attachReadmeLinkHandler(evalsReadmeTree, ctx);
    } catch (err) {
      if (evalsReadmeMeta) evalsReadmeMeta.textContent = String(err.message || err);
      if (evalsReadmeTree) evalsReadmeTree.innerHTML = "";
    }
  }

  async function loadEvalSummary() {
    try {
      const res = await fetch("/api/evals/summary");
      const data = await res.json();
      if (!res.ok) return;
      const presetOk = (data.presets && data.presets.ok) ? "preset OK" : "preset —";
      const readmeOk = (data.readme && data.readme.ok) ? "README OK" : "README —";
      const last = data.last_run;
      const lastTxt = last
        ? `上次 ${last.ok ? "OK" : "FAIL"} ${last.passed}/${last.total}`
        : "尚无上次结果";
      evalsMeta.textContent = `${data.case_count} cases · ${presetOk} · ${readmeOk} · ${lastTxt}`;
    } catch (_) {
      /* ignore */
    }
  }

  async function loadEvalCases() {
    const res = await fetch("/api/evals/cases");
    const data = await res.json();
    if (!evalsCase || !data.cases) return;
    evalsCase.innerHTML = `<option value="">全部（${data.cases.length}）</option>`;
    for (const c of data.cases) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = `${c.id} · ${c.intent || ""}`;
      evalsCase.appendChild(opt);
    }
  }

  function renderEvalReport(report) {
    if (!report) return;
    evalsLastReport = report;
    if (!evalsSummary || !evalsList) return;
    const ok = !!report.ok;
    evalsSummary.className = `evals-summary ${ok ? "ok" : "fail"}`;
    const saved = report.saved_at ? ` · ${report.saved_at}` : "";
    const presetPart =
      report.presets != null
        ? ` · preset=${report.presets.ok ? "OK" : "FAIL"}`
        : report.with_presets
          ? " · preset=—"
          : "";
    const readmePart =
      report.readme != null
        ? ` · readme=${report.readme.ok ? "OK" : "FAIL"}`
        : report.with_presets
          ? " · readme=—"
          : "";
    const quantPart = report.quant_only ? " · quant-only" : "";
    evalsSummary.textContent = ok
      ? `通过 ${report.passed}/${report.total} · mock=${report.use_mock ? "ON" : "OFF"}${report.with_agent ? " · Agent" : ""}${quantPart}${presetPart}${readmePart}${saved}`
      : `失败 ${report.failed}/${report.total}${quantPart}${presetPart}${readmePart} · ${(report.failures || []).slice(0, 2).join("；")}${saved}`;
    evalsList.innerHTML = (report.cases || [])
      .map((c) => {
        const badge = c.ok ? "OK" : "FAIL";
        const err = (c.failures || [])[0] || "";
        return `<li class="evals-item ${c.ok ? "ok" : "fail"}">
          <div class="name">${c.id} <span class="badge">${badge}</span></div>
          <div class="sub">${c.intent || ""}${err ? " · " + err : ""}</div>
        </li>`;
      })
      .join("");
  }

  async function pollEvalJob() {
    const res = await fetch("/api/evals/job");
    const data = await res.json();
    const job = data.job || {};
    if (job.status === "running") {
      evalsMeta.textContent = "后台运行中…";
      return;
    }
    clearInterval(evalsPollTimer);
    evalsPollTimer = null;
    if (job.status === "done" || job.status === "failed") {
      const last = await fetch("/api/evals/last");
      const lastData = await last.json();
      if (lastData.exists && lastData.report) {
        renderEvalReport(lastData.report);
      }
      evalsMeta.textContent = job.status === "done" ? "后台校验完成" : `失败: ${job.error || job.failures?.[0] || ""}`;
    }
  }

  async function openEvalsPanel({ showDialog = false } = {}) {
    try {
      await loadEvalCases();
      await loadEvalSummary();
      if (evalsRoutingWrap && evalsRoutingWrap.open) {
        await loadEvalRouting();
      }
      if (evalsSummary) {
        evalsSummary.textContent = "点击「运行」或「CI 同款」开始，或查看「上次结果」";
        evalsSummary.className = "evals-summary";
      }
      if (evalsList) evalsList.innerHTML = "";
      const last = await fetch("/api/evals/last");
      const lastData = await last.json();
      if (lastData.exists && lastData.report) {
        evalsLastReport = lastData.report;
      }
    } catch (err) {
      if (evalsMeta) evalsMeta.textContent = String(err.message || err);
    }
    if (showDialog && evalsDialog && typeof evalsDialog.showModal === "function") {
      evalsDialog.showModal();
    }
  }

  ctx.openEvalsPanel = openEvalsPanel;
  window.__investmentOpenEvals = () => openEvalsPanel({ showDialog: true });

  const btnEvals = document.getElementById("btn-evals");
  if (btnEvals) {
    btnEvals.addEventListener("click", async () => {
      await openEvalsPanel({ showDialog: true });
    });
  }

  document.getElementById("evals-last").addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/evals/last");
      const data = await res.json();
      if (!data.exists) {
        evalsMeta.textContent = "尚无保存的结果";
        return;
      }
      renderEvalReport(data.report);
      evalsMeta.textContent = "已加载上次结果";
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });

  document.getElementById("evals-download").addEventListener("click", (e) => {
    e.preventDefault();
    if (!evalsLastReport) {
      evalsMeta.textContent = "无结果可下载";
      return;
    }
    downloadJson(evalsLastReport, "evals_last_run.json");
  });

  document.getElementById("evals-daily").addEventListener("click", async (e) => {
    e.preventDefault();
    evalsMeta.textContent = "每日 eval（mock checklist）运行中…";
    try {
      const data = await runDaily({ evalMock: true });
      const last = await fetch("/api/evals/last");
      const lastData = await last.json();
      if (lastData.exists && lastData.report) {
        renderEvalReport(lastData.report);
      }
      const step = (data.steps || []).find((s) => s.name === "eval_mock");
      evalsMeta.textContent = step?.ok
        ? `每日 eval 完成 · 通过 ${step.passed}/${step.total}`
        : `每日 eval 失败 · ${formatDailySteps(data)}`;
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });

  if (evalsRoutingWrap) {
    evalsRoutingWrap.addEventListener("toggle", () => {
      if (evalsRoutingWrap.open) loadEvalRouting();
    });
  }

  if (evalsReadmeWrap) {
    evalsReadmeWrap.addEventListener("toggle", () => {
      if (evalsReadmeWrap.open) loadEvalReadme();
    });
  }

  attachReadmeLinkHandler(evalsReadmeTree, ctx);

  if (evalsRoutingTable) {
    evalsRoutingTable.addEventListener("click", (e) => {
      const btn = e.target.closest(".evals-route-case");
      if (!btn || !evalsCase) return;
      e.preventDefault();
      const caseId = btn.getAttribute("data-case-id");
      if (!caseId) return;
      evalsCase.value = caseId;
      evalsMeta.textContent = `已选用例 ${caseId} · 可点「运行」单 case 校验`;
    });
  }

  document.getElementById("evals-routing-refresh").addEventListener("click", async (e) => {
    e.preventDefault();
    await loadEvalRouting();
  });

  async function runEvals(options = {}) {
    const {
      withAgent = !!evalsAgent.checked,
      withPresets = !!evalsPresets.checked,
      useMock = !!evalsMock.checked,
      caseId = evalsCase.value || null,
      quantOnly = false,
      background = false,
      runningLabel = "运行中…",
    } = options;
    evalsMeta.textContent = runningLabel;
    const allCases = !caseId;
    const res = await fetch("/api/evals/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        case_id: caseId,
        use_mock: useMock,
        with_agent: withAgent,
        with_presets: withPresets,
        quant_only: quantOnly,
        background: background || (withAgent && allCases),
      }),
    });
    const data = await res.json();
    if (data.background) {
      evalsMeta.textContent = "已提交后台任务…";
      if (evalsPollTimer) clearInterval(evalsPollTimer);
      evalsPollTimer = setInterval(pollEvalJob, 2000);
      pollEvalJob();
      return data;
    }
    if (!res.ok && res.status !== 422) {
      throw new Error(data.detail || res.statusText);
    }
    renderEvalReport(data);
    await loadEvalSummary();
    evalsMeta.textContent = data.ok ? "校验完成" : "存在失败项";
    return data;
  }

  document.getElementById("evals-run").addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      await runEvals();
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });

  document.getElementById("evals-ci").addEventListener("click", async (e) => {
    e.preventDefault();
    if (evalsMock) evalsMock.checked = true;
    if (evalsPresets) evalsPresets.checked = true;
    if (evalsAgent) evalsAgent.checked = false;
    if (evalsCase) evalsCase.value = "";
    try {
      await runEvals({
        useMock: true,
        withPresets: true,
        withAgent: false,
        quantOnly: false,
        caseId: null,
        runningLabel: "CI 同款（mock + presets）运行中…",
      });
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });

  document.getElementById("evals-ci-quant").addEventListener("click", async (e) => {
    e.preventDefault();
    if (evalsMock) evalsMock.checked = true;
    if (evalsPresets) evalsPresets.checked = true;
    if (evalsAgent) evalsAgent.checked = false;
    if (evalsCase) evalsCase.value = "";
    evalsMeta.textContent = "量化 CI 同款（9 quant_* + preset）运行中…";
    try {
      const data = await postQuantCiEval();
      renderEvalReport(data);
      await loadEvalSummary();
      evalsMeta.textContent = data.ok ? "校验完成" : "存在失败项";
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });

  document.getElementById("evals-presets-run").addEventListener("click", async (e) => {
    e.preventDefault();
    evalsMeta.textContent = "preset 校验中…";
    try {
      const res = await fetch("/api/evals/presets");
      const data = await res.json();
      if (!res.ok) {
        evalsSummary.className = "evals-summary fail";
        evalsSummary.textContent = (data.failures || []).join("；") || "preset 校验失败";
      } else {
        evalsSummary.className = "evals-summary ok";
        evalsSummary.textContent = `preset OK · ${(data.checked || []).join(", ")}`;
      }
      await loadEvalSummary();
      evalsMeta.textContent = res.ok ? "preset 校验完成" : "preset 校验失败";
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });

  document.getElementById("evals-readme-run").addEventListener("click", async (e) => {
    e.preventDefault();
    evalsMeta.textContent = "README 覆盖校验中…";
    try {
      const res = await fetch("/api/evals/readme");
      const data = await res.json();
      if (!res.ok) {
        evalsSummary.className = "evals-summary fail";
        evalsSummary.textContent = (data.failures || []).slice(0, 3).join("；") || "README 校验失败";
      } else {
        evalsSummary.className = "evals-summary ok";
        evalsSummary.textContent = `README OK · ${data.present_count}/${data.total_dirs}`;
      }
      if (evalsReadmeWrap) evalsReadmeWrap.open = true;
      await loadEvalReadme();
      await loadEvalSummary();
      evalsMeta.textContent = res.ok ? "README 覆盖校验完成" : "README 覆盖校验失败";
    } catch (err) {
      evalsMeta.textContent = String(err.message || err);
    }
  });
}
