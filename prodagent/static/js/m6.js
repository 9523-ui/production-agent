"use strict";
/* ===== M6 交付视图：版本 / 命令 / 交付物 / 证据，一屏只读 =====
   数据源：GET /api/deliver/manifest（唯一端点，无任何写动作）。
   dev / staging / prod 均可查看；浮层接线走 app.js 的 registerPanel。 */

const DELIVER_PANEL = registerPanel({
  btnId: "deliverBtn",
  overlayId: "deliverOverlay",
  closeId: "deliverCloseBtn",
  autoId: "deliverAuto",
  pollMs: 5000,
  pollFn: pollDeliver,
  onOpen: openDeliver,
  onClose: closeDeliver
});

function openDeliver(){
  $("deliverOverlay").classList.add("show");
  loadDeliverAll();
}
function closeDeliver(){
  $("deliverOverlay").classList.remove("show");
}

async function loadDeliverAll(){
  const r = await api("/api/deliver/manifest");
  if(!r.ok) return;
  renderDeliverVersion(r.body);
  renderDeliverCommands(r.body);
  renderDeliverFiles(r.body);
  renderDeliverStore(r.body);
  renderDeliverLinks(r.body);
  $("deliverUpdated").textContent = "更新于 " + new Date().toLocaleTimeString();
}

async function pollDeliver(){
  loadDeliverAll();
}

function deliverDur(ms){
  if(ms == null) return "—";
  return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
}

function renderDeliverVersion(m){
  const env = m.env || {};
  const src = m.version_source === "VERSION"
    ? 'VERSION 文件' : '内置回退号（VERSION 缺失）';
  const cl = m.changelog_head
    ? "最近变更：" + m.changelog_head
    : "暂无 CHANGELOG（收口阶段生成）";
  const rows = [
    ["运行环境 APP_ENV", env.app_env],
    ["LLM 后端", env.llm_backend],
    ["模型", env.model],
    ["鉴权 AUTH_TOKEN", env.auth_required ? "已启用" : "未启用"],
    ["监听", (env.host || "—") + " : " + (env.port || "—")]
  ];
  $("deliverVersion").innerHTML =
    '<div class="deliver-ver"><span class="num mono">' + esc(m.version) +
      '</span><span class="deliver-src">' + esc(src) + "</span>" +
      '<div class="deliver-cl">' + esc(cl) + "</div></div>" +
    '<div class="deliver-kvs">' + rows.map(function(kv){
      return '<div class="deliver-kv"><span>' + esc(kv[0]) +
        '</span><b class="mono">' + esc(kv[1]) + "</b></div>";
    }).join("") + "</div>";
}

function renderDeliverCommands(m){
  const cmds = m.commands || [];
  $("deliverCommands").innerHTML = cmds.map(function(c){
    return '<div class="deliver-cmd"><div class="t">' + esc(c.label) +
      '<span class="note">' + esc(c.note || "") + "</span></div>" +
      '<code class="line mono">' + esc(c.command) + "</code></div>";
  }).join("") || '<div class="empty">无命令</div>';
}

function renderDeliverFiles(m){
  const files = m.delivery_files || [];
  $("deliverTbody").innerHTML = files.map(function(f){
    const miss = !f.exists;
    const size = miss ? "缺失" :
      (f.size_bytes >= 1024 ? (f.size_bytes / 1024).toFixed(1) + " KB"
        : f.size_bytes + " B");
    return '<tr class="row' + (miss ? " miss" : "") + '"><td class="mono">' +
      esc(f.path) + '</td><td>' + esc(f.desc || "") +
      '</td><td class="numcell' + (miss ? " err" : "") + '">' + esc(size) +
      "</td></tr>";
  }).join("");
}

function renderDeliverStore(m){
  const ds = m.datastore || {};
  const ev = m.evidence || {};
  const counts = ds.counts || {};
  const last = ev.last_run;
  const score = ev.report_available
    ? '<span class="num ' + (ev.failed === 0 ? "ok" : "err") + '">' +
      esc(ev.passed) + " / " + esc(ev.total) + "</span>"
    : '<span class="num">—</span>';
  const lastLine = last
    ? "第 " + esc(last.id) + " 次 · " + esc(last.passed) + "/" +
      esc(last.total) + " · exit " + esc(last.exit_code) + " · " +
      deliverDur(last.duration_ms)
    : "尚无落库历史";
  const rows = [
    ["数据库", esc(ds.db_path) + " · schema v" + esc(ds.schema_version) + " · WAL"],
    ["落库规模", "runs " + esc(counts.runs || 0) + " · steps " +
      esc(counts.steps || 0) + " · sessions " + esc(counts.sessions || 0) +
      " · eval_runs " + esc(counts.eval_runs || 0)],
    ["验收历史", "共 " + esc(ev.eval_runs_count || 0) + " 次 · " + lastLine]
  ];
  $("deliverStore").innerHTML =
    '<div class="deliver-score">' + score +
      '<span class="sub">离线验收最近一次 · data/acceptance.json' +
      (ev.report_available ? "" : "（尚未生成）") + "</span></div>" +
    '<div class="deliver-kvs">' + rows.map(function(kv){
      return '<div class="deliver-kv"><span>' + esc(kv[0]) + "</span><b>" +
        kv[1] + "</b></div>";
    }).join("") + "</div>";
}

function renderDeliverLinks(m){
  const links = m.links || {};
  const names = {
    console: "控制台", openapi: "OpenAPI 文档", meta: "运行元信息",
    metrics: "Prometheus 指标", healthz: "健康检查", readyz: "就绪检查"
  };
  $("deliverLinks").innerHTML = Object.keys(links).map(function(k){
    return '<a class="lk" href="' + esc(links[k]) + '" target="_blank" rel="noreferrer">' +
      esc(names[k] || k) + ' <span class="mono">' + esc(links[k]) + "</span></a>";
  }).join("");
}

$("deliverRefreshBtn").addEventListener("click", loadDeliverAll);
