/* ============================================================
   演示前端（原生 JS，无框架、无构建步骤）

   数据全部来自 /api/*（服务端直通本地真实产物）。
   这里只做三件事：把 JSON 变成 DOM、把字符区间变成高亮、把点击变成视图切换。
   ============================================================ */

const state = {
  samples: [],
  sampleId: null,
  report: null,
  text: "",
  titles: {},
  diff: [],
  filter: "all",
  selected: null,
  evaluation: null,
};

const RISK_ZH = { high: "高", medium: "中", low: "低", na: "—" };
const $ = (id) => document.getElementById(id);

/* ---------- 小工具 ---------- */
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const pct = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : (v * 100).toFixed(digits) + "%";

const num = (v, digits = 3) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(digits);

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function riskSwatch(level) {
  return `<span class="swatch ${level}"></span>`;
}

/* ---------- 切换视图 ---------- */
function showView(name) {
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("is-on", el.id === `view-${name}`));
  document.querySelectorAll(".tab").forEach((el) => el.classList.toggle("is-on", el.dataset.view === name));
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  // 进"原文对照"直接滚到当前条款的证据 —— 否则会停在合同封面（SEC 文件的封面占好几屏），
  // 用户以为"没高亮"（实测踩过：截图里只看到 REDACTED COPY）
  if (name === "source") scrollToSelectedEvidence();
}

function scrollToSelectedEvidence() {
  const f = state.report?.report?.findings?.find((x) => x.clause_id === state.selected);
  if (f?.evidence?.length) locateEvidence(state.selected, 0);
}

/* ============================================================
   审查报告
   ============================================================ */
function renderSampleSelect() {
  $("sample-select").innerHTML = state.samples
    .map(
      (s) =>
        `<option value="${s.id}"${s.id === state.sampleId ? " selected" : ""}>` +
        `${esc(s.contract_id.slice(0, 58))}${s.contract_id.length > 58 ? "…" : ""}</option>`
    )
    .join("");
}

function renderSummary() {
  const s = state.report?.sample || state.samples.find((x) => x.id === state.sampleId) || {};
  const findings = state.report.report.findings;
  const exists = findings.filter((f) => f.exists).length;
  const high = findings.filter((f) => f.risk_level === "high").length;
  const evidence = findings.reduce((n, f) => n + (f.evidence?.length || 0), 0);
  const chars = state.text.length;

  $("summary-strip").innerHTML = `
    <div class="summary-cell"><span class="k">存在条款</span><span class="v">${exists}<small> / ${findings.length}</small></span></div>
    <div class="summary-cell"><span class="k">高风险</span><span class="v">${high}</span></div>
    <div class="summary-cell"><span class="k">必备条款缺失</span><span class="v">${s.n_missing_required ?? 0}</span></div>
    <div class="summary-cell"><span class="k">证据条数</span><span class="v">${evidence}</span></div>
    <div class="summary-cell"><span class="k">合同长度</span><span class="v">${chars.toLocaleString()}<small> 字符</small></span></div>
    <div class="summary-note">${esc(state.report.report.summary_zh || "")}</div>`;
}

function renderDiffBadge() {
  const diff = state.diff || [];
  const bad = diff.filter((d) => !d.match);
  const same = diff.length - bad.length;
  const detail = bad.length
    ? bad.map((d) => `${d.clause_id.slice(0, 2)} ${d.kind}`).join("、")
    : "全部一致";
  $("diff-badge").innerHTML =
    `<span class="chip num" title="与 CUAD 金标准逐类对照：${esc(detail)}">` +
    `与金标准一致 ${same}/${diff.length}</span>` +
    (bad.length ? `<span class="chip" style="color:var(--high)">${bad.length} 处不一致</span>` : "");
}

function visibleFindings() {
  const all = state.report.report.findings;
  if (state.filter === "exists") return all.filter((f) => f.exists);
  if (state.filter === "missing") return all.filter((f) => !f.exists);
  if (state.filter === "high") return all.filter((f) => f.risk_level === "high");
  return all;
}

function renderClauseList() {
  const rows = visibleFindings();
  $("clause-list").innerHTML = rows
    .map((f) => {
      const t = state.titles[f.clause_id] || {};
      const on = f.clause_id === state.selected ? " is-on" : "";
      const dot = f.exists ? `<span class="mark-exists"></span>` : `<span class="mark-missing"></span>`;
      return (
        `<button class="clause-row${on}" data-clause="${f.clause_id}">` +
        `<span class="no">${f.clause_id.slice(0, 2)}</span>` +
        `<span class="name">${esc(t.zh || f.clause_id)}<em>${esc(t.en || "")}</em></span>` +
        `<span class="flag">${dot}${f.exists ? riskSwatch(f.risk_level) : ""}</span>` +
        `</button>`
      );
    })
    .join("");

  $("clause-list")
    .querySelectorAll(".clause-row")
    .forEach((el) => el.addEventListener("click", () => selectClause(el.dataset.clause)));
}

function renderClauseDetail() {
  const f = state.report.report.findings.find((x) => x.clause_id === state.selected);
  if (!f) {
    $("clause-detail").innerHTML = `<p class="empty">没有可显示的条款。</p>`;
    return;
  }
  const t = state.titles[f.clause_id] || {};
  const diff = state.diff.find((d) => d.clause_id === f.clause_id);
  const diffChip = diff
    ? diff.match
      ? `<span class="chip ok">与金标准一致</span>`
      : `<span class="chip" style="color:var(--high);border-color:rgba(142,47,28,.3)">与金标准不一致（${diff.kind}）</span>`
    : "";

  const gaps = (f.gaps || []).length
    ? `<div class="detail-section"><span class="section-label">缺口 / 建议</span><ul class="gap-list">${f.gaps
        .map((g) => `<li>${esc(g)}</li>`)
        .join("")}</ul></div>`
    : "";

  const evidence = (f.evidence || []).length
    ? `<div class="detail-section"><span class="section-label">原文证据（${f.evidence.length}）</span>${f.evidence
        .map(
          (e, i) =>
            `<div class="evidence"><div class="evidence-head">` +
            `<span class="evidence-loc">证据 ${i + 1}　原文位置 ${e.char_start}–${e.char_end}</span>` +
            `<button class="link-btn" data-locate="${f.clause_id}" data-index="${i}">在原文中查看</button>` +
            `</div><p class="evidence-quote">${esc(e.text)}</p></div>`
        )
        .join("")}</div>`
    : `<div class="detail-section"><span class="section-label">原文证据</span><p class="empty">本类未给出证据。</p></div>`;

  $("clause-detail").innerHTML = `
    <div class="detail-head">
      <h2 class="detail-title">${esc(t.zh || f.clause_id)}</h2>
      <div class="detail-sub">
        <span class="chip ${f.exists ? "ok" : "no"}">${f.exists ? "存在" : "缺失"}</span>
        ${f.exists ? `<span class="chip">风险 ${RISK_ZH[f.risk_level] || f.risk_level} ${riskSwatch(f.risk_level)}</span>` : ""}
        <span class="chip num">置信度 ${f.confidence.toFixed(2)}</span>
        <span class="chip num">${f.clause_id}</span>
        ${diffChip}
      </div>
    </div>
    <div class="detail-section"><span class="section-label">判定理由</span><p class="reason">${esc(f.reason_zh)}</p></div>
    ${gaps}
    ${evidence}`;

  $("clause-detail")
    .querySelectorAll("[data-locate]")
    .forEach((el) =>
      el.addEventListener("click", () => {
        showView("source");
        locateEvidence(el.dataset.locate, Number(el.dataset.index));
      })
    );
}

function selectClause(clauseId) {
  state.selected = clauseId;
  renderClauseList();
  renderClauseDetail();
  highlightNav(clauseId);
  // 已经在原文视图时，切换条款要跟着滚过去（在报告视图则不动，避免打扰阅读）
  if (document.getElementById("view-source").classList.contains("is-on")) scrollToSelectedEvidence();
}

/* ============================================================
   原文对照
   ============================================================ */
function evidenceSpans() {
  const spans = [];
  for (const f of state.report.report.findings) {
    (f.evidence || []).forEach((e, index) => {
      spans.push({
        start: e.char_start,
        end: e.char_end,
        clause: f.clause_id,
        index,
        no: f.clause_id.slice(0, 2),
      });
    });
  }
  return spans.sort((a, b) => a.start - b.start);
}

function renderDocument() {
  const text = state.text;
  const spans = evidenceSpans();
  const parts = [];
  let cursor = 0;
  for (const s of spans) {
    if (s.start < cursor) continue; // 重叠区间跳过，保证文本顺序与完整性不被破坏
    if (s.start > cursor) parts.push(esc(text.slice(cursor, s.start)));
    parts.push(
      `<mark class="ev" id="ev-${s.clause}-${s.index}" data-clause="${s.clause}" title="${s.clause}">` +
        `<span class="ev-tag">${s.no}</span>${esc(text.slice(s.start, s.end))}</mark>`
    );
    cursor = s.end;
  }
  parts.push(esc(text.slice(cursor)));
  $("document-body").innerHTML = parts.join("");

  $("document-body")
    .querySelectorAll("mark.ev")
    .forEach((el) =>
      el.addEventListener("click", () => {
        selectClause(el.dataset.clause);
        showView("report");
      })
    );
}

function renderSourceNav() {
  const items = state.report.report.findings.map((f) => {
    const t = state.titles[f.clause_id] || {};
    const loc = f.evidence?.[0] ? `${f.evidence[0].char_start}` : "";
    return (
      `<button class="nav-item" data-clause="${f.clause_id}">` +
      `<span class="no">${f.clause_id.slice(0, 2)}</span>` +
      `<span>${esc(t.zh || f.clause_id)}</span>` +
      `<span class="loc">${f.evidence?.length ? loc : "—"}</span></button>`
    );
  });
  $("source-nav").innerHTML = `<div class="nav-head">条款与证据位置</div>` + items.join("");
  $("source-nav")
    .querySelectorAll(".nav-item")
    .forEach((el) =>
      el.addEventListener("click", () => {
        selectClause(el.dataset.clause);
        if (el.dataset.clause) locateEvidence(el.dataset.clause, 0);
      })
    );
}

function highlightNav(clauseId) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.style.background = el.dataset.clause === clauseId ? "rgba(31,58,52,.05)" : "";
  });
}

function locateEvidence(clauseId, index) {
  const el = document.getElementById(`ev-${clauseId}-${index}`);
  if (!el) return;
  // 先强制一次布局：刚切换过来的视图在布局树里可能还是 display:none，
  // 此时 scrollIntoView 会静默失效（实测：目标元素在视口下方 12767px 处、页面纹丝不动）。
  // 注意不要用 requestAnimationFrame —— 回调跑在样式重算**之前**，同样拿不到新布局。
  void el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // 触发重排，让动画可以重复播放
  el.classList.add("flash");
}

/* ============================================================
   评测
   ============================================================ */
function metricBlock(label, value, note) {
  return `<div class="metric"><span class="k">${label}</span><span class="v">${value}</span><span class="n">${note}</span></div>`;
}

function renderEvaluation() {
  const d = state.evaluation;
  if (!d) return;
  const o = d.final.overall || {};
  const micro = o.micro || {};
  const macro = o.macro || {};
  const cfg = d.final.config || {};

  // per_clause 是**列表**（每项自带 clause_id），不是字典。
  // 早先按对象处理时，Object.entries 给的是数组下标，首列就显示成了 "0 0 / 1 1 …"。
  const perClause = Array.isArray(d.final.per_clause)
    ? d.final.per_clause
    : Object.values(d.final.per_clause || {});
  const clauseRows = perClause
    .map((m) => {
      const cid = m.clause_id;
      const t = state.titles[cid] || {};
      return (
        `<tr class="${m.f1 !== null && m.f1 < 0.7 ? "is-weak" : ""}">` +
        `<td><span class="no" style="font-family:var(--mono);color:var(--ink-3)">${cid.slice(0, 2)}</span> ${esc(t.zh || cid)}</td>` +
        `<td>${m.tp ?? 0} / ${m.fp ?? 0} / ${m.fn ?? 0}</td>` +
        `<td class="num">${num(m.precision, 3)}</td><td class="num">${num(m.recall, 3)}</td>` +
        `<td class="num"><b>${num(m.f1, 3)}</b><span class="bar"><i style="width:${((m.f1 || 0) * 100).toFixed(1)}%"></i></span></td>` +
        `<td class="num">${m.evidence_accuracy === null ? "—" : num(m.evidence_accuracy, 3)}</td></tr>`
      );
    })
    .join("");

  const abHead = d.ab.map((r) => `<th>${esc(r.label)}</th>`).join("");
  const abRowF1 = d.ab.map((r) => `<td class="num"><b>${num(r.overall.micro?.f1, 3)}</b></td>`).join("");
  const abRowP = d.ab.map((r) => `<td class="num">${num(r.overall.micro?.precision, 3)}</td>`).join("");
  const abRowR = d.ab.map((r) => `<td class="num">${num(r.overall.micro?.recall, 3)}</td>`).join("");
  const abRowEv = d.ab
    .map((r) => `<td class="num">${r.overall.evidence_accuracy === null ? "—" : num(r.overall.evidence_accuracy, 3)}</td>`)
    .join("");
  const abRowReq = d.ab
    .map((r) => `<td class="num">${r.overall.required_missing_recall === null ? "—" : num(r.overall.required_missing_recall, 3)}</td>`)
    .join("");

  const retHead = d.retrieval.map((r) => `<th>${esc(r.label)}</th>`).join("");
  const retKeys = ["hit@1", "hit@3", "hit@5", "hit@10", "hit@20", "mrr"];
  const retRows = retKeys
    .map(
      (k) =>
        `<tr><td>${k}</td>${d.retrieval.map((r) => `<td class="num">${num(r.overall[k], 3)}</td>`).join("")}</tr>`
    )
    .join("");

  $("eval-body").innerHTML = `
    <section class="eval-section">
      <h3 class="eval-title">全量结果</h3>
      <p class="eval-desc">${esc(String(cfg.contracts ?? 198))} 份 CUAD 官方文本 · 模型 ${esc(cfg.model || "")} · top-k ${esc(cfg.top_k ?? 5)} · 切块 ${esc(cfg.chunk_size || "")}/${esc(cfg.chunk_overlap || "")} · 立场 ${esc(cfg.reader_stance || "customer")}</p>
      <div class="metrics-row">
        ${metricBlock("存在性 F1", num(micro.f1, 3), `精确率 ${num(micro.precision, 3)} ｜ 召回率 ${num(micro.recall, 3)} ｜ 宏平均 ${num(macro.f1, 3)}`)}
        ${metricBlock("证据定位准确率", num(o.evidence_accuracy, 3), `${o.evidence_hit ?? "—"} / ${o.evidence_total ?? "—"} 条有标准答案的条款，证据位置与标准答案重叠`)}
        ${metricBlock("必备条款缺失识别", num(o.required_missing_recall, 3), "01 / 08 / 10 三类必备条款应判缺失时，系统判缺失的比例")}
        ${metricBlock("漏报 / 误报", `${micro.fn ?? "—"} / ${micro.fp ?? "—"}`, "标准答案有而漏判 / 标准答案无而误报")}
      </div>
      <table class="grid">
        <thead><tr><th>条款</th><th>TP / FP / FN</th><th>精确率</th><th>召回率</th><th>F1</th><th>证据定位</th></tr></thead>
        <tbody>${clauseRows}</tbody>
      </table>
    </section>

    <section class="eval-section">
      <h3 class="eval-title">两项增强的前后对比</h3>
      <p class="eval-desc">同一批 30 份合同、同一模型，每次只改变一项。</p>
      <table class="grid">
        <thead><tr><th>指标</th>${abHead}</tr></thead>
        <tbody>
          <tr><td>存在性 F1（微平均）</td>${abRowF1}</tr>
          <tr><td>精确率</td>${abRowP}</tr>
          <tr><td>召回率</td>${abRowR}</tr>
          <tr><td>证据定位准确率</td>${abRowEv}</tr>
          <tr><td>必备条款缺失识别</td>${abRowReq}</tr>
        </tbody>
      </table>
    </section>

    <section class="eval-section">
      <h3 class="eval-title">检索质量</h3>
      <p class="eval-desc">命中 = 该条款的标准答案证据出现在检索结果的前 k 段内。先用向量粗召 20 段，再用精排模型取前 5 段。</p>
      <table class="grid">
        <thead><tr><th>指标</th>${retHead}</tr></thead>
        <tbody>${retRows}</tbody>
      </table>
    </section>

    <section class="eval-section" id="eval-attribution"></section>`;

  renderAttribution();
}

function renderAttribution() {
  const a = state.attribution;
  const box = $("eval-attribution");
  if (!box) return;
  if (!a || !a.available) {
    box.innerHTML = `<h3 class="eval-title">判错归因</h3><p class="eval-desc">暂无归因数据。</p>`;
    return;
  }
  const cases = (rows) =>
    rows
      .slice(0, 14)
      .map(
        (r) =>
          `<tr><td>${esc(r.contract.slice(0, 42))}</td><td>${esc(r.clause.slice(0, 22))}</td>` +
          `<td><span class="layer-tag ${esc(r.layer)}">${esc(r.layer)}</span></td>` +
          `<td style="text-align:left;color:var(--ink-2)">${esc(r.note.slice(0, 96))}</td></tr>`
      )
      .join("");

  box.innerHTML = `
    <h3 class="eval-title">判错归因</h3>
    <p class="eval-desc">扫描 ${a.scanned} 条判定，判错 ${a.wrong} 条，按根因分层。</p>
    <div class="metrics-row">
      ${metricBlock("漏报 · 检索层", String(a.fn["检索层"]), "标准答案里的证据没有被检索到")}
      ${metricBlock("漏报 · 模型层", String(a.fn["模型层"]), "证据已召回，模型仍判不存在")}
      ${metricBlock("误报 · 口径落差", String(a.fp["口径落差"]), "我们的定义与 CUAD 标注口径不一致")}
      ${metricBlock("误报 · 待人工判定", String(a.fp["待人工判定"]), "需人工看原文定夺")}
    </div>
    <div class="two-col">
      <div>
        <span class="section-label">漏报明细</span>
        <table class="grid"><thead><tr><th>合同</th><th>条款</th><th>归因层</th><th>证据</th></tr></thead>
        <tbody>${cases(a.fn.cases)}</tbody></table>
      </div>
      <div>
        <span class="section-label">误报明细</span>
        <table class="grid"><thead><tr><th>合同</th><th>条款</th><th>归因层</th><th>模型引用的证据</th></tr></thead>
        <tbody>${cases(a.fp.cases)}</tbody></table>
      </div>
    </div>
    ${
      a.known_gaps?.length
        ? `<div style="margin-top:32px"><span class="section-label">已知口径落差</span>
           <table class="grid"><thead><tr><th>条款</th><th>说明</th></tr></thead><tbody>${a.known_gaps
             .map((g) => `<tr><td>${esc(g.clause)}</td><td style="text-align:left;color:var(--ink-2)">${esc(g.desc)}</td></tr>`)
             .join("")}</tbody></table></div>`
        : ""
    }`;
}

/* ============================================================
   数据加载
   ============================================================ */
async function loadSample(sampleId) {
  state.sampleId = sampleId;
  const data = await api(`/api/report/${encodeURIComponent(sampleId)}`);
  state.report = data;
  state.text = data.text || "";
  state.titles = data.clause_titles || {};
  state.diff = data.eval_diff || [];
  state.selected = data.report.findings[0]?.clause_id || null;

  $("source-title").textContent = `合同原文 · ${data.report.file_name}`;
  renderSampleSelect();
  renderSummary();
  renderDiffBadge();
  renderClauseList();
  renderClauseDetail();
  renderDocument();
  renderSourceNav();
}

async function init() {
  const [{ samples }, evaluation, attribution] = await Promise.all([
    api("/api/samples"),
    api("/api/eval"),
    api("/api/attribution"),
  ]);
  state.samples = samples;
  state.evaluation = evaluation;
  state.attribution = attribution;

  const cfg = evaluation.final.config || {};
  $("footnote").innerHTML =
    `数据：CUAD（Contract Understanding Atticus Dataset，The Atticus Project，CC BY 4.0）中的官方纯文本；` +
    `审查参数：${esc(cfg.model || "")} / temperature 0 / 重排 bge-reranker-v2-m3（粗召 20 → 精排取 ${esc(cfg.top_k ?? 5)}）/ 切块 ${esc(cfg.chunk_size || "")}-${esc(cfg.chunk_overlap || "")} / 立场 customer。`;

  await loadSample(samples[0].id);
  renderEvaluation();

  $("sample-select").addEventListener("change", (e) => loadSample(e.target.value));
  $("filter").addEventListener("click", (e) => {
    const btn = e.target.closest(".seg");
    if (!btn) return;
    state.filter = btn.dataset.filter;
    document.querySelectorAll(".seg").forEach((el) => el.classList.toggle("is-on", el === btn));
    const rows = visibleFindings();
    if (!rows.some((f) => f.clause_id === state.selected)) state.selected = rows[0]?.clause_id || state.selected;
    renderClauseList();
    renderClauseDetail();
  });
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (btn) showView(btn.dataset.view);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "SELECT" || e.metaKey || e.ctrlKey) return;
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = visibleFindings();
    const i = rows.findIndex((f) => f.clause_id === state.selected);
    const next = e.key === "ArrowDown" ? Math.min(i + 1, rows.length - 1) : Math.max(i - 1, 0);
    if (rows[next]) {
      e.preventDefault();
      selectClause(rows[next].clause_id);
    }
  });

  showView((location.hash || "#report").slice(1));
  // 就绪标志：自动化验证/截图脚本用它判断"数据已渲染"，不用去猜某个元素何时可见
  // （视图切换会让另一视图的元素 `display:none`，"等可见"会误判超时）
  window.__appReady = true;
}

init().catch((err) => {
  document.querySelector("main").innerHTML =
    `<p class="empty" style="padding:48px 0">加载失败：${esc(err.message)}<br>` +
    `确认服务已启动：<code>python -X utf8 webapp/server.py</code>，且已跑过 <code>scripts/build_examples.py</code>。</p>`;
});
