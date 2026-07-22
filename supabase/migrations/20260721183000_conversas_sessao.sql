-- Fase 3 · Passo 3: sessão da conversa — quando o morador "chama o chat de novo".
-- A conversa é eterna (uq_conversas_telefone_ativa, sem expiração e sem job que a encerre),
-- então um erro de identificação se fixa em silêncio. A reconfirmação por inatividade é a
-- defesa ATIVA contra isso: o sistema expõe o condomínio associado sem depender de o morador
-- perceber nada.

-- Marca de sessão, não de confirmação: a regra é "24h sem falar", não "24h desde que
-- confirmou" — quem conversa por três horas seguidas não pode ser interrompido no meio.
-- Coluna própria e não `updated_at`: aquela tem trigger (trg_conversas_updated_at) e muda a
-- cada UPDATE, inclusive o da transição de estado. Mediria atividade do SISTEMA, não do
-- morador, e passaria a mentir assim que outro UPDATE entrasse.
-- NOT NULL com DEFAULT: sempre preenchida, sem estado intermediário a interpretar. DEFAULT
-- não-volátil não reescreve a tabela (sql-altertable, Notes).
alter table public.conversas
  add column ultima_interacao_em timestamptz not null default now();

-- Sem CHECK de coerência com `estado`, ao contrário de chk_conversas_estado_coerente: esta
-- coluna não participa da forma da máquina de estados. Ela vale em todos os estados e nunca
-- é nula — não há combinação ilegal a proibir.
