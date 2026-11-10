"""ETL, fallback simulado e transformações das séries macro (Brasil & EUA).

Fontes sem chave de API:
    BCB/SGS    https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados
    IBGE/SIDRA https://apisidra.ibge.gov.br  (pesos/variação do IPCA por grupo)
    FRED       https://fred.stlouisfed.org/graph/fredgraph.csv?id={serie}
    Curva DI   sem API pública simples -> simulada (Nelson-Siegel)
Qualquer falha de fonte cai para dados simulados com fonte="Simulado".
"""
from __future__ import annotations

import io
import logging
import os
import time
import zipfile
import zlib
from datetime import date, timedelta
from threading import Lock

import numpy as np
import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)


def _load_dotenv(path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) -> None:
    """Carrega .env local (sem dependência extra). No Render, use Environment Variables."""
    if os.path.exists(path):
        for linha in open(path, encoding="utf-8"):
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# Aberturas do IPCA (SGS). Todas em % m/m, exceto difusão (% de itens com alta).
ABERTURAS = {
    "Grupos": {1635: "Alimentação e bebidas", 1636: "Habitação", 1637: "Artigos de residência",
               1638: "Vestuário", 1639: "Transportes", 1640: "Comunicação",
               1641: "Saúde e cuidados pessoais", 1642: "Despesas pessoais", 1643: "Educação"},
    "Serviços, industriais e alimentação": {10844: "Serviços", 27863: "Bens industriais",
                                            27864: "Alimentação no domicílio"},
    "Núcleos": {4466: "IPCA-MS", 11426: "IPCA-MA", 16122: "IPCA-DP", 28750: "IPCA-P55",
                11427: "IPCA-EX0", 16121: "IPCA-EX1", 27839: "IPCA-EX3", 28751: "IPCA-EXFE"},
    "Outras classificações": {11428: "Livres", 4449: "Monitorados", 4447: "Comercializáveis",
                              4448: "Não comercializáveis", 10843: "Bens não duráveis",
                              10842: "Bens semiduráveis", 10841: "Bens duráveis"},
}
DIFUSAO = 21379
IPCA_12M_OFICIAL = 13522
TIMEOUT = 20
HEADERS = {"User-Agent": "macro-dashboard/1.0"}
CACHE_TTL = 6 * 3600

SGS = {
    "ipca": 433, "ipca15": 7478, "igpm": 189, "selic_meta": 432, "selic_ef": 1178,
    "ibc": 24364, "pib": 4380, "desoc": 24369, "caged": 28763, "pim": 21859, "pmc": 1455,
    "ptax": 1, "balanca": 22707, "reservas": 3546,
}
SGS_DIARIAS = {1, 432, 1178}  # SGS limita séries diárias a janelas de 10 anos
FRED = {
    "cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "pce": "PCEPI", "core_pce": "PCEPILFE",
    "fedfunds": "FEDFUNDS", "payroll": "PAYEMS", "brent": "DCOILBRENTEU",
    "ferro": "PIORECRUSDM", "soja": "PSOYBUSDM",
}

# --------------------------------------------------------------------------- cache
_cache: dict[str, tuple[float, object]] = {}
_lock = Lock()


MOCK_TTL = 10 * 60  # série simulada (fonte falhou) é retentada em 10 min


def _is_mock(val) -> bool:
    if isinstance(val, pd.DataFrame):
        return len(val) > 0 and str(val["fonte"].iloc[0]).startswith("Simulado")
    if isinstance(val, dict):  # payload de aba: retenta se alguma série caiu no simulado
        return any(k.get("simulado") for k in val.get("kpis", [])) or any(
            s["fonte"] == "Simulado" for c in val.get("charts", []) for s in c.get("series", []))
    return False


def cached(key: str, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < (MOCK_TTL if _is_mock(hit[1]) else CACHE_TTL):
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _http_get(url: str, params: dict | None = None, tentativas: int = 3, timeout: int = TIMEOUT):
    """GET com retry e backoff exponencial (BCB devolve 502 intermitente, FRED oscila)."""
    for i in range(tentativas):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tentativas - 1:
                raise
            time.sleep(1.5 * 2 ** i)


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# --------------------------------------------------------------------------- mock
MOCK = {
    "ipca": dict(nivel=0.4, vol=0.25, tipo="pct"), "ipca15": dict(nivel=0.4, vol=0.25, tipo="pct"),
    "igpm": dict(nivel=0.5, vol=0.7, tipo="pct"), "selic_meta": dict(nivel=11, vol=0.3, tipo="ar"),
    "selic_ef": dict(nivel=11, vol=0.3, tipo="ar"), "ibc": dict(nivel=140, vol=0.8, drift=0.0015),
    "pib": dict(nivel=500_000, vol=1.0, drift=0.006), "desoc": dict(nivel=9.5, vol=0.2, tipo="ar"),
    "caged": dict(nivel=100_000, vol=60_000, tipo="pct"), "pim": dict(nivel=100, vol=1.5),
    "pmc": dict(nivel=100, vol=1.2, drift=0.001), "ptax": dict(freq="B", nivel=3.5, vol=0.8, drift=0.0003),
    "balanca": dict(nivel=5000, vol=2500, tipo="pct"), "reservas": dict(nivel=360_000, vol=0.8),
    "cpi": dict(nivel=230, vol=0.2, drift=0.002), "core_cpi": dict(nivel=240, vol=0.15, drift=0.002),
    "pce": dict(nivel=100, vol=0.15, drift=0.0018), "core_pce": dict(nivel=100, vol=0.12, drift=0.0018),
    "fedfunds": dict(nivel=2.5, vol=0.25, tipo="ar"), "payroll": dict(nivel=140_000, vol=0.1, drift=0.0012),
    "brent": dict(freq="B", nivel=70, vol=2.0), "ferro": dict(nivel=100, vol=6.0), "soja": dict(nivel=400, vol=4.0),
}


def _rng(seed: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(seed.encode()))  # determinístico entre processos


def mock_series(nome: str, freq: str = "MS", anos: int = 15, nivel: float = 100.0,
                vol: float = 1.0, tipo: str = "rw", drift: float = 0.0) -> pd.DataFrame:
    rng = _rng(nome)
    per = {"MS": 12, "B": 252}[freq] * anos
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=per, freq=freq)
    eps = rng.normal(0, vol, len(idx))
    if tipo == "rw":
        y = nivel * np.exp(np.cumsum(drift + eps / 100))
    else:
        y = np.empty(len(idx)); y[0] = nivel
        k = 0.05 if tipo == "ar" else 0.4
        for t in range(1, len(idx)):
            y[t] = y[t - 1] + k * (nivel - y[t - 1]) + eps[t] if tipo == "ar" else nivel + 0.6 * (y[t - 1] - nivel) + eps[t]
    return pd.DataFrame({"data": idx, "valor": y, "fonte": "Simulado"})


def mock_pesos_ipca() -> pd.DataFrame:
    grupos = ["Alimentação e bebidas", "Habitação", "Artigos de residência", "Vestuário", "Transportes",
              "Saúde e cuidados pessoais", "Despesas pessoais", "Educação", "Comunicação"]
    pesos = [21.5, 15.2, 3.8, 4.6, 20.6, 13.6, 10.2, 6.0, 4.5]
    var = _rng("pesos").normal(0.35, 0.4, len(grupos)).round(2)
    return pd.DataFrame({"grupo": grupos, "peso": pesos, "variacao": var, "fonte": "Simulado"})


def mock_curva_di() -> pd.DataFrame:
    """Curva DI simulada (Nelson-Siegel) — fallback se a B3 estiver fora."""
    venc = np.array([0.25, 0.5, 1, 1.5, 2, 3, 4, 5, 7, 10])

    def ns(b0, b1, b2, lam=1.5):
        x = venc / lam; f = (1 - np.exp(-x)) / x
        return b0 + b1 * f + b2 * (f - np.exp(-x))

    hoje = pd.Timestamp.today()
    curvas = {f"Hoje ({hoje:%d/%m/%Y})": ns(13.4, 1.6, -2.0), "1 mês atrás": ns(13.6, 1.4, -1.6),
              "1 ano atrás": ns(12.8, -1.5, 1.0)}
    return pd.DataFrame([{"curva": k, "vertice_anos": v, "taxa": t}
                         for k, ts in curvas.items() for v, t in zip(venc, ts)]).assign(fonte="Simulado (Nelson-Siegel)")


# --------------------------------------------------------------------------- fontes
def fetch_sgs(codigo: int, anos: int = 25) -> pd.DataFrame:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
    fim = date.today(); a = fim - timedelta(days=365 * anos)
    passo = timedelta(days=365 * 10 - 1) if codigo in SGS_DIARIAS else fim - a
    partes = []
    while a <= fim:
        b = min(a + passo, fim)
        r = _http_get(url, {"formato": "json", "dataInicial": a.strftime("%d/%m/%Y"),
                            "dataFinal": b.strftime("%d/%m/%Y")})
        partes.append(pd.DataFrame(r.json()))
        a = b + timedelta(days=1)
    df = pd.concat(partes, ignore_index=True)
    df["data"] = pd.to_datetime(df["data"], dayfirst=True)
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    df = df.dropna().drop_duplicates("data").sort_values("data")
    if df.empty:
        raise ValueError(f"SGS {codigo} vazio")
    return df.assign(fonte=f"BCB/SGS {codigo}")[["data", "valor", "fonte"]]


def fetch_fred(serie: str) -> pd.DataFrame:
    """Usa a API oficial se FRED_API_KEY estiver definida (grátis, mais estável); senão o CSV público."""
    key = os.getenv("FRED_API_KEY")
    if key:
        r = _http_get("https://api.stlouisfed.org/fred/series/observations",
                      {"series_id": serie, "api_key": key, "file_type": "json"})
        df = pd.DataFrame(r.json()["observations"])[["date", "value"]]
    else:
        r = _http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={serie}", tentativas=1, timeout=30)
        df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["data", "valor"]
    df["data"] = pd.to_datetime(df["data"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    df = df.dropna()
    if df.empty:
        raise ValueError(f"FRED {serie} vazio")
    return df.assign(fonte=f"FRED {serie}")


def fetch_pesos_ipca() -> pd.DataFrame:
    grupos = "7170,7445,7486,7558,7625,7660,7712,7766,7786"
    url = f"https://apisidra.ibge.gov.br/values/t/7060/n1/all/v/63,66/p/last%201/c315/{grupos}"
    r = _http_get(url)
    df = pd.DataFrame(r.json()[1:])[["D2C", "D4N", "D3N", "V"]]
    df.columns = ["var", "grupo", "mes", "v"]
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    df["grupo"] = df["grupo"].str.replace(r"^\d+\.", "", regex=True).str.strip()
    mes = df["mes"].iloc[0]
    out = df.pivot_table(index="grupo", columns="var", values="v").reset_index()
    out = out.rename(columns={"63": "variacao", "66": "peso"})[["grupo", "peso", "variacao"]]
    if out.empty or out.isna().any().any():
        raise ValueError("SIDRA incompleto")
    return out.assign(fonte=f"IBGE/SIDRA 7060 ({mes})")


def get(key: str) -> pd.DataFrame:
    """key: nome em SGS/FRED ou 'sgs:<codigo>' para qualquer série do SGS."""
    def load():
        try:
            if key.startswith("sgs:"):
                return fetch_sgs(int(key[4:]))
            if key in SGS:
                return fetch_sgs(SGS[key])
            if key in FRED:
                return fetch_fred(FRED[key])
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Fonte falhou para %s: %s — usando simulado", key, exc)
        default = dict(nivel=55, vol=6, tipo="ar") if key == f"sgs:{DIFUSAO}" else dict(nivel=0.4, vol=0.3, tipo="pct")
        return mock_series(key, **MOCK.get(key, default))
    return cached(key, load)


# --------------------------------------------------------------------------- curva DI (B3)
def fetch_taxaswap(dia: date) -> pd.DataFrame:
    """Arquivo público TaxaSwap da B3 (pesquisapregao). Curva 'PRE' = DI x Pré, base 252.
    Layout (posições 1-based): 22-26 código da taxa, 42-46 dias corridos, 47-51 dias úteis,
    52 sinal, 53-66 taxa (7 decimais)."""
    r = _http_get(f"https://www.b3.com.br/pesquisapregao/download?filelist=TS{dia:%y%m%d}.ex_,",
                  tentativas=2, timeout=30)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:          # zip externo
        inner = z.read(z.namelist()[0])
    with zipfile.ZipFile(io.BytesIO(inner)) as z:              # .ex_ autoextraível também é zip
        txt = z.read(z.namelist()[0]).decode("latin1")
    rows = []
    for l in txt.splitlines():
        if l[21:26].strip() == "PRE":
            taxa = int(l[52:66]) / 1e7 * (-1 if l[51] == "-" else 1)
            rows.append({"dias_corridos": int(l[41:46]), "dias_uteis": int(l[46:51]), "taxa": taxa})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"TaxaSwap {dia} sem curva PRE")
    return df


def _curva_ate(alvo: date, max_volta: int = 10) -> tuple[date, pd.DataFrame]:
    """Procura o último pregão disponível em até `max_volta` dias antes de `alvo`."""
    erro = None
    for i in range(max_volta):
        d = alvo - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            return d, fetch_taxaswap(d)
        except Exception as exc:  # noqa: BLE001  (feriado / arquivo ainda não publicado)
            erro = exc
    raise ValueError(f"Nenhuma curva B3 até {alvo}: {erro}")


def curva_di() -> pd.DataFrame:
    def load():
        try:
            hoje = date.today()
            d0, c0 = _curva_ate(hoje)
            alvos = {"1 mês atrás": d0 - timedelta(days=30), "1 ano atrás": d0 - timedelta(days=365)}
            partes = [c0.assign(curva=f"{d0:%d/%m/%Y}")]
            for nome, alvo in alvos.items():
                try:
                    d, c = _curva_ate(alvo)
                    partes.append(c.assign(curva=f"{nome} ({d:%d/%m/%Y})"))
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Curva %s indisponível: %s", nome, exc)
            df = pd.concat(partes, ignore_index=True)
            df = df[df["dias_uteis"] <= 252 * 10]
            df["vertice_anos"] = (df["dias_uteis"] / 252).round(4)
            return df[["curva", "vertice_anos", "dias_uteis", "dias_corridos", "taxa"]].assign(fonte="B3 TaxaSwap (DI x Pré)")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("B3 falhou: %s — curva simulada", exc)
            return mock_curva_di()
    return cached("curva_di", load)


def get_pesos_ipca() -> pd.DataFrame:
    def load():
        try:
            return fetch_pesos_ipca()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("SIDRA falhou: %s — usando simulado", exc)
            return mock_pesos_ipca()
    return cached("pesos_ipca", load)


# --------------------------------------------------------------------------- transformações
def _out(s: pd.Series, fonte: str) -> pd.DataFrame:
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    return pd.DataFrame({"data": s.index, "valor": s.values, "fonte": fonte})


def acum_12m(df):
    s = df.set_index("data")["valor"]
    return _out(((1 + s / 100).rolling(12).apply(np.prod, raw=True) - 1) * 100, df["fonte"].iloc[0])


def yoy(df):
    s = df.set_index("data")["valor"].resample("MS").last()
    return _out(s.pct_change(12, fill_method=None) * 100, df["fonte"].iloc[0])


def mensal(df):
    return _out(df.set_index("data")["valor"].resample("MS").mean(), df["fonte"].iloc[0])


def soma_12m(df):
    return _out(df.set_index("data")["valor"].rolling(12).sum(), df["fonte"].iloc[0])


def diff(df):
    return _out(df.set_index("data")["valor"].diff(), df["fonte"].iloc[0])


def media_movel(df, n=12):
    return _out(df.set_index("data")["valor"].rolling(n).mean(), df["fonte"].iloc[0])


def vol_realizada(df, janela):
    s = df.set_index("data")["valor"]
    return _out(np.log(s).diff().rolling(janela).std() * np.sqrt(252) * 100, df["fonte"].iloc[0])
