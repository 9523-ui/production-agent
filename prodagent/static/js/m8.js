"use strict";

/* ===== M8 身份与权限（规模化·角色鉴权）=====
 * 顶栏「身份」pill + identOverlay 面板。
 * 切换身份 = 写 localStorage prodagent_token（demo 令牌），api() 每次请求自动携带，
 * 服务端按令牌映射角色（客户/员工/领导），401/403 立即体现。
 * 演示令牌仅 APP_ENV=dev + ENABLE_DEMO_ROLE_TOKENS=1 时由 /api/auth/identities 下发。
 */

let IDENT_DATA = { whoami: null, identities: [], staff: null, staffError: null };

const IDENT_PANEL = registerPanel({
  btnId: "identityBtn",
  overlayId: "identOverlay",
  closeId: "identCloseBtn",
  autoId: "identAuto",
  pollMs: 5000,
  pollFn: pollIdent,
  onOpen: openIdent,
  onClose: closeIdent,
});

function openIdent() {
  $("identOverlay").classList.add("show");
  loadIdentAll();
}

function closeIdent() {
  $("identOverlay").classList.remove("show");
}

function pollIdent() {
  loadIdentAll();
}

function identTs(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
    p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

async function loadIdentAll() {
  const [who, ids, staff] = await Promise.all([
    api("/api/whoami"),
    api("/api/auth/identities"),
    api("/api/staff"),
  ]);
  IDENT_DATA = {
    whoami: who.body,
    identities: (ids.body && ids.body.identities) || [],
    staff: staff.ok ? staff.body : null,
    staffError: staff.ok ? null : staff.body,
  };
  renderIdentAll();
}

function renderIdentAll() {
  const w = IDENT_DATA.whoami;
  if (!w) return;
  renderRoleCur(w);
  renderWho(w);
  renderSwitch(w);
  renderDirectory();
  renderMatrix();
  $("identUpdated").textContent = "更新于 " + identTs(w.ts);
}

/* 顶栏当前身份 pill */
function renderRoleCur(w) {
  const el = $("roleCur");
  if (!el) return;
  el.textContent = "身份·" + (w.role_label || w.role);
  el.className = "pill info mono" + (w.role === "anonymous" ? " on-anonymous" : "");
}

/* 当前身份自查结果 */
function renderWho(w) {
  const auth = w.authenticated ? "已认证 ✓" : "未认证";
  const mode = w.auth_required ? "鉴权已启用" : "开放模式（未配置令牌，本地开发全权限）";
  $("identWho").innerHTML =
    '<div class="who-line"><span class="k small muted">当前身份</span><b>' + esc(w.role_label || w.role) +
    '（' + esc(w.role) + '）</b></div>' +
    '<div class="who-line"><span class="k small muted">认证状态</span>' + auth + '</div>' +
    '<div class="who-line"><span class="k small muted">鉴权模式</span>' + esc(mode) + '</div>' +
    '<div class="who-line"><span class="k small muted">令牌预览</span><span class="mono token-mono">' +
    esc(w.token_preview || "—") + '</span></div>' +
    '<div class="who-line"><span class="k small muted">运行环境</span>' + esc(w.app_env || "—") + '</div>';
  $("identStatePill").textContent = w.auth_required ? "鉴权已启用" : "开放模式";
}

/* 身份切换按钮区 */
function renderSwitch(w) {
  const box = $("identSwitch");
  if (!box) return;
  const demoOn = IDENT_DATA.identities.length > 0;
  box.querySelectorAll("button[data-role]").forEach((btn) => {
    const role = btn.dataset.role;
    const active = (w.role === role) || (role === "anonymous" && w.role === "anonymous");
    btn.classList.toggle("active", active);
    if (role !== "anonymous") {
      const ident = IDENT_DATA.identities.find((x) => x.role === role);
      btn.disabled = !demoOn || !ident || !ident.demo_token;
      btn.title = ident && ident.demo_token
        ? "令牌 " + ident.demo_token + "（写入 localStorage）"
        : "演示令牌不可用（需 dev + ENABLE_DEMO_ROLE_TOKENS=1）";
    }
  });
  $("identSwitchHint").textContent = demoOn
    ? "点击即切换：令牌写入 localStorage 并立即作用于全部接口；「匿名」清除令牌模拟未认证请求。"
    : "演示令牌未启用。生产/普通 dev 下请用 AUTH_TOKEN / AUTH_TOKEN_STAFF / AUTH_TOKEN_CUSTOMER 三令牌，或直接在顶栏令牌框粘贴。";
}

async function setRole(role) {
  if (role === "anonymous") {
    localStorage.removeItem("prodagent_token");
    const ti = $("tokenInput");
    if (ti) ti.value = "";
  } else {
    const ident = IDENT_DATA.identities.find((x) => x.role === role);
    if (!ident || !ident.demo_token) {
      $("identSwitchHint").textContent =
        "「" + role + "」演示令牌不可用：需 APP_ENV=dev 且 ENABLE_DEMO_ROLE_TOKENS=1，重启服务后生效。";
      return;
    }
    localStorage.setItem("prodagent_token", ident.demo_token);
    const ti = $("tokenInput");
    if (ti) ti.value = ident.demo_token;
  }
  await loadIdentAll();
  $("identSwitchHint").textContent = "已切换到「" +
    (IDENT_DATA.whoami ? IDENT_DATA.whoami.role_label : role) + "」身份，刷新页面各面板即可看到权限差异。";
}

/* 员工目录（分级视图） */
function renderDirectory() {
  const tbody = $("identTbody");
  if (!tbody) return;
  if (IDENT_DATA.staffError) {
    tbody.innerHTML = '<tr><td colspan="5"><div class="empty">当前身份无权查看员工目录（403 ROLE_FORBIDDEN）</div></td></tr>';
    return;
  }
  const st = IDENT_DATA.staff;
  if (!st || !st.items) {
    tbody.innerHTML = '<tr><td colspan="5"><div class="empty">目录未加载</div></td></tr>';
    return;
  }
  const full = st.view === "full";
  tbody.innerHTML = st.items.map((p) =>
    "<tr><td>" + esc(p.name) + "</td><td>" + esc(p.title) + "</td><td>" + esc(p.dept) +
    "</td><td>" + esc(p.office || "—") + "</td><td>" +
    (full
      ? esc((p.phone || "—") + " · " + (p.email || "—"))
      : '<span class="muted">仅领导可见</span>') +
    "</td></tr>").join("") ||
    '<tr><td colspan="5"><div class="empty">无数据</div></td></tr>';
  /* 视图提示：员工看到的是脱敏版 */
  const h = $("identDirTable").closest(".rt-sec").querySelector("h3");
  if (h) {
    h.textContent = "员工目录 · GET /api/staff（当前视图：" +
      (full ? "full · 含联系方式" : "basic · 仅基本信息") + "，共 " + st.count + " 人）";
  }
}

/* 权限矩阵（静态，与后端门禁一致） */
const IDENT_MATRIX = [
  ["对话 · POST /api/chat", "✓", "✓", "✓"],
  ["知识查询 · GET /api/kb · /api/kb/search", "✓", "✓", "✓"],
  ["知识入库 / 删除 · POST / DELETE /api/kb", "×", "✓", "✓"],
  ["内部运行数据 · /api/stats · /api/runs · /api/history", "×", "✓", "✓"],
  ["演练 / 评估面板 · /api/drill · /api/eval", "×", "✓（dev）", "✓（dev）"],
  ["员工目录 · /api/staff", "×", "基本信息", "全字段"],
];

function renderMatrix() {
  const tbody = $("identMatrix");
  if (!tbody) return;
  tbody.innerHTML = IDENT_MATRIX.map((row) =>
    "<tr><td>" + esc(row[0]) + "</td>" +
    row.slice(1).map((cell) =>
      '<td class="' + (cell === "×" ? "deny" : "allow") + '">' + cell + "</td>").join("") +
    "</tr>").join("");
}

/* 绑定 */
(function initIdent() {
  const cur = $("roleCur");
  if (cur) cur.addEventListener("click", () => IDENT_PANEL.open());
  const box = $("identSwitch");
  if (box) box.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-role]");
    if (btn && !btn.disabled) setRole(btn.dataset.role);
  });
  const refresh = $("identRefreshBtn");
  if (refresh) refresh.addEventListener("click", loadIdentAll);
})();
