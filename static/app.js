"use strict";
const COLORS = ["#1f5fbf", "#e07a2e", "#2a9d8f", "#8e5bc2", "#c9a227", "#d1495b"];
const state = { aba: "inflacao", anos: 5, data: {}, opts: {} };
const $ = (s) => document.querySelector(s);
const nf = (v, c = 2) => v.toLocaleString("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const LAYOUT = {
  margin: { l: 50, r: 12, t: 10, b: 40 }, hovermode: "x unified", font: { family: "system-ui, Segoe UI, sans-serif", size: 12 },
  legend: { orientation: "h", y: -0.18 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  xaxis: { gridcolor: "#eef1f5" }, yaxis: { gridcolor: "#eef1f5", zerolinecolor: "#c9d0db" }, separators: ",.",
};
const CONFIG = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

// ---------------------------------------------------------------- dados
async function loadAba(aba) {
  if (!state.data[aba]) {
    const r = await fetch(`/api/aba/${aba}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.data[aba] = await r.json();
  }
  return state.data[aba];
}

function inicio() {
  if (!state.anos) return null;
  const d = new Date(); d.setFullYear(d.getFullYear() - state.anos);
  return d.toISOString().slice(0, 10);
}

function filtrar(s, ini) {
  if (!ini) return s;
  const i = s.x.findIndex((x) => x >= ini);
  return i < 0 ? { ...s, x: [], y: [] } : { ...s, x: s.x.slice(i), y: s.y.slice(i) };
}

// Card com várias visões (ex.: m/m vs 12m): devolve o chart da visão ativa
function resolve(c) {
  if (!c.views) return c;
  const v = c.views[state.opts[c.id] || 0] || c.views[0];
  return { ...c, tipo: v.tipo, unidade: v.unidade, series: v.series, sufixo: v.nome };
}

function seriesVisiveis(c) {
  const ini = inicio();
  let ss = c.series.map((s) => filtrar(s, ini));
  if (c.base100 && state.opts[c.id + ":b100"] !== false) {
    ss = ss.map((s) => ({ ...s, nome: s.nome.replace(/ \(.*\)/, ""), y: s.y.map((v) => (v / s.y[0]) * 100) }));
  }
  return ss;
}

// ---------------------------------------------------------------- CSV
function baixar(nome, linhas) {
  const csv = "﻿" + linhas.map((l) => l.map((v) =>
    typeof v === "number" ? String(v).replace(".", ",") : `"${String(v ?? "").replace(/"/g, '""')}"`).join(";")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  a.download = `${nome}.csv`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function csvChart(c) {
  c = resolve(c);
  if (c.rows) {
    const cols = Object.keys(c.rows[0]);
    return baixar(c.id, [cols, ...c.rows.map((r) => cols.map((k) => r[k]))]);
  }
  const ss = seriesVisiveis(c), mapa = new Map();
  ss.forEach((s, j) => s.x.forEach((x, i) => {
    if (!mapa.has(x)) mapa.set(x, Array(ss.length).fill(""));
    mapa.get(x)[j] = s.y[i];
  }));
  const datas = [...mapa.keys()].sort();
  const nome = c.sufixo ? `${c.id}_${c.sufixo.normalize("NFD").replace(/[^\w]+/g, "_").toLowerCase()}` : c.id;
  baixar(nome, [["data", ...ss.map((s) => s.nome)], ...datas.map((d) => [d, ...mapa.get(d)])]);
}

// ---------------------------------------------------------------- render
function renderKpis(kpis) {
  $("#kpis").innerHTML = kpis.map((k) => {
    const d = k.delta == null ? "" : `<div class="d ${k.delta >= 0 ? "up" : "dn"}">${k.delta >= 0 ? "▲" : "▼"} ${nf(Math.abs(k.delta), k.casas)} vs. anterior</div>`;
    return `<div class="kpi"><div class="l">${esc(k.label)} · ${k.ref} ${k.simulado ? '<span class="tag sim">Simulado</span>' : ""}</div>
      <div class="v">${nf(k.valor, k.casas)}${esc(k.suf)}</div>${d}</div>`;
  }).join("");
}

function fontes(c) {
  c = resolve(c);
  let fs = c.fonte ? [c.fonte] : [...new Set(c.series.map((s) => s.fonte))];
  const sim = fs.filter((f) => f.startsWith("Simulado"));
  if (fs.length > 3) fs = [...new Set(fs.map((f) => (f.startsWith("BCB/SGS") ? "BCB/SGS" : f)))];
  let h = fs.map((f) => `<span class="tag ${f.startsWith("Simulado") ? "sim" : ""}">${esc(f)}</span>`).join(" ");
  if (c.rows && c.tipo === "table" && c.rows.some((r) => r.simulado)) h += ' <span class="tag sim">Algumas linhas simuladas</span>';
  return h + (c.nota ? ` <span>${esc(c.nota)}</span>` : "");
}

function opcoes(c) {
  let h = "";
  if (c.views && c.views.length > 1) h += `<select data-view="${c.id}" aria-label="Visão">${c.views.map((v, i) =>
    `<option value="${i}" ${(state.opts[c.id] || 0) == i ? "selected" : ""}>${esc(v.nome)}</option>`).join("")}</select>`;
  if (c.base100) h += `<label><input type="checkbox" data-b100="${c.id}" ${state.opts[c.id + ":b100"] !== false ? "checked" : ""}> Base 100</label>`;
  return h;
}

function traces(c) {
  if (c.tipo === "pie") return [{ type: "pie", hole: 0.45, labels: c.rows.map((r) => r.grupo), values: c.rows.map((r) => r.peso),
    marker: { colors: COLORS.concat(["#5c7a99", "#a3b18a", "#bc6c25"]) }, textinfo: "percent", hovertemplate: "%{label}<br>Peso: %{value:.2f}%<extra></extra>", sort: false }];
  if (c.tipo === "hbar") {
    const r = [...c.rows].sort((a, b) => a.contribuicao_pp - b.contribuicao_pp);
    return [{ type: "bar", orientation: "h", y: r.map((x) => x.grupo), x: r.map((x) => x.contribuicao_pp),
      marker: { color: r.map((x) => (x.contribuicao_pp >= 0 ? "#d1495b" : "#2a9d8f")) },
      customdata: r.map((x) => [x.variacao, x.peso]),
      hovertemplate: "%{y}<br>Contrib.: %{x:.3f} p.p.<br>Var.: %{customdata[0]:.2f}%<br>Peso: %{customdata[1]:.2f}%<extra></extra>" }];
  }
  if (c.tipo === "curve") {
    const curvas = [...new Set(c.rows.map((r) => r.curva))];
    return curvas.map((k, i) => { const rs = c.rows.filter((r) => r.curva === k);
      return { type: "scatter", mode: rs.length > 40 ? "lines" : "lines+markers", name: k,
        x: rs.map((r) => r.vertice_anos), y: rs.map((r) => r.taxa),
        customdata: rs.map((r) => r.dias_uteis ?? Math.round(r.vertice_anos * 252)),
        hovertemplate: "%{x:.2f} anos (%{customdata} DU): %{y:.2f}%<extra>" + esc(k) + "</extra>",
        line: { color: COLORS[i], width: i ? 1.5 : 2.5, dash: i ? "dot" : "solid" } }; });
  }
  const ss = seriesVisiveis(c);
  return ss.map((s, i) => c.tipo === "bar"
    ? { type: "bar", name: s.nome, x: s.x, y: s.y, marker: { color: ss.length === 1 ? s.y.map((v) => (v >= 0 ? "#2a9d8f" : "#d1495b")) : COLORS[i] } }
    : { type: "scatter", mode: "lines", name: s.nome, x: s.x, y: s.y, line: { color: COLORS[i], width: 2 } });
}

function tabela(c) {
  const f = (v) => (v == null ? "—" : `<span class="${v > 0 ? "pos" : v < 0 ? "neg" : ""}">${nf(v, 2)}</span>`);
  let bloco = "";
  const body = c.rows.map((r) => {
    const sep = r.bloco !== bloco ? `<tr class="grp"><td colspan="6">${esc((bloco = r.bloco))}</td></tr>` : "";
    return `${sep}<tr><td>${esc(r.item)}${r.simulado ? ' <span class="tag sim">Sim.</span>' : ""}</td><td class="mute">${r.codigo_sgs}</td>
      <td class="mute">${r.ref}</td><td>${f(r.mm_anterior)}</td><td><b>${f(r.mm_ultimo)}</b></td><td>${f(r.acum_12m)}</td></tr>`;
  }).join("");
  return `<div class="tbl"><table><thead><tr><th>Item</th><th>SGS</th><th>Ref.</th><th>m/m anterior</th><th>m/m último</th><th>12m</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function plot(c) {
  if (c.tipo === "table") return;
  c = resolve(c);
  const lay = structuredClone(LAYOUT);
  const b100 = c.base100 && state.opts[c.id + ":b100"] !== false;
  lay.yaxis.title = { text: b100 ? "Índice (base 100)" : c.unidade, font: { size: 11 } };
  lay.showlegend = c.tipo === "pie" || traces(c).length > 1;
  if (c.tipo === "pie") lay.legend = { orientation: "v", x: 1, y: 0.5 };
  if (c.tipo === "hbar") { lay.margin.l = 170; lay.hovermode = "closest"; lay.xaxis.title = { text: "p.p.", font: { size: 11 } }; delete lay.yaxis.title; }
  if (c.tipo === "curve") { lay.hovermode = "x"; lay.xaxis.title = { text: "Vencimento (anos)", font: { size: 11 } }; }
  if (c.tipo === "bar") lay.barmode = "group";
  Plotly.react(`plot_${c.id}`, traces(c), lay, CONFIG);
}

function render() {
  const d = state.data[state.aba];
  renderKpis(d.kpis);
  $("#grid").innerHTML = d.charts.map((c) => `
    <article class="card ${c.full ? "full" : ""}">
      <div class="top"><h3>${esc(c.titulo)}</h3><div class="opts">${opcoes(c)}</div></div>
      ${c.tipo === "table" ? tabela(c) : `<div class="plot ${c.full ? "tall" : ""}" id="plot_${c.id}"></div>`}
      <div class="foot"><div>${fontes(c)}</div><button class="dl" data-dl="${c.id}">⬇ CSV</button></div>
    </article>`).join("");
  d.charts.forEach(plot);
}

async function show(aba) {
  state.aba = aba;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.aba === aba));
  if (!state.data[aba]) { $("#kpis").innerHTML = ""; $("#grid").innerHTML = '<div class="loading">Carregando séries…</div>'; }
  try { await loadAba(aba); if (state.aba === aba) render(); }
  catch (e) { $("#grid").innerHTML = `<div class="err">Falha ao carregar (${esc(e.message)}). Tente novamente.</div>`; }
}

// ---------------------------------------------------------------- eventos
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) show(b.dataset.aba); });
$("#periodo").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.anos = +b.dataset.anos;
  document.querySelectorAll("#periodo button").forEach((x) => x.classList.toggle("on", x === b));
  if (state.data[state.aba]) state.data[state.aba].charts.forEach(plot);
});
$("#grid").addEventListener("click", (e) => {
  const b = e.target.closest("[data-dl]"); if (!b) return;
  csvChart(state.data[state.aba].charts.find((c) => c.id === b.dataset.dl));
});
$("#grid").addEventListener("change", (e) => {
  const t = e.target, charts = state.data[state.aba].charts;
  if (t.dataset.view) {
    const c = charts.find((x) => x.id === t.dataset.view);
    state.opts[c.id] = +t.value; plot(c);
    t.closest(".card").querySelector(".foot > div").innerHTML = fontes(c);
  }
  if (t.dataset.b100) { state.opts[t.dataset.b100 + ":b100"] = t.checked; plot(charts.find((c) => c.id === t.dataset.b100)); }
});
$("#refresh").addEventListener("click", async () => {
  $("#refresh").disabled = true;
  try { await fetch("/api/refresh", { method: "POST" }); } catch (_) { /* segue com recarga */ }
  state.data = {}; await show(state.aba); $("#refresh").disabled = false;
});
show(state.aba);
