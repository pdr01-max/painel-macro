# Painel Macroeconômico — Brasil & EUA

FastAPI (backend/ETL) + HTML/JS com Plotly (frontend). Mesmo modelo de deploy do Quant Risk Engine (Docker no Render).

## Rodar localmente
```powershell
.\.venv\Scripts\python.exe -m uvicorn api_app:app --reload --port 7860
# abrir http://localhost:7860
```

## Estrutura
| Arquivo | Papel |
|---|---|
| `macro_data.py` | ETL (BCB/SGS, IBGE/SIDRA, FRED), cache em memória de 6h, fallback simulado, transformações |
| `api_app.py` | Rotas: `/` (página), `/api/aba/{inflacao,atividade,externo,eua}`, `/api/refresh`, `/health` |
| `static/` | `index.html`, `app.js` (gráficos, filtro de período, download CSV), `style.css` |

## Fontes
- **BCB/SGS**: IPCA, IPCA-15, IGP-M, Selic, IBC-Br, PIB, PNAD, CAGED (estoque → saldo por diferença), PIM, PMC, PTAX, balança, reservas.
- **IBGE/SIDRA 7060**: pesos e variação dos 9 grupos do IPCA.
- **FRED**: CPI, PCE, Fed Funds, Payroll, Brent, minério, soja. Defina `FRED_API_KEY`
  (grátis em https://fred.stlouisfed.org/docs/api/api_key.html) para usar a API oficial; sem ela usa o CSV público, que costuma ser lento/instável.
- **Aberturas do IPCA (SGS)**: grupos (1635–1643), serviços/industriais/alimentação no domicílio (10844, 27863, 27864),
  núcleos (4466, 11426, 16122, 28750, 11427, 16121, 27839, 28751), outras classificações (11428, 4449, 4447, 4448, 10843, 10842, 10841),
  difusão (21379) e IPCA 12m oficial (13522). Acumulado 12m calculado a partir do m/m.
- **Curva DI x Pré**: arquivo público TaxaSwap da B3 (`b3.com.br/pesquisapregao/download?filelist=TSaammdd.ex_,`) — último pregão, ~1 mês e ~1 ano antes. Fallback Nelson-Siegel simulado.

Localmente a chave do FRED fica em `.env` (`FRED_API_KEY=...`), que está no `.gitignore` — nunca comite esse arquivo.

Séries que falham caem para dados simulados (marcados “Simulado”) e são retentadas em 10 min.

## Deploy no Render
1. Suba esta pasta para um repositório no GitHub (o `.venv` está no `.gitignore`).
2. Render → **New → Blueprint** → selecione o repositório (lê o `render.yaml`).
3. Em **Environment**, adicione `FRED_API_KEY`.
4. Plano free hiberna após inatividade: o 1º acesso demora ~1 min (boot + download das séries).
