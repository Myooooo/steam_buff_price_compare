"use strict";

const state = {
  buff: { logged_in: false, nickname: null, qr_state: "idle" },
  scan: {},
  scheduler: {},
  config: {},
  items: [],
  facets: { weapons: [], item_types: [], exteriors: [] },
  list: { count: 0, page: 1, pages: 1, page_size: 100 },
  reconnectDelay: 1000,
  historyCache: new Map(),
  historyPending: new Map(),
  sparkObserver: null,
  itemRequestId: 0,
  filterTimer: null,
  hasSnapshot: false,
};

const $ = (id) => document.getElementById(id);
const numberFmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

/* ---------- 实时状态 ---------- */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    state.reconnectDelay = 1000;
    setConnStatus(true);
  };
  ws.onmessage = (event) => {
    try { handleMessage(JSON.parse(event.data)); }
    catch (error) { console.warn("WS 消息解析失败", error); }
  };
  ws.onclose = () => {
    setConnStatus(false);
    const delay = Math.min(state.reconnectDelay, 30000);
    setTimeout(connectWS, delay);
    state.reconnectDelay *= 2;
  };
  ws.onerror = () => ws.close();
}

function handleMessage(message) {
  if (message.type === "snapshot") applySnapshot(message);
  else if (message.type === "qr") handleQR(message);
  else if (message.type === "config") state.config = message.config || {};
}

function applySnapshot(snapshot) {
  const previousRun = state.scan.last_run;
  state.buff = snapshot.buff || state.buff;
  state.scan = snapshot.scan || {};
  state.scheduler = snapshot.scheduler || {};
  state.config = snapshot.config || state.config;
  renderChrome();
  if (state.hasSnapshot && state.scan.last_run && state.scan.last_run !== previousRun) {
    refreshItems({ page: state.list.page });
  }
  state.hasSnapshot = true;
}

function setConnStatus(connected) {
  const badge = $("connection-badge");
  badge.className = `signal-badge ${connected ? "online" : "offline"}`;
  badge.innerHTML = `<i></i>${connected ? "实时连接" : "正在重连"}`;
}

/* ---------- 状态渲染 ---------- */
function renderChrome() {
  renderLogin();
  renderScan();
  renderDeepProgress();
}

function renderLogin() {
  const badge = $("login-badge");
  if (state.buff.logged_in) {
    badge.textContent = state.buff.nickname ? `BUFF · ${state.buff.nickname}` : "BUFF 已登录";
    badge.className = "badge badge-on";
    $("btn-login").style.display = "none";
    $("btn-logout").style.display = "";
  } else {
    badge.textContent = "BUFF 未登录";
    badge.className = "badge badge-off";
    $("btn-login").style.display = "";
    $("btn-logout").style.display = "none";
  }
}

function renderScan() {
  const scan = state.scan || {};
  const status = scan.state || "idle";
  const stateEl = $("scan-state");
  const labels = { scanning: "扫描中", login_required: "需要登录", idle: "空闲" };
  stateEl.textContent = labels[status] || status;
  stateEl.className = `state-pill ${status === "scanning" ? "scanning" : status === "login_required" ? "error" : ""}`;
  $("scan-last").textContent = scan.last_run ? fmtTime(scan.last_run) : "—";
  $("scan-next").textContent = state.scheduler.next_run ? fmtTime(state.scheduler.next_run) : "—";

  const result = $("scan-result");
  if (status === "scanning") result.textContent = currentProgressText(scan);
  else if (status === "login_required") result.textContent = "会话失效，请重新扫码登录";
  else if (scan.last_status === "error") result.textContent = `错误 · ${scan.last_error || "未知异常"}`;
  else if (scan.last_status === "paused") result.textContent = "深度任务已保存检查点并暂停";
  else if (scan.last_status === "ok") {
    const mode = scan.last_mode === "deepscan" ? "深度扫描" : "关键词扫描";
    result.textContent = `${mode} · ${numberFmt.format(scan.last_item_count || 0)} 件 · ${scan.last_duration_sec || 0}s`;
  } else result.textContent = "等待扫描指令";

  const quick = $("btn-scan");
  const quickRunning = status === "scanning" && scan.current_mode === "keyword";
  quick.querySelector("strong").textContent = quickRunning ? "正在扫描" : (scan.current_mode === "deepscan" ? "优先立即扫描" : "立即扫描");
  quick.querySelector("small").textContent = scan.current_mode === "deepscan" ? "将在安全检查点插队" : "关键词 · 全部搜索分页";
}

function currentProgressText(scan) {
  const progress = scan.progress || {};
  if (scan.current_mode === "deepscan") {
    const deep = scan.deep_scan || progress;
    if (deep.phase === "indexing") return `建立索引 · 第 ${Math.max(0, (deep.next_page || 1) - 1)} / ${deep.total_pages || "?"} 页`;
    if (deep.phase === "pricing") return `逐件定价 · ${numberFmt.format((deep.priced_count || 0) + (deep.failed_count || 0))} / ${numberFmt.format(deep.indexed_count || 0)}`;
    return "准备深度扫描";
  }
  if (progress.phase === "collecting") {
    return `${progress.keyword || "关键词"} · 第 ${progress.page || 1}/${progress.total_pages || 1} 页 · ${numberFmt.format(progress.candidates || 0)} 个候选`;
  }
  if (progress.phase === "pricing") return `查询 Steam · ${progress.current || 0}/${progress.total || 0} · 已完成 ${progress.processed || 0}`;
  return "正在初始化扫描任务";
}

function renderDeepProgress() {
  const deep = (state.scan || {}).deep_scan;
  const phase = $("deep-phase");
  const fill = $("deep-progress-fill");
  const percentEl = $("deep-progress-text");
  const detail = $("deep-progress-detail");
  const button = $("btn-deepscan");
  const active = !!(deep && deep.active);
  const queued = !!(deep && deep.queued);

  if (!deep) {
    phase.textContent = "尚未开始";
    fill.style.width = "0%";
    percentEl.textContent = "0%";
    detail.textContent = "等待建立全市场索引";
    button.querySelector("strong").textContent = "深度扫描";
    button.querySelector("small").textContent = "全市场索引 · 断点续传";
    return;
  }

  const phaseNames = { indexing: "建立索引", pricing: "逐件定价", complete: "本轮完成" };
  const percent = Number(deep.percent || 0);
  phase.textContent = active ? `${phaseNames[deep.phase] || deep.phase}中` : (queued ? "等待恢复" : (phaseNames[deep.phase] || deep.phase));
  fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  percentEl.textContent = `${percent.toFixed(1)}%`;
  if (deep.phase === "indexing") {
    detail.textContent = `索引页 ${Math.max(0, (deep.next_page || 1) - 1)} / ${deep.total_pages || "?"} · ${numberFmt.format(deep.indexed_count || 0)} 件`;
  } else if (deep.phase === "pricing") {
    const done = (deep.priced_count || 0) + (deep.failed_count || 0);
    detail.textContent = `已处理 ${numberFmt.format(done)} / ${numberFmt.format(deep.indexed_count || 0)} · 无有效价 ${numberFmt.format(deep.failed_count || 0)}`;
  } else {
    detail.textContent = `已索引 ${numberFmt.format(deep.indexed_count || 0)} · 成功定价 ${numberFmt.format(deep.priced_count || 0)}`;
  }

  button.querySelector("strong").textContent = active || queued ? "暂停深度扫描" : (deep.resumable ? "继续深度扫描" : "刷新全量索引");
  button.querySelector("small").textContent = active ? "进度已持续写入 SQLite" : (queued ? "普通扫描后将自动恢复" : (deep.resumable ? "从上次检查点继续" : "开始新一轮全市场刷新"));
}

/* ---------- 商品查询与筛选 ---------- */
function filterParams(page) {
  const params = new URLSearchParams({ page: String(page), page_size: "100" });
  const values = {
    q: $("filter-keyword").value.trim(),
    weapon: $("filter-weapon").value,
    item_type: $("filter-type").value,
    exterior: $("filter-exterior").value,
    data_state: $("filter-freshness").value,
    source: $("filter-source").value,
    min_price: $("filter-price-min").value,
    max_price: $("filter-price-max").value,
  };
  Object.entries(values).forEach(([key, value]) => { if (value !== "") params.set(key, value); });
  const [sortBy, sortOrder] = $("filter-sort").value.split(":");
  params.set("sort_by", sortBy);
  params.set("sort_order", sortOrder);
  if ($("only-profitable").checked) params.set("only_profitable", "true");
  return params;
}

async function refreshItems({ page = 1 } = {}) {
  const requestId = ++state.itemRequestId;
  $("market-panel").classList.add("loading");
  try {
    const data = await api(`/api/items?${filterParams(page)}`);
    if (requestId !== state.itemRequestId) return;
    state.items = data.items || [];
    state.facets = data.facets || state.facets;
    state.list = {
      count: data.count || 0,
      page: data.page || 1,
      pages: data.pages || 1,
      page_size: data.page_size || 100,
    };
    renderFacets();
    renderTable();
  } catch (error) {
    if (requestId === state.itemRequestId) toast(`列表加载失败：${error.message}`, "error");
  } finally {
    if (requestId === state.itemRequestId) $("market-panel").classList.remove("loading");
  }
}

function renderFacets() {
  setSelectOptions($("filter-weapon"), state.facets.weapons || [], "全部枪械");
  setSelectOptions($("filter-type"), state.facets.item_types || [], "全部类别");
  setSelectOptions($("filter-exterior"), state.facets.exteriors || [], "全部磨损");
}

function setSelectOptions(select, values, emptyLabel) {
  const selected = select.value;
  select.innerHTML = `<option value="">${emptyLabel}</option>` + values.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("");
  if (values.includes(selected)) select.value = selected;
}

function renderTable() {
  const tbody = $("rank-body");
  if (state.sparkObserver) state.sparkObserver.disconnect();
  tbody.innerHTML = "";
  $("count-hint").textContent = `${numberFmt.format(state.list.count)} 条结果`;
  $("empty-hint").style.display = state.items.length ? "none" : "block";
  $("page-hint").textContent = `第 ${state.list.page} / ${state.list.pages} 页`;
  $("btn-page-prev").disabled = state.list.page <= 1;
  $("btn-page-next").disabled = state.list.page >= state.list.pages;

  state.items.forEach((item, index) => {
    const row = document.createElement("tr");
    const isLatest = item.data_state === "latest";
    const url = item.buff_url || fallbackBuffUrl(item.market_hash_name);
    row.className = `${isLatest ? "latest-row" : "cached-row"} ${item.discount > 1 ? "loss-row" : ""}`;
    row.tabIndex = 0;
    row.setAttribute("aria-label", `打开 BUFF 商品：${item.display_name || item.market_hash_name}`);
    row.style.animationDelay = `${Math.min(index, 14) * 18}ms`;
    const tags = [item.weapon, item.item_type, item.exterior].filter(Boolean).slice(0, 3);
    const displayName = item.display_name || item.market_hash_name;
    const marketName = displayName === item.market_hash_name ? (item.weapon || "BUFF 市场商品") : item.market_hash_name;
    row.innerHTML = `
      <td class="rank-col">${(state.list.page - 1) * state.list.page_size + index + 1}</td>
      <td class="item-cell">
        <a class="item-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">
          <span class="item-image">${item.icon_url ? `<img src="${esc(item.icon_url)}" alt="" loading="lazy">` : ""}</span>
          <span class="item-copy">
            <strong title="${esc(displayName)}">${esc(displayName)}</strong>
            <small title="${esc(marketName)}">${esc(marketName)}</small>
            <span class="item-tags">${tags.map((tag) => `<span>${esc(tag)}</span>`).join("")}<span class="source-tag">${item.source === "deepscan" ? "深度" : "关键词"}</span></span>
          </span>
          <span class="jump">↗</span>
        </a>
      </td>
      <td><span class="freshness-pill ${isLatest ? "latest" : "cached"}"><i></i>${isLatest ? "最新数据" : "缓存数据"}</span></td>
      <td class="num"><span class="price-main">${fmtCurrency(item.buff_price)}</span></td>
      <td class="num optional-col">${fmtCurrency(item.steam_price)}</td>
      <td class="num optional-col">${fmtCurrency(item.steam_net)}</td>
      <td class="num"><span class="discount ${item.discount > 1 ? "loss" : ""}">${item.discount != null ? (item.discount * 10).toFixed(1) : "—"}${item.discount != null ? "<small>折</small>" : ""}</span></td>
      <td class="num volume-col">${item.steam_volume == null ? "—" : numberFmt.format(item.steam_volume)}</td>
      <td class="num volume-col">${item.buff_sell_num == null ? "—" : numberFmt.format(item.buff_sell_num)}</td>
      <td class="spark-col"><span class="spark-cell"></span></td>
      <td class="updated-cell" title="${esc(item.updated_at || "")}">${fmtTime(item.updated_at)}</td>`;
    const openItem = () => window.open(url, "_blank", "noopener,noreferrer");
    row.addEventListener("click", (event) => { if (!event.target.closest("a,button,input,select")) openItem(); });
    row.addEventListener("keydown", (event) => { if (event.key === "Enter") openItem(); });
    tbody.appendChild(row);
    const sparkCell = row.querySelector(".spark-cell");
    sparkCell.dataset.name = item.market_hash_name;
    if (state.sparkObserver) state.sparkObserver.observe(sparkCell);
    else ensureSparkline(item.market_hash_name, sparkCell);
  });
}

function scheduleFilterRefresh() {
  clearTimeout(state.filterTimer);
  state.filterTimer = setTimeout(() => refreshItems({ page: 1 }), 260);
}

function resetFilters() {
  ["filter-keyword", "filter-price-min", "filter-price-max"].forEach((id) => { $(id).value = ""; });
  ["filter-weapon", "filter-type", "filter-exterior", "filter-freshness", "filter-source"].forEach((id) => { $(id).value = ""; });
  $("filter-sort").value = "discount:asc";
  $("only-profitable").checked = true;
  refreshItems({ page: 1 });
}

/* ---------- 走势 ---------- */
function ensureSparkline(name, cell) {
  if (!name || !cell) return;
  if (state.historyCache.has(name)) {
    renderSparkline(cell, state.historyCache.get(name));
    return;
  }
  let pending = state.historyPending.get(name);
  if (!pending) {
    pending = api(`/api/items/${encodeURIComponent(name)}/history?days=30`)
      .then((data) => (data.history || []).map((point) => point.discount).filter((value) => value != null))
      .then((points) => { state.historyCache.set(name, points); return points; })
      .catch(() => [])
      .finally(() => state.historyPending.delete(name));
    state.historyPending.set(name, pending);
  }
  pending.then((points) => renderSparkline(cell, points));
}

function renderSparkline(cell, points) {
  if (!cell.isConnected) return;
  if (!points || points.length < 2) { cell.textContent = "—"; return; }
  const width = 92, height = 26, pad = 2;
  const min = Math.min(...points), max = Math.max(...points), range = max - min || 1;
  const coords = points.map((value, index) => {
    const x = pad + (index / (points.length - 1)) * (width - pad * 2);
    const y = height - pad - ((value - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const color = points.at(-1) <= 1 ? "var(--green)" : "var(--red)";
  cell.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

/* ---------- 扫描操作 ---------- */
async function triggerScan(mode) {
  try {
    const data = await api("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (data.already_running) toast("同类扫描已经在运行，不会重复排队");
    else if (data.queued) {
      toast(mode === "keyword" && state.scan.current_mode === "deepscan" ? "立即扫描已排队，将在深度任务的下一个检查点优先执行" : "扫描请求已进入队列");
    } else toast(mode === "deepscan" ? "深度扫描已启动，进度会持续保存" : "完整关键词扫描已启动");
  } catch (error) { toast(error.message || "触发扫描失败", "error"); }
}

async function handleDeepAction() {
  const deep = state.scan.deep_scan;
  if (deep && (deep.active || deep.queued)) {
    try {
      await api("/api/scan/deep/pause", { method: "POST" });
      toast("深度扫描已暂停，检查点已保存");
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  const resumable = deep && deep.resumable;
  $("deep-modal-title").textContent = resumable ? "从检查点继续深度扫描？" : (deep && deep.phase === "complete" ? "刷新全市场索引？" : "启动全市场深度扫描？");
  $("deep-modal-copy").textContent = resumable
    ? `将从第 ${deep.next_page || 1} 页或上次未完成的定价项继续，不会重置已有进度。`
    : "这不是一次快速搜索。应用会索引 BUFF 全市场，再逐件查询 Steam 价格，可能持续数小时至数天。";
  openModal("deep-modal");
}

/* ---------- 登录 ---------- */
async function openLogin() {
  openModal("login-modal");
  $("qr-box").innerHTML = "<div class='qr-status'>正在生成安全二维码…</div>";
  $("qr-status").textContent = "";
  try {
    const data = await api("/api/auth/qr", { method: "POST" });
    if (!data.qr_image) throw new Error(data.error || "二维码生成失败");
    $("qr-box").innerHTML = `<img src="${data.qr_image}" alt="BUFF 登录二维码">`;
    $("qr-status").textContent = "请使用 BUFF App 扫码";
  } catch (error) { $("qr-status").textContent = `请求失败：${error.message}`; }
}

function handleQR(message) {
  if (message.state === "wait_scan" && message.qr_image) {
    $("qr-box").innerHTML = `<img src="${message.qr_image}" alt="BUFF 登录二维码">`;
    $("qr-status").textContent = "请使用 BUFF App 扫码";
  } else if (message.state === "wait_confirm") $("qr-status").textContent = "扫码成功，请在手机上确认登录";
  else if (message.state === "confirmed") {
    $("qr-status").textContent = "登录成功，正在同步市场状态…";
    setTimeout(() => closeModal("login-modal"), 650);
  } else if (["error", "expired"].includes(message.state)) {
    $("qr-status").textContent = `${message.error || "二维码已失效"}，请刷新二维码`;
    $("qr-box").innerHTML = "<div class='qr-status'>二维码已失效</div>";
  }
}

/* ---------- 设置 ---------- */
function openSettings() {
  const config = state.config || {};
  $("cfg-keywords").value = (config.keywords || []).join(", ");
  $("cfg-page-size").value = config.page_size || 80;
  $("cfg-interval").value = config.scan_interval_minutes || 15;
  $("cfg-auto-scan").checked = config.auto_scan !== false;
  $("cfg-deep-enabled").checked = !!(config.deep_scan && config.deep_scan.enabled);
  $("cfg-deep-interval").value = (config.deep_scan && config.deep_scan.interval_minutes) || 240;
  $("cfg-fee-steam").value = config.steam_fee_steam_pct ?? 5;
  $("cfg-fee-game").value = config.steam_fee_game_pct ?? 10;
  $("cfg-fee-round").value = config.fee_round || "cent";
  openModal("settings-modal");
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    keywords: $("cfg-keywords").value.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean),
    page_size: parseInt($("cfg-page-size").value, 10) || 80,
    scan_interval_minutes: parseInt($("cfg-interval").value, 10) || 15,
    auto_scan: $("cfg-auto-scan").checked,
    deep_scan: {
      enabled: $("cfg-deep-enabled").checked,
      interval_minutes: parseInt($("cfg-deep-interval").value, 10) || 240,
    },
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
    closeModal("settings-modal");
    toast("设置已保存并立即生效");
  } catch (error) { toast(`保存失败：${error.message}`, "error"); }
}

/* ---------- 通用 ---------- */
function openModal(id) { $(id).style.display = "flex"; document.body.style.overflow = "hidden"; }
function closeModal(id) { $(id).style.display = "none"; document.body.style.overflow = ""; }
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}
function fmtCurrency(value) { return value == null ? "—" : `¥${numberFmt.format(value)}`; }
function fmtTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function fallbackBuffUrl(name) { return `https://buff.163.com/market/csgo#tab=selling&search=${encodeURIComponent(name || "")}`; }
function toast(message, kind = "info") {
  const element = $("scan-msg");
  const token = `${Date.now()}`;
  element.dataset.toast = token;
  element.textContent = message;
  element.style.color = kind === "error" ? "var(--red)" : "var(--amber)";
  setTimeout(() => { if (element.dataset.toast === token) { element.textContent = ""; delete element.dataset.toast; } }, 5000);
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

document.addEventListener("DOMContentLoaded", () => {
  if ("IntersectionObserver" in window) {
    state.sparkObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        state.sparkObserver.unobserve(entry.target);
        ensureSparkline(entry.target.dataset.name, entry.target);
      });
    }, { rootMargin: "180px 0px" });
  }
  $("btn-login").addEventListener("click", openLogin);
  $("btn-qr-refresh").addEventListener("click", openLogin);
  $("btn-qr-cancel").addEventListener("click", () => closeModal("login-modal"));
  $("btn-logout").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); toast("已退出 BUFF 会话"); }
    catch (error) { toast(`登出失败：${error.message}`, "error"); }
  });
  $("btn-scan").addEventListener("click", () => triggerScan("keyword"));
  $("btn-deepscan").addEventListener("click", handleDeepAction);
  $("btn-deep-confirm").addEventListener("click", () => { closeModal("deep-modal"); triggerScan("deepscan"); });
  $("btn-deep-cancel").addEventListener("click", () => closeModal("deep-modal"));
  $("btn-settings").addEventListener("click", openSettings);
  $("btn-settings-cancel").addEventListener("click", () => closeModal("settings-modal"));
  $("settings-form").addEventListener("submit", saveSettings);

  ["filter-weapon", "filter-type", "filter-exterior", "filter-freshness", "filter-source", "filter-sort", "only-profitable"].forEach((id) => $(id).addEventListener("change", () => refreshItems({ page: 1 })));
  ["filter-keyword", "filter-price-min", "filter-price-max"].forEach((id) => $(id).addEventListener("input", scheduleFilterRefresh));
  $("btn-filter-reset").addEventListener("click", resetFilters);
  $("btn-page-prev").addEventListener("click", () => refreshItems({ page: state.list.page - 1 }));
  $("btn-page-next").addEventListener("click", () => refreshItems({ page: state.list.page + 1 }));

  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => closeModal(button.dataset.close)));
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => backdrop.addEventListener("click", (event) => { if (event.target === backdrop) closeModal(backdrop.id); }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") document.querySelectorAll(".modal-backdrop").forEach((modal) => { if (modal.style.display !== "none") closeModal(modal.id); });
    if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $("filter-keyword").focus();
    }
  });

  connectWS();
  api("/api/status").then(applySnapshot).catch(() => {});
  refreshItems();
});
