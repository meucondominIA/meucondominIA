# Fase 2 — Arquitetura geral: RAG multi-tenant sobre as regras

> Documento de arquitetura de alto nível — a espinha dorsal da fase. Cada passo da
> ordem de implementação (§12) terá depois um responsável dedicado, que toma as
> decisões finas daquele pedaço. Este documento define **as peças, os papéis, os
> contratos e as fronteiras** — não o miolo de cada componente.
>
> Toda decisão de arquitetura aqui vem amarrada à documentação oficial (URL citada).
> Onde não foi possível verificar, está escrito **"não verificado"**.

## 1. Objetivo e critério de pronto

Dar à IA conhecimento das regras de cada condomínio via **RAG** (*Retrieval-Augmented
Generation*: em vez de o modelo "saber" as regras, ele **recupera** os trechos
relevantes de uma base e responde com base neles). A Fase 2 constrói só o **R** do
RAG — a recuperação; o loop de resposta com LLM é a Fase 3.

**Critério de pronto:** uma pergunta sobre uma regra retorna os trechos corretos,
**com fonte citável**, **só do condomínio consultado** (mais a lei geral como
retaguarda) — comprovado por avaliação com perguntas reais e por teste de
não-vazamento entre tenants.

Três entregas encadeadas:

1. **Ingestão** — regimento → *chunks* citáveis → embeddings → tabela `regras`.
2. **Busca** — embedding da pergunta → varredura exata filtrada por
   `condominio_id` → trechos com fonte e distância.
3. **Avaliação** — prova de qualidade (as perguntas certas acham os trechos certos)
   e de isolamento (nunca um trecho de outro condomínio).

## 2. Onde a fase se encaixa no que já existe

A Fase 1 fechou o ciclo webhook → inbox durável → processador → eco → Z-PRO. A Fase 2
**não mexe nesse ciclo**: ela constrói a capacidade de recuperação **ao lado** dele e
expõe uma costura (`busca.py`, §4.4) que a Fase 3 vai plugar no lugar do eco.

Motivo para não plugar agora: o fluxo do WhatsApp ainda **não sabe a qual condomínio
o telefone pertence** (`conversas.condominio_id` é nullable e a identificação do
morador é Fase 3). O contrato da busca recebe `condominio_id` **como parâmetro** —
descobrir esse parâmetro a partir do telefone é exatamente o gancho que a Fase 3
preenche. A fase se demonstra por avaliação e (opcionalmente) por um CLI de
pergunta, não pelo WhatsApp.

O que a fase **reutiliza** do existente:

- A tabela `regras` (baseline `20260701000000`): `conteudo`, `fonte`, `metadata jsonb`,
  `embedding vector(3072)`, `escopo` com `CHECK` (`geral` ⇒ `condominio_id IS NULL`,
  `especifico` ⇒ `NOT NULL`) e `idx_regras_condominio_id`.
- Os padrões consolidados: adapter anti-corrupção com ciclo `criar_/fechar_/get_`
  (`db.py`, `zpro_client.py`); repositórios que recebem `conn`; Pydantic na borda;
  SQL parametrizado; **nenhuma conexão do pool atravessa I/O de rede**.

## 3. Visão geral dos componentes

```
INGESTÃO (offline, CLI — roda na mão, por documento)
────────────────────────────────────────────────────────────────────
 regimento (arquivo)
      │
      ▼
 ┌──────────┐   list[Chunk]   ┌───────────────┐  list[embedding]
 │chunker.py│ ───────────────▶│ embeddings.py │────────────┐
 └──────────┘                 └───────────────┘            │
   (puro, sem I/O)              (adapter OpenAI)           ▼
                                                   ┌──────────────┐
                              ingestao.py ────────▶│  regras.py   │──▶ tabela regras
                              (orquestra; CLI)     │ (repo, conn) │    (1 tx/documento)
                                                   └──────────────┘

BUSCA (online — a costura que a Fase 3 consome)
────────────────────────────────────────────────────────────────────
 pergunta + condominio_id
      │
      ▼
 ┌──────────┐  1 embedding  ┌───────────────┐
 │ busca.py │◀──────────────│ embeddings.py │   (rede ANTES de conn)
 │ (serviço)│               └───────────────┘
 │          │  conn curta   ┌──────────────┐
 │          │◀─────────────▶│  regras.py   │──▶ WHERE tenant + ORDER BY <=> + LIMIT
 └──────────┘               └──────────────┘
      │
      ▼
 list[TrechoRegra]  (conteudo, fonte, escopo, distancia, metadata)

AVALIAÇÃO (prova o critério de pronto)
────────────────────────────────────────────────────────────────────
 eval/golden.* (perguntas reais + trechos esperados, por condomínio)
      │
      ▼
 eval/rodar_eval.py ──▶ busca real ──▶ métricas: hit@k · vazamento = 0
```

Módulos novos, todos flat na raiz como os existentes: `embeddings.py`, `chunker.py`,
`regras.py`, `busca.py`, `ingestao.py`, mais o diretório `eval/`.

## 4. As peças e seus contratos

O formato de cada contrato: **o que entra, o que sai, o que a peça garante — e o que
ela deliberadamente NÃO decide** (isso fica com o responsável pelo passo).

### 4.1 `embeddings.py` — adapter da OpenAI (a fronteira, §6) — **IMPLEMENTADO (passo 2)**

- **Papel:** única porta do sistema para a API de embeddings. Espelha o padrão do
  `zpro_client.py`: cliente com ciclo `criar_cliente()/fechar_cliente()/get_cliente()`
  criado no startup; o core nunca vê tipos do SDK da OpenAI.
- **Contrato (final — difere do proposto na 1ª versão deste doc, que não tinha
  `caminho`):**
  ```python
  Caminho = Literal["busca", "ingestao"]

  async def gerar_embeddings(textos: list[str], *, caminho: Caminho) -> list[list[float]]
  ```
  Entra uma lista de textos (1 para a pergunta na busca; N para o lote da ingestão —
  a API aceita array de entradas numa chamada:
  https://developers.openai.com/api/docs/guides/embeddings). Sai uma lista de vetores
  de 3072 floats, **na mesma ordem** — remontada pelo `index` de cada item
  (https://developers.openai.com/api/docs/api-reference/embeddings); a doc **não**
  garante a ordem do array, então não confiamos nela. Exceções sobem — quem decide o
  que fazer é o chamador (ingestão aborta a transação do documento; busca propaga).

  `caminho` é **obrigatório e keyword-only, sem default**: busca e ingestão têm perfis
  de timeout/retry opostos (A6) e um lote de ingestão rodando com o timeout da busca
  quebraria em produção. Aplicado por chamada via `client.with_options(...)`, que
  reutiliza o mesmo cliente HTTP (verificado no fonte do SDK 2.45.0).
- **Garante:** modelo e dimensões fixados por config; timeout/retry explícitos por
  caminho; ordem da saída; e as invariantes da resposta (contagem == entrada, índices
  cobrindo `0..n-1`, dimensão == config) — violação vira `EmbeddingRespostaError`,
  exceção de domínio nossa. Erros do SDK (`openai.*`) sobem crus, espelhando o
  `HTTPStatusError` que o `zpro_client` deixa propagar.
- **Bordas (defensivas, sem gastar chamada):** lista vazia → `[]`; texto vazio ou só
  espaços → `ValueError` (a doc proíbe string vazia); acima de 2048 itens →
  `ValueError` (A4).
- **Não decide:** tamanho de lote da ingestão, contagem de tokens, uso ou não de
  `tiktoken` (§10, D5).

### 4.2 `chunker.py` — regimento → chunks citáveis (função pura)

- **Papel:** transformar o texto de um regimento em pedaços **citáveis** — cada chunk
  carrega a própria referência humana ("Regimento Interno, Art. 15"). Sem I/O nenhum:
  função pura, trivial de testar (mesma filosofia do `zpro_models.py`: parse na borda,
  objeto tipado confiável na saída).
- **Contrato:**
  ```python
  class Chunk(BaseModel):        # frozen
      conteudo: str              # o texto do trecho
      fonte: str                 # citação humana: "Regimento X, Art. 15"
      metadata: dict             # documento, artigo/capítulo, posição, ...

  def dividir_regimento(texto: str, documento: str) -> list[Chunk]
  ```
- **Garante:** todo chunk tem `fonte` não-vazia (a citabilidade é contrato, não
  acidente) e cabe no limite de entrada do modelo de embedding (máx. 8192 tokens por
  entrada — https://developers.openai.com/api/docs/guides/embeddings). Regimentos são
  texto jurídico estruturado (capítulos/artigos): dividir **pela estrutura**, não por
  tamanho fixo — um artigo é a unidade natural de citação.
- **Não decide (fica com o responsável do passo):** granularidade exata (artigo
  inteiro? parágrafo? com overlap?), formato de entrada aceito (txt/markdown; PDF é
  pré-processamento fora do sistema), como validar o limite de tokens (§10, D5).
- **Evidência já levantada para o passo 3 (§13), que não decide por ele:** prefixar o
  título do CAPÍTULO no chunk **não melhorou** o retrieval (hit@k idêntico, distâncias
  levemente piores) — o artigo puro basta; e o maior artigo real deu 187 tokens contra
  o teto de 8192, o que torna a contagem exata de tokens (D5) provavelmente
  desnecessária.

### 4.3 `regras.py` — repositório (recebe `conn`, como `mensagens.py`)

- **Papel:** todo SQL da tabela `regras`. Ponto arquitetural central: a função de
  busca é **a única query de similaridade do sistema** — o filtro de tenant mora num
  único lugar auditável (§7).
- **Contrato:**
  ```python
  async def inserir_regras(conn, escopo: str, condominio_id: UUID | None,
                           chunks: list[Chunk], embeddings: list[list[float]]) -> None

  class RegraEncontrada(BaseModel):  # frozen
      conteudo: str
      fonte: str
      escopo: str
      metadata: dict
      distancia: float

  async def buscar_por_similaridade(conn, condominio_id: UUID,
                                    embedding: list[float],
                                    limite: int) -> list[RegraEncontrada]
  ```
- **A forma da query de busca** (o coração da fase):
  ```sql
  select conteudo, fonte, escopo, metadata, embedding <=> $2 as distancia
    from regras
   where condominio_id = $1 or escopo = 'geral'
   order by embedding <=> $2
   limit $3
  ```
  - `<=>` é distância de cosseno (https://github.com/pgvector/pgvector). A OpenAI
    recomenda similaridade de cosseno e os embeddings dela são normalizados para
    norma 1 — cosseno e distância euclidiana dão o mesmo ranking
    (https://developers.openai.com/api/docs/guides/embeddings).
  - Sem índice vetorial = **busca exata com recall perfeito**, e o `WHERE` filtra
    **antes** da ordenação. A doc do pgvector é explícita: com índice aproximado o
    filtro se aplica **depois** do scan do índice (podendo devolver menos resultados
    — ou exigir *iterative scans*), e "se a tabela é pequena, um table scan pode ser
    mais rápido" (https://github.com/pgvector/pgvector). Ou seja: para poucas
    centenas de trechos por condomínio, a decisão sem-índice não é só suficiente —
    é **mais correta** para isolamento multi-tenant.
  - O `CHECK chk_regras_escopo` do schema garante que `condominio_id = $1` só casa
    linhas `especifico` e que toda `geral` tem `condominio_id IS NULL` — o OR não
    tem zona cinzenta.
- **Não decide:** política de quotas geral × específico (§10), limiar de distância
  (§10), estratégia exata de reingestão (§10).

### 4.4 `busca.py` — serviço de recuperação (a costura da Fase 3)

- **Papel:** orquestrar pergunta → embedding → busca no banco, na ordem que respeita
  a regra da casa: **rede primeiro, conexão depois** — o embedding é gerado ANTES de
  adquirir `conn`; a conexão dura só o SELECT (mesma disciplina do `processador.py`,
  pelo mesmo motivo: o pool compartilha o teto de conexões do Supabase).
- **Contrato:**
  ```python
  class TrechoRegra(BaseModel):  # frozen — o que a Fase 3 vai receber
      conteudo: str
      fonte: str
      escopo: str
      distancia: float
      metadata: dict

  async def buscar_trechos(pergunta: str, condominio_id: UUID,
                           limite: int | None = None) -> list[TrechoRegra]
  ```
  `limite` default vem de config (`rag_top_k`). Este é o contrato que o agente da
  Fase 3 consome; nada além dele precisa ser estável entre as fases.
- **Não decide:** reranking, busca híbrida (léxica + vetorial), cache de embeddings
  de perguntas — tudo fora do escopo até a avaliação (§ 4.6) dizer que precisa.

### 4.5 `ingestao.py` — pipeline de ingestão (CLI, offline)

- **Papel:** amarrar chunker + embeddings + repositório para carregar um documento
  de um condomínio (ou da base geral). É um **script de linha de comando**, não uma
  rota FastAPI: ingestão é operação de administrador, rara, executada na mão no
  piloto. Faz o próprio bootstrap (cria pool e cliente, roda, fecha) — o mesmo par
  `criar_/fechar_` do lifespan, fora do FastAPI.
- **Contrato (interface de uso):** entra um arquivo + a identidade do documento +
  o alvo (`--condominio <slug>` ou `--geral`) + `fonte` base; sai a tabela `regras`
  populada, com escrita **transacional por documento** (tudo ou nada — nunca meio
  regimento no ar).
- **Garante:** reingestão do mesmo documento **não duplica** trechos (regimento
  atualizado substitui o anterior atomicamente). A forma exata (§10) é do
  responsável do passo.
- **Ordem interna obrigatória:** chunk → embeddings (rede, sem conn) → só então
  transação de escrita. Nenhuma transação aberta esperando a OpenAI.

### 4.6 `eval/` — avaliação de qualidade e não-vazamento

- **Papel:** provar o critério de pronto com números, não com impressão. Duas
  camadas complementares (detalhe no §7):
  1. **Testes de integração** (pytest `-m integration`, testcontainers) — provam a
     **estrutura**: o filtro de tenant nunca vaza, com vetores sintéticos, sem
     OpenAI e sem custo. A imagem já usada é `pgvector/pgvector:pg17` — o tipo
     `vector` já está disponível no harness existente.
  2. **Harness de avaliação** (`eval/rodar_eval.py` + `eval/golden.*`) — prova a
     **qualidade empírica**: perguntas reais de morador contra a base real, com
     embeddings reais. Roda na mão (custa tokens); não entra no `pytest` default.
- **Contrato do golden set:** lista de casos `{condominio, pergunta, fontes
  esperadas}` — perguntas como um morador escreveria ("pode churrasco no domingo?"),
  não paráfrases do regimento.
- **Métricas mínimas:** `hit@k` (a fonte esperada aparece no top-k?) por condomínio,
  e **vazamento = 0** (nenhum trecho `especifico` de outro condomínio, em nenhum
  caso). Métricas além dessas (MRR, etc.) ficam a critério do responsável.

## 5. Fluxos de dados

### 5.1 Ingestão (offline, por documento)

1. Operador roda `ingestao.py` apontando o arquivo e o alvo (condomínio ou geral).
2. `chunker.py` divide o texto em `list[Chunk]` citáveis (puro, sem I/O).
3. `embeddings.py` gera os vetores em lote(s) — chamada de rede, **sem conexão do
   pool aberta**.
4. `regras.py` grava tudo numa **transação única por documento**: remove a versão
   anterior do documento (se houver) e insere os chunks novos com seus embeddings
   e metadados (modelo de embedding incluído — trilha para reindexar se o modelo
   mudar um dia).
5. Falha em qualquer passo ⇒ rollback ⇒ a versão anterior permanece intacta.

### 5.2 Busca (online)

1. Chamador (Fase 3, eval, CLI de demo) chama `buscar_trechos(pergunta, condominio_id)`.
2. `embeddings.py` gera **um** embedding da pergunta (rede; nenhuma conn presa).
3. `busca.py` adquire `conn` (curta) e chama `buscar_por_similaridade` — o único
   SELECT de similaridade do sistema, com o filtro de tenant no `WHERE`.
4. Sai `list[TrechoRegra]` ordenada por distância: conteúdo + fonte + escopo +
   distância. O chamador decide o que fazer com isso (na Fase 2, o eval mede).

## 6. A fronteira com a OpenAI

Mesmo padrão anti-corrupção do Z-PRO, aplicado ao segundo serviço externo do sistema:

- **Onde mora:** exclusivamente em `embeddings.py`. O core conhece
  `gerar_embeddings(list[str]) -> list[list[float]]` — tipos Python puros, nenhum
  tipo do SDK vazando. Trocar de provedor de embedding = trocar um arquivo (o mesmo
  argumento do `zpro_models.py`: "quando migrarmos para WABA, só este arquivo muda").
- **SDK oficial:** `openai` com `AsyncOpenAI`; lê `OPENAI_API_KEY`, mas aqui a chave
  vem de `Settings` (consistência com o padrão do projeto: segredo no `.env`,
  contrato tipado no `config.py`). Retentativas: o SDK já retenta 2× erros
  transitórios (conexão, 408, 429, ≥500) com backoff — não reimplementar retry por
  cima (https://github.com/openai/openai-python).
- **Testabilidade:** o SDK é movido a **httpx** (verificado, mesma URL acima) e
  aceita `http_client` customizado — o padrão de teste já consolidado no
  `test_zpro_client.py` (`httpx.MockTransport`) serve igual: testes unitários do
  adapter sem rede.
- **Regra de ouro herdada:** nenhuma chamada à OpenAI acontece com conexão do pool
  adquirida ou transação aberta. Vale para busca e ingestão.
- **Custo/latência:** embeddings são a parte barata do RAG, mas cada busca é uma
  chamada de rede síncrona no caminho da resposta — a Fase 3 herda essa latência
  (~centenas de ms). Registrado, sem ação nesta fase.

## 7. Isolamento multi-tenant — e como será provado

**O modelo de ameaça da fase:** um trecho do regimento do condomínio B aparecer na
resposta ao morador do condomínio A. Até a Fase 5 (RLS de verdade), o isolamento é
**o filtro `WHERE` no código** — então a arquitetura o torna pequeno, único e
testável:

1. **Um único ponto de passagem.** Só `buscar_por_similaridade` (em `regras.py`)
   consulta `regras` por similaridade. Não há segundo caminho para auditar. Quando a
   RLS chegar (Fase 5), a policy `condominio_id = current_setting(...)` entra por
   baixo **sem mudar o contrato** de nenhuma peça.
2. **O banco já ajuda.** `chk_regras_escopo` garante a coerência dos dados
   (`geral` ⇔ `condominio_id IS NULL`); a busca exata garante que o filtro roda
   antes do ranking (§4.3).
3. **Prova estrutural (barata, determinística):** teste de integração com vetores
   **sintéticos** — semeia regras de A, de B e gerais com vetores fabricados;
   consulta como A; afirma que **todo** resultado é `geral` ou de A, que os de B
   nunca aparecem, e que a ordenação por distância é a esperada. Sem OpenAI, sem
   custo, roda em qualquer CI com Docker.
4. **Prova empírica (qualidade):** o harness de eval (§4.6) roda o golden set com
   embeddings reais e reporta `hit@k` + vazamento. O critério de pronto da fase é
   lido daqui: hit nas perguntas reais, **zero** vazamento em todos os casos.

## 8. Mudanças estruturais no projeto

| Onde | Mudança | Fundamento |
|---|---|---|
| `requirements.txt` | + `openai` (SDK oficial), + `pgvector` (codec do tipo p/ asyncpg) | https://github.com/openai/openai-python · https://github.com/pgvector/pgvector-python |
| `db.py` | `_registrar_codecs` passa a registrar também o tipo `vector` via `pgvector.asyncpg.register_vector(conn)` — o hook `init=` do pool já existe e é exatamente o padrão da doc | https://github.com/pgvector/pgvector-python (seção asyncpg: `create_pool(..., init=init)`) |
| `config.py` | + `openai_api_key`, `embedding_model` (default `text-embedding-3-large`), `embedding_dimensions` (3072), `rag_top_k`, e **4 campos de timeout/retry por caminho** (`openai_timeout_busca_seconds`, `openai_retries_busca`, `openai_timeout_ingestao_seconds`, `openai_retries_ingestao`) — A6. Não existe timeout "base": todo acesso passa por um caminho, e campo de config sem leitor é dívida | padrão `pydantic-settings` já em uso |
| `main.py` | lifespan cria/fecha o cliente OpenAI junto do pool e do cliente Z-PRO (simetria; o app só consome na Fase 3, mas o ciclo de vida já fica correto) | https://fastapi.tiangolo.com/advanced/events/ |
| `tests/integration/schema.sql` | ganha a tabela `regras` (cópia fiel da migration, como já é feito p/ as outras 3 tabelas) — a imagem `pgvector/pgvector:pg17` já dá o tipo `vector` | prática já estabelecida no repo (risco de drift já documentado na Fase 1) |
| `supabase/migrations/` | **proposta de mudança explícita** (§10, D1): endurecer `regras.fonte` para `NOT NULL` — citabilidade como invariante no banco, não promessa no código. Tabela vazia hoje: custo zero | princípio do projeto "garantias no banco" (CLAUDE.md) |
| `.env` | + `OPENAI_API_KEY` (segredo só no ambiente) | — |

**Atenção do passo 1 — RESOLVIDO (14/07/2026):** no Supabase a extensão `vector` vive
no schema `extensions` (baseline: `create extension vector with schema extensions`),
enquanto a imagem de teste instala em `public`. Verificado: `register_vector` **não**
resolve o tipo em schema não-default sozinho — sem `schema='extensions'` falha com
`unknown type: public.vector`. Por isso `db.py` chama
`register_vector(conn, schema=settings.pgvector_schema)`, e o roundtrip de
`vector(3072)` está provado por teste de integração contra os dois ambientes.

## 9. Decisões de arquitetura (com fundamento)

| # | Decisão | Fundamento (oficial) |
|---|---|---|
| A1 | **Busca exata, sem índice vetorial** (mantém decisão registrada) | pgvector: exact search = recall perfeito; HNSW/IVFFlat limitam `vector` a 2.000 dims; "if the table is small, a table scan may be faster"; filtro com índice aproximado é pós-scan — https://github.com/pgvector/pgvector |
| A2 | **Operador `<=>` (cosseno)** na busca | OpenAI recomenda cosseno; embeddings normalizados (norma 1) ⇒ ranking idêntico ao euclidiano — https://developers.openai.com/api/docs/guides/embeddings. **Verificado empiricamente** (§13): norma L2 medida = 1,0000 |
| A3 | **3072 dims, sem encurtar** (mantém decisão registrada) | `dimensions` permite encurtar se um dia precisar de índice (HNSW ≤ 2000), mas isso **muda o tipo da coluna e exige reingestão**; `halfvec` indexa até 4.000 dims por índice de expressão **sem mudar o schema** — é a saída mais barata. Supabase confirmado com pgvector **0.8.0** (halfvec existe desde 0.7.0). Tentativa de medir se dimensão menor degrada qualidade **saturou** (§13): não há benefício medido em reduzir hoje — https://developers.openai.com/api/docs/guides/embeddings · https://github.com/pgvector/pgvector |
| A4 | **Ingestão em lote** (array de entradas numa chamada) | endpoint aceita array; resposta correlaciona por `index`. Limites **VERIFICADOS** em 15/07/2026 (o "não verificado" desta linha morreu aqui): ≤2048 itens/array, ≤8192 tokens/input, ≤300.000 tokens/request, string vazia proibida — https://developers.openai.com/api/reference/resources/embeddings/methods/create. Com a densidade medida (§13), o teto de **tokens** morde antes do de itens (~1886 chunks/chamada); um regimento real cabe em **uma** chamada |
| A5 | **Codec `vector` no init do pool** (não converter string na mão) | pgvector-python, seção asyncpg — https://github.com/pgvector/pgvector-python |
| A6 | **SDK oficial com retry nativo; timeout e retry explícitos POR CAMINHO** (default de 10 min derrubado por config) | https://github.com/openai/openai-python. Valores **medidos, não arbitrados** (§13): busca **3s + 1 retry**, ingestão **60s + 2 retries**. O timeout do SDK é por tentativa e ele retenta sozinho com backoff. Na busca o 3s é **corte de cauda**, não margem: a latência é bimodal (p50 ~590ms, p95 ~780ms, ~2% travam em 5–6s), e cortar em 3s + retentar chega **antes** de esperar a chamada travada — o 6s inicial era o pior dos mundos (disparava e entregava 6,5s) |
| A7 | **Chunking estrutural** (artigo/seção como unidade citável), entrada ≤ 8192 tokens | limite de entrada do modelo — https://developers.openai.com/api/docs/guides/embeddings; a granularidade fina é do passo 3. Medido (§13): o maior artigo real deu **187 tokens = 2,3% do limite** — a folga é de 43x |
| A8 | **Ingestão como CLI**, não rota HTTP | operação administrativa rara no piloto; evita superfície de ataque e upload de arquivo no app (decisão de produto/simplicidade — sem doc externa) |
| A9 | **Fase termina na costura `busca.py`** — processador segue no eco | `condominio_id` do telefone só existe na Fase 3 (identificação); plugar antes forçaria gambiarra de tenant fixo (decisão de escopo — sem doc externa) |

## 10. Pontos de decisão a bater ANTES de começar

Decisões que atravessam mais de um passo — vale fechar com o dono do projeto antes
de destravar os responsáveis:

- **D1 — `fonte NOT NULL` em `regras`** (§8). É mudança de schema (migration nova).
  Recomendo: sim, agora, com a tabela vazia. *Quem trava: dono do projeto.*
- **D2 — Identidade do documento para reingestão.** A substituição atômica precisa
  de uma chave "este é o mesmo documento" (coluna própria? `metadata->>'documento'`?
  hash?). Afeta migration (se coluna) e o passo 5. Recomendo decidir junto com D1
  para fazer **uma** migration só. *Quem trava: dono + responsável do passo 5.*
- **D3 — Geral × específico: um ranking só ou quotas?** A query única (OR) deixa a
  lei geral competir com o regimento no mesmo top-k — simples e provavelmente
  suficiente. Alternativa: duas buscas (N específicos + M gerais) se o eval mostrar
  que a lei geral abafa o regimento (ou vice-versa). Recomendo: começar com query
  única; o eval do passo 7 decide se muda. *Quem trava: eval (dados), não opinião.*
- **D4 — Limiar de distância (cut-off).** Devolver sempre top-k, ou cortar trechos
  distantes demais ("não achei nada relevante")? Importa para a Fase 3 (quando não
  responder). Recomendo: sem limiar na Fase 2; o eval mede a distribuição de
  distâncias e informa o limiar para a Fase 3. *Quem trava: eval.*
  **⚠️ Alerta medido (§13): limiar global é mais perigoso do que parecia.** As
  distâncias dos acertos variam 0,332–0,713 e **se sobrepõem** às do top-1 errado
  (0,707): um corte em ~0,6 mataria uma resposta correta. Se um limiar entrar, tem que
  ser justificado por dados do eval — nunca por intuição.
- **D5 — Contagem de tokens no chunker.** `tiktoken` (dependência nova, contagem
  exata) vs. heurística de caracteres (zero dependência; artigos de regimento ficam
  ordens de grandeza abaixo de 8192 tokens). *Quem trava: responsável do passo 3.*
  **Dados para decidir (§13): a hipótese está confirmada** — texto jurídico PT-BR mede
  **3,29 chars/token** e o maior artigo real deu 187 tokens (2,3% do teto). Uma guarda
  de ~16.000 chars/chunk é segura até no pior caso patológico de 2 chars/token e nunca
  dispara num artigo real (~600 chars). Recomendação: **heurística, sem `tiktoken`** —
  a dependência não se paga. *A palavra final segue do passo 3.*
- **D6 — Golden set: tamanho e curadoria.** Quantas perguntas por condomínio (sugiro
  10–20), quem escreve (perguntas reais de morador > paráfrases), formato do arquivo.
  *Quem trava: dono + responsável do passo 7.*
- **D7 — CLI de demonstração** (`perguntar.py --condominio X "pergunta"`): fora do
  critério de pronto, mas barato e valioso para portfólio/vídeo. Recomendo: sim,
  dentro do passo 7. *Quem trava: dono.*

## 11. Fora do escopo (e os ganchos que ficam prontos)

| Futuro | Gancho deixado pela Fase 2 |
|---|---|
| Loop do agente/LLM (Fase 3) | consome `buscar_trechos(pergunta, condominio_id)` — contrato estável, tipos Pydantic |
| Identificação do morador/LGPD (Fase 3) | `condominio_id` é parâmetro da busca; a Fase 3 só precisa resolvê-lo a partir do telefone |
| RLS de verdade (Fase 5) | filtro de tenant num único ponto (§7.1); policy entra por baixo sem mudar contrato |
| Base crescer muito | saídas documentadas sem mudança de schema hoje: `dimensions` menor (OpenAI) ou `halfvec`/índice (pgvector) — A3 |
| Busca híbrida / reranking | só se o eval provar necessidade; entraria dentro de `busca.py` sem mudar o contrato |
| Idempotência do processamento de resposta (Fase 3) | inalterada — a Fase 2 não toca o processador |

## 12. Ordem de implementação proposta

Cada passo é fechado por um responsável dedicado, **com testes**, um de cada vez
(commits pelo dono, como combinado). A ordem minimiza retrabalho: fundações → folhas
puras → integração → prova.

1. **Fundações** — dependências (`openai`, `pgvector`), `Settings` novos, codec
   `vector` no `db.py`, `regras` no `schema.sql` dos testes de integração; migration
   de D1/D2 se aprovadas. *Testes:* roundtrip de `vector(3072)` via testcontainers;
   validação do codec contra schema `extensions` (o "não verificado" do §8 morre aqui).
2. **Adapter `embeddings.py`** — ciclo `criar_/fechar_/get_`, `gerar_embeddings`,
   timeout explícito. *Testes:* unitários com `httpx.MockTransport` (padrão
   `test_zpro_client.py`); ordem preservada; erro propaga.
3. **`chunker.py`** — regimento → chunks citáveis; decide D5. *Testes:* unitários
   puros com trecho de regimento real como fixture; toda saída tem `fonte`; limite
   de tamanho respeitado.
4. **Repositório `regras.py`** — `inserir_regras` + `buscar_por_similaridade` (a
   única query de similaridade). *Testes:* integração com vetores sintéticos —
   inclui a **prova estrutural de não-vazamento** (§7.3) e a ordenação por distância.
5. **Pipeline `ingestao.py`** — CLI com bootstrap próprio; transação por documento;
   reingestão idempotente (D2). *Testes:* integração com embeddings fake (rollback
   em falha; substituição atômica); depois, **ingestão real** dos regimentos do
   piloto — com aviso e aprovação antes de escrever no Supabase, como combinado.
6. **Serviço `busca.py`** — pergunta → embedding → repo; rede antes de conn; expõe
   `TrechoRegra`. *Testes:* unitários com fakes (padrão `test_processador.py`).
7. **Avaliação** — golden set (D6), `eval/rodar_eval.py`, métricas `hit@k` +
   vazamento = 0; decide D3/D4 com dados; CLI de demo (D7). **Fecha o critério de
   pronto da fase.**

## 13. Fatos medidos (15/07/2026) — empíricos, não derivam de doc

> Medições reais contra a API da OpenAI, feitas ao fechar o passo 2. Aqui só entra o
> que foi **observado**; o fundamento documental de cada decisão continua no §9.
> Corpus: regimento sintético realista (12 artigos, estrutura jurídica brasileira) +
> 20 perguntas no estilo que morador escreve. Método e limitações declarados junto.

| # | Fato medido | Número | Onde impacta |
|---|---|---|---|
| M1 | Densidade de token em texto jurídico PT-BR | **3,29 chars/token** (faixa 3,0–4,0) | D5, A7 |
| M2 | Maior artigo real vs. teto de 8192 tokens/input | **187 tokens = 2,3%** (folga 43x) | D5, A7 |
| M3 | Qual teto morde primeiro no lote | **tokens** (2048 × 159 = 324k > 300k) → ~1886 chunks/chamada | A4, passo 5 |
| M4 | Norma L2 do embedding | **1,0000** (e ≈1,0 também em dims reduzidas — a API normaliza no servidor) | A2 |
| M5 | Latência da busca (1 pergunta), 500 chamadas | p50 **590ms**, p95 **780ms**, cauda bimodal: ~2% travam em **5–6s** | A6 |
| M6 | Latência da ingestão por lote | 150 chunks **3,8s** · 500 **10,5s** · 1000 **20s** · 1800 **35s** | A6, passo 5 |
| M7 | Retrieval, perguntas naturais | hit@1 **9/10**, hit@3 **10/10** | critério de pronto |
| M8 | Retrieval, perguntas de discriminação fina | hit@1 **10/10** | `rag_top_k=5` está folgado |
| M9 | Capítulo prefixado no chunk | hit@k **idêntico**, distâncias **piores** (0,468 → 0,480) | §4.2, passo 3 |
| M10 | Distância dos acertos vs. do top-1 errado | acertos **0,332–0,713** · errado **0,707** → **se sobrepõem** | D4 |
| M11 | Erro de input > 8192 tokens | `openai.BadRequestError` 400, `"Invalid 'input[0]': maximum input length is 8192 tokens."` — identifica o **índice** do item ofensor | passo 5 |

**O que NÃO foi concluído (declarado para não virar falso saber):**

- **Dimensão menor degrada qualidade?** O teste **saturou**: 3072/1536/1024/512/**256**
  deram hit@1 idêntico, inclusive nas perguntas difíceis. Com 12 candidatos a tarefa é
  fácil demais para ranquear dimensão. Isso **não prova** que 256 serve em escala — prova
  que o corpus é pequeno demais para medir. A3 fica como está; reavaliar só com base
  real e grande.
- **O corpus é sintético.** M7–M10 usam um regimento escrito para o teste, não os
  regimentos do piloto. Servem para destravar o passo 3, **não** para fechar o critério
  de pronto da fase — quem fecha é o eval do passo 7 com documento e perguntas reais.
- **A latência (M5, M6) foi medida da máquina de dev**, não do VPS de produção (KVM1,
  1 vCPU, rede diferente). Os 3s da busca têm ~4x de folga sobre o p95 medido justamente
  para absorver essa diferença.
