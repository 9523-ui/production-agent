"use strict";
/* ===== M7 知识库与长期记忆面板 =====
   数据源：GET/POST/DELETE /api/kb · /api/memory
   检索预演与 agent 工具 search_kb 走同一 BM25 实现；
   长期记忆按当前会话 ID（与对话侧栏一致的 sessionInput）过滤。
   浮层接线走 app.js 的 registerPanel。 */

const KB_PANEL = registerPanel({
  btnId: "kbBtn",
  overlayId: "kbOverlay",
  closeId: "kbCloseBtn",
  autoId: "kbAuto",
  pollMs: 5000,
  pollFn: pollKb,
  onOpen: openKb,
  onClose: closeKb
});

function kbSessionId(){
  const el = $("sessionInput");
  return (el && el.value.trim()) || "default";
}

function kbTs(ts){
  if(!ts) return "—";
  const d = new Date(ts * 1000), p = function(n){ return n < 10 ? "0" + n : "" + n; };
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
         p(d.getHours()) + ":" + p(d.getMinutes());
}

function openKb(){
  $("kbOverlay").classList.add("show");
  loadKbAll();
}
function closeKb(){
  $("kbOverlay").classList.remove("show");
}

async function loadKbAll(){
  const results = await Promise.all([
    api("/api/kb"),
    api("/api/memory?session_id=" + encodeURIComponent(kbSessionId()))
  ]);
  if(results[0].ok) renderKbDocs(results[0].body);
  if(results[1].ok) renderMemories(results[1].body);
  $("kbUpdated").textContent = "更新于 " + new Date().toLocaleTimeString();
}

async function pollKb(){
  await loadKbAll();
}

function renderKbDocs(body){
  const items = body.items || [], stats = body.stats || {};
  $("kbStatePill").textContent =
    "文档 " + (stats.docs || 0) + " · 分块 " + (stats.chunks || 0);
  const tb = $("kbTbody");
  if(!items.length){
    tb.innerHTML = '<tr><td colspan="6"><div class="empty">知识库为空：点「载入示例知识」或手动入库</div></td></tr>';
    return;
  }
  tb.innerHTML = items.map(function(d){
    return "<tr><td class='numcell'>" + d.id + "</td>" +
      "<td>" + esc(String(d.title)) + "</td>" +
      "<td>" + esc(String(d.source || "—")) + "</td>" +
      "<td class='numcell'>" + (d.chunk_count || 0) + "</td>" +
      "<td class='mono'>" + kbTs(d.created_at) + "</td>" +
      "<td><button class='btn' data-kb-del='" + d.id + "'>删除</button></td></tr>";
  }).join("");
  Array.prototype.forEach.call(tb.querySelectorAll("[data-kb-del]"), function(b){
    b.addEventListener("click", async function(){
      const r = await api("/api/kb/" + b.getAttribute("data-kb-del"), {method: "DELETE"});
      if(!r.ok) return;
      loadKbAll();
    });
  });
}

function renderMemories(body){
  const items = body.items || [];
  $("kbMemTitle").textContent = "长期记忆 · 当前会话（" + kbSessionId() + "）";
  const tb = $("kbMemTbody");
  if(!items.length){
    tb.innerHTML = '<tr><td colspan="5"><div class="empty">本会话暂无长期记忆：对话里说「记住…」即可保存</div></td></tr>';
    return;
  }
  tb.innerHTML = items.map(function(m){
    return "<tr><td class='numcell'>" + m.id + "</td>" +
      "<td>" + esc(String(m.content)) + "</td>" +
      "<td>" + esc(String(m.kind || "fact")) + "</td>" +
      "<td class='numcell'>" + (m.hits || 0) + "</td>" +
      "<td><button class='btn' data-mem-del='" + m.id + "'>删</button></td></tr>";
  }).join("");
  Array.prototype.forEach.call(tb.querySelectorAll("[data-mem-del]"), function(b){
    b.addEventListener("click", async function(){
      const r = await api("/api/memory/" + b.getAttribute("data-mem-del"), {method: "DELETE"});
      if(!r.ok) return;
      loadKbAll();
    });
  });
}

async function addKbDoc(){
  const title = $("kbTitleInput").value.trim();
  const content = $("kbContentInput").value.trim();
  const source = $("kbSourceInput").value.trim() || null;
  if(!(title.length >= 1 && title.length <= 80) ||
     !(content.length >= 1 && content.length <= 8000)){
    $("kbSearchResults").innerHTML =
      '<div class="kb-miss">入库失败：标题 1-80 字、正文 1-8000 字</div>';
    return;
  }
  const r = await api("/api/kb", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({title: title, content: content, source: source})});
  if(!r.ok){
    $("kbSearchResults").innerHTML =
      '<div class="kb-miss">入库失败：HTTP ' + r.status + "</div>";
    return;
  }
  $("kbTitleInput").value = "";
  $("kbSourceInput").value = "";
  $("kbContentInput").value = "";
  $("kbSearchResults").innerHTML =
    '<div class="kb-miss">已入库 #' + r.body.doc_id + "，" + r.body.chunks + " 个分块</div>";
  loadKbAll();
}

const KB_SEED_DOCS = [
  {title: "发票政策", source: "policy-demo",
   content: "电子发票在订单签收后 48 小时内自动开具，可在订单详情页下载。\n如需修改发票抬头，请在开票前联系客服，每笔订单只能修改一次。\n纸质发票需额外支付 5 元邮费，7 个工作日内寄出。"},
  {title: "会员积分规则", source: "policy-demo",
   content: "会员每消费 1 元积 1 分，积分在确认收货后 7 天内到账。\n积分可在下次购物时抵扣，100 积分抵 1 元，单笔最高抵扣订单金额的 10%。\n积分有效期为 12 个月，过期自动清零，清零前 30 天会短信提醒。"}
];

async function seedKb(){
  for(const doc of KB_SEED_DOCS){
    const r = await api("/api/kb", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(doc)});
    if(!r.ok) return;
  }
  $("kbSearchResults").innerHTML =
    '<div class="kb-miss">已载入 2 篇示例知识，试试检索「发票」或「积分」</div>';
  loadKbAll();
}

async function searchKbPreview(){
  const q = $("kbQueryInput").value.trim();
  if(!q) return;
  const r = await api("/api/kb/search?q=" + encodeURIComponent(q) + "&k=3");
  const box = $("kbSearchResults");
  if(!r.ok){
    box.innerHTML = '<div class="kb-miss">检索失败：HTTP ' + r.status + "</div>";
    return;
  }
  const hits = r.body.hits || [];
  if(!hits.length){
    box.innerHTML = '<div class="kb-miss">未命中：知识库里没有与「' + esc(q) + '」相关的内容</div>';
    return;
  }
  box.innerHTML = hits.map(function(h){
    return '<div class="kb-hit"><span class="t">' + esc(String(h.title)) +
      '</span><span class="score">doc ' + h.doc_id + " · 块 " + h.seq +
      " · 相关度 " + h.score + '</span><div class="chunk">' + esc(String(h.chunk)) + "</div></div>";
  }).join("");
}

async function clearMemories(){
  if(!window.confirm("清空当前会话（" + kbSessionId() + "）的全部长期记忆？")) return;
  const r = await api("/api/memory?session_id=" + encodeURIComponent(kbSessionId()),
                      {method: "DELETE"});
  if(!r.ok) return;
  loadKbAll();
}

$("kbAddBtn").addEventListener("click", addKbDoc);
$("kbSeedBtn").addEventListener("click", seedKb);
$("kbSearchBtn").addEventListener("click", searchKbPreview);
$("kbQueryInput").addEventListener("keydown", function(e){
  if(e.key === "Enter") searchKbPreview();
});
$("kbMemClearBtn").addEventListener("click", clearMemories);
$("kbRefreshBtn").addEventListener("click", loadKbAll);
