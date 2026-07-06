# Fase 0 — Fundação

> Concluída em 02/07/2026. Este documento é a referência técnica da fase e a base
> para apresentá-la (portfólio/vídeo).

## Objetivo

Construir a espinha dorsal do produto antes de qualquer funcionalidade visível:
um **banco multi-tenant** onde as regras de negócio críticas são garantidas pelo
próprio banco (constraints como invariantes), e uma **porta de entrada do
WhatsApp** segura, tipada e testada — o webhook do Z-PRO. Tudo que vier nas fases
seguintes (IA, reservas, solicitações) se apoia nesta fundação.

Duas ideias guiam o desenho:

- **Defensivo na borda, rígido no core.** A borda (webhook) aceita entrada
  não-confiável e a transforma em objetos tipados confiáveis ("parse, don't
  validate"). O core só conhece esses objetos.
- **Garantias no banco.** Invariantes que não podem ser violadas (reserva dupla,
  unidade duplicada, mensagem processada duas vezes) viram constraints — valem
  para qualquer código, hoje e no futuro, inclusive sob concorrência.

## O banco (Supabase / PostgreSQL 17)

São **10 tabelas**: 9 de domínio — `condominios`, `unidades`, `moradores`,
`areas_comuns`, `reservas`, `solicitacoes`, `regras`, `conversas`,
`encaminhamentos` — mais `webhook_events` (dedup da borda). Todas com UUID
(`gen_random_uuid()`), `created_at`/`updated_at` com trigger, FKs indexadas e
RLS habilitado. O multi-tenant aparece como `condominio_id` em todas as tabelas
de domínio, com `ON DELETE CASCADE` a partir de `condominios`.

O schema completo está em
[`supabase/migrations/20260701000000_baseline.sql`](../supabase/migrations/20260701000000_baseline.sql).
Abaixo, os objetos que merecem destaque.

### A constraint de exclusão de `reservas` (a joia da fase)

```sql
constraint excl_reservas_sem_conflito exclude using gist (
  area_id with =,
  tstzrange(inicio, fim, '[)') with &&
) where (status = 'aprovada')
```

O que ela garante: **duas reservas aprovadas da mesma área nunca se sobrepõem no
tempo** — nem sob concorrência, nem por bug de aplicação. Uma `EXCLUDE
constraint` generaliza o `UNIQUE`: em vez de "não pode haver duas linhas iguais",
ela diz "não pode haver duas linhas cujos valores *conflitem* segundo estes
operadores" ([PostgreSQL: exclusion
constraints](https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-EXCLUSION)).
Aqui, conflito = mesma área (`=`) **e** períodos que se intersectam (`&&` sobre
[range types](https://www.postgresql.org/docs/current/rangetypes.html)).

Três detalhes finos:

- **`'[)'`** — início incluso, fim excluso: a reserva das 18h–19h não conflita
  com a das 19h–20h. É a convenção padrão para intervalos de tempo.
- **`where (status = 'aprovada')`** — a constraint só vale para aprovadas;
  pedidos pendentes podem coexistir à vontade. Isso a torna também um índice
  GiST *parcial* (menor e mais rápido).
- **`btree_gist`** — a extensão que permite usar `=` de igualdade comum dentro
  de um índice GiST, combinando `area_id` e o range no mesmo índice.

Prova funcional (executada contra o banco real, numa transação revertida): a
segunda inserção sobreposta falhou com
`ERROR 23P01: conflicting key value violates exclusion constraint
"excl_reservas_sem_conflito"`.

### Uniques e CHECKs que codificam regras de negócio

```sql
-- Unidade é única no condomínio; NULLS NOT DISTINCT impede que
-- bloco NULL crie duplicatas ("apto 101" duas vezes sem bloco).
create unique index uq_unidades_cond_bloco_numero
  on public.unidades (condominio_id, bloco, numero) nulls not distinct;

-- Telefone único POR CONDOMÍNIO (multi-tenant), só quando informado.
create unique index uq_moradores_cond_telefone
  on public.moradores (condominio_id, telefone) where (telefone is not null);

-- Escopo da regra amarrado à nulidade do tenant: regra 'geral' não tem
-- condomínio; regra 'especifico' exige um.
constraint chk_regras_escopo check (
  (escopo = 'especifico' and condominio_id is not null)
  or (escopo = 'geral' and condominio_id is null)
);
```

Por padrão, valores `NULL` são considerados distintos entre si em índices únicos;
`NULLS NOT DISTINCT` inverte isso ([PostgreSQL: unique
constraints](https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-UNIQUE-CONSTRAINTS)).
Os índices únicos com `WHERE` são [índices
parciais](https://www.postgresql.org/docs/current/indexes-partial.html): a
unicidade só é imposta no subconjunto que interessa. Há ainda CHECKs de formato
(telefone `^[0-9]{10,15}$`, UF `^[A-Z]{2}$`, slug `^[a-z0-9-]+$`), enums de
status por CHECK e validação de tipo JSON (`jsonb_typeof`).

### RLS habilitado + rede de segurança automática

Todas as tabelas têm [Row Level
Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) habilitado
sem policies — o que significa **negar tudo** para acesso via API do Supabase
(anon/authenticated), enquanto a aplicação conecta direto como role da conexão
(`DATABASE_URL`). Um [event
trigger](https://www.postgresql.org/docs/current/event-triggers.html) garante que
ninguém esqueça:

```sql
create event trigger ensure_rls
  on ddl_command_end
  when tag in ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
  execute function public.rls_auto_enable();
```

Toda tabela nova criada em `public` já nasce com RLS habilitado, automaticamente.
Policies de verdade só entram quando (e se) houver acesso via API do Supabase.

### `webhook_events`: idempotência com uma tabela de duas colunas

```sql
create table public.webhook_events (
  message_id text primary key,
  recebido_em timestamptz not null default now()
);
```

O ID da mensagem do WhatsApp é a chave de idempotência. O registro usa
[`INSERT ... ON CONFLICT DO NOTHING ... RETURNING`](https://www.postgresql.org/docs/current/sql-insert.html#SQL-ON-CONFLICT):
se o `RETURNING` devolve linha, a mensagem é nova; se não devolve, é reentrega e
o processamento é pulado. A unicidade é atômica no banco — dois workers
recebendo a mesma reentrega ao mesmo tempo não processam duas vezes.

### Embeddings: `vector(3072)` sem índice, de propósito

`regras.embedding` é `vector(3072)` (dimensão do `text-embedding-3-large`).
Índices HNSW/IVFFlat do pgvector suportam "up to 2,000 dimensions" para o tipo
`vector` ([pgvector README](https://github.com/pgvector/pgvector)) — então a
busca é **exata** (seq scan), sempre filtrada por `condominio_id`. Para uma base
de regras por condomínio (centenas de linhas), exato é rápido e tem recall
perfeito. Se a base crescer, o caminho é half-precision indexing (`halfvec`, até
4.000 dims), documentado no mesmo README.

## O código (Python assíncrono + FastAPI)

### Adapter anticorrupção do Z-PRO (`zpro_models.py`)

O JSON do webhook do Z-PRO (canal Baileys) nunca vaza para o resto do sistema.
Modelos Pydantic v2 espelham o payload externo (com `validation_alias` para os
campos camelCase — [doc de alias](https://docs.pydantic.dev/latest/concepts/alias/))
e uma função de parse converte tudo num único modelo interno:

```python
class IncomingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str   # chave de idempotência
    phone: str        # só dígitos com DDI: "555592372732"
    text: str | None
    ...
```

É o padrão **parse, don't validate**: a fronteira transforma entrada
não-confiável em um objeto tipado imutável (`frozen=True`); quem recebe um
`IncomingMessage` não precisa revalidar nada. Mensagens que não devem seguir
(eco `fromMe`, grupos, eventos sem telefone) levantam `IgnoreMessage` — descarte
explícito, não silencioso. Quando o canal migrar para o WhatsApp oficial (WABA),
só este arquivo muda.

Detalhe de versão: a config usa `validate_by_name=True` — o antigo
`populate_by_name` não é recomendado desde o Pydantic 2.11 e será deprecado na
v3; com `validate_by_alias` mantendo o default `True`, o comportamento é
estritamente equivalente
([doc de Config](https://pydantic.dev/docs/validation/latest/api/pydantic/config/)).

### Webhook ACK-first (`webhook.py`)

O Z-PRO espera resposta rápida; todo o trabalho pesado acontece **depois** do
ACK:

```python
@router.post("/webhook/{secret}")
async def receber_webhook(secret, request, background_tasks):
    if not hmac.compare_digest(secret.encode(), settings.webhook_secret.encode()):
        raise HTTPException(status_code=404)
    raw_body = await request.body()
    background_tasks.add_task(_consumir, raw_body)
    return {"status": "ok"}
```

- **URL secreta com comparação em tempo constante.** O webhook do Z-PRO não tem
  assinatura, então a proteção é um segredo no path. `hmac.compare_digest` evita
  timing attacks na comparação
  ([doc do Python](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)),
  e segredo errado responde 404 genérico, sem revelar que a rota existe.
- **Corpo lido antes de responder** — após a resposta, o stream da request pode
  não estar mais disponível.
- **BackgroundTasks** rodam após o envio da resposta
  ([FastAPI: Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)) —
  parse, dedup e processamento acontecem lá, e qualquer falha é logada sem virar
  500 para o Z-PRO.

### Pool asyncpg com lifespan (`db.py`, `main.py`)

O pool de conexões abre uma vez no startup e fecha no shutdown, via
[lifespan do FastAPI](https://fastapi.tiangolo.com/advanced/events/) (o
substituto moderno dos eventos `on_event`). O
[pool do asyncpg](https://magicstack.github.io/asyncpg/current/api/index.html#connection-pools)
usa `min_size=1, max_size=20` — dimensionado para a VPS de 1 vCPU com a regra
`workers × max_size ≤ ~40`, respeitando o limite do Supabase free via Session
pooler. O shutdown tem timeout: se `pool.close()` passar de 10s, `terminate()`
derruba as conexões à força em vez de travar o deploy.

### Testes com mocks (`test_webhook.py`)

Seis testes cobrem a borda inteira **sem banco real**: segredo errado (404),
mensagem nova (processa), duplicata (não processa), eco `fromMe` (ignora), grupo
(ignora) e payload malformado (não quebra). O pool é substituído por um fake e
as funções de banco por `AsyncMock`; o `TestClient` é usado sem `with` de
propósito, para o lifespan (que abriria o pool) não disparar. Rodando:
`6 passed`.

## Histórico de migrations

O schema nasceu no SQL Editor, fora do sistema de migrations. Em 02/07/2026 o
histórico foi regularizado (ver [`supabase/README.md`](../supabase/README.md)):

1. `20260701000000_baseline.sql` — captura fiel do schema, registrada como
   aplicada sem reexecução (equivalente a `supabase migration repair --status
   applied` — [doc de migrations](https://supabase.com/docs/guides/deployment/database-migrations)).
2. `20260702171614_advisor_security_fixes.sql` — fixes do Security Advisor:
   `search_path = ''` na função de trigger
   ([lint 0011](https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0011_function_search_path_mutable))
   e `REVOKE EXECUTE` público da função de event trigger
   ([lint 0028](https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0028_anon_security_definer_function_executable)).

Daqui em diante, **todo DDL entra por migration**.

## Riscos conhecidos e aceitos (documentados, sem ação nesta fase)

- **`btree_gist` no schema `public`**
  ([lint 0014](https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0014_extension_in_public)):
  mover exige ser dono da extensão (`supabase_admin`); risco baixo, detalhes no
  `supabase/README.md`.
- **Segredo na URL aparece no access log do servidor**: trade-off de webhook sem
  assinatura; os logs do servidor devem ser tratados como sensíveis.

## Registrado para as próximas fases

- **Fase 1 — inbox durável**: gravar o payload cru junto ao dedup
  (`webhook_events` ganha `payload jsonb` + `status`), tornando o registro um
  buffer reprocessável. Hoje o dedup marca a mensagem como vista *antes* do
  processamento (semântica at-most-once): se o processamento falhar após o ACK,
  a mensagem não é reprocessada.
- **Multi-canal (WABA/produção)**: chave de dedup composta
  `(whatsapp_id, message_id)`, para IDs de canais diferentes não colidirem.
