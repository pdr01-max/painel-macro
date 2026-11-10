"""Dashboard Macro — Brasil & EUA.  Rodar: uvicorn api_app:app --host 0.0.0.0 --port 7860"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import macro_data as md

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Dashboard Macro", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --------------------------------------------------------------------------- serialização
def ser(nome: str, df: pd.DataFrame) -> dict:
    return {"nome": nome, "fonte": df["fonte"].iloc[0] if len(df) else "—",
            "x": df["data"].dt.strftime("%Y-%m-%d").tolist(), "y": df["valor"].round(4).tolist()}


def chart(cid, titulo, unidade, series, tipo="line", **extra):
    return {"id": cid, "titulo": titulo, "unidade": unidade, "tipo": tipo,
            "series": [ser(n, d) for n, d in series.items()], **extra}


def views_chart(cid, titulo, views, **extra):
    """Card com seletor de visão: views = [(nome, unidade, tipo, {serie: df})]."""
    return {"id": cid, "titulo": titulo, "tipo": "views", **extra,
            "views": [{"nome": n, "unidade": u, "tipo": t, "series": [ser(k, d) for k, d in ss.items()]}
                      for n, u, t, ss in views]}


def kpi(label, df, casas=2, suf=""):
    v = float(df["valor"].iloc[-1]); d = float(v - df["valor"].iloc[-2]) if len(df) > 1 else None
    return {"label": label, "valor": v, "delta": d, "ref": df["data"].iloc[-1].strftime("%m/%Y"),
            "casas": casas, "suf": suf, "simulado": df["fonte"].iloc[0] == "Simulado"}


def load(*keys):
    with ThreadPoolExecutor(max_workers=6) as ex:  # BCB devolve 502 com muita concorrência
        return dict(zip(keys, ex.map(md.get, keys)))


# --------------------------------------------------------------------------- abas
def _aberturas(s):
    """Cards por bloco (m/m e 12m) + tabela-resumo com o último dado de cada abertura."""
    ipca = s["ipca"]
    ipca12 = s[f"sgs:{md.IPCA_12M_OFICIAL}"]
    if ipca12["fonte"].iloc[0] == "Simulado":
        ipca12 = md.acum_12m(ipca)
    cards, linhas = [], []

    def linha(bloco, nome, codigo, mm, a12):
        linhas.append({"bloco": bloco, "item": nome, "codigo_sgs": codigo,
                       "ref": mm["data"].iloc[-1].strftime("%m/%Y"),
                       "mm_ultimo": round(float(mm["valor"].iloc[-1]), 2),
                       "mm_anterior": round(float(mm["valor"].iloc[-2]), 2),
                       "acum_12m": round(float(a12["valor"].iloc[-1]), 2) if len(a12) else None,
                       "simulado": mm["fonte"].iloc[0] == "Simulado"})

    linha("Índice cheio", "IPCA", 433, ipca, ipca12)
    for i, (bloco, itens) in enumerate(md.ABERTURAS.items()):
        mm = {nome: s[f"sgs:{c}"] for c, nome in itens.items()}
        a12 = {nome: md.acum_12m(d) for nome, d in mm.items()}
        for c, nome in itens.items():
            linha(bloco, nome, c, mm[nome], a12[nome])
        cards.append(views_chart(f"ipca_ab_{i}", f"IPCA — {bloco}", [
            ("Acumulado 12m", "% 12m", "line", {"IPCA": ipca12, **a12}),
            ("Variação mensal", "% m/m", "line", {"IPCA": ipca, **mm})], full=True))
    dif = s[f"sgs:{md.DIFUSAO}"]
    cards.append(views_chart("ipca_difusao", "IPCA — Índice de difusão", [
        ("Mensal e média móvel 12m", "% dos itens com alta", "line",
         {"Difusão": dif, "Média móvel 12m": md.media_movel(dif)})]))
    linhas.append({"bloco": "Difusão", "item": "Índice de difusão (% itens em alta)", "codigo_sgs": md.DIFUSAO,
                   "ref": dif["data"].iloc[-1].strftime("%m/%Y"), "mm_ultimo": round(float(dif["valor"].iloc[-1]), 2),
                   "mm_anterior": round(float(dif["valor"].iloc[-2]), 2),
                   "acum_12m": round(float(md.media_movel(dif)["valor"].iloc[-1]), 2),
                   "simulado": dif["fonte"].iloc[0] == "Simulado"})
    tabela = {"id": "ipca_aberturas_tabela", "titulo": "Aberturas do IPCA — último dado", "tipo": "table", "full": True,
              "fonte": "BCB/SGS", "rows": linhas,
              "nota": "Difusão: coluna 12m = média móvel de 12 meses do índice (não é acumulado)."}
    return tabela, cards


def aba_inflacao():
    cods = [f"sgs:{c}" for bloco in md.ABERTURAS.values() for c in bloco] + [f"sgs:{md.DIFUSAO}", f"sgs:{md.IPCA_12M_OFICIAL}"]
    s = load("ipca", "ipca15", "igpm", "selic_meta", "selic_ef", *cods)
    i12, i15, g12 = md.acum_12m(s["ipca"]), md.acum_12m(s["ipca15"]), md.acum_12m(s["igpm"])
    pesos = md.get_pesos_ipca().copy()
    pesos["contribuicao_pp"] = (pesos["peso"] * pesos["variacao"] / 100).round(4)
    di = md.curva_di()
    tabela, ab_cards = _aberturas(s)
    return {
        "kpis": [kpi("IPCA 12m", i12, suf="%"), kpi("IPCA-15 12m", i15, suf="%"),
                 kpi("IGP-M 12m", g12, suf="%"), kpi("Selic meta", s["selic_meta"], suf="%")],
        "charts": [
            chart("inflacao_12m", "Inflação acumulada em 12 meses", "%", {"IPCA": i12, "IPCA-15": i15, "IGP-M": g12}),
            chart("inflacao_mensal", "Variação mensal", "% m/m", {"IPCA": s["ipca"], "IPCA-15": s["ipca15"]}, "bar"),
            {"id": "ipca_pesos", "titulo": "IPCA — pesos por grupo", "tipo": "pie", "unidade": "%",
             "fonte": pesos["fonte"].iloc[0], "rows": pesos.drop(columns="fonte").to_dict("records")},
            {"id": "ipca_contrib", "titulo": "Contribuição para o IPCA do mês", "tipo": "hbar", "unidade": "p.p.",
             "fonte": pesos["fonte"].iloc[0], "rows": pesos.drop(columns="fonte").to_dict("records")},
            tabela, *ab_cards,
            chart("selic", "Taxa Selic", "% a.a.", {"Selic meta": s["selic_meta"], "Selic efetiva": s["selic_ef"]}),
            {"id": "curva_di", "titulo": "Curva de juros DI x Pré — estrutura a termo", "tipo": "curve",
             "unidade": "% a.a. (252)", "fonte": di["fonte"].iloc[0],
             "rows": di.drop(columns="fonte").round(4).to_dict("records")},
        ]}


def aba_atividade():
    s = load("ibc", "pib", "desoc", "caged", "pim", "pmc")
    # SGS 28763 é o ESTOQUE de empregos formais; o saldo mensal é a variação
    if s["caged"]["fonte"].iloc[0] != "Simulado":
        s["caged"] = md.diff(s["caged"])
    return {
        "kpis": [kpi("IBC-Br", s["ibc"], 1), kpi("Desocupação", s["desoc"], 1, "%"),
                 kpi("CAGED", s["caged"], 0), kpi("PIM", s["pim"], 1)],
        "charts": [
            chart("ibc_br", "IBC-Br (dessazonalizado)", "Índice", {"IBC-Br": s["ibc"]}),
            chart("pib_12m", "PIB nominal acumulado em 12 meses", "R$ milhões", {"PIB 12m": md.soma_12m(s["pib"])}),
            chart("desocupacao", "Taxa de desocupação — PNAD Contínua", "%", {"Desocupação": s["desoc"]}),
            chart("caged", "Saldo do Novo CAGED", "Vagas", {"Saldo CAGED": s["caged"]}, "bar"),
            chart("pim_pmc", "Produção industrial e varejo — variação interanual", "% a/a",
                  {"PIM": md.yoy(s["pim"]), "PMC": md.yoy(s["pmc"])}, full=True),
        ]}


def aba_externo():
    s = load("ptax", "balanca", "reservas", "brent", "ferro", "soja")
    vols = {f"{j}d": md.vol_realizada(s["ptax"], j) for j in (21, 63, 126, 252)}
    return {
        "kpis": [kpi("PTAX", s["ptax"], 4), kpi("Balança (mês)", s["balanca"], 0, " mi"),
                 kpi("Reservas", s["reservas"], 0, " mi"), kpi("Brent", s["brent"], 2)],
        "charts": [
            chart("ptax", "Dólar PTAX venda (R$/US$)", "R$", {"PTAX venda": s["ptax"]}),
            views_chart("vol_ptax", "Volatilidade realizada anualizada do câmbio",
                        [(f"Janela {k}", "% a.a.", "line", {f"Vol. {k}": d}) for k, d in vols.items()]),
            chart("balanca", "Balança comercial — saldo mensal", "US$ milhões", {"Saldo": s["balanca"]}, "bar"),
            chart("reservas", "Reservas internacionais", "US$ milhões", {"Reservas": s["reservas"]}),
            chart("commodities", "Commodities (média mensal)", "US$", {
                "Brent (US$/bbl)": md.mensal(s["brent"]), "Minério de ferro (US$/t)": md.mensal(s["ferro"]),
                "Soja (US$/t)": md.mensal(s["soja"])}, base100=True, full=True),
        ]}


def aba_eua():
    s = load("cpi", "core_cpi", "pce", "core_pce", "fedfunds", "payroll")
    infl = {"CPI": md.yoy(s["cpi"]), "Core CPI": md.yoy(s["core_cpi"]),
            "PCE": md.yoy(s["pce"]), "Core PCE": md.yoy(s["core_pce"])}
    nfp = md.diff(s["payroll"])
    return {
        "kpis": [kpi("CPI a/a", infl["CPI"], suf="%"), kpi("Core PCE a/a", infl["Core PCE"], suf="%"),
                 kpi("Fed Funds", s["fedfunds"], suf="%"), kpi("NFP (var. mensal)", nfp, 0, "k")],
        "charts": [
            chart("inflacao_eua", "Inflação americana — variação interanual", "% a/a", infl, full=True),
            chart("fed_funds", "Fed Funds Effective Rate", "% a.a.", {"Fed Funds": s["fedfunds"]}),
            chart("nfp", "Non-Farm Payroll — variação mensal", "Milhares", {"Criação de vagas": nfp}, "bar"),
        ]}


ABAS = {"inflacao": aba_inflacao, "atividade": aba_atividade, "externo": aba_externo, "eua": aba_eua}


# --------------------------------------------------------------------------- rotas
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/aba/{nome}")
def api_aba(nome: str):
    if nome not in ABAS:
        raise HTTPException(404, "Aba inexistente")
    return md.cached(f"aba:{nome}", ABAS[nome])


_last_refresh = 0.0
REFRESH_COOLDOWN = 600  # evita que visitantes martelem as APIs públicas


@app.post("/api/refresh")
def api_refresh():
    global _last_refresh
    if time.time() - _last_refresh < REFRESH_COOLDOWN:
        return {"status": "cache recente — mantido"}
    _last_refresh = time.time()
    md.clear_cache()
    return {"status": "cache limpo"}
