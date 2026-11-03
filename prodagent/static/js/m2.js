"use strict";
/* ===== M2 运行时状态面板（只读 GET /api/stats） ===== */
const RT_LABELS = {
  status_ok:"正常完成", status_rejected:"请求被拒", status_degraded:"降级回复", status_error:"内部错误",
  idempotent_hit:"幂等命中", output_redacted:"输出脱敏",
  guard_rejected_PROMPT_INJECTION:"提示注入拦截", guard_rejected_EMPTY_MESSAGE:"空消息拦截",
  guard_rejected_MESSAGE_TOO_LONG:"超长消息拦截", tool_blocked:"工具被拒绝",
  tool_timeout:"工具超时", throttled_RateLimited:"单会话限流",
  throttled_BudgetExceeded:"日预算拒绝", throttled_Overloaded:"并发过载"
};
const RT_CFG = [
  ["max_rounds","ReAct 最大轮数"], ["request_timeout_s","上游超时 s"], ["tool_timeout_s","工具超时 s"],
  ["max_concurrency","全局并发上限"], ["per_session_quota","单会话 60s 配额"],
  ["daily_budget_cny","日预算 ¥"], ["price_in_cny_per_1k","输入单价 ¥/1K"], ["price_out_cny_per_1k","输出单价 ¥/1K"],
  ["circuit_threshold","熔断失败阈值"], ["circuit_cooldown_s","熔断冷却 s"],
  ["retry_max","最大重试次数"], ["retry_base_ms","退避基值 ms"],
  ["max_message_chars","消息长度上限"], ["session_ttl_s","会话 TTL s"],
  ["auth_required","鉴权已启用"]
];
let rtTimerId = null;

function rtFmt(v){ return (v === null || v === undefined || v === "") ? "—" : String(v); }
function rtBar(label, used, total, right){
  const u = Number(used) || 0, t = Number(total) || 0;
  const pct = t > 0 ? Math.min(100, Math.max(0, u / t * 100)) : 0;
  const cls = pct >= 90 ? "err" : (pct >= 70 ? "warn" : "");
  return '<div class="gauge"><div class="lab"><span>' + esc(label) + '</span><span class="mono">' + esc(right) + '</span></div>'
    + '<div class="bar ' + cls + '"><span style="width:' + pct.toFixed(1) + '%"></span></div></div>';
}
function rtRow(k, v){ return '<div class="counter"><span class="n">' + esc(k) + '</span><span class="val">' + esc(v) + '</span></div>'; }

function renderRuntime(d){
  const br = d.breaker || {}, bg = d.budget || {}, cc = d.concurrency || {},
        lm = d.limiter || {}, mt = d.metrics || {}, st = d.store || {},
        pr = d.process || {}, rt = d.runtime || {};
  const stateMap = {closed:["CLOSED · 正常放行","ok"], open:["OPEN · 熔断中","err"], half_open:["HALF_OPEN · 探测中","warn"]};
  const sm = stateMap[br.state] || [rtFmt(br.state),"info"];
  $("rtStatePill").className = "pill " + sm[1];
  $("rtStatePill").textContent = sm[0];
  $("rtMiniDot").className = "dot " + (br.state === "closed" ? "on" : (br.state === "open" ? "off" : "warn"));
  $("rtMiniBudget").textContent = "¥" + Number(bg.used_pct || 0).toFixed(1) + "%";

  const byStatus = st.by_status || {};
  const totalRuns = Object.keys(byStatus).reduce(function(a,k){ return a + (Number(byStatus[k]) || 0); }, 0);
  const okRuns = Number(byStatus.ok || 0);
  const okPct = totalRuns > 0 ? (okRuns / totalRuns * 100).toFixed(1) : null;
  const cards = [
    ["熔断状态", String(br.state || "—").toUpperCase(), "连续失败 " + rtFmt(br.failures) + " / " + rtFmt(br.threshold) + " · 冷却剩余 " + rtFmt(br.cooldown_remaining_s) + "s"],
    ["日预算", "¥" + rtFmt(bg.spent_cny) + " / ¥" + rtFmt(bg.limit_cny), "已用 " + rtFmt(bg.used_pct) + "% · 剩余 ¥" + rtFmt(bg.remaining_cny) + (bg.exceeded ? " · 已超限" : "")],
    ["并发闸", rtFmt(cc.in_flight) + " / " + rtFmt(cc.limit), "峰值 " + rtFmt(cc.peak) + " · 获取超时 " + rtFmt(cc.acquire_timeout_s) + "s"],
    ["请求成功率", (okPct === null ? "—" : okPct + "%"), "ok " + okRuns + " / 总 " + totalRuns + " 次落库运行"]
  ];
  $("rtCards").innerHTML = cards.map(function(c){
    return '<div class="rt-card"><div class="k">' + esc(c[0]) + '</div><div class="v">' + esc(c[1]) + '</div><div class="sub">' + esc(c[2]) + '</div></div>';
  }).join("");

  ["fsmClosed","fsmOpen","fsmHalf"].forEach(function(id){ $(id).classList.remove("active"); });
  const activeId = br.state === "open" ? "fsmOpen" : (br.state === "half_open" ? "fsmHalf" : "fsmClosed");
  $(activeId).classList.add("active");
  $("rtFsmInfo").textContent = "当前 " + String(br.state || "—").toUpperCase()
    + " · 连续失败 " + rtFmt(br.failures) + "/" + rtFmt(br.threshold)
    + " · 达到 " + rtFmt(br.threshold) + " 次即 OPEN，冷却 " + rtFmt(br.cooldown_s)
    + "s 后放一个 HALF_OPEN 探测；探测成功闭合，失败重新计时。"
    + (br.state === "open" ? " 当前冷却剩余 " + rtFmt(br.cooldown_remaining_s) + "s。" : "");

  const sid = $("sessionInput").value.trim() || "default";
  let myHits = 0;
  (lm.by_session || []).forEach(function(x){ if(x.session_id === sid) myHits = x.hits; });
  const g = [];
  g.push(rtBar("日预算使用", bg.spent_cny, bg.limit_cny, "¥" + rtFmt(bg.spent_cny) + " / ¥" + rtFmt(bg.limit_cny)));
  g.push(rtBar("全局并发占用", cc.in_flight, cc.limit, rtFmt(cc.in_flight) + " / " + rtFmt(cc.limit)));
  g.push(rtBar("当前会话滑动窗口限流", myHits, lm.max_calls, sid + " " + myHits + " / " + rtFmt(lm.max_calls) + " 次 / " + rtFmt(lm.window_s) + "s"));
  const others = (lm.by_session || []).slice(0, 5).map(function(x){ return esc(x.session_id) + " " + x.hits; }).join(" · ");
  g.push('<div class="small muted">活跃会话 ' + rtFmt(lm.active_sessions) + " 个 · 窗口内总请求 " + rtFmt(lm.total_hits) + " 次" + (others ? "　|　Top: " + others : "") + "</div>");
  $("rtGauges").innerHTML = g.join("");

  const counters = mt.counters || {};
  const keys = Object.keys(counters).sort(function(a,b){ return (Number(counters[b]) || 0) - (Number(counters[a]) || 0); });
  $("rtCounters").innerHTML = keys.length
    ? keys.map(function(k){ return rtRow(RT_LABELS[k] || k, counters[k]); }).join("")
    : '<div class="empty">还没有请求，计数器为空</div>';

  const lat = mt.latency || {};
  const rows = [];
  Object.keys(lat).forEach(function(k){
    const v = lat[k] || {};
    rows.push(rtRow("延迟 " + k + " · p50", rtFmt(v.p50_ms) + " ms"));
    rows.push(rtRow("延迟 " + k + " · p95", rtFmt(v.p95_ms) + " ms"));
    rows.push(rtRow("延迟 " + k + " · max", rtFmt(v.max_ms) + " ms"));
    rows.push(rtRow("延迟 " + k + " · 样本", rtFmt(v.count)));
  });
  rows.push(rtRow("落库工具步骤 steps", rtFmt(st.steps)));
  rows.push(rtRow("落库会话 sessions", rtFmt(st.sessions)));
  rows.push(rtRow("已建工单 tickets", rtFmt(st.tickets)));
  rows.push(rtRow("累计成本 total_cost", "¥" + rtFmt(st.total_cost_cny)));
  const avg = st.avg_latency_ms || {};
  Object.keys(avg).forEach(function(k){ rows.push(rtRow("落库均时延 " + k, rtFmt(avg[k]) + " ms")); });
  $("rtLatency").innerHTML = rows.join("") || '<div class="empty">—</div>';

  $("rtConfig").innerHTML = RT_CFG.map(function(p){
    let v = rt[p[0]];
    if(typeof v === "boolean") v = v ? "是" : "否";
    return "<tr><td>" + esc(p[1]) + '</td><td>' + esc(rtFmt(v)) + "</td></tr>";
  }).join("");

  const tsTxt = d.ts ? new Date(Number(d.ts) * 1000).toLocaleTimeString() : "—";
  $("rtProcess").textContent = "进程 " + rtFmt(pr.app_env) + " / " + rtFmt(pr.llm_backend) + " / " + rtFmt(pr.model)
    + " · 已运行 " + rtFmt(pr.uptime_s) + "s · 采样 " + tsTxt + " · UI " + rtFmt(d.ui_module);
}
async function loadRuntime(){
  try {
    const r = await api("/api/stats");
    if(!r.ok || !r.body){ $("rtUpdated").textContent = "刷新失败 HTTP " + r.status; return; }
    renderRuntime(r.body);
    $("rtUpdated").textContent = "更新于 " + new Date().toLocaleTimeString();
  } catch(e){
    $("rtUpdated").textContent = "刷新失败：" + e.message;
  }
}
function setRtAuto(on){
  if(rtTimerId){ clearInterval(rtTimerId); rtTimerId = null; }
  if(on) rtTimerId = setInterval(function(){
    if($("rtOverlay").classList.contains("show")) loadRuntime();
  }, 2000);
}
function openRuntime(){
  $("rtOverlay").classList.add("show");
  $("rtOverlay").setAttribute("aria-hidden", "false");
  loadRuntime();
  setRtAuto($("rtAuto").checked);
}
function closeRuntime(){
  $("rtOverlay").classList.remove("show");
  $("rtOverlay").setAttribute("aria-hidden", "true");
  setRtAuto(false);
}
$("runtimeBtn").addEventListener("click", openRuntime);
$("rtCloseBtn").addEventListener("click", closeRuntime);
$("rtRefreshBtn").addEventListener("click", loadRuntime);
$("rtAuto").addEventListener("change", function(){ if($("rtOverlay").classList.contains("show")) setRtAuto(this.checked); });
$("rtOverlay").addEventListener("click", function(e){ if(e.target === this) closeRuntime(); });
document.addEventListener("keydown", function(e){
  if(e.key === "Escape" && $("rtOverlay").classList.contains("show")) closeRuntime();
});

