# ✈️ Monitor de passagens — Rio → China (4 pessoas, maio/2027)

Robô que consulta o Google Flights (via SerpApi) **todo dia** e manda aviso no
Telegram e/ou e-mail quando aparece uma promoção na janela flexível.

| Item | Valor |
|---|---|
| Origem | GIG (Galeão) |
| Destinos | Pequim (PEK/PKX), Xangai (PVG/SHA), Hong Kong (HKG) |
| Data alvo | ida **12/05/2027**, volta **28/05/2027** |
| Flexibilidade | ±2 dias em cada ponta (ida 10–14, volta 26–30 = 25 combinações) |
| Passageiros | 4 adultos (o preço é o total da família) |

## Como funciona

- **Todo dia às 08:17:** consulta a data principal nos 3 destinos (3 buscas). Isso
  gera a "linha de base" de preço normal.
- **Domingo às 10:43:** varre a grade flexível. Ela alterna entre semanas:
  - semana par: dias −2 / 0 / +2 (27 buscas)
  - semana ímpar: dias −1 / +1 (12 buscas)
  - Em 2 semanas, as 25 combinações são cobertas.
  - Se algo estiver barato, o robô também consulta os dias vizinhos.
  - Depois manda um **resumo semanal**, que também serve para saber que o robô está vivo.
- **Resumo diário** no Telegram/e-mail: o preço de hoje nos 3 destinos, a variação desde a consulta anterior e o menor preço já visto.
- **Painel web** (`docs/index.html`): gráfico da evolução do preço e grade colorida das 25 combinações de datas, atualizado a cada execução.
- **Quando avisa** (qualquer uma das regras abaixo):
  - preço ≥ 15% abaixo da mediana dos últimos 14 dias da data principal;
  - o Google classifica o preço como "low";
  - teto absoluto opcional (`teto_absoluto` no `config.yaml`).
- **Sem spam:** a mesma combinação só gera aviso de novo se cair mais 5%.
- **Trava de orçamento:** para em 240 buscas por mês (o plano grátis da SerpApi tem 250).
  O uso esperado fica entre 170 e 200 por mês.

Tudo isso é ajustável em `config.yaml`.

## Setup (≈10 min)

1. **SerpApi:** crie uma conta grátis em serpapi.com e copie a API key.
2. **GitHub:** crie um repositório (veja o item 5 sobre público × privado) e envie estes arquivos.
3. **Telegram** (recomendado):
   - fale com o **@BotFather**, use `/newbot` e copie o token;
   - mande qualquer mensagem para o seu bot;
   - abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o `chat.id`.
4. **Secrets do repositório** (Settings → Secrets and variables → Actions):
   - `SERPAPI_KEY` (obrigatório)
   - `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`
   - opcional, por e-mail: `SMTP_USER` (seu Gmail), `SMTP_PASS` (uma *senha de app* do
     Google, não a sua senha normal) e `EMAIL_TO`
5. **Painel web (para consultar de qualquer lugar):**
   - Settings → Pages → *Deploy from a branch* → `main` / pasta `/docs` → Save
   - O endereço fica `https://<seu-usuario>.github.io/<nome-do-repo>/`
   - Em Settings → Secrets and variables → Actions → aba **Variables**, crie
     `PAINEL_URL` com esse endereço. O link passa a vir no resumo diário.
   - ⚠️ No plano grátis do GitHub, o Pages só publica repositório **público**.
     Isso é seguro: os *secrets* (chaves) nunca aparecem. Ficam públicos só o
     código e o histórico de preços. Se quiser o repositório privado, precisa do
     GitHub Pro, ou dispense o painel e use só o Telegram.
6. **Primeiro teste:** Actions → *Monitor de passagens* → **Run workflow** → `diario`.

## ⚠️ Confira na primeira execução

Abra o link do resumo e compare com o que o robô registrou. O ponto a conferir é se
o valor da SerpApi é o **total para 4 pessoas** ou o **valor por pessoa**. O código
assume que é o total. Se for por pessoa, me avise que eu ajusto (é uma linha).

## Teste offline (sem gastar cota)

```bash
pip install -r requirements.txt
python tests/gerar_fixture.py /tmp/fx.json
python monitor.py --modo diario    --mock /tmp/fx.json --sem-aviso
python monitor.py --modo varredura --mock /tmp/fx.json --sem-aviso --semana 47
```

## Arquivos de dados (gravados pelo próprio robô)

- `data/history.csv`: todas as consultas. Dá para abrir no Excel e fazer um gráfico.
- `data/alerts.json`: o que já foi avisado (evita repetir aviso).
- `data/usage.json`: buscas gastas por mês.
