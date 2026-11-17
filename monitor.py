#!/usr/bin/env python3
"""
Monitor de passagens aéreas (Google Flights via SerpApi).

Modos:
  diario     -> consulta só a data principal, para cada destino (3 buscas)
  varredura  -> varre a grade de datas flexíveis (+ vizinhos de achados baratos)

Uso:
  python monitor.py --modo diario
  python monitor.py --modo varredura
  python monitor.py --modo diario --mock tests/fixture.json --sem-aviso   # teste offline
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import smtplib
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
HIST = DADOS / "history.csv"
USO = DADOS / "usage.json"
ALERTAS = DADOS / "alerts.json"

CAMPOS = [
    "consultado_em", "modo", "destino", "ida", "volta", "preco_total",
    "preco_por_pessoa", "price_level", "faixa_tipica_min", "faixa_tipica_max",
    "cias", "escalas", "duracao_ida_min", "link",
]

AGORA = datetime.now(timezone.utc)


# ------------------------------------------------------------------ utilidades
def carregar_json(caminho: Path, padrao):
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return padrao


def salvar_json(caminho: Path, obj) -> None:
    caminho.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def brl(v: float) -> str:
    return "R$ " + f"{v:,.0f}".replace(",", ".")


def d(s: str) -> date:
    return date.fromisoformat(s)


# ------------------------------------------------------------------ orçamento
class Orcamento:
    """Conta buscas por mês para não estourar a cota da SerpApi."""

    def __init__(self, limite: int):
        self.limite = limite
        self.uso = carregar_json(USO, {})
        self.mes = AGORA.strftime("%Y-%m")

    @property
    def usadas(self) -> int:
        return self.uso.get(self.mes, 0)

    def pode(self) -> bool:
        return self.usadas < self.limite

    def gastar(self) -> None:
        self.uso[self.mes] = self.usadas + 1
        salvar_json(USO, self.uso)


# ------------------------------------------------------------------ busca
def link_google(cfg, aeroportos: str, ida: str, volta: str) -> str:
    destino = aeroportos.split(",")[0]
    q = f"Flights to {destino} from {cfg['origem']} on {ida} through {volta} for {cfg['adultos']} adults"
    return f"https://www.google.com/travel/flights?q={quote(q)}&curr={cfg['moeda']}&hl=pt-BR"


def buscar(cfg, aeroportos: str, ida: str, volta: str, mock: dict | None) -> dict:
    if mock is not None:
        chave = f"{aeroportos}|{ida}|{volta}"
        return mock.get(chave, mock.get("_default", {}))

    params = {
        "engine": "google_flights",
        "departure_id": cfg["origem"],
        "arrival_id": aeroportos,
        "outbound_date": ida,
        "return_date": volta,
        "type": "1",                 # ida e volta
        "adults": cfg["adultos"],
        "currency": cfg["moeda"],
        "hl": cfg["idioma"],
        "gl": cfg["pais"],
        "api_key": os.environ["SERPAPI_KEY"],
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=90)
    r.raise_for_status()
    return r.json()


def interpretar(cfg, resp: dict, destino: str, aeroportos: str, ida: str, volta: str, modo: str) -> dict | None:
    opcoes = (resp.get("best_flights") or []) + (resp.get("other_flights") or [])
    opcoes = [o for o in opcoes if isinstance(o.get("price"), (int, float))]
    if not opcoes:
        return None
    melhor = min(opcoes, key=lambda o: o["price"])
    trechos = melhor.get("flights", [])
    cias = sorted({t.get("airline", "?") for t in trechos})
    insights = resp.get("price_insights") or {}
    faixa = insights.get("typical_price_range") or [None, None]
    link = (resp.get("search_metadata") or {}).get("google_flights_url") or link_google(cfg, aeroportos, ida, volta)
    return {
        "consultado_em": AGORA.isoformat(timespec="seconds"),
        "modo": modo,
        "destino": destino,
        "ida": ida,
        "volta": volta,
        "preco_total": float(melhor["price"]),
        "preco_por_pessoa": round(float(melhor["price"]) / cfg["adultos"], 2),
        "price_level": insights.get("price_level", ""),
        "faixa_tipica_min": faixa[0] if len(faixa) > 0 else None,
        "faixa_tipica_max": faixa[1] if len(faixa) > 1 else None,
        "cias": " + ".join(cias),
        "escalas": max(len(trechos) - 1, 0),
        "duracao_ida_min": melhor.get("total_duration", ""),
        "link": link,
    }


# ------------------------------------------------------------------ histórico
def ler_historico() -> list[dict]:
    if not HIST.exists():
        return []
    with HIST.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gravar(linhas: list[dict]) -> None:
    novo = not HIST.exists()
    with HIST.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        for l in linhas:
            w.writerow({k: l.get(k, "") for k in CAMPOS})


def mediana_principal(cfg, hist: list[dict], destino: str) -> float | None:
    """Mediana do preço da DATA PRINCIPAL nos últimos N dias — é a referência do 'normal'."""
    corte = AGORA - timedelta(days=cfg["alerta"]["janela_mediana_dias"])
    precos = [
        float(h["preco_total"]) for h in hist
        if h["destino"] == destino
        and h["ida"] == cfg["ida_principal"] and h["volta"] == cfg["volta_principal"]
        and datetime.fromisoformat(h["consultado_em"]) >= corte
    ]
    return statistics.median(precos) if len(precos) >= 3 else None


# ------------------------------------------------------------------ regras
def avaliar(cfg, r: dict, base: float | None) -> list[str]:
    a = cfg["alerta"]
    motivos = []
    if a.get("usar_price_level_low") and r["price_level"] == "low":
        motivos.append("Google classifica como preço BAIXO")
    if base:
        queda = (base - r["preco_total"]) / base * 100
        if queda >= a["queda_pct"]:
            motivos.append(f"{queda:.0f}% abaixo da mediana recente ({brl(base)})")
    if a.get("teto_absoluto") and r["preco_total"] <= a["teto_absoluto"]:
        motivos.append(f"abaixo do teto de {brl(a['teto_absoluto'])}")
    return motivos


def deve_reavisar(cfg, estado: dict, r: dict) -> bool:
    chave = f"{r['destino']}|{r['ida']}|{r['volta']}"
    anterior = estado.get(chave)
    if anterior is None:
        return True
    return r["preco_total"] <= anterior * (1 - cfg["alerta"]["reaviso_queda_pct"] / 100)


# ------------------------------------------------------------------ avisos
def enviar(titulo: str, corpo: str, sem_aviso: bool) -> None:
    print(f"\n=== {titulo} ===\n{corpo}\n")
    if sem_aviso:
        return

    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": chat, "text": f"✈️ {titulo}\n\n{corpo}", "disable_web_page_preview": True},
                timeout=30,
            ).raise_for_status()
        except Exception as e:  # aviso nunca deve derrubar o monitor
            print(f"[telegram] falhou: {e}", file=sys.stderr)

    user, pwd, para = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"), os.getenv("EMAIL_TO")
    if user and pwd and para:
        try:
            msg = MIMEText(corpo, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = f"✈️ {titulo}", user, para
            with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as s:
                s.login(user, pwd)
                s.send_message(msg)
        except Exception as e:
            print(f"[email] falhou: {e}", file=sys.stderr)


def formatar(r: dict, motivos: list[str] | None = None) -> str:
    dias = (d(r["volta"]) - d(r["ida"])).days
    txt = (
        f"{r['destino']}: {brl(r['preco_total'])} p/ 4 ({brl(r['preco_por_pessoa'])}/pessoa)\n"
        f"  {d(r['ida']):%d/%m} → {d(r['volta']):%d/%m} ({dias} dias) · {r['cias']} · {r['escalas']} escala(s)"
    )
    if r.get("faixa_tipica_min"):
        txt += f"\n  Faixa típica Google: {brl(float(r['faixa_tipica_min']))}–{brl(float(r['faixa_tipica_max']))}"
    if motivos:
        txt += "\n  Motivo: " + "; ".join(motivos)
    return txt + f"\n  {r['link']}"


# ------------------------------------------------------------------ datas
def grade(cfg, passo: int, semana: int) -> list[tuple[str, str]]:
    """Com passo 2 a grade alterna: semana par testa -2/0/+2, semana ímpar testa -1/+1.
    Em 2 semanas todas as combinações da flexibilidade são cobertas, sem estourar a cota."""
    ida0, volta0, f = d(cfg["ida_principal"]), d(cfg["volta_principal"]), cfg["flex_dias"]
    desloc = range(-f + (semana % passo), f + 1, passo)
    return [((ida0 + timedelta(i)).isoformat(), (volta0 + timedelta(v)).isoformat())
            for i in desloc for v in desloc]


def vizinhos(cfg, ida: str, volta: str) -> list[tuple[str, str]]:
    """Datas a 1 dia de distância (para refinar quando a grade é de passo 2)."""
    ida0, volta0, f = d(cfg["ida_principal"]), d(cfg["volta_principal"]), cfg["flex_dias"]
    out = []
    for di in (-1, 0, 1):
        for dv in (-1, 0, 1):
            ni, nv = d(ida) + timedelta(di), d(volta) + timedelta(dv)
            if abs((ni - ida0).days) <= f and abs((nv - volta0).days) <= f:
                out.append((ni.isoformat(), nv.isoformat()))
    return out


# ------------------------------------------------------------------ painel web
def gerar_painel(cfg, hist: list[dict], orc: "Orcamento") -> None:
    """Gera docs/index.html (publicado pelo GitHub Pages): gráfico diário + grade de datas."""
    PAINEL = RAIZ / "docs"
    PAINEL.mkdir(exist_ok=True)
    destinos = list(cfg["destinos"])

    serie = {k: [] for k in destinos}
    ultimo: dict[tuple, dict] = {}
    for h in hist:
        if h["destino"] not in serie:
            continue
        if h["ida"] == cfg["ida_principal"] and h["volta"] == cfg["volta_principal"]:
            serie[h["destino"]].append({"x": h["consultado_em"][:10], "y": float(h["preco_total"])})
        ultimo[(h["destino"], h["ida"], h["volta"])] = h  # fica a consulta mais recente

    # um ponto por dia (último do dia) para o gráfico
    for k in serie:
        porDia = {p["x"]: p["y"] for p in serie[k]}
        serie[k] = [{"x": x, "y": y} for x, y in sorted(porDia.items())]

    ida0, volta0, f = d(cfg["ida_principal"]), d(cfg["volta_principal"]), cfg["flex_dias"]
    idas = [(ida0 + timedelta(i)).isoformat() for i in range(-f, f + 1)]
    voltas = [(volta0 + timedelta(i)).isoformat() for i in range(-f, f + 1)]
    grades = {}
    for k in destinos:
        grades[k] = [[(lambda h: {"p": float(h["preco_total"]), "c": h["cias"], "l": h["link"],
                                  "q": h["consultado_em"][:10]} if h else None)(ultimo.get((k, i, v)))
                      for v in voltas] for i in idas]

    dados = {
        "serie": serie, "grades": grades, "idas": idas, "voltas": voltas,
        "principal": [cfg["ida_principal"], cfg["volta_principal"]],
        "atualizado": AGORA.astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y %H:%M"),
        "uso": f"{orc.usadas}/{orc.limite}",
    }
    html = (RAIZ / "painel_template.html").read_text(encoding="utf-8")
    (PAINEL / "index.html").write_text(
        html.replace("/*__DADOS__*/null", json.dumps(dados, ensure_ascii=False)), encoding="utf-8")


# ------------------------------------------------------------------ principal
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", choices=["diario", "varredura"], required=True)
    ap.add_argument("--mock", help="JSON com respostas falsas (teste offline)")
    ap.add_argument("--sem-aviso", action="store_true", help="não envia Telegram/e-mail")
    ap.add_argument("--semana", type=int, help="força a semana (teste da grade alternada)")
    args = ap.parse_args()

    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))
    DADOS.mkdir(exist_ok=True)
    mock = json.loads(Path(args.mock).read_text()) if args.mock else None
    if mock is None and not os.getenv("SERPAPI_KEY"):
        print("Defina SERPAPI_KEY (ou use --mock).", file=sys.stderr)
        return 2

    orc = Orcamento(cfg["orcamento_mensal"])
    hist = ler_historico()
    estado = carregar_json(ALERTAS, {})
    resultados: list[dict] = []
    feitas: set[tuple] = set()

    def consultar(destino: str, aeroportos: str, ida: str, volta: str) -> dict | None:
        if (destino, ida, volta) in feitas:
            return None
        if not orc.pode():
            print(f"[orçamento] limite de {orc.limite} buscas/mês atingido — pulando.")
            return None
        feitas.add((destino, ida, volta))
        try:
            resp = buscar(cfg, aeroportos, ida, volta, mock)
        except Exception as e:
            print(f"[erro] {destino} {ida}->{volta}: {e}", file=sys.stderr)
            return None
        if mock is None:
            orc.gastar()
        r = interpretar(cfg, resp, destino, aeroportos, ida, volta, args.modo)
        if r:
            resultados.append(r)
            print(f"  {destino:10} {ida} → {volta}  {brl(r['preco_total']):>14}  {r['price_level'] or '-'}")
        else:
            print(f"  {destino:10} {ida} → {volta}  sem resultado")
        return r

    print(f"Modo: {args.modo} · buscas usadas no mês: {orc.usadas}/{orc.limite}")

    if args.modo == "diario":
        for destino, aer in cfg["destinos"].items():
            consultar(destino, aer, cfg["ida_principal"], cfg["volta_principal"])
    else:
        semana = args.semana if args.semana is not None else AGORA.isocalendar().week
        print(f"Grade da semana {semana} ({'par' if semana % 2 == 0 else 'ímpar'})")
        for destino, aer in cfg["destinos"].items():
            for ida, volta in grade(cfg, cfg["passo_grade"], semana):
                consultar(destino, aer, ida, volta)
        # Refino: só gasta busca com vizinhos quando o mais barato é realmente interessante
        if cfg["passo_grade"] > 1:
            for destino, aer in cfg["destinos"].items():
                doDestino = [r for r in resultados if r["destino"] == destino]
                if not doDestino:
                    continue
                top = min(doDestino, key=lambda r: r["preco_total"])
                ref = mediana_principal(cfg, hist, destino) or max(r["preco_total"] for r in doDestino)
                interessante = top["price_level"] == "low" or top["preco_total"] <= ref * 0.95
                if interessante:
                    print(f"  ↳ refinando vizinhos de {destino} {top['ida']}→{top['volta']}")
                    for ida, volta in vizinhos(cfg, top["ida"], top["volta"]):
                        consultar(destino, aer, ida, volta)

    gravar(resultados)
    hist = ler_historico()  # inclui o que acabou de entrar

    # ---- alertas de promoção
    achados = []
    for r in sorted(resultados, key=lambda r: r["preco_total"]):
        motivos = avaliar(cfg, r, mediana_principal(cfg, hist, r["destino"]))
        if motivos and deve_reavisar(cfg, estado, r):
            achados.append(formatar(r, motivos))
            estado[f"{r['destino']}|{r['ida']}|{r['volta']}"] = r["preco_total"]
    salvar_json(ALERTAS, estado)

    if achados:
        enviar(f"PROMOÇÃO encontrada ({len(achados)})", "\n\n".join(achados[:8]), args.sem_aviso)

    # ---- resumo diário: preço de hoje da data principal + variação vs. consulta anterior
    if args.modo == "diario" and resultados and cfg.get("resumo_diario", True):
        linhas = []
        for r in resultados:
            ant = [h for h in hist if h["destino"] == r["destino"] and h["modo"] == "diario"
                   and h["ida"] == r["ida"] and h["volta"] == r["volta"]
                   and h["consultado_em"] < r["consultado_em"]]
            var = ""
            if ant:
                p0 = float(ant[-1]["preco_total"])
                delta = r["preco_total"] - p0
                seta = "🔻" if delta < 0 else ("🔺" if delta > 0 else "＝")
                var = f"  {seta} {brl(abs(delta))} vs. anterior" if delta else "  ＝ estável"
            melhor_janela = min((float(h["preco_total"]) for h in hist if h["destino"] == r["destino"]),
                                default=r["preco_total"])
            linhas.append(f"{r['destino']}: {brl(r['preco_total'])}{var}\n"
                          f"  menor já visto na janela: {brl(melhor_janela)}")
        url = os.getenv("PAINEL_URL")
        if url:
            linhas.append(f"\nPainel: {url}")
        enviar(f"Resumo diário — {d(cfg['ida_principal']):%d/%m}→{d(cfg['volta_principal']):%d/%m}, 4 pessoas",
               "\n\n".join(linhas), args.sem_aviso)

    gerar_painel(cfg, ler_historico(), orc)

    # ---- resumo semanal (após a varredura) — também prova que o robô está vivo
    if args.modo == "varredura" and resultados:
        linhas = []
        for destino in cfg["destinos"]:
            rs = [r for r in resultados if r["destino"] == destino]
            if rs:
                linhas.append(formatar(min(rs, key=lambda r: r["preco_total"])))
        linhas.append(f"\nBuscas usadas no mês: {orc.usadas}/{orc.limite}")
        enviar("Resumo semanal — mais barato por destino", "\n\n".join(linhas), args.sem_aviso)

    return 0


if __name__ == "__main__":
    sys.exit(main())
