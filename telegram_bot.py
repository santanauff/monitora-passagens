#!/usr/bin/env python3
"""
Lê comandos enviados ao bot do Telegram e responde.

Roda a cada ~5 min pelo GitHub Actions (workflow telegram.yml). Só obedece ao
chat configurado em TELEGRAM_CHAT_ID — mensagens de qualquer outra pessoa são ignoradas.

Comandos:
  /status     últimos preços gravados (não gasta busca)
  /consultar  consulta agora a data principal nos 3 destinos (3 buscas)
  /varredura  varre a grade de datas flexíveis (~12 a 35 buscas)
  /ajuda      lista os comandos
Retorna código 10 quando executou uma consulta (o workflow então grava os dados).
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

RAIZ = Path(__file__).parent
OFFSET = RAIZ / "data" / "telegram_offset.json"
HIST = RAIZ / "data" / "history.csv"
USO = RAIZ / "data" / "usage.json"

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = str(os.environ["TELEGRAM_CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"

AJUDA = (
    "Comandos:\n"
    "/status — últimos preços gravados (grátis)\n"
    "/consultar — busca agora a data principal (3 buscas)\n"
    "/varredura — busca as datas flexíveis (12 a 35 buscas)\n"
    "/ajuda — esta lista\n\n"
    "A resposta chega em ~5–15 min (o GitHub verifica o bot a cada 5 min)."
)


def responder(texto: str) -> None:
    requests.post(f"{API}/sendMessage",
                  json={"chat_id": CHAT, "text": texto, "disable_web_page_preview": True},
                  timeout=30)


def brl(v: float) -> str:
    return "R$ " + f"{v:,.0f}".replace(",", ".")


def status() -> str:
    if not HIST.exists():
        return "Ainda não há preços gravados."
    linhas = list(csv.DictReader(HIST.open(encoding="utf-8")))
    ultimo: dict[str, dict] = {}
    menor: dict[str, dict] = {}
    for h in linhas:
        k = h["destino"]
        if h["modo"] == "diario":
            ultimo[k] = h
        if k not in menor or float(h["preco_total"]) < float(menor[k]["preco_total"]):
            menor[k] = h
    uso = json.loads(USO.read_text()) if USO.exists() else {}
    partes = []
    for k, h in ultimo.items():
        m = menor[k]
        partes.append(
            f"{k}: {brl(float(h['preco_total']))} ({h['cias']})\n"
            f"  consultado {h['consultado_em'][:10]}\n"
            f"  menor já visto: {brl(float(m['preco_total']))} "
            f"({m['ida'][8:10]}/{m['ida'][5:7]}→{m['volta'][8:10]}/{m['volta'][5:7]}, {m['cias']})"
        )
    mes = max(uso) if uso else "-"
    partes.append(f"\nBuscas usadas no mês: {uso.get(mes, 0)}/240")
    return "📊 Status\n\n" + "\n\n".join(partes)


def main() -> int:
    estado = json.loads(OFFSET.read_text()) if OFFSET.exists() else {"offset": 0}
    r = requests.get(f"{API}/getUpdates",
                     params={"offset": estado["offset"], "timeout": 0}, timeout=30).json()
    if not r.get("ok"):
        print(r, file=sys.stderr)
        return 1

    comandos = []
    for u in r["result"]:
        estado["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != CHAT:
            continue  # ignora estranhos
        texto = (msg.get("text") or "").strip().lower().split("@")[0]
        comandos.append(texto)
    OFFSET.write_text(json.dumps(estado))

    if not comandos:
        print("Nenhum comando novo.")
        return 0

    rodou = False
    # várias mensagens acumuladas → executa cada tipo no máximo 1 vez
    for cmd in dict.fromkeys(comandos):
        if cmd in ("/status", "status"):
            responder(status())
        elif cmd in ("/consultar", "consultar") or (cmd in ("/varredura", "varredura")):
            modo = "diario" if "consultar" in cmd else "varredura"
            responder(f"🔎 Consultando ({modo})… o resultado chega em instantes.")
            subprocess.run([sys.executable, "monitor.py", "--modo", modo], cwd=RAIZ, check=False)
            rodou = True
        else:
            responder(AJUDA)
    return 10 if rodou else 0


if __name__ == "__main__":
    sys.exit(main())
