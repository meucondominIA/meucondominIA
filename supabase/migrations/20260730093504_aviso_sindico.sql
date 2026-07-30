-- Fase 4 · Etapa 5: o aviso ao síndico (a mensagem proativa).
-- Forward-only, como as 13 anteriores. Reversão, se um dia precisar:
--   drop table public.avisos_sindico;
--   alter table public.condominios drop column sindico_telefone;
--
-- Custo operacional (sql-altertable.html): o ALTER pega ACCESS EXCLUSIVE e,
-- por adicionar um CHECK, "requires scanning the table to verify that existing
-- rows meet the constraint, but does not require a table rewrite". Em
-- condominios (2 linhas) isso é instantâneo; a nota fica para quando não for.

-- ── 1) A fonte do síndico (D2) ───────────────────────────────────────────────
-- Coluna e não tabela de responsáveis: a decisão de produto é UM síndico por
-- condomínio. Isso torna a resolução "a partir do condomínio" literal (lookup
-- por PK) e — o que importa mais — faz o lookup REVERSO da borda
-- (telefone → condomínio) ser single-valued POR CONSTRUÇÃO: o unique parcial
-- deixa "mesmo número em dois condomínios" ingravável, em vez de virar
-- ambiguidade a tratar no Python. Se a Fase 5 pedir conselho/subsíndico, isto
-- vira tabela; o drop column é barato e a dívida fica explícita.
--
-- O CHECK é o MESMO de moradores.telefone ('^[0-9]{10,15}$'): casa com
-- normalize_phone (zpro_models.py), que só mantém dígitos. As linhas que já
-- existem recebem NULL e passam pelo `is null or`. Sem sindico_nome — não teria
-- leitor: o aviso vai PARA o síndico, não o nomeia.
alter table public.condominios
  add column sindico_telefone text
    constraint chk_condominios_sindico_telefone
      check (sindico_telefone is null or sindico_telefone ~ '^[0-9]{10,15}$');

-- Parcial (indexes-partial.html): "enforces uniqueness among the rows that
-- satisfy the index predicate, without constraining those that do not" — os
-- condomínios sem síndico ficam de fora e não disputam o índice entre si.
create unique index uq_condominios_sindico_telefone
  on public.condominios (sindico_telefone)
  where (sindico_telefone is not null);

-- ── 2) O outbox do aviso ─────────────────────────────────────────────────────
-- Por que tabela nova e não `mensagens`: registrar_saida() grava DEPOIS do
-- enviar() (processador.py), então a linha de mensagens é RECIBO, não intenção.
-- Um outbox é o inverso — a fila do que ainda vai sair.
--
-- Por que outbox e não enviar direto: o aviso é o primeiro efeito externo SEM
-- entrada do destinatário para ancorá-lo. uq_mensagens_em_resposta_a é parcial
-- (WHERE em_resposta_a IS NOT NULL), então uma saída proativa cai fora do
-- predicado e o índice não protege nada. A intenção gravada na MESMA transação
-- do pedido é o que dá "um aviso por pedido".
create table public.avisos_sindico (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  -- Discriminação por FK, não por par (tipo, id) polimórfico: assim "o pedido
  -- existe" é garantia do banco, e o portal (Fase 5) tem o link direto.
  reserva_id uuid references public.reservas (id) on delete cascade,
  solicitacao_id uuid references public.solicitacoes (id) on delete cascade,
  -- Texto congelado no enfileiramento: o worker fica burro (lê, envia, marca) e
  -- a mensagem diz o que era verdade no momento do pedido.
  texto text not null,
  status text not null default 'pendente'
    constraint chk_avisos_sindico_status check (status in ('pendente', 'enviado')),
  tentativas integer not null default 0,
  -- Lease (visibility timeout). MEDIDO em 29/07/2026: sem ela, dois workers
  -- enviam CADA aviso 2x — o lock do FOR UPDATE SKIP LOCKED cai no commit, e a
  -- regra de ouro exige commitar ANTES de enviar. Com ela, crash durante o envio
  -- só faz a reserva expirar e outro ciclo repegar: nada de estado 'enviando'
  -- preso nem job de reaper.
  reservado_ate timestamptz,
  enviado_em timestamptz,
  created_at timestamptz not null default now(),
  -- Mesmo padrão de chk_regras_escopo e chk_conversas_rascunho: a linha sem
  -- pedido, ou com os dois, fica ingravável.
  constraint chk_avisos_sindico_um_pedido check (
    (reserva_id is not null and solicitacao_id is null)
    or (reserva_id is null and solicitacao_id is not null)
  )
);

-- O gate de "um aviso por pedido", espelhando uq_reservas_origem_mensagem: o
-- enfileiramento fala com estes índices via ON CONFLICT ... DO NOTHING (o
-- `where` do conflict_target é o que "allows inference of partial unique
-- indexes", sql-insert.html), e quem decide a corrida é o banco.
create unique index uq_avisos_sindico_reserva
  on public.avisos_sindico (reserva_id) where (reserva_id is not null);
create unique index uq_avisos_sindico_solicitacao
  on public.avisos_sindico (solicitacao_id) where (solicitacao_id is not null);

-- Espelha idx_webhook_events_pendentes: é a fila que o worker varre.
create index idx_avisos_sindico_pendentes
  on public.avisos_sindico (created_at) where (status = 'pendente');

-- Sem updated_at/trigger: a tabela é append + flip, como webhook_events e
-- mensagens. O event trigger ensure_rls já habilitaria RLS; explícito como o
-- baseline faz. SEM force row level security de propósito — "table owners
-- normally bypass row security" (ddl-rowsecurity.html), e é por isso que a app,
-- que conecta como dona, segue lendo enquanto a API pública fica em
-- default-deny.
alter table public.avisos_sindico enable row level security;
