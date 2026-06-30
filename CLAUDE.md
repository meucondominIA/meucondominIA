# CLAUDE.md — Assistente para Condomínios (WhatsApp + IA)

## Produto
SaaS de atendimento para condomínios via WhatsApp com IA. O morador resolve o máximo no
WhatsApp; o síndico só aprova/recebe quando necessário. Multi-tenant por condomínio.
Piloto com 2 condomínios. O projeto está em construção e a arquitetura pode evoluir.

## Seu papel (IA) neste projeto
Você é **colaborador(a) da arquitetura**, não só executor(a). Espera-se que você:
- **Proponha melhorias** de arquitetura, padrões, ferramentas e roadmap quando enxergar
  algo melhor — mesmo sem eu pedir.
- **Questione decisões e premissas** (inclusive as "Decisões atuais" abaixo) com argumento
  técnico e referência à doc oficial.
- **Sinalize riscos, trade-offs e dívida técnica** proativamente.
- **Ofereça alternativas** e diga quando há um caminho mais simples/robusto.

Em troca, combinamos:
- **Transparência:** ao propor mudar uma decisão atual, diga claramente que é uma mudança
  e o porquê (não altere em silêncio).
- **Mudanças grandes:** proponha e discuta **antes** de aplicar.
- **Fundamente na documentação oficial** mais recente; se não verificou, diga "não verificado".
- A meta é a **melhor qualidade do produto** — pode discordar de mim com respeito e dados.

## Stack (atual)
- Python (async) · FastAPI · asyncpg
- Supabase (PostgreSQL + pgvector) — fonte de verdade
- OpenAI: chat `gpt-4o-mini` (trocável p/ Claude); embeddings `text-embedding-3-large` (3072 dims)
- Entrada: webhook do ZPRO (canal "baileys" agora; WABA/oficial depois)
- `pydantic-settings` · `pytest` · `ruff`

## Princípios de design (guias, não algemas)
- **Adapter / anti-corrupção:** o formato externo (ZPRO) não vaza pro core; tudo vira `IncomingMessage`.
- **Parse, don't validate:** a borda transforma entrada não-confiável em objeto tipado confiável.
- **Defensivo na borda, rígido no core.**
- **Garantias no banco:** invariantes via constraints (PK/unique/EXCLUDE).
- **Segredos só no ambiente**; nunca hardcoded.
- **Enxuto que resolve:** evitar over-engineering; otimizar onde há ganho real.

## Decisões atuais (com a razão — abertas a revisão)
> Pode propor mudar qualquer uma destas, explicitamente e com argumento/doc.
1. Código-first em Python; sem n8n nesta fase (testabilidade, git, controle).
2. Embeddings 3072 dims (busca exata, filtrada por condomínio; revisitar se a base crescer muito).
3. Telefone como `text`, normalizado; `contact.number` primário, `sender_pn` fallback;
   `remoteJid` é LID (não é telefone).
4. Modelos Pydantic aninhados (legibilidade > micro-perf; parsing não é gargalo).
5. `str` na borda p/ campos do ZPRO; Enum só no core.
6. Dedup por chave de idempotência (INSERT ON CONFLICT).
7. Pool asyncpg: `min_size=1`, `max_size=20` (limite = VPS KVM1, 1 vCPU; regra `workers × max_size ≤ ~40`).
8. Identidade: número único + auto-declaração agora; futuro = número/tenant por condomínio,
   roster da administradora, auto-registro c/ aceite LGPD.

## Fatos do ambiente
- Supabase free (Nano): 60 conexões diretas / 200 pooler (compartilhadas).
- Conexão via Session pooler (porta 5432); `DATABASE_URL` com `?sslmode=require`.
- Webhook do ZPRO não tem assinatura → proteger por URL secreta (`webhook_secret`).

## Convenções
- Type hints sempre; Pydantic v2 (`model_validate`, `ConfigDict`, `validation_alias`).
- SQL sempre parametrizado (`$1`); `async`/`await` p/ I/O.
- Conventional Commits; commit por passo.
+
## Docs oficiais
- Sempre pesquisar a documentação ofical para a função especifica, mas algumas na qual ja me basiei: 
- Pydantic v2 — https://docs.pydantic.dev/latest/
- asyncpg — https://magicstack.github.io/asyncpg/current/
- FastAPI — https://fastapi.tiangolo.com/
- PostgreSQL — https://www.postgresql.org/docs/current/
- Supabase — https://supabase.com/docs · OpenAI — https://platform.openai.com/docs