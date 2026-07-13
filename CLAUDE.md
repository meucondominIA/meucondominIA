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

## Contrato com o Z-PRO (whitelabel Magniia) — verificado por teste real em 09/07/2026
> Fatos observados, não derivam do código. Payload de ENTRADA real: `json_ZPRO` (raiz do repo).

**Envio (API externa v2):**
- `POST {ZPRO_API_URL}/` com header `Authorization: Bearer {ZPRO_API_TOKEN}` (env vars já
  existentes; `ZPRO_API_URL` inclui o ApiID).
- Body: `{"body": "<texto>", "number": "<dígitos com DDI>", "externalKey": "<chave>",
  "isClosed": false}` — endereça POR NÚMERO e abre/reutiliza ticket sozinho.
- Resposta: `{"success":true,"data":{"message":"Message sent successfully","ticketId":N}}`.
  **Não devolve id de mensagem do WhatsApp**, só `ticketId` — que se REPETE por conversa:
  nunca gravar em `mensagens.message_id` (violaria o índice único); só logar.
- `externalKey`: **NÃO deduplica** (verificado 09/07/2026: dois envios com a mesma chave,
  ambos entregues). Enviamos o `message_id` da entrada mesmo assim, só como correlação/
  rastreio — a idempotência de envio é 100% responsabilidade NOSSA (guarda no banco via
  `em_resposta_a`; janela residual entre envio e INSERT da saída é aceita e documentada).

**Pegadinhas conhecidas:**
- `GET /params/` responde `403 "Token was not provided."` MESMO com Bearer válido (auth
  dessa rota difere do POST). Não usar como healthcheck nem para validar credencial.
- Nono dígito: **resolvido em 13/07/2026** — testados os dois formatos por envio real
  (`5555992372732` com 9, e `555592372732` sem 9, o mesmo formato do `contact.number`
  inbound); **os dois entregam**. Não precisa de normalização de nono dígito no envio.
- "Aguardando mensagem. Essa ação pode levar alguns instantes." no WhatsApp = placeholder
  de decifração E2E, comum via baileys; some quando a sessão sincroniza (ex.: destinatário
  responde). Comportamento do canal, não bug nosso
  (https://faq.whatsapp.com/3398056720476987/?locale=pt_BR). WABA oficial não sofre disso.
- Retry do Z-PRO em não-2xx do NOSSO webhook: **resolvido em 13/07/2026 — NÃO reenvia.**
  Teste real: túnel público (ngrok) + endpoint-armadilha (sempre 500, log completo) no
  lugar do webhook oficial; duas mensagens reais enviadas via WhatsApp, cada uma gerou
  entregas **simultâneas** (mesmo segundo, mesmo `message_id`) em paths diferentes — são
  múltiplas configurações de webhook no painel do Magniia disparando em paralelo, não
  retry. Nenhuma tentativa isolada foi repetida depois de receber 500 (~9min de janela
  observada).

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