-- Fase 4 · Etapa 4: o gate de idempotência da ocorrência.
--
-- Espelha uq_reservas_origem_mensagem (Etapa 2, 20260727151743) pela MESMA razão,
-- e ela não é a exclusion constraint: a guarda saida_ja_existe do processador só
-- passa a valer depois da TX2, e o crash mora justamente entre a escrita da
-- solicitação (1ª janela de conexão) e ela. Reentrega do sweeper encontraria
-- saida_ja_existe = false, rodaria o wizard de novo, e sem este índice nasceria
-- uma SEGUNDA solicitação — mesmo morador, mesmo texto.
--
-- solicitacoes não tinha defesa nenhuma para isso: sem origem_mensagem_id, sem
-- unique além da PK (verificado em pg_indexes no banco real).
--
-- on delete set null (não cascade): apagar a conversa não pode apagar a
-- ocorrência — ela é o registro do pedido, não um detalhe da conversa.
alter table public.solicitacoes
  add column origem_mensagem_id uuid
    references public.mensagens (id) on delete set null;

-- Índice PARCIAL: muitos NULL seguem permitidos, e é isso que deixa a porta
-- aberta para solicitações criadas fora do WhatsApp (dashboard da Fase 5), que
-- não têm mensagem de origem.
create unique index uq_solicitacoes_origem_mensagem
  on public.solicitacoes (origem_mensagem_id)
  where (origem_mensagem_id is not null);

-- Índice de leitura por telefone: é por ele que o morador vai ser reconhecido
-- enquanto morador_id for nulo (identidade hoje é o telefone), e é o mesmo
-- critério que idx_conversas_telefone já usa.
create index idx_solicitacoes_telefone on public.solicitacoes (telefone);

-- ── DOWN ─────────────────────────────────────────────────────────────────────
-- Sem limpeza de dado: a coluna some com o que carrega, e nenhuma linha fica
-- inválida (ao contrário do down de conversas_ocorrencia).
--
-- drop index if exists idx_solicitacoes_telefone;
-- drop index if exists uq_solicitacoes_origem_mensagem;
-- alter table public.solicitacoes drop column origem_mensagem_id;
