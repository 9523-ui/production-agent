"use strict";
const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8010" : "";
const $ = id => document.getElementById(id);
const MAP = {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"};
const STATUS_MAP = {ok:["正常","ok"],rejected:["已拒绝","err"],degraded:["降级","warn"],error:["错误","err"],running:["执行中","info"]};
let busyTimerId = null, busyStart = 0, welcomed = false;

function esc(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, c => MAP[c]); }
function statusLabel(s){ return (STATUS_MAP[s] || [s || "—","info"])[0]; }
function statusCls(s){ return (STATUS_MAP[s] || [s || "—","info"])[1]; }

function authHeaders(extra){
  const t = localStorage.getItem("prodagent_token") || "";
  return Object.assign({}, extra || {}, t ? {"Authorization":"Bearer " + t} : {});
}
async function api(path, opts){
  opts = opts || {};
  const res = await fetch(API_BASE + path, Object.assign({}, opts, {
    headers: authHeaders(Object.assign({"Content-Type":"application/json"}, opts.headers || {}))
  }));
  const txt = await res.text();
  let body = null;
  try { body = txt ? JSON.parse(txt) : null; } catch(e){ body = {raw: txt}; }
  return {ok: res.ok, status: res.status, body: body, headers: res.headers};
}
function showBanner(msg){ $("connBanner").textContent = msg; $("connBanner").classList.remove("hidden"); }
function hideBanner(){ $("connBanner").classList.add("hidden"); }

// M5 引入的浮层通用接线：按钮 / 关闭 / 点遮罩 / Esc / 自动刷新的样板收口。
// M2–M4 的原接线保持不动（0.4 无损拆分原则）；新面板一律走这里，
// 加第五个面板不再复制粘贴 4 处监听器。
function registerPanel(p){
  function open(){ if(typeof p.onOpen === "function") p.onOpen(); }
  function close(){ if(typeof p.onClose === "function") p.onClose(); }
  $(p.btnId).addEventListener("click", open);
  if(p.closeId && $(p.closeId)){
    $(p.closeId).addEventListener("click", close);
  }
  $(p.overlayId).addEventListener("click", function(e){ if(e.target === this) close(); });
  document.addEventListener("keydown", function(e){
    if(e.key === "Escape" && $(p.overlayId).classList.contains("show")) close();
  });
  let timer = null;
  function fire(){
    if($(p.overlayId).classList.contains("show") && typeof p.pollFn === "function"){
      try { p.pollFn(); } catch(err){ /* 轮询失败不打断面板 */ }
    }
  }
  function syncAuto(){
    if(timer){ clearInterval(timer); timer = null; }
    if(p.autoId && $(p.autoId) && $(p.autoId).checked && p.pollMs){
      timer = setInterval(fire, p.pollMs);
    }
  }
  if(p.autoId && $(p.autoId)){
    $(p.autoId).addEventListener("change", syncAuto);
    syncAuto();
  }
  return { open: open, close: close, syncAuto: syncAuto };
}

