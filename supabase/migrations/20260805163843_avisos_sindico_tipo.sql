-- Reserva automática · o outbox passa a aceitar MAIS DE UM aviso por pedido.
--
-- Com o cancelamento pelo morador, o síndico recebe DOIS avisos sobre a mesma
-- reserva (nasceu, morreu). O índice antigo era UNIQUE (reserva_id): o segundo
-- INSERT caía no `on conflict do nothing` do repositório e sumia — sem erro, sem
-- log, sem síndico avisado. A chave passa a ser (reserva_id, tipo).
--
-- solicitacao_id leva o mesmo tratamento não por simetria estética: é o MESMO
-- defeito. Deixá-lo de pé garantiria que a próxima etapa a precisar de um
-- segundo aviso de ocorrência descobrisse o problema em produção, não aqui.
--
-- Técnica, com a doc do PostgreSQL 17 na mão:
--   * ADD COLUMN com DEFAULT não-volátil NÃO reescreve a tabela ("the default is
--     evaluated at the time of the statement and the result stored in the
--     table's metadata"); o NOT NULL custa um scan, não um rewrite.
--   * o DEFAULT nasce e morre neste arquivo. Sem ele o ADD COLUMN ... NOT NULL
--     recusaria uma tabela não-vazia; mantê-lo faria um aviso de OCORRÊNCIA
--     herdar 'reserva_criada' em silêncio. O valor vai ESCRITO em cada INSERT,
--     como o 'pendente' de reservas.py e o 'aberta' de solicitacoes.py.
--   * nada de CONCURRENTLY: "a regular CREATE INDEX command can be performed
--     within a transaction block, but CREATE INDEX CONCURRENTLY cannot" — e
--     migration é transação. O mesmo vale para DROP INDEX.
--   * sem NOT VALID: o scan da criação é a PROVA de que nenhuma linha existente
--     ficou inválida. Mesma escolha do 20260729121823.
--
-- Os dois índices antigos são ÍNDICES, não constraints (nasceram por CREATE
-- UNIQUE INDEX no 20260730093504) — por isso DROP INDEX, e não
-- ALTER TABLE ... DROP CONSTRAINT.
--
-- Verificado no container das migrations reais (05/08/2026): dois avisos da
-- mesma reserva com tipos distintos passam; um terceiro repetindo um tipo leva
-- 23505; tipo incoerente com o id preenchido leva chk_avisos_sindico_tipo_coerente.

alter table public.avisos_sindico
  add column tipo text not null default 'reserva_criada';
alter table public.avisos_sindico alter column tipo drop default;

alter table public.avisos_sindico
  add constraint chk_avisos_sindico_tipo
    check (tipo in ('reserva_criada', 'reserva_cancelada', 'ocorrencia_aberta'));

-- Irmã da chk_avisos_sindico_um_pedido: aquela garante que exatamente UM dos
-- dois ids está preenchido; esta garante que o tipo fala do id que existe. Sem
-- ela, um aviso de cancelamento apontando para uma solicitação é gravável.
--
-- Seguro contra NULL sem truque: tipo é NOT NULL desde a linha de cima, então
-- nenhum ramo deste CHECK pode resultar NULL (e CHECK que resulta NULL PASSA —
-- ddl-constraints).
alter table public.avisos_sindico
  add constraint chk_avisos_sindico_tipo_coerente check (
       (reserva_id is not null
          and tipo in ('reserva_criada', 'reserva_cancelada'))
    or (solicitacao_id is not null and tipo = 'ocorrencia_aberta')
  );

drop index public.uq_avisos_sindico_reserva;
create unique index uq_avisos_sindico_reserva
  on public.avisos_sindico (reserva_id, tipo) where reserva_id is not null;

drop index public.uq_avisos_sindico_solicitacao;
create unique index uq_avisos_sindico_solicitacao
  on public.avisos_sindico (solicitacao_id, tipo) where solicitacao_id is not null;

-- ── DOWN (validado em Postgres 17; não roda por engano) ──────────────────────
-- A primeira linha NÃO é opcional: com dois avisos da mesma reserva no banco,
-- recriar o índice antigo falha ("could not create unique index"). Apagar o
-- aviso de cancelamento é o desfazer honesto — ele é o que não cabe no mundo
-- antigo. Limpar dado primeiro, estreitar depois, como no 20260729121823.
--
-- delete from public.avisos_sindico where tipo = 'reserva_cancelada';
--
-- drop index public.uq_avisos_sindico_solicitacao;
-- create unique index uq_avisos_sindico_solicitacao
--   on public.avisos_sindico (solicitacao_id) where solicitacao_id is not null;
-- drop index public.uq_avisos_sindico_reserva;
-- create unique index uq_avisos_sindico_reserva
--   on public.avisos_sindico (reserva_id) where reserva_id is not null;
-- alter table public.avisos_sindico
--   drop constraint chk_avisos_sindico_tipo_coerente;
-- alter table public.avisos_sindico drop constraint chk_avisos_sindico_tipo;
-- alter table public.avisos_sindico drop column tipo;
