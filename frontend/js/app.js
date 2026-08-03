"use strict";

/* ---------- 全局状态 ---------- */
const state = {
  buff: { logged_in: false, nickname: null, qr_state: "idle" },
  scan: {},
  scheduler: {},
  config: {},
  items: [],
  reconnectDelay: 1000,
  historyCache: new Map(), // market_hash_name -> [{ts, discount}]
  historyPending: new Set(),
};

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

/* ---------- WebSocket ---------- */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    state.reconnectDelay = 1000;
    setConnStatus(true);
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      handleMessage(msg);
    } catch (e) { console.warn("WS 消息解析失败", e); }
  };
  ws.onclose = () => {
    setConnStatus(false);
    scheduleReconnect();
  };
  ws.onerror = () => ws.close();
}

function scheduleReconnect() {
  const delay = Math.min(state.reconnectDelay, 30000);
  setTimeout(connectWS, delay);
  state.reconnectDelay *= 2;
}

function handleMessage(msg) {
  switch (msg.type) {
    case "snapshot":
      applySnapshot(msg);
      break;
    case "qr":
      handleQR(msg);
      break;
    case "config":
      state.config = msg.config || {};
      break;
  }
}

function applySnapshot(snap) {
  state.buff = snap.buff || state.buff;
  state.scan = snap.scan || {};
  state.scheduler = snap.scheduler || {};
  state.config = snap.config || state.config;
  if (Array.isArray(snap.items)) state.items = snap.items;
  render();
}

/* ---------- 连接状态 ---------- */
function setConnStatus(ok) {
  const el = $("scan-msg");
  if (ok) {
    if (el.dataset.kind === "connection") {
      el.textContent = "";
      delete el.dataset.kind;
    }
  } else {
    el.dataset.kind = "connection";
    el.textContent = "连接已断开，正在重连…";
  }
}

/* ---------- 渲染 ---------- */
function render() {
  renderLogin();
  renderScan();
  renderTable();
}

function renderLogin() {
  const badge = $("login-badge");
  if (state.buff.logged_in) {
    badge.textContent = "已登录" + (state.buff.nickname ? "：" + state.buff.nickname : "");
    badge.className = "badge badge-on";
    $("btn-login").style.display = "none";
    $("btn-logout").style.display = "";
  } else {
    badge.textContent = "未登录";
    badge.className = "badge badge-off";
    $("btn-login").style.display = "";
    $("btn-logout").style.display = "none";
  }
}

function renderScan() {
  const s = state.scan;
  const st = s.state || "idle";
  $("scan-state").textContent = st === "scanning" ? "扫描中…" : st === "login_required" ? "需要重新登录" : (st === "idle" ? "空闲" : st);
  $("scan-state").className = st === "scanning" ? "badge badge-scan" : "";
  $("scan-last").textContent = s.last_run ? fmtTime(s.last_run) : "—";
  $("scan-next").textContent = state.scheduler.next_run ? fmtTime(state.scheduler.next_run) : "—";

  const res = $("scan-result");
  if (st === "login_required") {
    res.textContent = "会话失效，请重新扫码登录";
    res.style.color = "var(--bad)";
  } else if (s.last_status === "error") {
    res.textContent = "出错：" + (s.last_error || "未知");
    res.style.color = "var(--bad)";
  } else if (s.last_status === "ok") {
    res.textContent = `${s.last_mode || ""} ${s.last_item_count ?? 0} 件 · ${s.last_duration_sec ?? "-"}s`;
    res.style.color = "";
  } else {
    res.textContent = "—";
    res.style.color = "";
  }
}

function renderTable() {
  const tbody = $("rank-body");
  tbody.innerHTML = "";
  const onlyProfitable = $("only-profitable").checked;
  const source = $("filter-source").value;

  let items = state.items;
  if (onlyProfitable) items = items.filter((it) => it.discount != null && it.discount <= 1.0);
  if (source) items = items.filter((it) => it.source === source);

  $("count-hint").textContent = `共 ${items.length} 条`;
  $("empty-hint").style.display = items.length ? "none" : "";

  const staleCutoff = Date.now() - 3 * 60 * 60 * 1000; // 3 小时未更新视为过期

  items.forEach((it, idx) => {
    const tr = document.createElement("tr");
    const loss = it.discount != null && it.discount > 1.0;
    if (loss) tr.classList.add("loss");
    const updated = new Date(it.updated_at).getTime();
    if (!Number.isNaN(updated) && updated < staleCutoff) tr.classList.add("stale");

    tr.innerHTML = `
      <td class="num">${idx + 1}</td>
      <td class="item-name" title="${esc(it.market_hash_name)}">${esc(it.market_hash_name)}
        <span class="src-tag">${it.source === "deepscan" ? "深度" : "关键词"}</span>
      </td>
      <td class="num">${it.buff_price != null ? fmt.format(it.buff_price) : "—"}</td>
      <td class="num">${it.steam_price != null ? fmt.format(it.steam_price) : "—"}</td>
      <td class="num">${it.steam_net != null ? fmt.format(it.steam_net) : "—"}</td>
      <td class="num"><span class="zhe ${idx < 3 ? "best" : ""}">${it.discount != null ? (it.discount * 10).toFixed(1) + "折" : "—"}</span></td>
      <td class="num">${it.steam_volume ?? "—"}</td>
      <td class="num">${it.buff_sell_num ?? "—"}</td>
      <td class="spark"><span class="spark-cell" data-name="${esc(it.market_hash_name)}"></span></td>
      <td title="${esc(it.updated_at)}">${fmtTime(it.updated_at)}</td>
    `;
    tbody.appendChild(tr);
    // 懒加载走势图（最多前 60 条，缓存避免重复请求）
    if (idx < 60) ensureSparkline(it.market_hash_name, tr.querySelector(".spark-cell"));
  });
}

/* ---------- 走势图（SVG） ---------- */
function ensureSparkline(name, cell) {
  if (!name) return;
  const cached = state.historyCache.get(name);
  if (cached) {
    renderSparkline(cell, cached);
    return;
  }
  if (state.historyPending.has(name)) return;
  state.historyPending.add(name);
  api(`/api/items/${encodeURIComponent(name)}/history?days=30`)
    .then((d) => {
      const points = (d.history || []).map((h) => h.discount).filter((v) => v != null);
      state.historyCache.set(name, points);
      renderSparkline(cell, points);
    })
    .catch(() => {})
    .finally(() => state.historyPending.delete(name));
}

function renderSparkline(cell, points) {
  if (!cell || !points || points.length < 2) {
    if (cell) cell.textContent = "—";
    return;
  }
  const w = 90, h = 24, pad = 2;
  const min = Math.min(...points), max = Math.max(...points);
  const range = max - min || 1;
  const coords = points.map((v, i) => {
    const x = pad + (i / (points.length - 1)) * (w - pad * 2);
    const y = h - pad - ((v - min) / range) * (h - pad * 2);
    return [x, y];
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const color = points[points.length - 1] <= 1.0 ? "var(--good)" : "var(--bad)";
  cell.innerHTML = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

/* ---------- 登录流程 ---------- */
async function openLogin() {
  $("login-modal").style.display = "flex";
  $("qr-box").innerHTML = "<div class='qr-status'>正在生成二维码…</div>";
  $("qr-status").textContent = "";
  try {
    const data = await api("/api/auth/qr", { method: "POST" });
    if (!data.qr_image) throw new Error(data.error || "二维码生成失败");
    $("qr-box").innerHTML = `<img src="${data.qr_image}" alt="二维码">`;
    $("qr-status").textContent = "请用 Buff 客户端扫码";
  } catch (error) {
    $("qr-status").textContent = "请求失败：" + error.message;
  }
}

function handleQR(msg) {
  const st = msg.state;
  if (st === "wait_scan") {
    if (msg.qr_image) {
      $("qr-box").innerHTML = `<img src="${msg.qr_image}" alt="二维码">`;
      $("qr-status").textContent = "请用 Buff 客户端扫码";
    }
  } else if (st === "wait_confirm") {
    $("qr-status").textContent = "扫码成功，请在手机上确认登录";
  } else if (st === "confirmed") {
    $("qr-status").textContent = "登录成功！";
    setTimeout(() => { $("login-modal").style.display = "none"; }, 600);
  } else if (st === "error" || st === "expired") {
    $("qr-status").textContent = (msg.error || "登录失败") + "，可点击「刷新二维码」";
    $("qr-box").innerHTML = "<div class='qr-status'>二维码已失效</div>";
  }
}

/* ---------- 设置 ---------- */
function openSettings() {
  const c = state.config || {};
  $("cfg-keywords").value = (c.keywords || []).join(", ");
  $("cfg-page-size").value = c.page_size ?? 20;
  $("cfg-deep-enabled").checked = !!(c.deep_scan && c.deep_scan.enabled);
  $("cfg-deep-min").value = c.deep_scan ? c.deep_scan.min_price : 20;
  $("cfg-deep-max").value = c.deep_scan ? c.deep_scan.max_price : 300;
  $("cfg-deep-pages").value = c.deep_scan ? c.deep_scan.max_pages : 10;
  $("cfg-interval").value = c.scan_interval_minutes ?? 15;
  $("cfg-auto-scan").checked = c.auto_scan !== false;
  $("cfg-fee-steam").value = c.steam_fee_steam_pct ?? 5;
  $("cfg-fee-game").value = c.steam_fee_game_pct ?? 10;
  $("cfg-fee-round").value = c.fee_round || "cent";
  $("settings-modal").style.display = "flex";
}

async function saveSettings(ev) {
  ev.preventDefault();
  const body = {
    keywords: $("cfg-keywords").value.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean),
    page_size: parseInt($("cfg-page-size").value, 10) || 20,
    deep_scan: {
      enabled: $("cfg-deep-enabled").checked,
      min_price: parseFloat($("cfg-deep-min").value) || 20,
      max_price: parseFloat($("cfg-deep-max").value) || 300,
      max_pages: parseInt($("cfg-deep-pages").value, 10) || 10,
    },
    scan_interval_minutes: parseInt($("cfg-interval").value, 10) || 15,
    auto_scan: $("cfg-auto-scan").checked,
    steam_fee_steam_pct: parseFloat($("cfg-fee-steam").value) || 5,
    steam_fee_game_pct: parseFloat($("cfg-fee-game").value) || 10,
    fee_round: $("cfg-fee-round").value,
  };
  try {
    state.config = await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("settings-modal").style.display = "none";
  } catch (error) {
    alert("保存失败：" + error.message);
  }
}

/* ---------- 工具 ---------- */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toast(msg) {
  const el = $("scan-msg");
  el.dataset.kind = "toast";
  el.textContent = msg;
  setTimeout(() => {
    if (el.dataset.kind === "toast" && el.textContent === msg) {
      el.textContent = "";
      delete el.dataset.kind;
    }
  }, 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = data && typeof data === "object" ? (data.detail || data.error) : data;
    throw new Error(message || `HTTP ${response.status}`);
  }
  return data;
}

/* ---------- 事件绑定 ---------- */
document.addEventListener("DOMContentLoaded", () => {
  $("btn-login").addEventListener("click", openLogin);
  $("btn-qr-refresh").addEventListener("click", openLogin);
  $("btn-qr-cancel").addEventListener("click", () => { $("login-modal").style.display = "none"; });
  $("btn-logout").addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (error) {
      toast("登出失败：" + error.message);
    }
  });
  $("btn-scan").addEventListener("click", () => triggerScan("keyword"));
  $("btn-deepscan").addEventListener("click", () => triggerScan("deepscan"));
  $("btn-settings").addEventListener("click", openSettings);
  $("btn-settings-cancel").addEventListener("click", () => { $("settings-modal").style.display = "none"; });
  $("settings-form").addEventListener("submit", saveSettings);
  $("only-profitable").addEventListener("change", renderTable);
  $("filter-source").addEventListener("change", renderTable);

  connectWS();
});

async function triggerScan(mode) {
  try {
    const data = await api("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (data.queued) toast("已有扫描在进行，已排队等待");
  } catch (error) {
    toast(error.message || "触发扫描失败");
  }
}

// 首次加载兜底（WS 未连上前拉一次快照）
api("/api/status")
  .then((d) => { if (d) applySnapshot(d); })
  .catch(() => {});
