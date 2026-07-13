# Fase 1 — Entrega durável e o primeiro ciclo de conversa

> Concluída em 13/07/2026 (Partes 1–3 verificadas pelo dono). Este documento é a
> referência técnica da fase e a base para apresentá-la (portfólio/vídeo).

## Objetivo

A Fase 0 entregou uma porta de entrada segura e tipada, mas com uma semântica frágil:
o dedup marcava a mensagem como vista **antes** de processá-la, então uma falha depois
do ACK perdia a mensagem para sempre (**at-most-once**). A Fase 1 tem dois objetivos
encadeados:

1. **Tornar a entrada durável** — o payload cru é gravado **antes** de qualquer ACK e
   vira um buffer reprocessável; uma órfã é retomada por um varredor de fundo
   (**at-least-once**).
2. **Fechar o primeiro ciclo de conversa** — morador manda uma mensagem, o sistema
   responde um **eco** (`Eco: <texto>`) e registra os dois lados num log imutável.

As duas ideias da Fase 0 continuam mandando, agora estendidas:

- **Defensivo na borda, garantias no banco.** A borda segue transformando entrada
  não-confiável em objeto tipado (`IncomingMessage`), mas a **idempotência** deixou de
  ser responsabilidade do Python: mora inteira em índices únicos parciais e em
  `ON CONFLICT`. O banco decide o vencedor de qualquer corrida.
- **Durabilidade antes de confirmar.** Nada é confirmado (200) antes de estar commitado.
  O trabalho que pode falhar (processar, enviar) roda **depois** do registro durável, e
  o desfecho é gravado de volta na própria linha.

A fase tem duas partes: **Parte 1 — inbox durável** (evolução da borda) e
**Parte 2 — ciclo de mensagem** (o eco ponta-a-ponta). A Parte 3 foi a validação
empírica manual contra o Z-PRO real.

## Parte 1 — inbox durável

### `webhook_events` vira buffer reprocessável

Na Fase 0, `webhook_events` tinha duas colunas (`message_id` + `recebido_em`): servia só
para dedup. A migration
[`20260706191900_webhook_events_inbox.sql`](../supabase/migrations/20260706191900_webhook_events_inbox.sql)
a promove a **inbox** — um buffer que guarda o payload cru e o desfecho do processamento:

```sql
alter table public.webhook_events
  add column payload jsonb,
  add column status text not null default 'pendente'
    constraint chk_webhook_events_status
      check (status in ('pendente', 'processado', 'falhou')),
  add column processado_em timestamptz;

create index idx_webhook_events_pendentes
  on public.webhook_events (recebido_em)
  where status = 'pendente';
```

Três decisões nessa DDL:

- **`payload jsonb`** guarda a mensagem crua. Se o processamento falhar (ou o processo
  morrer), a mensagem pode ser **reparseada e reprocessada** a partir da linha — não
  depende de nada em memória.
- **`status` com default `'pendente'`** é o ciclo de vida da linha: nasce pendente, vira
  `'processado'` ou `'falhou'`. O `CHECK` fixa o vocabulário no banco.
- **`idx_webhook_events_pendentes`** é um [índice
  parcial](https://www.postgresql.org/docs/current/indexes-partial.html): indexa **só** as
  linhas `'pendente'` (o conjunto que o varredor precisa achar). Como a maioria das linhas
  vira `'processado'`, o índice fica pequeno e a varredura é barata. A migration ainda
  agenda uma limpeza diária via `pg_cron` (remove `'processado'` com mais de 30 dias),
  para a inbox não crescer sem limite.

O registro em si é um `INSERT ... ON CONFLICT DO NOTHING ... RETURNING`
([`dedup.py`](../dedup.py)), o mesmo padrão de idempotência da Fase 0 — agora carregando o
payload:

```python
async def registrar_mensagem(
    conn: asyncpg.Connection, message_id: str, payload: dict
) -> bool:
    row = await conn.fetchrow(
        """
        insert into webhook_events (message_id, payload)
        values ($1, $2)
        on conflict (message_id) do nothing
        returning message_id
        """,
        message_id,
        payload,
    )
    return row is not None
```

Se o `RETURNING` devolve linha, a mensagem é nova; se não devolve, é reentrega. A
unicidade é atômica no banco ([PostgreSQL: ON
CONFLICT](https://www.postgresql.org/docs/current/sql-insert.html#SQL-ON-CONFLICT)) — dois
workers recebendo a mesma reentrega ao mesmo tempo não gravam duas vezes.

### A rota ACK-after-durable (`webhook.py`)

A diferença semântica em relação à Fase 0 está na **ordem**. Antes: ACK primeiro,
processamento na `BackgroundTask` — se caísse depois do ACK, a mensagem se perdia
(at-most-once). Agora a rota **persiste antes de responder 200**: o ACK só sai quando a
linha está commitada. O trabalho pesado continua na `BackgroundTask`, mas mesmo que ela
nunca rode, a mensagem está salva e será recuperada (at-least-once).

```python
@router.post("/webhook/{secret}")
async def receber_webhook(
    secret: str, request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    if not hmac.compare_digest(
        secret.encode("utf-8"), settings.webhook_secret.encode("utf-8")
    ):
        raise HTTPException(status_code=404)

    raw_body = await request.body()

    extraido = _extrair_mensagem(raw_body)
    if extraido is None:
        return {"status": "ignored"}
    msg, raw = extraido

    try:
        async with get_pool().acquire() as conn:
            novo = await registrar_mensagem(conn, msg.message_id, raw)
    except Exception:
        logger.exception("webhook: falha ao persistir message_id=%s", msg.message_id)
        raise HTTPException(status_code=503)

    if novo:
        background_tasks.add_task(_processar_e_marcar, msg)
    else:
        logger.info("webhook duplicado — não reprocessa: message_id=%s", msg.message_id)

    return {"status": "ok"}
```

Os **caminhos de resposta** codificam a política de confirmação:

| Situação | Resposta | Por quê |
|---|---|---|
| Segredo errado | **404** | Comparação em tempo constante (`hmac.compare_digest`); 404 genérico não revela que a rota existe. |
| Não é mensagem real | **200** (`ignored`) | Eco `fromMe`, grupo, JSON inválido ou payload fora do formato Z-PRO — nada a guardar; confirmar evita reentrega inútil. |
| Falha ao persistir | **503** | Não confirmamos o que não gravou; o provedor pode reenviar. |
| Mensagem nova persistida | **200** (`ok`) | Linha commitada (durável) + agenda o processamento. |
| Reentrega (duplicata) | **200** (`ok`) | Gate pelo `RETURNING`: `novo == False` → não reprocessa. |

O desfecho volta para a linha na `BackgroundTask`, num `acquire` separado — o processamento
roda e o resultado (`processado`/`falhou`) é gravado com `marcar_status`, sem virar 500
para o Z-PRO ([`webhook.py`](../webhook.py), `_processar_e_marcar`):

```python
async def _processar_e_marcar(msg: IncomingMessage) -> None:
    try:
        await processar_mensagem(msg)
        status = StatusEvento.PROCESSADO
    except Exception:
        logger.exception("processamento falhou: message_id=%s", msg.message_id)
        status = StatusEvento.FALHOU

    try:
        async with get_pool().acquire() as conn:
            await marcar_status(conn, msg.message_id, status)
    except Exception:
        logger.exception("falha ao marcar status: message_id=%s", msg.message_id)
```

### O sweeper: uma fila com `FOR UPDATE SKIP LOCKED` (`sweeper.py`)

A `BackgroundTask` vive só na memória do worker. Uma linha fica **órfã** quando o processo
morre entre o 200 (INSERT commitado) e a marcação do desfecho: ela segue `'pendente'`, mas
ninguém mais vai processá-la. O **sweeper** é um loop de fundo (ligado no lifespan) que
recupera essas órfãs tratando a inbox como uma **fila**:

```python
_SQL_LOTE_ORFAS = f"""
    select message_id, payload
      from webhook_events
     where status = '{StatusEvento.PENDENTE.value}'
       and recebido_em < now() - make_interval(secs => $1)
     order by recebido_em
     limit $2
       for update skip locked
"""


async def varrer_pendentes() -> int:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                _SQL_LOTE_ORFAS,
                float(settings.sweeper_grace_seconds),
                settings.sweeper_batch_size,
            )
            for row in rows:
                status = await _reprocessar(row["message_id"], row["payload"])
                await marcar_status(conn, row["message_id"], status)
    if rows:
        logger.info("sweeper: %d órfã(s) tratada(s)", len(rows))
    return len(rows)
```

Três detalhes finos:

- **`FOR UPDATE SKIP LOCKED`** é o padrão de fila da doc oficial do `SELECT`. A doc diz,
  literalmente: *"any selected rows that cannot be immediately locked are skipped.
  Skipping locked rows provides an inconsistent view of the data, so this is not suitable
  for general purpose work, but can be used to avoid lock contention with multiple
  consumers accessing a queue-like table"* ([PostgreSQL: The Locking
  Clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE)).
  Consumidores concorrentes (vários workers, ou um worker + o sweeper) pulam linhas já
  trancadas em vez de esperar — sem dupla-tomada, sem deadlock.
- **Toda a transação é uma unidade**: tranca o lote, reprocessa e marca o desfecho na
  **mesma** transação. Crash no meio ⇒ rollback ⇒ as linhas voltam a `'pendente'` para o
  próximo ciclo. É o que garante o at-least-once do lado da recuperação.
- **Carência (`grace`)**: o `recebido_em < now() - make_interval(...)` só pega órfãs mais
  velhas que N segundos (default 300), para o sweeper não disputar com `BackgroundTask`s
  ainda em voo — que são o caminho normal e rápido.

E a sutileza que virou comentário no código: o `status = 'pendente'` entra como **literal
no SQL**, não como parâmetro. Um índice parcial só é usado quando o planner consegue provar
que o `WHERE` da consulta **implica** o predicado do índice ([PostgreSQL: partial
indexes](https://www.postgresql.org/docs/current/indexes-partial.html)); com `status = $1`,
o valor é desconhecido no momento do plano e a implicação não pode ser provada, então
`idx_webhook_events_pendentes` não seria usado. O literal resolve — e o resto dos valores
(`grace`, `batch`) segue parametrizado (`$1`, `$2`), como manda o SQL seguro.

## Parte 2 — o ciclo de mensagem (eco)

### `mensagens`: log imutável + idempotência no banco

A migration
[`20260709193445_mensagens_e_conversa_ativa.sql`](../supabase/migrations/20260709193445_mensagens_e_conversa_ativa.sql)
cria o log de conversa. Duas escolhas de modelagem: a tabela **não tem `updated_at`**
(mensagem não se edita — é um log imutável) e as garantias de idempotência são todas
índices únicos **parciais**:

```sql
create table public.mensagens (
  id uuid primary key default gen_random_uuid(),
  conversa_id uuid not null references public.conversas (id) on delete cascade,
  papel text not null check (papel in ('morador', 'assistente')),
  tipo text not null default 'text' check (tipo in ('text', 'unsupported')),
  conteudo text,
  message_id text,
  em_resposta_a uuid references public.mensagens (id) on delete set null,
  created_at timestamptz not null default now(),
  constraint chk_mensagens_assistente_conteudo
    check (papel <> 'assistente' or conteudo is not null)
);

create unique index uq_mensagens_message_id
  on public.mensagens (message_id) where (message_id is not null);
create unique index uq_mensagens_em_resposta_a
  on public.mensagens (em_resposta_a) where (em_resposta_a is not null);

-- na tabela conversas:
create unique index uq_conversas_telefone_ativa
  on public.conversas (telefone) where (status = 'ativa');
```

Cada índice único é um **gate de idempotência**:

- **`uq_mensagens_message_id`** (parcial, só quando `message_id is not null`): a mesma
  mensagem de entrada não entra duas vezes. Reentrega do sweeper conflita aqui.
- **`uq_mensagens_em_resposta_a`** (parcial, só quando `em_resposta_a is not null`): uma
  entrada tem **no máximo uma** resposta. Duas respostas concorrentes para a mesma entrada
  não violam a constraint — a segunda vira `DO NOTHING`.
- **`uq_conversas_telefone_ativa`** (parcial, `where status = 'ativa'`): um telefone tem
  **no máximo uma** conversa ativa por vez; conversas encerradas podem coexistir à vontade.

O repositório ([`mensagens.py`](../mensagens.py)) é fino de propósito: cada função é um
statement único que conversa com esses índices via `ON CONFLICT`. Quem decide o vencedor de
uma corrida é o banco, não o Python:

```python
async def upsert_conversa_ativa(conn: asyncpg.Connection, telefone: str) -> UUID:
    row = await conn.fetchrow(
        """
        insert into conversas (telefone)
        values ($1)
        on conflict (telefone) where status = 'ativa'
        do update set updated_at = now()
        returning id
        """,
        telefone,
    )
    return row["id"]
```

O `on conflict (telefone) where status = 'ativa'` referencia o índice parcial diretamente
— é a forma de mirar num índice único parcial num `ON CONFLICT` (o predicado tem que casar
com o do índice).

#### Decisão travada: o `CHECK` de conteúdo é por `papel`

```sql
constraint chk_mensagens_assistente_conteudo
  check (papel <> 'assistente' or conteudo is not null)
```

A regra é: **"assistente sempre tem conteúdo"** — uma resposta do assistente com
`conteudo` nulo é um bug e o banco a rejeita. Mensagens do **morador**, porém, podem ter
`conteudo` nulo (uma mídia/áudio que ainda não sabemos processar chega como
`tipo = 'unsupported'` sem texto). Amarrar o `NOT NULL` ao `papel`, e não à coluna,
codifica exatamente essa assimetria. **Esta é uma decisão travada da fase** — o gate de
conteúdo é por `papel`, não por `tipo` nem por coluna sempre-obrigatória.

### Adapter de saída httpx (`zpro_client.py`)

Espelho do `zpro_models.py`, no sentido inverso: o core só conhece `OutgoingMessage`; o
formato do body da API externa v2 do Z-PRO vive só aqui. O cliente `httpx.AsyncClient` tem
o **mesmo ciclo de vida do pool** — criado no startup, fechado no shutdown (via lifespan):

```python
async def enviar(msg: OutgoingMessage) -> None:
    resp = await get_cliente().post(
        "/",
        json={
            "body": msg.text,
            "number": msg.phone,
            "externalKey": msg.external_key,
            "isClosed": False,
        },
    )
    resp.raise_for_status()

    try:
        corpo = resp.json()
    except ValueError:
        corpo = None
        logger.warning("zpro: resposta 2xx sem JSON — seguindo como sucesso")

    if isinstance(corpo, dict) and corpo.get("success") is False:
        raise ZproEnvioError(f"Z-PRO respondeu 2xx com success=false: {corpo}")
```

O body segue o **contrato verificado por teste real em 09/07/2026** (documentado no
`CLAUDE.md`): `POST {ZPRO_API_URL}/` com `Authorization: Bearer`, body
`body`/`number`/`externalKey`/`isClosed`. Dois fatos desse contrato moldam o código:

- **A resposta não traz id de mensagem do WhatsApp** — só `ticketId`, que se repete por
  conversa. Vai para log, **nunca** para `mensagens.message_id` (violaria
  `uq_mensagens_message_id`).
- **`externalKey` NÃO deduplica no provedor** (verificado: dois envios com a mesma chave,
  ambos entregues). Mandamos o `message_id` da entrada como `externalKey` só para
  correlação/rastreio; **a idempotência de envio é 100% nossa**, e mora no banco.

O tratamento de erro distingue três desfechos: **não-2xx** vira `HTTPStatusError` (o
transport só retenta falha de conexão, quando o request ainda não saiu — não duplica eco);
**2xx com `success=false`** vira `ZproEnvioError`; e **2xx sem JSON** segue como sucesso
(não inventamos falha onde o contrato não declara uma). Qualquer exceção sobe para o
chamador marcar `'falhou'`.

### A fronteira transacional (`processador.py`) e a janela residual

Este é o coração da Parte 2 — onde banco e rede se encontram sem se enroscar. A regra é
dura: **nenhuma transação nem conexão do pool atravessa I/O de rede**. Uma conexão parada
esperando o Z-PRO estrangularia o próprio caminho do ACK durável (o pool é limitado pelo
teto de conexões do Supabase, compartilhado).

```python
async def processar_mensagem(msg: IncomingMessage) -> None:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            conversa_id = await upsert_conversa_ativa(conn, msg.phone)
            entrada_id, nova = await registrar_entrada(conn, conversa_id, msg)

        if not nova and await saida_ja_existe(conn, entrada_id):
            logger.info(
                "entrada já respondida — não reenvia: message_id=%s", msg.message_id
            )
            return

    texto = (
        f"Eco: {msg.text}"
        if msg.message_type is MessageType.TEXT
        else TEXTO_UNSUPPORTED
    )

    await enviar(
        OutgoingMessage(phone=msg.phone, text=texto, external_key=msg.message_id)
    )

    async with get_pool().acquire() as conn:
        await registrar_saida(conn, conversa_id, texto, entrada_id)
```

A sequência é deliberada:

1. **TX1 (curta)**: upsert da conversa ativa + INSERT da entrada, e **commita**. A conexão
   é solta logo em seguida (fim do `async with acquire`), **antes** de qualquer rede.
2. **Gate NOVA/DUPLICATA**: se a entrada não era nova **e** já tem saída gravada, retorna
   sem reenviar — não repetimos um eco que já foi respondido.
3. **Envio HTTP** roda com **zero conexão do pool presa**.
4. **A saída grava numa aquisição nova** e curta (`registrar_saida`, `ON CONFLICT
   (em_resposta_a) DO NOTHING`).

A **janela residual aceita**: se o processo cair **entre o passo 3 (envio) e o passo 4
(INSERT da saída)**, o WhatsApp já recebeu o eco, mas o banco não registrou a saída. No
reprocessamento (sweeper), a entrada é duplicata (`nova = False`) e `saida_ja_existe` é
`False` → o eco é **reenviado**. Resultado: **duplica no WhatsApp, nunca no banco** (a
saída continua única por `uq_mensagens_em_resposta_a`). É o trade-off consciente do
at-least-once: preferimos um eco repetido a um eco perdido. Fechar essa janela de todo
exigiria coordenação transacional entre banco e provedor (ex.: outbox), fora do escopo
desta fase.

### Testes unitários: 57 verdes, sem banco nem rede

A suíte unitária roda **57 testes** sem tocar Postgres nem a rede — fakes de conexão/pool e
`httpx.MockTransport` no lugar do transporte real. Rodam em ~0,5s com `pytest`:

| Arquivo | Testes | Cobre |
|---|---|---|
| [`tests/test_zpro_models.py`](../tests/test_zpro_models.py) | 13 | Adapter de entrada: extração de campos, texto via `conversation`/`extendedTextMessage`, `unsupported` sem texto, fallback de telefone (`contact.number` → `sender_pn`), `remoteJid` (LID) que **não** vira telefone, filtros (`fromMe`/grupo/`method`/`msg` ausente) e `normalize_phone`. |
| [`tests/test_webhook.py`](../tests/test_webhook.py) | 12 | 404, ignorados (eco/grupo/JSON inválido/fora do formato/evento não-`message`), 503 de persistência, novo processa, duplicata não reprocessa, desfecho `processado`/`falhou`, e `marcar_status` que estoura sem propagar. |
| [`tests/test_dedup.py`](../tests/test_dedup.py) | 4 | Inbox repo: `registrar_mensagem` novo vs. duplicata (via `RETURNING`), `marcar_status` montando o `UPDATE` com o `value` do enum. |
| [`tests/test_mensagens.py`](../tests/test_mensagens.py) | 6 | Upsert de conversa, entrada nova vs. duplicata, conteúdo nulo em `unsupported`, saída idempotente. |
| [`tests/test_processador.py`](../tests/test_processador.py) | 6 | Fluxo feliz, texto do eco vs. `unsupported`, duplicata com/sem saída, falha de envio propaga, e o **ciclo ponta-a-ponta**. |
| [`tests/test_sweeper.py`](../tests/test_sweeper.py) | 8 | Ciclo sem órfãs, recupera → `processado`, falha → `falhou`, payload indecifrável, falha em uma não derruba o lote, parâmetros da busca, e o loop `rodar_sweeper` (segue após erro; encerra no cancelamento). |
| [`tests/test_zpro_client.py`](../tests/test_zpro_client.py) | 8 | 2xx ok, não-2xx → `HTTPStatusError`, `success=false` → `ZproEnvioError`, 2xx sem JSON, request seguindo o contrato verificado, e o ciclo de vida do cliente (`get_cliente` sem startup, `criar_cliente` idempotente, `fechar_cliente` sem cliente). |

O `httpx.MockTransport` intercepta o request e devolve uma resposta programada — inclusive
a `RESPOSTA_REAL` capturada num envio real em 09/07/2026 ([httpx: mock
transports](https://www.python-httpx.org/advanced/transports/#mock-transports)). O
destaque é `test_ciclo_ponta_a_ponta`: um `POST` real na rota do webhook via
[`TestClient`](https://fastapi.tiangolo.com/tutorial/background-tasks/), com o processador
**real** rodando sobre `conn` fake e envio mockado — prova entrada → eco → saída →
`'processado'` de uma ponta à outra, sem banco nem rede.

### Testes de integração: as garantias do banco, provadas

Os unitários provam que o *código* manda o SQL certo, mas usam um `conn` fake — não
exercitam as constraints. Como a idempotência da Fase 1 **mora no banco** (índices únicos
parciais e `CHECK`s), 6 testes de integração rodam contra um **Postgres real** e provam que
elas barram de fato. Ficam sob o marcador `integration`, **deselecionados** no `pytest`
padrão; rodam com `pytest -m integration` (precisa de Docker):

| Arquivo | Testes | Prova |
|---|---|---|
| [`tests/integration/test_constraints.py`](../tests/integration/test_constraints.py) | 5 | `uq_mensagens_message_id` barra entrada duplicada (e `message_id` nulo repete); `uq_mensagens_em_resposta_a` barra a 2ª saída (e `registrar_saida` é idempotente); `uq_conversas_telefone_ativa` mantém 1 ativa por telefone (e `encerrada` coexiste); `chk_mensagens_assistente_conteudo` rejeita assistente sem conteúdo e aceita morador sem conteúdo; dedup atômico de `webhook_events`. |
| [`tests/integration/test_skip_locked.py`](../tests/integration/test_skip_locked.py) | 1 | Concorrência real com 2 conexões: A trava a órfã `'pendente'`, B roda a query real do sweeper e **pula** a linha travada — e volta a enxergá-la quando A solta o lock. |

A infraestrutura é o [testcontainers](https://testcontainers.com/) (só dev): sobe um
container efêmero `pgvector/pgvector:pg17`, aplica um bootstrap de schema **fiel** — as 3
tabelas sob teste copiadas das migrations reais, sem as partes Supabase-only (`pg_cron`,
papéis, RLS policies) que não afetam estas constraints — e derruba no fim. Cada teste roda
numa transação revertida (`ROLLBACK`) para isolar estado, e as conexões registram o **mesmo
codec `jsonb` do `db.py`**, então os repositórios reais rodam idênticos à produção. Sem
Docker/testcontainers, os testes são **pulados** (a suíte unitária segue verde em qualquer
lugar).

## Validação empírica (Parte 3)

Teste manual contra o **Z-PRO real** (canal baileys), executado e observado pelo dono em
13/07/2026:

- **Eco ponta-a-ponta** ✅ — mensagem do morador entrou pelo webhook, percorreu o
  processador e o `Eco: <texto>` **chegou no WhatsApp**. O ciclo entrada → eco → saída
  funciona contra o provedor real, não só nos testes.
- **Placeholder do baileys** — apareceu o "Aguardando mensagem. Essa ação pode levar
  alguns instantes." e **sumiu quando o destinatário respondeu** (a sessão sincronizou).
  É comportamento do canal (placeholder de decifração E2E do baileys), **não bug nosso**
  ([FAQ WhatsApp](https://faq.whatsapp.com/3398056720476987/?locale=pt_BR)); WABA oficial
  não sofre disso.
- **Nono dígito** ✅ **resolvido em 13/07/2026**: os dois formatos foram testados por
  envio real — `5555992372732` (com 9) e `555592372732` (sem 9, o mesmo formato do
  `contact.number` inbound) — e **ambos entregaram**. `msg.phone` pode seguir usado como
  vem do webhook, sem normalização de nono dígito.
- **Crash + recuperação pelo sweeper** ✅ **verificado em 13/07/2026**: servidor real
  (uvicorn + Postgres real) recebeu um webhook, respondeu 200 (linha commitada em
  `webhook_events`), e o processo foi morto (`kill -9`) imediatamente depois — antes de
  qualquer processamento, a linha ficou `'pendente'` e `mensagens` vazia (o crash mais
  extremo possível: nem a entrada chegou a ser gravada). Ao subir o servidor de novo, o
  sweeper (com `grace`/`interval` reduzidos só para o teste) recuperou a órfã: reenviou
  de verdade ao Z-PRO, gravou entrada + saída em `mensagens` (`message_id` da saída
  `null`, como projetado) e marcou `webhook_events.status = 'processado'`. O eco
  **chegou no WhatsApp real do dono**. Prova ponta a ponta de que "reiniciar o serviço no
  meio não perde mensagem" — a cláusula central do critério de conclusão da fase.
- **Retry do Z-PRO em não-2xx** ✅ **resolvido em 13/07/2026 — NÃO reenvia**: um túnel
  público (ngrok) + um endpoint-armadilha (sempre devolve 500, loga cada request) foram
  colocados temporariamente no lugar do webhook real, em duas rodadas independentes com
  mensagens reais via WhatsApp. As duas rodadas mostraram entregas **simultâneas** (mesmo
  segundo, mesmo `message_id`) em paths diferentes — evidência de **múltiplas
  configurações de webhook no painel do Magniia disparando em paralelo**, não de retry.
  Nenhuma tentativa isolada foi repetida depois do 500, numa janela de ~9 minutos.

## Riscos conhecidos e aceitos (documentados, sem ação nesta fase)

- **Janela residual do at-least-once**: crash entre o envio HTTP e o INSERT da saída
  duplica o eco no WhatsApp (nunca no banco). Trade-off consciente; a alternativa (outbox
  transacional com o provedor) fica para depois.
- **Placeholder do baileys**: comportamento do canal, não bug — some quando a sessão
  sincroniza. Desaparece com a migração para WABA oficial.
- **Estilo pendente para o commit final**: `ruff format --check` acusa `db.py` e
  `zpro_models.py` (formatação da Parte 1). `ruff check` passa limpo; a formatação será
  aplicada no commit de fechamento.
- **Schema de teste espelha as migrations à mão**: `tests/integration/schema.sql` reproduz
  as 3 tabelas sob teste em vez de aplicar o `baseline` do Supabase (que traz `pg_cron` e
  papéis inexistentes num Postgres cru). Se a DDL dessas tabelas mudar numa migration, esse
  arquivo precisa acompanhar — risco de drift assumido e anotado no próprio arquivo.
- **RLS habilitado sem policy em `mensagens`**: o advisor `rls_enabled_no_policy` é
  **esperado** nesta fase. Como na Fase 0, RLS sem policies significa "negar tudo" para a
  API do Supabase (anon/authenticated), enquanto a aplicação conecta direto por asyncpg
  (role da conexão, que ignora RLS). Policies de verdade só entram quando (e se) houver
  acesso via API do Supabase.

## Registrado para as próximas fases (Fase 2)

- **RAG sobre as regras do condomínio**: em vez do eco, a resposta passa a consultar
  `regras` (embeddings `vector(3072)`, busca exata filtrada por `condominio_id`) e gerar
  uma resposta com IA.
- **Políticas RLS multi-tenant**: quando (e se) houver acesso via API do Supabase, sair do
  "nega tudo" para policies por `condominio_id`.
- **Retry automático de envios `'falhou'`**: hoje uma linha `'falhou'` fica registrada mas
  não é retentada; a Fase 2 pode dar a ela o mesmo tratamento de fila do sweeper.
- **Chave de dedup composta para multi-canal (WABA)**: `(whatsapp_id, message_id)`, para
  IDs de canais diferentes não colidirem quando o canal oficial entrar.
