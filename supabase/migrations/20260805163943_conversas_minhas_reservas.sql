-- Reserva automática · o estado do fluxo "Minhas reservas".
--
-- Espelha o 20260729121823 (ocorrência) nas mesmas três decisões: o estado
-- carrega o NOME do fluxo, o passo e os dados parciais vivem no 'rascunho'
-- jsonb, e a garantia do banco fica no CONTORNO — a FORMA de dentro
-- ({passo, opcoes[]} / {passo, item}) é validada no código (Pydantic).
--
-- Por que o rascunho, e não uma reconsulta: a lista de reservas do morador
-- ENCOLHE sozinha com o tempo (o filtro é `fim > now()`). Reconsultar entre a
-- tela e o número deslocaria a numeração, e o morador cancelaria a reserva
-- errada — irreversivelmente. O mapeamento número→reserva vai CONGELADO, como
-- o número→data do wizard de dias.
--
-- As TRÊS constraints cedem juntas, e não é zelo: MEDIDO em Postgres 17 em
-- 05/08/2026, no container das migrations reais. Alargando só a primeira, o
-- INSERT ainda é barrado por chk_conversas_estado_coerente; alargando as duas
-- primeiras, ainda é barrado por chk_conversas_rascunho; só com as três o
-- estado vira gravável.
--
-- Técnica: drop/recria sem NOT VALID, de propósito. O scan da recriação é a
-- PROVA de que nenhuma linha existente ficou inválida.

alter table public.conversas drop constraint chk_conversas_estado;
alter table public.conversas
  add constraint chk_conversas_estado
    check (estado in ('identificacao', 'aguardando_confirmacao', 'menu', 'duvidas',
                      'reserva', 'ocorrencia', 'minhas_reservas'));

-- 'minhas_reservas' entra no MESMO ramo de menu/duvidas/reserva/ocorrencia:
-- exige tenant confirmado e proíbe candidato pendente. Aqui isso pesa mais que
-- nos outros fluxos — o cancelamento é ESCRITA sobre uma linha que já existe, e
-- sem condominio_id garantido no banco a query de cancelar não teria como se
-- limitar ao tenant da conversa.
alter table public.conversas drop constraint chk_conversas_estado_coerente;
alter table public.conversas
  add constraint chk_conversas_estado_coerente check (
       (estado = 'identificacao'
          and condominio_id is null and condominio_pendente is null)
    or (estado = 'aguardando_confirmacao'
          and condominio_id is null)
    or (estado in ('menu', 'duvidas', 'reserva', 'ocorrencia', 'minhas_reservas')
          and condominio_id is not null and condominio_pendente is null)
  );

-- A sacola passa a valer para os TRÊS fluxos, e segue bicondicional: dentro do
-- fluxo sempre há sacola e ela é objeto; fora, nunca sobra órfã.
--
-- Alargamento, não reescrita: para toda linha existente
-- estado ∈ {identificacao, aguardando_confirmacao, menu, duvidas, reserva,
-- ocorrencia}, e nesse domínio `estado not in ('reserva','ocorrencia',
-- 'minhas_reservas')` tem o MESMO valor-verdade que o `not in
-- ('reserva','ocorrencia')` antigo, linha a linha.
--
-- O `not in` só é seguro porque conversas.estado é NOT NULL: `x not in (...)`
-- com x nulo devolve NULL, e CHECK que resulta NULL PASSA (ddl-constraints).
-- Quem mexer aqui depois precisa saber que essa propriedade está sendo usada —
-- é a mesma nota do 20260729121823, e continua valendo.
alter table public.conversas drop constraint chk_conversas_rascunho;
alter table public.conversas
  add constraint chk_conversas_rascunho check (
       (estado in ('reserva', 'ocorrencia', 'minhas_reservas')
          and rascunho is not null and jsonb_typeof(rascunho) = 'object')
    or (estado not in ('reserva', 'ocorrencia', 'minhas_reservas')
          and rascunho is null)
  );

-- ── DOWN (validado em Postgres 17; não roda por engano) ──────────────────────
-- A primeira linha NÃO é opcional: conversas paradas em 'minhas_reservas' não
-- têm para onde ir quando o estado deixa de existir, e sem a limpeza o scan da
-- recriação RECUSA ("is violated by some row"). Voltam ao menu, que é onde o
-- escape '0' as levaria. Seguro sob a constraint LARGA, que ainda está de pé
-- quando o UPDATE roda: limpar dado primeiro, estreitar depois.
--
-- update public.conversas set estado = 'menu', rascunho = null
--  where estado = 'minhas_reservas';
--
-- alter table public.conversas drop constraint chk_conversas_rascunho;
-- alter table public.conversas
--   add constraint chk_conversas_rascunho check (
--        (estado in ('reserva', 'ocorrencia')
--           and rascunho is not null and jsonb_typeof(rascunho) = 'object')
--     or (estado not in ('reserva', 'ocorrencia') and rascunho is null)
--   );
-- alter table public.conversas drop constraint chk_conversas_estado_coerente;
-- alter table public.conversas
--   add constraint chk_conversas_estado_coerente check (
--        (estado = 'identificacao'
--           and condominio_id is null and condominio_pendente is null)
--     or (estado = 'aguardando_confirmacao'
--           and condominio_id is null)
--     or (estado in ('menu', 'duvidas', 'reserva', 'ocorrencia')
--           and condominio_id is not null and condominio_pendente is null)
--   );
-- alter table public.conversas drop constraint chk_conversas_estado;
-- alter table public.conversas
--   add constraint chk_conversas_estado
--     check (estado in ('identificacao', 'aguardando_confirmacao', 'menu',
--                       'duvidas', 'reserva', 'ocorrencia'));
