"use strict";
function addMessage(role, text, metaHtml, cls){
  const wrap = document.createElement("div");
  wrap.className = "msg " + role + (cls ? " " + cls : "");
  const av = document.createElement("div");
  av.className = "avatar";
  if(role === "user"){
    av.textContent = "我";
  } else {
    const img = document.createElement("img");
    img.src = "/static/img/bot-avatar.jpg";
    img.alt = "客服";
    av.appendChild(img);
  }
  const bub = document.createElement("div");
  bub.className = "bubble";
  const body = document.createElement("div");
  body.textContent = text;
  bub.appendChild(body);
  if(metaHtml){
    const m = document.createElement("div");
    m.className = "meta";
    m.innerHTML = metaHtml;
    bub.appendChild(m);
  }
  wrap.appendChild(av);
  wrap.appendChild(bub);
  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
  return wrap;
}
function metaHtmlFor(r){
  const out = [];
  out.push('<span class="pill ' + statusCls(r.status) + '">' + esc(statusLabel(r.status)) + "</span>");
  if(r.cached) out.push('<span class="pill info">幂等命中</span>');
  if(r.degraded) out.push('<span class="pill warn">降级</span>');
  if(r.error_code) out.push('<span class="pill err">' + esc(r.error_code) + "</span>");
  out.push("<span>" + esc(r.rounds) + " 轮 · " + esc(r.latency_ms) + " ms · ¥" + esc(Number(r.cost_cny || 0).toFixed(6)) + "</span>");
  out.push('<button class="btn sm" data-copy-enc="' + encodeURIComponent(r.reply || "") + '">复制回复</button>');
  out.push('<span class="mono">run ' + esc(String(r.run_id || "").slice(0,8)) + "</span>");
  return out.join("");
}

function showTrace(meta, steps, hint){
  $("traceTitle").textContent = meta.title || "执行轨迹";
  $("traceHint").textContent = hint || "";
  const cells = [
    ["run_id", meta.run_id], ["trace_id", meta.trace_id],
    ["status", meta.status], ["rounds", meta.rounds],
    ["latency_ms", meta.latency_ms], ["cost_cny", meta.cost_cny],
    ["cached", meta.cached], ["error_code", meta.error_code]
  ];
  $("traceMeta").innerHTML = cells.map(function(p){
    const v = p[1];
    const shown = (v === null || v === undefined || v === "") ? "—" : (typeof v === "boolean" ? (v ? "true" : "false") : v);
    return '<div class="meta-cell"><div class="k">' + esc(p[0]) + '</div><div class="v">' + esc(shown) + "</div></div>";
  }).join("");
  if(!steps || !steps.length){
    $("traceTimeline").innerHTML = '<div class="empty">本次运行没有工具调用轨迹（直接回复 / 请求被拒 / 降级 / 命中幂等缓存）</div>';
    return;
  }
  const maxMs = Math.max(1, ...steps.map(s => Number(s.ms) || 0));
  $("traceTimeline").innerHTML = steps.map(function(s, i){
    const args = typeof s.args === "string" ? s.args : JSON.stringify(s.args || {});
    const ok = s.ok !== false && s.ok !== 0;
    const pct = Math.max(4, Math.round((Number(s.ms) || 0) / maxMs * 100));
    return '<div class="step' + (ok ? "" : " bad") + '"><div class="step-card">'
      + '<div class="step-name">' + (i + 1) + ". " + esc(s.tool)
      + ' <span class="pill ' + (ok ? "ok" : "err") + '">' + (ok ? "成功" : "被拒/失败") + "</span>"
      + ' <span class="mono muted">' + esc(s.ms) + " ms</span></div>"
      + '<div class="kv">args: ' + esc(args) + "</div>"
      + '<div class="kv">result: ' + esc(s.result) + "</div>"
      + '<div class="msbar"><span style="width:' + pct + '%"></span></div>'
      + "</div></div>";
  }).join("");
}
function clearTrace(){
  $("traceTitle").textContent = "等待一次对话";
  $("traceHint").textContent = "发一条消息后，这里按 ReAct 轮次显示每一步工具调用。";
  $("traceMeta").innerHTML = "";
  $("traceTimeline").innerHTML = '<div class="empty">还没有轨迹数据</div>';
}
function traceFromResult(r){
  return {
    title: "本次运行轨迹",
    hint: r.cached ? "命中幂等缓存：直接返回历史结果，本次没有重新执行工具。" : "工具调用按 ReAct 轮次顺序排列，右侧数值为该步耗时。",
    meta: {run_id:r.run_id, trace_id:r.trace_id, status:r.status, rounds:r.rounds,
           latency_ms:r.latency_ms, cost_cny:Number(r.cost_cny || 0).toFixed(6),
           cached:!!r.cached, error_code:r.error_code || "—"},
    steps: (r.trace || []).map(s => ({tool:s.tool, args:s.args, result:s.result, ok:s.ok, ms:s.ms}))
  };
}
function traceFromRun(run, steps){
  return {
    title: "回放 · " + String(run.run_id).slice(0,8),
    hint: "请求：" + (run.request_text || "") + (run.reply_text ? "　|　回复：" + run.reply_text : ""),
    meta: {run_id:run.run_id, trace_id:run.trace_id, status:run.status, rounds:run.rounds,
           latency_ms:run.latency_ms, cost_cny:Number(run.cost_cny || 0).toFixed(6),
           cached:false, error_code:run.error_code || "—"},
    steps: (steps || []).map(function(s){
      let a = s.args_json;
      try { a = JSON.stringify(JSON.parse(s.args_json)); } catch(e){}
      return {tool:s.tool, args:a, result:s.result, ok:!!s.ok, ms:s.ms};
    })
  };
}

function setBusy(on){
  $("sendBtn").disabled = on;
  $("busyIndicator").style.display = on ? "flex" : "none";
  if(on){
    busyStart = Date.now();
    $("busyTimer").textContent = "0 ms";
    busyTimerId = setInterval(function(){ $("busyTimer").textContent = (Date.now() - busyStart) + " ms"; }, 100);
  } else if(busyTimerId){
    clearInterval(busyTimerId);
    busyTimerId = null;
  }
}
async function send(){
  const text = $("composerInput").value.trim();
  if(!text || $("sendBtn").disabled) return;
  $("composerInput").value = "";
  addMessage("user", text, "");
  setBusy(true);
  const payload = {message: text, session_id: $("sessionInput").value.trim() || "default"};
  if($("idemToggle").checked){
    payload.idempotency_key = $("idemKey").value.trim() || "ui-demo-key";
  }
  try {
    const r = await api("/api/chat", {method:"POST", body: JSON.stringify(payload)});
    const d = r.body || {};
    if(!r.ok && !d.status){
      addMessage("agent", "请求失败：HTTP " + r.status + " " + (d.error || d.detail || ""), "", "error");
      return;
    }
    addMessage("agent", d.reply || "(无回复)", metaHtmlFor(d));
    const t = traceFromResult(d);
    showTrace(t.meta, t.steps, t.hint);
    $("lastRun").textContent = d.run_id || "—";
    $("lastLatency").textContent = (d.latency_ms == null ? "—" : d.latency_ms) + " ms";
    await loadRecent();
    loadRuntime();
    refreshHistoryIfOpen();
  } catch(e){
    addMessage("agent", "无法连接后端：" + e.message + "。请先启动 python -m prodagent.app", "", "error");
  } finally {
    setBusy(false);
  }
}

async function loadMeta(){
  try {
    const r = await api("/api/meta");
    if(!r.ok || !r.body){ showBanner("后端未连接或鉴权失败：请先启动 python -m prodagent.app" + (API_BASE ? "（当前指向 " + API_BASE + "）" : "")); return; }
    const m = r.body;
    $("envBadge").textContent = m.app_env;
    $("backendBadge").textContent = m.llm_backend;
    $("modelBadge").textContent = m.model;
    $("toolChips").innerHTML = (m.allowed_tools || []).map(t => '<span class="pill info mono">' + esc(t) + "</span>").join("");
    $("metaSummary").textContent = "MAX_ROUNDS " + m.max_rounds + " · 超时 " + m.request_timeout_s + "s · 并发 " + m.max_concurrency + " · 预算 ¥" + m.daily_budget_cny + " · 工具 " + (m.allowed_tools || []).length + " 个";
    hideBanner();
    if(!welcomed){
      welcomed = true;
      addMessage("agent", "控制台已连接。发一条消息就能看到 ReAct 轮次与工具调用轨迹；左侧可切换会话、快捷试跑、点最近运行回放。", "");
    }
  } catch(e){
    showBanner("无法连接后端：" + e.message + "。请先启动 python -m prodagent.app");
  }
}
async function loadRecent(){
  try {
    const r = await api("/api/runs?limit=15");
    const runs = (r.body && r.body.runs) || [];
    if(!runs.length){ $("recentList").innerHTML = '<div class="empty">还没有运行记录，发一条消息试试</div>'; return; }
    $("recentList").innerHTML = runs.map(function(x){
      return '<div class="recent-item" data-run="' + esc(x.run_id) + '">'
        + '<div class="r-top"><span class="mono">' + esc(String(x.run_id).slice(0,8)) + "</span>"
        + '<span class="pill ' + statusCls(x.status) + '">' + esc(statusLabel(x.status)) + "</span></div>"
        + '<div class="r-msg">' + esc(x.request_text) + "</div></div>";
    }).join("");
  } catch(e){}
}
async function pollHealth(){
  try {
    const r = await api("/readyz");
    const ok = r.ok && r.body && r.body.status === "ready";
    $("healthDot").className = "dot " + (ok ? "on" : "off");
    $("healthText").textContent = ok ? "就绪" : "未就绪";
  } catch(e){
    $("healthDot").className = "dot off";
    $("healthText").textContent = "离线";
  }
  if(!$("rtOverlay").classList.contains("show")) loadRuntime();
}
function copyText(text, btn){
  const done = function(){
    const old = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(function(){ btn.textContent = old; }, 1200);
  };
  const fallback = function(){
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch(e){}
    document.body.removeChild(ta);
  };
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(done, fallback);
  } else { fallback(); }
}

document.addEventListener("click", async function(e){
  const cp = e.target.closest("[data-copy-enc]");
  if(cp){ copyText(decodeURIComponent(cp.getAttribute("data-copy-enc") || ""), cp); return; }
  const item = e.target.closest("[data-run]");
  if(item){
    const id = item.getAttribute("data-run");
    const r = await api("/api/runs/" + encodeURIComponent(id));
    if(!r.ok || !r.body || !r.body.run) return;
    const t = traceFromRun(r.body.run, r.body.steps);
    showTrace(t.meta, t.steps, t.hint);
    $("lastRun").textContent = id;
  }
});
$("quickList").addEventListener("click", function(e){
  const b = e.target.closest("[data-q]");
  if(!b) return;
  $("composerInput").value = b.getAttribute("data-q");
  send();
});
$("sendBtn").addEventListener("click", send);
$("composerInput").addEventListener("keydown", function(e){
  if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); send(); }
});
$("newSessionBtn").addEventListener("click", function(){
  const sid = "s-" + Math.random().toString(36).slice(2,8);
  $("sessionInput").value = sid;
  $("chatSession").textContent = "session: " + sid;
  localStorage.setItem("prodagent_session", sid);
  $("messages").innerHTML = "";
  addMessage("agent", "已新建会话 " + sid + "，上下文从零开始。", "");
});
$("sessionInput").addEventListener("change", function(){
  const sid = $("sessionInput").value.trim() || "default";
  $("chatSession").textContent = "session: " + sid;
  localStorage.setItem("prodagent_session", sid);
});
$("resetBtn").addEventListener("click", async function(){
  const sid = $("sessionInput").value.trim() || "default";
  const r = await api("/api/reset", {method:"POST", body: JSON.stringify({session_id: sid})});
  addMessage("agent", r.ok ? "已清空会话 " + sid + " 的短期上下文（审计记录保留）。" : "重置失败：HTTP " + r.status, "");
});
$("refreshBtn").addEventListener("click", loadRecent);
$("clearTraceBtn").addEventListener("click", clearTrace);
$("tokenSaveBtn").addEventListener("click", function(){
  localStorage.setItem("prodagent_token", $("tokenInput").value.trim());
  loadMeta().then(function(){ loadRecent(); pollHealth(); loadRuntime(); });
});
$("themeBtn").addEventListener("click", function(){
  const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", cur);
  localStorage.setItem("prodagent_theme", cur);
  $("themeBtn").textContent = cur === "dark" ? "浅色" : "深色";
});

