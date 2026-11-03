"use strict";
/* ===== M4 故障注入演练台 ===== */
let drillTimerId = null, drillCatalog = null, drillActive = null;

function drillFmtTs(ts){
  try { return new Date(ts * 1000).toLocaleTimeString(); } catch(e){ return String(ts); }
}
function renderDrillGuard(cat){
  const g = cat.guard || {}, on = cat.enabled, ttl = cat.ttl || {};
  $("drillGuard").innerHTML = "<b>" + (on ? "演练已启用" : "演练已禁用") + "</b> · APP_ENV="
    + esc(cat.app_env) + " · 白名单 " + (g.whitelist || []).length + " 类 · 单演练互斥"
    + " · TTL " + esc(ttl.min_s) + "–" + esc(ttl.max_s) + "s（默认 "
    + esc(ttl.default_s) + "s）自动复位<br>写端点："
    + (g.write_endpoints || []).map(esc).join(" / ")
    + (on ? "" : "<br><b>" + esc(cat.reason) + "</b>");
}
function renderDrillCards(cat){
  drillCatalog = cat;
  const enabled = !!cat.enabled;
  $("drillCards").innerHTML = (cat.allowed_kinds || []).map(function(k){
    const isOn = drillActive && drillActive.kind === k.kind;
    return '<div class="drill-card' + (isOn ? " active" : "") + '">'
      + '<h4>' + esc(k.label) + ' <span class="pill ' + (k.stateless ? "info" : "warn") + '">'
      + (k.stateless ? "对照组" : "改状态") + '</span></h4>'
      + '<div class="d-row"><b>注入</b>：' + esc(k.inject) + '</div>'
      + '<div class="d-row"><b>预期</b>：' + esc(k.expect) + '</div>'
      + '<div class="d-act"><span class="small muted mono">' + esc(k.kind) + '</span>'
      + '<button class="btn sm" data-drill="' + esc(k.kind) + '"'
      + (enabled ? "" : " disabled") + '>' + (isOn ? "重新启动" : "启动") + '</button></div></div>';
  }).join("");
}
function renderDrillActive(a, enabled){
  drillActive = a;
  $("drillProbeBtn").disabled = !a || !enabled;
  $("drillStopBtn").disabled = !a || !enabled;
  $("drillMiniDot").className = "dot " + (a ? "off" : "on");
  if(!a){
    $("drillActive").className = "drill-active off";
    $("drillActive").textContent = "当前无演练 · 运行时处于正常状态";
  } else {
    $("drillActive").className = "drill-active";
    $("drillActive").innerHTML = '<b>' + esc(a.label) + " · " + esc(a.kind) + '</b><br>'
      + '会话 <span class="mono">' + esc(a.session_id) + '</span> · 剩余 <b>'
      + esc(a.remaining_s) + 's</b> / ' + esc(a.ttl_s) + 's 后自动复位<br>'
      + '<span class="mono">' + esc(a.drill_id) + '</span> · ' + esc(a.inject);
  }
  if(drillCatalog) renderDrillCards(drillCatalog);
}
function renderDrillLog(log){
  if(!log || !log.length){ $("drillLog").innerHTML = '<div class="empty">暂无演练事件</div>'; return; }
  $("drillLog").innerHTML = log.map(function(x){
    return '<div class="lg"><span class="ts">' + esc(drillFmtTs(x.ts)) + '</span>'
      + '<span class="ev">' + esc(x.event) + '</span>'
      + '<span class="mono">' + esc(x.kind || "—") + '</span>'
      + '<span>' + esc(x.detail) + '</span></div>';
  }).join("");
}
function renderDrillProbe(p){
  const r = p.result || {}, b = p.before || {}, a = p.after || {};
  const rows = [
    ["演练", p.label + " / " + p.kind],
    ["业务 HTTP 状态", p.http_status],
    ["status", r.status],
    ["error_code", r.error_code || "—"],
    ["degraded", String(r.degraded)],
    ["轮次 / 工具步", r.rounds + " / " + r.steps],
    ["延迟", r.latency_ms + " ms"],
    ["熔断", b.breaker + " → " + a.breaker],
    ["日累计成本", b.budget_spent_cny + " → " + a.budget_spent_cny + " CNY"],
    ["LLM 适配器", b.llm + " → " + a.llm],
    ["限流命中", b.limiter_hits + " → " + a.limiter_hits]
  ];
  let html = '<div style="margin-bottom:8px"><span class="pill '
    + (p.matched ? "ok" : "err") + '">' + (p.matched ? "符合预期" : "与预期不符")
    + '</span> <span class="small muted">' + esc(p.expect) + '</span></div>';
  html += '<table class="kv-table">' + rows.map(function(f){
    return '<tr><td>' + esc(f[0]) + '</td><td>' + esc(f[1]) + '</td></tr>';
  }).join("") + '</table>';
  html += '<div class="small muted" style="margin-top:8px">回复：' + esc(r.reply) + '</div>';
  html += '<div class="small muted" style="margin-top:4px">run <span class="mono">'
    + esc(String(r.run_id || "").slice(0,8))
    + '</span> · 表中 HTTP 为演练判定值，演练端点本身恒返回 200</div>';
  $("drillProbeResult").innerHTML = html;
}
async function loadDrillCatalog(){
  try {
    const r = await api("/api/drill/catalog");
    if(!r.ok || !r.body) return;
    renderDrillGuard(r.body);
    renderDrillCards(r.body);
  } catch(e){ $("drillUpdated").textContent = "目录加载失败：" + e.message; }
}
async function loadDrillStatus(){
  try {
    const r = await api("/api/drill/status");
    if(!r.ok || !r.body){ $("drillUpdated").textContent = "刷新失败 HTTP " + r.status; return; }
    const d = r.body;
    renderDrillActive(d.active, d.enabled);
    renderDrillLog(d.log);
    $("drillUpdated").textContent = "更新于 " + new Date().toLocaleTimeString()
      + " · 探测 " + esc(d.probes) + " 次";
  } catch(e){ $("drillUpdated").textContent = "刷新失败：" + e.message; }
}
async function startDrill(kind){
  try {
    const ttl = Number($("drillTtl").value) || 20;
    const r = await api("/api/drill/start", {
      method: "POST", body: JSON.stringify({kind: kind, ttl_s: ttl})
    });
    if(!r.ok || !r.body || r.body.error_code){
      showBanner("演练启动失败：HTTP " + r.status + " "
        + ((r.body && (r.body.error_code || r.body.error)) || ""));
      return;
    }
    hideBanner();
    $("drillProbeResult").innerHTML = '<div class="empty">已注入，点「发一条探测请求」观察现象</div>';
    await loadDrillStatus();
  } catch(e){ showBanner("演练启动失败：" + e.message); }
}
async function probeDrill(){
  $("drillProbeBtn").disabled = true;
  try {
    const r = await api("/api/drill/probe", {method: "POST", body: "{}"});
    if(!r.ok || !r.body || r.body.error_code){
      showBanner("探测失败：HTTP " + r.status + " "
        + ((r.body && (r.body.error_code || r.body.error)) || ""));
    } else {
      hideBanner();
      renderDrillProbe(r.body);
    }
    await loadDrillStatus();
    loadRuntime();
  } catch(e){ showBanner("探测失败：" + e.message); }
  finally { $("drillProbeBtn").disabled = false; }
}
async function stopDrill(){
  try {
    const r = await api("/api/drill/stop", {method: "POST", body: "{}"});
    if(!r.ok) showBanner("复位失败：HTTP " + r.status);
    else hideBanner();
    await loadDrillStatus();
    loadRuntime();
    pollHealth();
  } catch(e){ showBanner("复位失败：" + e.message); }
}
function setDrillAuto(on){
  if(drillTimerId){ clearInterval(drillTimerId); drillTimerId = null; }
  if(on) drillTimerId = setInterval(function(){
    if($("drillOverlay").classList.contains("show")) loadDrillStatus();
  }, 2000);
}
function openDrill(){
  $("drillOverlay").classList.add("show");
  $("drillOverlay").setAttribute("aria-hidden", "false");
  loadDrillCatalog();
  loadDrillStatus();
  setDrillAuto(true);
}
function closeDrill(){
  $("drillOverlay").classList.remove("show");
  $("drillOverlay").setAttribute("aria-hidden", "true");
  setDrillAuto(false);
}
$("drillBtn").addEventListener("click", openDrill);
$("drillCloseBtn").addEventListener("click", closeDrill);
$("drillRefreshBtn").addEventListener("click", function(){ loadDrillCatalog(); loadDrillStatus(); });
$("drillResetBtn").addEventListener("click", stopDrill);
$("drillStopBtn").addEventListener("click", stopDrill);
$("drillProbeBtn").addEventListener("click", probeDrill);
$("drillCards").addEventListener("click", function(e){
  const b = e.target.closest("[data-drill]");
  if(b && !b.disabled) startDrill(b.getAttribute("data-drill"));
});
$("drillOverlay").addEventListener("click", function(e){ if(e.target === this) closeDrill(); });
document.addEventListener("keydown", function(e){
  if(e.key === "Escape" && $("drillOverlay").classList.contains("show")) closeDrill();
});

(function init(){
  const theme = localStorage.getItem("prodagent_theme") || "light";
  document.documentElement.setAttribute("data-theme", theme);
  $("themeBtn").textContent = theme === "dark" ? "浅色" : "深色";
  const sid = localStorage.getItem("prodagent_session") || "demo-01";
  $("sessionInput").value = sid;
  $("chatSession").textContent = "session: " + sid;
  $("tokenInput").value = localStorage.getItem("prodagent_token") || "";
  $("idemKey").value = "ui-demo-key-001";
  loadMeta().then(function(){ loadRecent(); pollHealth(); loadRuntime(); });
  loadDrillStatus();
  setInterval(pollHealth, 10000);
})();
