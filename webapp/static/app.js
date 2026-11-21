"use strict";

/* All dynamic report content is inserted with textContent, never innerHTML,
   so model-generated text cannot inject markup into the page. */

const state = {
  mode: "demo",
  images: [],
  task: "",
  lastResult: null,
  readQueue: Promise.resolve(),
  matchBusy: false,
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ---------------------------------------------------------------- health */
async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    state.mode = data.mode;
    const badge = $("mode-badge");
    badge.textContent = data.mode === "real" ? "真实模型" : "离线演示";
    badge.className = "badge " + data.mode;
    $("health-note").textContent = data.mode === "real"
      ? "服务器已配置模型"
      : "未配置模型（可在「模型配置」中填写）";
    const addressNote = $("address-note");
    const lan = (data.addresses || []).filter((url) => !url.includes("127.0.0.1"));
    if (lan.length) {
      addressNote.hidden = false;
      addressNote.replaceChildren();
      addressNote.append("队友访问地址：");
      lan.forEach((url) => addressNote.append(el("b", "", " " + url)));
    } else {
      addressNote.hidden = true;
    }
  } catch (err) {
    $("mode-badge").textContent = "服务未连接";
    $("mode-badge").className = "badge";
  }
}

/* ------------------------------------------------------- model config */
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    $("cfg-format").value = data.format || "openai";
    $("cfg-base-url").value = data.base_url || "";
    $("cfg-model").value = data.model || "";
    $("cfg-key").value = "";
    $("cfg-note").textContent = data.has_key ? "服务器已保存 API Key（不回显，留空则沿用）" : "";
  } catch (err) {
    /* config panel is optional */
  }
}

function configFields() {
  return {
    format: $("cfg-format").value,
    base_url: $("cfg-base-url").value.trim(),
    api_key: $("cfg-key").value.trim(),
    model: $("cfg-model").value.trim(),
  };
}

$("cfg-save").addEventListener("click", async () => {
  const note = $("cfg-note");
  note.textContent = "保存中…";
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configFields()),
    });
    const data = await res.json();
    if (!res.ok) {
      note.textContent = "保存失败：" + (data.error || "HTTP " + res.status);
      return;
    }
    note.textContent = "已保存到服务器，全队共用此配置。";
    $("cfg-key").value = "";
    await loadHealth();
  } catch (err) {
    note.textContent = "保存失败：" + err.message;
  }
});

$("cfg-test").addEventListener("click", async () => {
  const note = $("cfg-note");
  const probes = $("cfg-probes");
  note.textContent = "正在探测模型能力（文字 / 看图 / 工具调用）…";
  probes.replaceChildren();
  try {
    const res = await fetch("/api/config/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configFields()),
    });
    const data = await res.json();
    if (!res.ok) {
      note.textContent = "测试失败：" + (data.error || "HTTP " + res.status);
      return;
    }
    note.textContent = data.all_passed ? "全部通过，可以开始使用。" : "有探测未通过，见下方详情。";
    data.probes.forEach((probe) => {
      const line = el("div", "probe " + (probe.passed ? "pass" : "fail"));
      line.append(el("span", "p-name", (probe.passed ? "PASS " : "FAIL ") + probe.name));
      line.append(el("span", "p-detail", probe.detail));
      probes.append(line);
    });
  } catch (err) {
    note.textContent = "测试失败：" + err.message;
  }
});

/* ------------------------------------------------------------------ tasks */
document.querySelectorAll("#task-group button").forEach((button) => {
  button.addEventListener("click", () => {
    if (state.matchBusy) return;
    document.querySelectorAll("#task-group button").forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    state.task = button.dataset.task;
    $("match-controls").hidden = state.task !== "match_review";
    $("match-timeline").hidden = state.task !== "match_review";
    renderPreviews();
  });
});

/* ---------------------------------------------------------------- images */
const dropZone = $("drop-zone");
const fileInput = $("file-input");

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => addFiles(fileInput.files));
["dragenter", "dragover"].forEach((name) =>
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragover");
  }));
["dragleave", "drop"].forEach((name) =>
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragover");
  }));
dropZone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));

function addFiles(fileList) {
  if (state.matchBusy) { showError("当前比赛正在分析，请完成后再添加截图。"); return; }
  const files = Array.from(fileList).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  state.readQueue = state.readQueue.then(async () => {
    const limit = state.task === "match_review" ? 100 : 10;
    for (const file of files) {
      if (state.images.length >= limit) { showError("已达到截图数量上限 " + limit); break; }
      if (file.size > 20 * 1024 * 1024) { showError(file.name + " 超过 20 MB"); continue; }
      const url = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("无法读取 " + file.name));
        reader.readAsDataURL(file);
      });
      state.images.push({ name: file.name, data: url.slice(url.indexOf(",") + 1), url });
      renderPreviews();
    }
  }).catch((err) => showError(err.message));
  fileInput.value = "";
}

function renderPreviews() {
  const box = $("previews");
  box.replaceChildren();
  state.images.forEach((image, index) => {
    const wrap = el("div", "preview");
    const img = document.createElement("img");
    img.src = image.url;
    const round = Number($("start-round").value || 1) + index;
    const name = el("div", "name", (state.task === "match_review" ? "第 " + round + " 回合 · " : "") + image.name);
    const remove = el("button", "remove", "×");
    remove.title = "移除";
    remove.disabled = state.matchBusy;
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      state.images.splice(index, 1);
      renderPreviews();
    });
    wrap.append(img, name, remove);
    if (state.task === "match_review") {
      const controls = el("div", "preview-order");
      [-1, 1].forEach((offset) => {
        const move = el("button", "", offset < 0 ? "←" : "→");
        move.type = "button";
        move.title = offset < 0 ? "前移一回合" : "后移一回合";
        move.disabled = index + offset < 0 || index + offset >= state.images.length || state.matchBusy;
        move.addEventListener("click", (event) => {
          event.stopPropagation();
          [state.images[index], state.images[index + offset]] = [state.images[index + offset], state.images[index]];
          renderPreviews();
        });
        controls.append(move);
      });
      wrap.append(controls);
    }
    box.append(wrap);
  });
}

/* ---------------------------------------------------------------- samples */
async function loadSamples() {
  try {
    const res = await fetch("/api/samples");
    const data = await res.json();
    const row = $("sample-buttons");
    const specs = [
      ["complete_round", "完整回合（双方 5 人）", "buy_review"],
      ["partial_round", "部分回合", "buy_review"],
      ["position_snapshot", "站位快照", "position_review"],
      ["invalid_round", "非法样例（演示失败路径）", "buy_review"],
    ];
    for (const [key, label, task] of specs) {
      if (!(key in (data.samples || {}))) continue;
      const button = el("button", "", "载入" + label);
      button.type = "button";
      button.addEventListener("click", () => {
        $("snapshot-input").value = JSON.stringify(data.samples[key], null, 2);
        selectTask(task);
        $("advanced").open = true;
        $("snapshot-note").textContent = "已载入：" + label;
      });
      row.append(button);
    }
  } catch (err) {
    /* samples are optional; the UI still works without them */
  }
}

function selectTask(task) {
  state.task = task;
  document.querySelectorAll("#task-group button").forEach((button) => {
    button.classList.toggle("active", button.dataset.task === task);
  });
  $("match-controls").hidden = task !== "match_review";
  $("match-timeline").hidden = task !== "match_review";
  renderPreviews();
}

/* ------------------------------------------------------------ snapshot */
$("format-snapshot").addEventListener("click", () => {
  const input = $("snapshot-input");
  const note = $("snapshot-note");
  try {
    const value = JSON.parse(input.value);
    input.value = JSON.stringify(value, null, 2);
    note.textContent = "JSON 格式有效";
  } catch (err) {
    note.textContent = "JSON 无效：" + err.message;
  }
});

/* ------------------------------------------------------------- analyze */
$("run-button").addEventListener("click", runAnalysis);

async function runAnalysis() {
  if (state.matchBusy) return;
  const button = $("run-button");
  const errorBox = $("client-error");
  errorBox.hidden = true;
  button.disabled = true;
  button.textContent = "分析中…";
  await state.readQueue;

  let snapshot = null;
  const raw = $("snapshot-input").value.trim();
  if (raw) {
    try {
      snapshot = JSON.parse(raw);
    } catch (err) {
      showError("快照 JSON 无效：" + err.message);
      button.disabled = false;
      button.textContent = "开始分析";
      return;
    }
  }

  const payload = {
    task_type: state.task || null,
    text: $("text-input").value,
    observations: $("observation-input").value.split("\n").map((line) => line.trim()).filter(Boolean),
    images: state.images.map((image) => ({ name: image.name, data: image.data })),
    snapshot,
    force_demo: !$("cfg-ephemeral").checked && $("force-demo").checked,
  };
  if ($("cfg-ephemeral").checked) {
    payload.config = configFields();
    if (!payload.config.api_key || !payload.config.model) {
      showError("仅本次使用模式需要填写 API Key 和模型名，或取消勾选该选项。");
      button.disabled = false;
      button.textContent = "开始分析";
      return;
    }
  }

  try {
    if (state.task === "match_review") {
      await runMatchAnalysis(payload);
      return;
    }
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      showError(data.error || "分析失败（HTTP " + res.status + "）");
      return;
    }
    state.lastResult = data;
    renderReport(data);
  } catch (err) {
    showError("分析未完成：" + err.message);
  } finally {
    button.disabled = false;
    button.textContent = "开始分析";
  }
}

/* -------------------------------------------------------- match sessions */
$("start-round").addEventListener("input", renderPreviews);
try { $("match-id").value = localStorage.getItem("valorant-last-match") || ""; } catch (_) { /* optional */ }

function matchId() {
  let id = $("match-id").value.trim();
  if (!id) id = "match-" + (crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") :
    Date.now().toString(36) + Math.random().toString(36).slice(2));
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(id)) throw new Error("比赛编号只支持字母、数字、下划线和短横线，最多 64 位。");
  $("match-id").value = id;
  try { localStorage.setItem("valorant-last-match", id); } catch (_) { /* optional */ }
  return id;
}

async function matchRequest(url, payload) {
  const response = await fetch(url, payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败（HTTP " + response.status + "）");
  return data;
}

function matchConfig() {
  const config = { force_demo: !$("cfg-ephemeral").checked && $("force-demo").checked };
  if ($("cfg-ephemeral").checked) config.config = configFields();
  return config;
}

function renderMatchResult(data) {
  state.lastResult = data;
  const phases = { extracting: "读取截图", validating: "校验数据", analyzing: "计算战术数据",
    reviewing: "生成回合复盘", saving_round: "保存回合", round_saved: "回合已保存", summarizing: "汇总整场",
    starting: "开始分析", validation_failed: "数据校验未通过", ready: "已保存" };
  $("match-progress").textContent = "比赛 " + data.match_id + " · 已保存 " + data.completed_rounds + " 回合" +
    (data.current_round ? " · 第 " + data.current_round + " 回合" : "") +
    " · " + (data.status === "interrupted" ? "分析中断，可继续" : phases[data.phase] || "处理中");
  const box = $("match-timeline");
  box.hidden = false;
  box.replaceChildren(el("h3", "", "逐回合连续复盘"));
  (data.rounds || []).forEach((round) => {
    const details = el("details", "match-round");
    const snapshot = round.round_snapshot || {};
    const score = snapshot.score;
    const label = "第 " + round.round_index + " 回合 · " + (STATUS[round.report.task_status] || round.report.task_status) +
      (score && score.ally !== null && score.enemy !== null ? " · 开局 " + score.ally + ":" + score.enemy : "") +
      (snapshot.side ? " · " + (snapshot.side === "attack" ? "进攻" : "防守") : "");
    details.append(el("summary", "", label), el("p", "", round.report.coach_summary));
    if (round.continuity?.previous_round_outcome) details.append(el("p", "muted",
      "本回合开局比分确认：上一回合" + (round.continuity.previous_round_outcome === "ally_win" ? "己方胜" : "敌方胜")));
    if (round.report.buy_review) details.append(el("p", "", round.report.buy_review));
    if (round.report.position_review) details.append(el("p", "", round.report.position_review));
    (round.report.missing_information || []).slice(0, 10).forEach((item) => details.append(el("div", "muted", item)));
    box.append(details);
  });
  if (data.report) renderReport(data);
  else { $("report-content").hidden = true; $("report-placeholder").hidden = false;
    $("report-placeholder").textContent = "逐回合结果已保存，上传结束后生成整场总结。"; }
}

async function runMatchAnalysis(payload) {
  const id = matchId();
  const common = { match_id: id, text: payload.text, observations: payload.observations,
    ...matchConfig(), replace_existing: $("replace-rounds").checked };
  const start = Number($("start-round").value);
  if (!Number.isInteger(start) || start < 1) throw new Error("起始回合必须是正整数。");
  if (payload.snapshot && state.images.length) throw new Error("整场请使用截图或快照 JSON 其中一种输入。");
  let rounds = state.images.map((image, index) => ({ round_index: start + index,
    images: [{ name: image.name, data: image.data }] }));
  if (payload.snapshot) {
    const snapshots = Array.isArray(payload.snapshot) ? payload.snapshot : [payload.snapshot];
    rounds = snapshots.map((snapshot) => ({ round_index: snapshot.round_index, snapshot }));
  }
  if (rounds.some((round) => !Number.isInteger(round.round_index) || round.round_index < 1 || round.round_index > 100))
    throw new Error("回合编号必须在 1–100 之间，快照需包含 round_index。");
  state.matchBusy = true;
  $("match-id").disabled = true;
  $("start-round").disabled = true;
  renderPreviews();
  let polling = false;
  const timer = setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      const result = await matchRequest("/api/matches/" + encodeURIComponent(id));
      if (state.matchBusy) renderMatchResult(result);
    } catch (_) { /* first checkpoint may not exist yet */ }
    finally { polling = false; }
  }, 2500);
  try {
    for (let index = 0; index < rounds.length; index++) {
      $("run-button").textContent = "分析第 " + rounds[index].round_index + " 回合（" + (index + 1) + "/" + rounds.length + "）…";
      // One image per HTTP request avoids a huge 13+ image body and enables incremental retries.
      renderMatchResult(await matchRequest("/api/matches/analyze", { ...common, rounds: [rounds[index]], summarize: false }));
    }
    $("run-button").textContent = "生成整场连续复盘…";
    const result = await matchRequest("/api/matches/analyze", { ...common, rounds: [], summarize: true });
    renderMatchResult(result);
    state.images = [];
    $("snapshot-input").value = "";
    $("start-round").value = result.next_round;
  } finally {
    clearInterval(timer); state.matchBusy = false;
    $("match-id").disabled = false; $("start-round").disabled = false; renderPreviews();
  }
}

$("match-load").addEventListener("click", async () => {
  if (state.matchBusy) return;
  try {
    const result = await matchRequest("/api/matches/" + encodeURIComponent(matchId()));
    renderMatchResult(result);
    if (!state.images.length) $("start-round").value = result.next_round;
  } catch (err) { showError(err.message); }
});
$("match-resume").addEventListener("click", async () => {
  if (state.matchBusy) return;
  let id;
  try { id = matchId(); } catch (err) { showError(err.message); return; }
  const button = $("match-resume");
  button.disabled = true;
  state.matchBusy = true;
  $("run-button").disabled = true;
  const timer = setInterval(async () => {
    try {
      const result = await matchRequest("/api/matches/" + encodeURIComponent(id));
      if (state.matchBusy) renderMatchResult(result);
    } catch (_) { /* a later poll can retry */ }
  }, 2500);
  try { renderMatchResult(await matchRequest("/api/matches/" + encodeURIComponent(id) + "/resume", matchConfig())); }
  catch (err) { showError(err.message); }
  finally { clearInterval(timer); button.disabled = false; state.matchBusy = false; $("run-button").disabled = false; }
});
$("match-new").addEventListener("click", () => {
  if (state.matchBusy) return;
  $("match-id").value = "";
  $("start-round").value = 1;
  $("replace-rounds").checked = false;
  $("match-progress").textContent = "新比赛：请上传按回合排列的截图。";
  $("match-timeline").replaceChildren();
  matchId();
  renderPreviews();
});

function showError(message) {
  const box = $("client-error");
  box.textContent = message;
  box.hidden = false;
}

/* --------------------------------------------------------------- report */
const SEVERITY = { info: "提示", low: "低", medium: "中", high: "高" };
const STATUS = { success: "完整", partial: "部分完成", failed: "未完成" };
const EVIDENCE = { observed: "截图观察", inferred: "推测", user_confirmed: "用户确认",
                   derived: "工具计算", unknown: "未知" };

function renderReport(data) {
  const report = data.report;
  $("report-placeholder").hidden = true;
  const box = $("report-content");
  box.hidden = false;
  box.replaceChildren();

  const pill = el("div", "status-pill " + report.task_status,
                  "状态：" + (STATUS[report.task_status] || report.task_status));
  box.append(pill);

  const copyRow = el("div", "copy-row");
  const copyJson = el("button", "", "复制 JSON");
  copyJson.type = "button";
  copyJson.addEventListener("click", () => copyText(JSON.stringify(data, null, 2)));
  const copyMd = el("button", "", "复制 Markdown 报告");
  copyMd.type = "button";
  copyMd.addEventListener("click", () => copyText(data.markdown));
  copyRow.append(copyJson, copyMd);
  box.append(copyRow);

  if (report.overall_judgement) {
    box.append(section("总体判断", [el("div", "judgement", report.overall_judgement)]));
  }
  if (report.buy_review) box.append(section("购买复盘", [el("p", "", report.buy_review)]));
  if (report.position_review) box.append(section("站位复盘", [el("p", "", report.position_review)]));

  if (report.findings && report.findings.length) {
    const items = report.findings.map((finding) => {
      const card = el("div", "finding");
      const row = el("div", "title-row");
      row.append(el("span", "title", finding.title));
      row.append(el("span", "sev " + finding.severity,
                    SEVERITY[finding.severity] || finding.severity));
      if (finding.confidence !== null && finding.confidence !== undefined) {
        row.append(el("span", "conf", "置信度 " + Math.round(finding.confidence * 100) + "%"));
      }
      card.append(row, el("div", "explain", finding.explanation));
      if (finding.evidence_refs && finding.evidence_refs.length) {
        card.append(el("div", "refs", "依据：" + finding.evidence_refs.join("、")));
      }
      return card;
    });
    box.append(section("发现", items));
  }

  if (report.recommendations && report.recommendations.length) {
    const list = el("ol", "recs");
    report.recommendations.forEach((rec) => {
      const item = el("li");
      item.append(el("div", "rec-action", "P" + rec.priority + " · " + rec.action));
      item.append(el("div", "rec-meta", "原因：" + rec.reason + "　预期收益：" + rec.expected_benefit));
      list.append(item);
    });
    box.append(section("建议", [list]));
  }

  if (report.evidence && report.evidence.length) {
    const table = el("table", "evidence");
    const head = el("thead");
    const headRow = el("tr");
    ["字段", "类型", "来源", "置信度", "备注"].forEach((name) => headRow.append(el("th", "", name)));
    head.append(headRow);
    const body = el("tbody");
    report.evidence.forEach((entry) => {
      const row = el("tr");
      row.append(el("td", "", entry.field_name));
      row.append(el("td", "", EVIDENCE[entry.status] || entry.status));
      row.append(el("td", "", entry.source || ""));
      row.append(el("td", "", entry.confidence === null || entry.confidence === undefined
        ? "-" : Math.round(entry.confidence * 100) + "%"));
      row.append(el("td", "", entry.note || ""));
      body.append(row);
    });
    table.append(head, body);
    box.append(section("依据", [table]));
  }

  if (report.missing_information && report.missing_information.length) {
    const chips = el("div", "chips");
    report.missing_information.forEach((item) => chips.append(el("span", "chip warn-chip", item)));
    box.append(section("缺失信息", [chips]));
  }

  box.append(section("总结", [el("p", "", report.coach_summary)]));

  renderTrace(data);
  if ($("show-json").checked) {
    const pre = el("pre", "json", JSON.stringify(data, null, 2));
    box.append(section("原始 JSON", [pre]));
  }
}

function section(title, children) {
  const wrap = el("div", "report-section");
  wrap.append(el("h3", "", title), ...children);
  return wrap;
}

function renderTrace(data) {
  const trace = data.trace;
  const details = el("details", "trace");
  details.append(el("summary", "", "执行详情（trace）"));
  const meta = el("div", "trace-meta");
  meta.textContent = "Skill: " + (trace.skill || "无") +
    " · 轮次: " + trace.iterations +
    " · 工具调用: " + trace.tool_calls +
    " · 格式修复: " + trace.repairs +
    " · 模式: " + (data.mode === "real" ? "真实模型" : "离线演示");
  details.append(meta);
  if (trace.tool_executions && trace.tool_executions.length) {
    trace.tool_executions.forEach((execution) => {
      const line = el("div", "tool-line");
      line.append(el("span", "tool-name", execution.tool_name));
      line.append(el("span", "tool-status " + execution.status, execution.status));
      line.append(el("span", "tool-time", execution.duration_ms + " ms"));
      details.append(line);
    });
  }
  if (trace.notes && trace.notes.length) {
    trace.notes.forEach((note) => details.append(el("div", "trace-meta", "note: " + note)));
  }
  $("report-content").append(details);
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => {
    /* brief feedback */
  }).catch(() => {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  });
}

/* ------------------------------------------------------------------ init */
loadHealth();
loadConfig();
loadSamples();
