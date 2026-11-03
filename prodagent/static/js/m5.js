"use strict";
/* ===== M5 评估回归面板：一键离线验收 + 历史回归对比 =====
   数据源：GET /api/eval/status | /api/eval/report | /api/eval/history
   唯一写动作：POST /api/eval/run（子进程离线跑 eval/run_acceptance.py，
   互斥 + 仅 dev；面板其余全部只读）。浮层接线走 app.js 的 registerPanel。 */
let evalCatalog = null, evalWasRunning = false, evalHistSel = null;

const EVAL_PANEL = registerPanel({
  btnId: "evalBtn",
  overlayId: "evalOverlay",
  closeId: "evalCloseBtn",
  autoId: "evalAuto",
  pollMs: 2000,
  pollFn: pollEval,
  onOpen: openEval,
  onClose: closeEval
});

function openEval(){
  $("evalOverlay").classList.add("show");
  loadEvalCatalog();
  loadEvalAll();
}
function closeEval(){
  $("evalOverlay").classList.remove("show");
}

function evalTs(ts){
  if(!ts) return "—";
  const d = new Date(ts * 1000), p = function(n){ return n < 10 ? "0" + n : "" + n; };
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
         p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

async function loadEvalCatalog(){
  const r = await api("/api/eval/catalog");
  if(!r.ok) return;
  evalCatalog = r.body;
  const btn = $("evalRunBtn");
  if(!evalCatalog.enabled){
    btn.disabled = true;
    btn.title = evalCatalog.reason || "当前环境不可运行";
    $("evalFoot").textContent = evalCatalog.reason || "当前环境不可运行";
  } else {
    btn.disabled = false;
    btn.title = "在子进程里离线跑 eval/run_acceptance.py（强制 FakeLLM）";
  }
}

async function loadEvalAll(){
  const results = await Promise.all([
    api("/api/eval/status"),
    api("/api/eval/report"),
    api("/api/eval/history?limit=10")
  ]);
  if(results[0].ok) renderEvalStatus(results[0].body, true);
  if(results[1].ok) renderEvalReport(results[1].body);
  if(results[2].ok) renderEvalHistory(results[2].body.items || []);
  $("evalUpdated").textContent = "更新于 " + new Date().toLocaleTimeString();
}

async function pollEval(){
  const r = await api("/api/eval/status");
  if(!r.ok) return;
  renderEvalStatus(r.body, false);
  if(r.body.running){
    evalWasRunning = true;
  } else if(evalWasRunning){
    evalWasRunning = false;   // 刚结束：拉全量新报告与历史
    loadEvalAll();
  }
}

function renderEvalStatus(st, full){
  const pill = $("evalStatePill");
  const btn = $("evalRunBtn");
  btn.disabled = st.running || !st.enabled;
  if(st.running){
    pill.textContent = "验收运行中 · " + (st.elapsed_s || 0) + "s";
    pill.className = "pill info";
    const out = $("evalOutput");
    out.classList.add("running");
    out.textContent = "运行中 · 已 " + (st.elapsed_s || 0) +
      "s（离线 FakeLLM，最长 " + (evalCatalog ? evalCatalog.timeout_s : 300) + "s）";
    $("evalMiniDot").className = "dot warn";
  } else {
    const last = st.last;
    if(last){
      pill.textContent = "上次 " + last.passed + "/" + last.total +
        (last.exit_code === 0 ? " · exit 0" : " · exit " + last.exit_code);
      pill.className = "pill " + (last.failed === 0 && last.exit_code === 0 ? "ok" : "err");
    } else {
      pill.textContent = "尚无运行记录";
      pill.className = "pill info";
    }
    $("evalMiniDot").className = st.last && st.last.failed === 0 && st.last.exit_code === 0
      ? "dot on" : "dot off";
    if(full && st.last && st.last.output_tail){
      const out = $("evalOutput");
      out.classList.remove("running");
      out.textContent = st.last.output_tail;
    }
  }
  if(st.regression && st.regression.regressed){
    $("evalHistTitle").textContent = "最近运行 · ⚠ 回归 " +
      st.regression.delta_passed + " 项（对比第 " + st.regression.prev_id + " 次）";
  } else {
    $("evalHistTitle").textContent = "最近运行 · 回归对比";
  }
}

function renderEvalReport(rep){
  const r = rep.report;
  const score = $("evalScore");
  if(!r){
    score.innerHTML = '<span class="num">—</span><span class="sub">报告文件尚未生成：先跑一次验收</span>';
    $("evalGroups").innerHTML = '<span class="muted small">无分组数据</span>';
    return;
  }
  const okCls = r.failed === 0 ? "ok" : "err";
  score.innerHTML = '<span class="num ' + okCls + '">' + esc(r.passed) + " / " +
    esc(r.total) + '</span><span class="sub">失败 ' + esc(r.failed) +
    " · " + esc(rep.report_path) + "</span>";
  const groups = r.groups || {};
  const keys = Object.keys(groups);
  $("evalGroups").innerHTML = keys.length
    ? keys.map(function(g){
        const v = String(groups[g] || ""), parts = v.split("/");
        const bad = parts.length === 2 && parts[0] !== parts[1];
        return '<span class="chip ' + (bad ? "err" : "ok") + '">' + esc(g) + " " + esc(v) + "</span>";
      }).join("")
    : '<span class="muted small">无分组数据</span>';
  renderEvalChecks(r.checks || [], "当前报告");
}

function renderEvalChecks(checks, label){
  const box = $("evalFailures");
  const bad = checks.filter(function(c){ return c && c.ok === false; });
  if(!bad.length){
    box.innerHTML = '<div class="empty">' + esc(label) + "：全部通过</div>";
    return;
  }
  box.innerHTML = bad.map(function(c){
    return '<div class="fail"><div class="t">' + esc(c.group || "?") + " · " +
      esc(c.name || "?") + '</div><div class="d">' + esc(c.detail || "") + "</div></div>";
  }).join("");
}

function renderEvalHistory(items){
  const tbody = $("evalTbody");
  if(!items.length){
    tbody.innerHTML = '<tr><td colspan="5" class="muted">还没有历史记录：点「运行验收」生成第一条</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(function(it, i){
    const prev = items[i + 1];
    let delta = '<span class="delta-zero">·</span>';
    if(prev){
      const d = it.passed - prev.passed;
      delta = d === 0 ? '<span class="delta-zero">±0</span>'
        : '<span class="' + (d < 0 ? "delta-neg" : "delta-pos") + '">' +
          (d < 0 ? "−" : "+") + Math.abs(d) + "</span>";
    }
    const dur = it.duration_ms >= 1000
      ? (it.duration_ms / 1000).toFixed(1) + "s" : it.duration_ms + "ms";
    const res = it.passed + "/" + it.total;
    return '<tr class="row' + (evalHistSel === it.id ? " sel" : "") +
      '" data-eval-run="' + esc(it.id) + '"><td class="numcell">' + esc(it.id) +
      '</td><td class="numcell">' + evalTs(it.created_at) + '</td><td class="numcell ' +
      (it.failed === 0 ? "ok" : "err") + '">' + res + '</td><td class="numcell">' +
      delta + '</td><td class="numcell">' + esc(dur) + "</td></tr>";
  }).join("");
  Array.prototype.forEach.call(tbody.querySelectorAll("tr[data-eval-run]"), function(tr){
    tr.addEventListener("click", function(){
      evalLoadDetail(parseInt(tr.getAttribute("data-eval-run"), 10));
    });
  });
}

async function evalLoadDetail(id){
  const r = await api("/api/eval/history/" + id);
  if(!r.ok) return;
  const item = r.body;
  evalHistSel = id;
  renderEvalHistoryCache();
  const rep = item.report;
  if(rep && rep.checks){
    renderEvalChecks(rep.checks, "第 " + id + " 次运行 · " +
      rep.passed + "/" + rep.total);
  } else {
    $("evalFailures").innerHTML =
      '<div class="empty">第 ' + esc(id) + " 次运行：报告全文缺失（仅存摘要）</div>";
  }
}

function renderEvalHistoryCache(){
  // 重画一遍已渲染的历史行，仅更新选中高亮
  const rows = $("evalTbody").querySelectorAll("tr[data-eval-run]");
  Array.prototype.forEach.call(rows, function(tr){
    tr.classList.toggle("sel", parseInt(tr.getAttribute("data-eval-run"), 10) === evalHistSel);
  });
}

async function runEval(){
  if(evalCatalog && !evalCatalog.enabled){
    $("evalOutput").textContent = evalCatalog.reason || "当前环境不可运行";
    return;
  }
  const btn = $("evalRunBtn");
  btn.disabled = true;
  const r = await api("/api/eval/run", { method: "POST" });
  if(r.ok){
    evalWasRunning = true;
    renderEvalStatus(r.body.status || {}, false);
  } else if(r.status === 409){
    $("evalOutput").textContent = "已有一次验收在跑，等它结束再启动";
    evalWasRunning = true;
  } else {
    $("evalOutput").textContent = (r.body && (r.body.error || r.body.error_code)) ||
      ("运行失败 HTTP " + r.status);
  }
  pollEval();
}

$("evalRunBtn").addEventListener("click", runEval);
$("evalRefreshBtn").addEventListener("click", loadEvalAll);
