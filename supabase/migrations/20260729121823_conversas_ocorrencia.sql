-- Fase 4 · Etapa 4: o estado do wizard de ocorrência.
--
-- Espelha a Etapa 1 (20260727124521) nas três decisões que já valem: o estado
-- carrega o NOME do fluxo ('ocorrencia', irmão de 'reserva'), o passo e os dados
-- parciais vivem no mesmo 'rascunho' jsonb, e a garantia do banco fica no
-- CONTORNO — a FORMA de dentro ({passo, tipo, descricao, anexos}) é validada no
-- código (Pydantic). O custo do D1 é este arquivo: uma migration por TIPO de
-- fluxo novo, nunca por passo novo.
--
-- As TRÊS constraints cedem juntas, e não é zelo: alargar só chk_conversas_estado
-- deixa o estado ingravável — chk_conversas_estado_coerente barra o INSERT
-- (medido em Postgres 17 antes de escrever esta migration).
--
-- Técnica: drop/recria sem NOT VALID, de propósito. O scan da recriação é a
-- PROVA de que nenhuma linha existente ficou inválida.

alter table public.conversas drop constraint chk_conversas_estado;
alter table public.conversas
  add constraint chk_conversas_estado
    check (estado in ('identificacao', 'aguardando_confirmacao', 'menu', 'duvidas',
                      'reserva', 'ocorrencia'));

-- 'ocorrencia' entra no MESMO ramo de menu/duvidas/reserva: exige tenant
-- confirmado e proíbe candidato pendente. É o isolamento multi-tenant garantido
-- NO BANCO durante toda a ocorrência — uma solicitação sem condomínio é
-- ingravável, não "improvável".
alter table public.conversas drop constraint chk_conversas_estado_coerente;
alter table public.conversas
  add constraint chk_conversas_estado_coerente check (
       (estado = 'identificacao'
          and condominio_id is null and condominio_pendente is null)
    or (estado = 'aguardando_confirmacao'
          and condominio_id is null)
    or (estado in ('menu', 'duvidas', 'reserva', 'ocorrencia')
          and condominio_id is not null and condominio_pendente is null)
  );

-- A sacola passa a valer para os DOIS wizards, e segue bicondicional: dentro do
-- wizard sempre há sacola e ela é objeto; fora, nunca sobra órfã.
--
-- Isto é alargamento, não reescrita: para toda linha existente
-- estado ∈ {identificacao, aguardando_confirmacao, menu, duvidas, reserva}, e
-- nesse domínio `estado not in ('reserva','ocorrencia')` tem o MESMO valor-verdade
-- que o `estado <> 'reserva'` antigo, linha a linha.
--
-- O `not in` só é seguro porque conversas.estado é NOT NULL: `x not in (...)` com
-- x nulo devolve NULL, e CHECK que resulta NULL PASSA (ddl-constraints). Quem
-- mexer aqui depois precisa saber que essa propriedade está sendo usada.
alter table public.conversas drop constraint chk_conversas_rascunho;
alter table public.conversas
  add constraint chk_conversas_rascunho check (
       (estado in ('reserva', 'ocorrencia')
          and rascunho is not null and jsonb_typeof(rascunho) = 'object')
    or (estado not in ('reserva', 'ocorrencia') and rascunho is null)
  );

-- ── DOWN (validado em Postgres 17; não roda por engano) ──────────────────────
-- A primeira linha NÃO é opcional: conversas paradas em 'ocorrencia' não têm
-- para onde ir quando o estado deixa de existir, e sem a limpeza o scan da
-- recriação RECUSA ("is violated by some row"). Voltam ao menu, que é onde o
-- escape '0' as levaria. Seguro sob a constraint LARGA, que ainda está de pé
-- quando o UPDATE roda: limpar dado primeiro, estreitar depois.
--
-- update public.conversas set estado = 'menu', rascunho = null
--  where estado = 'ocorrencia';
--
-- alter table public.conversas drop constraint chk_conversas_rascunho;
-- alter table public.conversas
--   add constraint chk_conversas_rascunho check (
--        (estado = 'reserva'
--           and rascunho is not null and jsonb_typeof(rascunho) = 'object')
--     or (estado <> 'reserva' and rascunho is null)
--   );
-- alter table public.conversas drop constraint chk_conversas_estado_coerente;
-- alter table public.conversas
--   add constraint chk_conversas_estado_coerente check (
--        (estado = 'identificacao'
--           and condominio_id is null and condominio_pendente is null)
--     or (estado = 'aguardando_confirmacao'
--           and condominio_id is null)
--     or (estado in ('menu', 'duvidas', 'reserva')
--           and condominio_id is not null and condominio_pendente is null)
--   );
-- alter table public.conversas drop constraint chk_conversas_estado;
-- alter table public.conversas
--   add constraint chk_conversas_estado
--     check (estado in ('identificacao', 'aguardando_confirmacao', 'menu',
--                       'duvidas', 'reserva'));
