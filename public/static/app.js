// MintCheck frontend: plain JavaScript, no build step.
const $ = (id) => document.getElementById(id);

// Always escape text that comes from the chain (token names can contain HTML!).
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const short = (a) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : "—");
const solscan = (a, type = "account") => `<a class="mono" target="_blank" rel="noopener" href="https://solscan.io/${type}/${esc(a)}">${esc(short(a))}</a>`;
const fmt = (n, d = 2) => (n === null || n === undefined || isNaN(n) ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d }));
const usd = (n) => (n === null || n === undefined || isNaN(n) ? "—" : "$" + fmt(n, n < 1 ? 6 : 0));

// ---------------------------------------------------------------- status bar
async function loadStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    const rpcOn = s.slot !== undefined;
    $("status").innerHTML = `
      <span><span class="dot ${rpcOn ? "on" : "off"}"></span>RPC: <b>${esc(s.rpc_provider)}</b></span>
      <span>slot <b>${fmt(s.slot, 0)}</b></span>
      <span>latency <b>${fmt(s.rpc_latency_ms, 0)} ms</b></span>
      <span title="${esc(s.tps_source)}">network <b>~${fmt(s.tps, 0)} tx/s</b>${s.user_tps ? ` (${fmt(s.user_tps, 0)} non-vote)` : ""}</span>
      ${s.blur?.ok ? `<span><span class="dot on"></span>Blur data</span>` : ""}`;
    setBlur(!!s.blur?.ok);
  } catch (e) {
    $("status").textContent = "backend offline";
  }
}

// ---------------------------------------------------------------- token report
async function check(mint) {
  const btn = document.querySelector("#form button");
  btn.disabled = true;
  $("report").innerHTML = `<p class="hint">Reading mainnet through Solami…</p>`;
  try {
    const r = await (await fetch("/api/check?mint=" + encodeURIComponent(mint))).json();
    if (r.error) { $("report").innerHTML = `<p class="err">${esc(r.error)}</p>`; return; }
    $("report").innerHTML = renderReport(r);
    history.replaceState(null, "", "?mint=" + encodeURIComponent(mint));
  } catch (e) {
    $("report").innerHTML = `<p class="err">Request failed: ${esc(e)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

function renderReport(r) {
  const t = r.token, risk = r.risk, a = r.activity, h = r.holders, m = r.market;
  const cls = risk.score >= 80 ? "ok" : risk.score >= 50 ? "mid" : "bad";
  const name = m && (m.name || m.symbol) ? `${esc(m.name || "")} ${m.symbol ? "($" + esc(m.symbol) + ")" : ""}` : solscan(r.mint, "token");
  let html = `
    <div class="score">
      <div class="ring ${cls}">${risk.score}</div>
      <div><div class="grade">${esc(risk.grade)}</div><div>${name}</div>
      <div class="hint">${esc(t.program)} · data path: ${esc(r.data_path.rpc)}${r.data_path.blur ? " + Blur" : ""} · checked ${new Date(r.checked_at * 1000).toLocaleTimeString()}</div></div>
    </div>
    <ul class="findings">${risk.findings.map((f) => `<li class="lv-${f.level}">${esc(f.text)}</li>`).join("")}</ul>
    <h3>Token</h3>
    <div class="grid">
      ${stat("Supply", fmt(t.supply, 0))}
      ${stat("Decimals", t.decimals)}
      ${stat("Mint authority", t.mint_authority ? solscan(t.mint_authority) : "revoked ✅")}
      ${stat("Freeze authority", t.freeze_authority ? solscan(t.freeze_authority) : "revoked ✅")}
      ${stat("Token-2022 extensions", Object.keys(t.extensions).length ? esc(Object.keys(t.extensions).join(", ")) : "none")}
    </div>`;

  if (a) {
    html += `<h3>Live on-chain activity (${esc(r.data_path.rpc)})</h3><div class="grid">
      ${stat("Tx last 1 min", fmt(a.tx_1m, 0) + (a.capped && a.tx_1m === a.sampled ? "+" : ""))}
      ${stat("Tx last 5 min", fmt(a.tx_5m, 0) + (a.capped && a.tx_5m === a.sampled ? "+" : ""))}
      ${stat("Tx last 1 hour", fmt(a.tx_1h, 0) + (a.capped && a.tx_1h === a.sampled ? "+" : ""))}
      ${stat("Last tx", a.last_tx_seconds_ago === null ? "—" : a.last_tx_seconds_ago + " s ago")}
      ${stat("Failed tx (sample)", fmt(a.failed_pct, 1) + "%")}
    </div>`;
  }

  if (m) {
    const p = m.pressure_5m || {};
    const share = p.buy_share_pct;
    html += `<h3>Market, last 5 min (Solami Blur)</h3><div class="grid">
      ${stat("Trades", fmt(p.count, 0))}
      ${stat("Buy volume", usd(p.buy_usd))}
      ${stat("Sell volume", usd(p.sell_usd))}
      ${stat("Unique traders", fmt(p.unique_traders, 0))}
    </div>
    ${share === null || share === undefined ? "" : `<p>Buy pressure ${fmt(share, 0)}%</p><div class="bar"><div style="width:${Math.max(0, Math.min(100, share))}%"></div></div>`}
    ${m.recent_trades?.length ? `<table><tr><th>Side</th><th>USD</th><th>DEX</th><th>Tx</th></tr>${m.recent_trades.map((x) => `
      <tr><td>${x.side === "buy" ? '<span class="badge b-ok">buy</span>' : '<span class="badge b-bad">sell</span>'}</td>
      <td>${usd(Number(x.volume_usd))}</td><td>${esc(x.dex)}</td><td>${x.signature ? solscan(x.signature, "tx") : "—"}</td></tr>`).join("")}</table>` : ""}
    <details><summary>Raw Blur stats / security response</summary><pre>${esc(JSON.stringify({ stats: m.stats, security: m.security }, null, 2))}</pre></details>`;
  }

  if (h) {
    html += `<h3>Top holders</h3>
      <p class="hint">Pools, bonding curves and lockers are program-owned addresses (PDAs), so they are excluded from the wallet-concentration score.
      Largest wallet: <b>${fmt(h.top1_wallet_pct, 1)}%</b> · top 10 wallets: <b>${fmt(h.top10_wallet_pct, 1)}%</b> · held by programs/pools: <b>${fmt(h.program_pct, 1)}%</b></p>
      <table><tr><th>#</th><th>Owner</th><th>Type</th><th>% of supply</th></tr>
      ${h.rows.map((row, i) => `<tr><td>${i + 1}</td><td>${row.owner ? solscan(row.owner) : "—"}</td>
        <td>${row.kind === "wallet" ? '<span class="badge b-mid">wallet</span>' : row.kind === "unknown" ? '<span class="badge b-mut">unknown</span>' : `<span class="badge b-ok">${esc(row.label || "program / pool")}</span>`}</td>
        <td>${fmt(row.pct, 2)}%</td></tr>`).join("")}</table>`;
  }
  if (!h && r.holders_note) html += `<h3>Top holders</h3><p class="hint">${esc(r.holders_note)}</p>`;
  if (r.errors?.length) html += `<details><summary>${r.errors.length} partial error(s)</summary><pre>${esc(r.errors.join("\n"))}</pre></details>`;
  return html;
}
const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;

// ---------------------------------------------------------------- live feed (only when Blur is available)
let feedKind = "launches";
let blurOn = false;
function setBlur(on) {
  $("proNote").hidden = on;
  $("feedCard").hidden = !on;
  if (on && !blurOn) loadFeed();
  blurOn = on;
}
async function loadFeed() {
  if (!blurOn) return;
  try {
    const f = await (await fetch("/api/feed?kind=" + feedKind)).json();
    if (!f.items.length) { $("feed").innerHTML = `<p class="hint">${esc(f.error || "No items right now.")}</p>`; return; }
    $("feed").innerHTML = `<table><tr><th>Token</th><th>Mint auth</th><th>Freeze auth</th><th>Quick score</th><th></th></tr>
      ${f.items.map((i) => `<tr>
        <td>${esc(i.symbol || i.name || "")} ${solscan(i.mint, "token")} ${i.launchpad ? `<span class="badge b-mut">${esc(i.launchpad)}</span>` : ""}</td>
        <td>${badge(i.mint_authority_on)}</td><td>${badge(i.freeze_authority_on)}</td>
        <td>${i.quick_risk ?? "—"}</td>
        <td><a href="#" data-mint="${esc(i.mint)}">full check →</a></td></tr>`).join("")}</table>
      ${f.error ? `<p class="err">${esc(f.error)}</p>` : ""}`;
  } catch (e) {
    $("feed").innerHTML = `<p class="err">Feed failed: ${esc(e)}</p>`;
  }
}
const badge = (on) => (on === undefined ? "—" : on ? '<span class="badge b-bad">ON</span>' : '<span class="badge b-ok">revoked</span>');

// ---------------------------------------------------------------- wiring
$("form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("mint").value.trim(); if (v) check(v); });
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-mint]");
  if (a) { e.preventDefault(); $("mint").value = a.dataset.mint; check(a.dataset.mint); window.scrollTo({ top: 0, behavior: "smooth" }); }
  const tab = e.target.closest(".tab");
  if (tab) { document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active")); tab.classList.add("active"); feedKind = tab.dataset.kind; loadFeed(); }
});
loadStatus();
setInterval(loadStatus, 5000);
setInterval(loadFeed, 10000);
const q = new URLSearchParams(location.search).get("mint");
if (q) { $("mint").value = q; check(q); }
