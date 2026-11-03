"use strict";
/* ===== M3 运行历史 / 轨迹回放 / 双条对比（只读） ===== */
let histPage = 0, histSelected = [], histRows = [], histFacets = null, histTotal = 0;

function histParams(){
  const p = new URLSearchParams();
  const pairs = [["histStatus","status"], ["histSession","session_id"],
                 ["histCode","error_code"], ["histDegraded","degraded"],
                 ["histHours","hours"], ["histQuery","q"]];
  pairs.forEach(function(x){
    const v = $(x[0]).value.trim();
    if(v) p.set(x[1], v);
  });
  const lim = Number($("histLimit").value) || 50;
  p.set("limit", String(lim));
  p.set("offset", String(histPage * lim));
  return p;
}
function fillSelect(id, opts, allLabel){
  const el = $(id), keep = el.value;
  el.innerHTML = '<option value="">' + esc(allLabel) + "</option>"
    + opts.map(function(o){
        return '<option value="' + esc(o.value) + '">' + esc(o.label) + "</option>";
      }).join("");
  el.value = keep;
}
function renderFacets(){
  const f = histFacets || {};
  fillSelect("histStatus", Object.keys(f.status || {}).map(function(k){
    return {value:k, label:k + " (" + f.status[k] + ")"}; }), "全部状态");
  fillSelect("histSession", (f.sessions || []).map(function(x){
    return {value:x.session_id, label:x.session_id + " (" + x.n + ")"}; }), "全部会话");
  fillSelect("histCode", Object.keys(f.error_codes || {}).map(function(k){
    return {value:k, label:k + " (" + f.error_codes[k] + ")"}; }), "全部错误码");
}
function updateCompareBtn(){
  const b = $("histCompareBtn");
  b.textContent = "对比选中 (" + histSelected.length + "/2)";
  b.disabled = histSelected.length !== 2;
}
function renderHistoryTable(){
  if(!histRows.length){
    $("histTable").innerHTML = '<tr><td colspan="8"><div class="empty">没有符合条件的运行记录</div></td></tr>';
    updateCompareBtn();
    return;
  }
  const head = "<thead><tr><th></th><th>时间</th><th>run_id</th><th>会话</th><th>请求</th><th>状态</th><th>轮次</th><th>延迟</th></tr></thead>";
  const body = histRows.map(function(x){
    const sel = histSelected.indexOf(x.run_id) >= 0;
    const t = x.created_at ? new Date(Number(x.created_at) * 1000).toLocaleTimeString() : "—";
    return '<tr class="row' + (sel ? " sel" : "") + '" data-run="' + esc(x.run_id) + '">'
      + '<td><input type="checkbox" data-pick="' + esc(x.run_id) + '"' + (sel ? " checked" : "") + "></td>"
      + '<td class="mono">' + esc(t) + "</td>"
      + '<td class="mono">' + esc(String(x.run_id).slice(0,8)) + "</td>"
      + '<td class="mono">' + esc(x.session_id) + "</td>"
      + '<td class="msgcell">' + esc(x.request_text) + "</td>"
      + '<td><span class="pill ' + statusCls(x.status) + '">' + esc(statusLabel(x.status)) + "</span></td>"
      + '<td class="mono">' + esc(x.rounds) + "</td>"
      + '<td class="mono">' + esc(x.latency_ms) + " ms</td></tr>";
  }).join("");
  $("histTable").innerHTML = head + "<tbody>" + body + "</tbody>";
  updateCompareBtn();
}
function renderHistorySummary(){
  const f = histFacets || {};
  const dist = Object.keys(f.status || {}).map(function(k){ return k + " " + f.status[k]; }).join(" · ");
  $("histSummary").textContent = "筛选结果 " + histTotal + " 条 · 本页 " + histRows.length + " 条"
    + (dist ? "　|　全库状态分布：" + dist : "");
}
function renderHistoryPager(){
  const lim = Number($("histLimit").value) || 50;
  const pages = Math.max(1, Math.ceil(histTotal / lim));
  $("histPager").innerHTML = '<button class="btn sm" data-nav="prev"'
    + (histPage <= 0 ? " disabled" : "") + ">上一页</button>"
    + "<span>第 " + (histPage + 1) + " / " + pages + " 页 · 每页 " + lim + " 条</span>"
    + '<button class="btn sm" data-nav="next"'
    + ((histPage + 1) * lim >= histTotal ? " disabled" : "") + ">下一页</button>";
}
async function loadHistory(){
  try {
    const r = await api("/api/history?" + histParams().toString());
    if(!r.ok || !r.body){ $("histUpdated").textContent = "加载失败 HTTP " + r.status; return; }
    histRows = r.body.runs || [];
    histTotal = r.body.total || 0;
    histFacets = r.body.facets || {};
    renderFacets(); renderHistoryTable(); renderHistorySummary(); renderHistoryPager();
    $("histUpdated").textContent = "更新于 " + new Date().toLocaleTimeString();
  } catch(e){ $("histUpdated").textContent = "加载失败：" + e.message; }
}
function togglePick(id){
  const i = histSelected.indexOf(id);
  if(i >= 0) histSelected.splice(i, 1);
  else { if(histSelected.length >= 2) histSelected.shift(); histSelected.push(id); }
  renderHistoryTable();
}
async function showRunDetail(runId){
  const r = await api("/api/runs/" + encodeURIComponent(runId));
  if(!r.ok || !r.body || !r.body.run){ $("histDetail").innerHTML = '<div class="empty">回放失败</div>'; return; }
  const run = r.body.run, steps = r.body.steps || [];
  $("histDetailTitle").textContent = "轨迹回放 · " + String(runId).slice(0,8);
  const meta = [
    ["status", statusLabel(run.status)], ["session_id", run.session_id],
    ["rounds", run.rounds], ["latency_ms", run.latency_ms + " ms"],
    ["cost_cny", "¥" + Number(run.cost_cny || 0).toFixed(6)],
    ["degraded", run.degraded ? "是" : "否"], ["error_code", run.error_code || "—"],
    ["trace_id", run.trace_id],
    ["created_at", run.created_at ? new Date(Number(run.created_at) * 1000).toLocaleString() : "—"]
  ];
  let html = '<div class="small muted" style="margin-bottom:8px">请求：' + esc(run.request_text) + "</div>";
  html += meta.map(function(p){
    return '<div class="cmp-row"><span class="k">' + esc(p[0]) + '</span><span class="v">' + esc(p[1]) + "</span></div>";
  }).join("");
  html += '<div class="sec-title" style="margin-top:10px">工具轨迹 · ' + steps.length + " 步</div>";
  html += steps.length ? steps.map(function(s){
    let a = s.args_json;
    try { a = JSON.stringify(JSON.parse(s.args_json)); } catch(e){}
    return '<div class="hist-step' + (s.ok ? "" : " bad") + '">'
      + '<span class="mono">' + esc(s.seq) + ". " + esc(s.tool) + "</span>"
      + ' <span class="pill ' + (s.ok ? "ok" : "err") + '">' + (s.ok ? "成功" : "失败") + "</span>"
      + ' <span class="mono muted">' + esc(s.ms) + " ms</span>"
      + '<div class="small muted">args: ' + esc(a) + "</div>"
      + '<div class="small">result: ' + esc(s.result) + "</div></div>";
  }).join("") : '<div class="empty">本次运行没有工具调用</div>';
  html += '<div class="small muted" style="margin-top:9px">回复：' + esc(run.reply_text || "—") + "</div>";
  $("histDetail").innerHTML = html;
}
async function compareRuns(){
  if(histSelected.length !== 2) return;
  const res = await Promise.all(histSelected.map(function(id){
    return api("/api/runs/" + encodeURIComponent(id));
  }));
  if(!res[0].ok || !res[1].ok || !res[0].body.run || !res[1].body.run){
    $("histDetail").innerHTML = '<div class="empty">对比数据加载失败</div>';
    return;
  }
  const ra = res[0].body.run, rb = res[1].body.run;
  const sa = res[0].body.steps || [], sb = res[1].body.steps || [];
  $("histDetailTitle").textContent = "运行对比 · 2 条";
  const fields = [
    ["status", statusLabel(ra.status), statusLabel(rb.status)],
    ["session_id", ra.session_id, rb.session_id],
    ["rounds", ra.rounds, rb.rounds],
    ["latency_ms", ra.latency_ms, rb.latency_ms],
    ["cost_cny", Number(ra.cost_cny || 0).toFixed(6), Number(rb.cost_cny || 0).toFixed(6)],
    ["degraded", ra.degraded ? "是" : "否", rb.degraded ? "是" : "否"],
    ["error_code", ra.error_code || "—", rb.error_code || "—"],
    ["steps", sa.length, sb.length],
    ["tools", sa.map(function(s){ return s.tool; }).join(" → ") || "—",
              sb.map(function(s){ return s.tool; }).join(" → ") || "—"],
    ["request", ra.request_text, rb.request_text],
    ["reply", ra.reply_text || "—", rb.reply_text || "—"]
  ];
  const diff = function(f){ return String(f[1]) !== String(f[2]); };
  const col = function(run, idx){
    return '<div class="cmp-col"><div class="cmp-h mono">' + (idx === 0 ? "A" : "B")
      + " · " + esc(String(run.run_id).slice(0,8)) + "</div>"
      + fields.map(function(f){
          return '<div class="cmp-row' + (diff(f) ? " diff" : "") + '"><span class="k">'
            + esc(f[0]) + '</span><span class="v">' + esc(idx === 0 ? f[1] : f[2]) + "</span></div>";
        }).join("") + "</div>";
  };
  $("histDetail").innerHTML = '<div class="cmp">' + col(ra, 0) + col(rb, 1) + "</div>"
    + '<div class="small muted" style="margin-top:9px">黄色行 = 两条运行取值不同；共 '
    + fields.filter(diff).length + " 项差异</div>";
}
function openHistory(){
  $("histOverlay").classList.add("show");
  $("histOverlay").setAttribute("aria-hidden", "false");
  histPage = 0; histSelected = [];
  $("histDetailTitle").textContent = "详情 / 对比";
  $("histDetail").innerHTML = '<div class="empty">点列表中的一行查看轨迹回放，或勾选 2 条后点「对比选中」</div>';
  loadHistory();
}
function closeHistory(){
  $("histOverlay").classList.remove("show");
  $("histOverlay").setAttribute("aria-hidden", "true");
}
function refreshHistoryIfOpen(){ if($("histOverlay").classList.contains("show")) loadHistory(); }
$("historyBtn").addEventListener("click", openHistory);
$("histCloseBtn").addEventListener("click", closeHistory);
$("histRefreshBtn").addEventListener("click", loadHistory);
$("histCompareBtn").addEventListener("click", compareRuns);
$("histApplyBtn").addEventListener("click", function(){ histPage = 0; histSelected = []; loadHistory(); });
$("histResetBtn").addEventListener("click", function(){
  ["histStatus","histSession","histCode","histDegraded","histHours","histQuery"].forEach(function(id){ $(id).value = ""; });
  $("histLimit").value = "50";
  histPage = 0; histSelected = [];
  loadHistory();
});
$("histQuery").addEventListener("keydown", function(e){
  if(e.key === "Enter"){ histPage = 0; loadHistory(); }
});
$("histPager").addEventListener("click", function(e){
  const b = e.target.closest("[data-nav]");
  if(!b || b.disabled) return;
  const lim = Number($("histLimit").value) || 50;
  if(b.getAttribute("data-nav") === "prev" && histPage > 0) histPage--;
  else if(b.getAttribute("data-nav") === "next" && (histPage + 1) * lim < histTotal) histPage++;
  else return;
  loadHistory();
});
$("histTable").addEventListener("click", function(e){
  const pick = e.target.closest("[data-pick]");
  if(pick){ togglePick(pick.getAttribute("data-pick")); return; }
  const row = e.target.closest("[data-run]");
  if(row) showRunDetail(row.getAttribute("data-run"));
});
$("histOverlay").addEventListener("click", function(e){ if(e.target === this) closeHistory(); });
document.addEventListener("keydown", function(e){
  if(e.key === "Escape" && $("histOverlay").classList.contains("show")) closeHistory();
});

