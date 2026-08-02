-- Fase 5 · Etapa 2: as policies e a revogação dos grants de fábrica.
--
-- A Etapa 1 criou o vocabulário (condominios.sindico_user_id, schema privado,
-- privado.meu_condominio()). Vocabulário não autoriza nada. Esta migration
-- escreve as frases: três policies de SELECT e o fechamento do que o Supabase
-- deixa aberto por padrão.
--
-- Estado ANTES, medido em 31/07/2026: as 12 tabelas de public têm RLS ligado e
-- ZERO policies (default deny para authenticated), mas anon e authenticated têm
-- os 8 privilégios (arwdDxtm) em todas elas. O default deny do RLS estava
-- sozinho; o GRANT continuava de pé por baixo.
--
-- ── A ORDEM É DELIBERADA: fail-closed ────────────────────────────────────────
-- Revoke ANTES das policies. Não sei se quem aplica esta migration a envolve em
-- transação (o asyncpg do conftest envolve — MEDIDO; o apply_migration do
-- Supabase, NÃO VERIFICADO), então a ordem foi escolhida para que TODA falha
-- parcial deixe o banco mais fechado, nunca mais aberto:
--   falha após o revoke  → authenticated sem nada
--   falha após o grant   → tem SELECT, mas nenhuma policy existe ainda, e "if
--                          row-level security is enabled for a table, but no
--                          applicable policies exist, a 'default deny' policy is
--                          assumed" (sql-createpolicy.html) → zero linhas
--   falha após as policies → funcionando; defaults abertos = status quo de hoje
-- Na ordem inversa (policies primeiro) haveria uma janela com authenticated
-- ainda podendo INSERT/UPDATE/DELETE. Não há.

-- ── 1) O que sai ─────────────────────────────────────────────────────────────
-- `all` são os 8 privilégios do PG 17 — inclusive MAINTAIN (`m`), que
-- information_schema.role_table_grants NÃO mostra (não é padrão SQL) e que
-- autoriza LOCK TABLE (ddl-priv.html). Uma auditoria escrita sobre o
-- information_schema diria "limpo" com ele de pé; por isso o revoke é `all` e a
-- asserção correspondente usa has_table_privilege.
--
-- anon entra junto, e não por simetria: MEDIDO — anon tinha os MESMOS 8
-- privilégios nas 12 tabelas. Nada no projeto usa anon.
--
-- Alcança tudo? Sim, verificado antes de escrever: "a user can only revoke
-- privileges that were granted directly by that user" (sql-revoke.html), e os
-- 24 grants (12 tabelas × 2 papéis) têm grantor = postgres, que é quem aplica
-- esta migration. Não há grant de supabase_admin nas tabelas, nem grant a PUBLIC.
--
-- `all tables in schema` não tem alcance surpresa aqui: public não tem view,
-- matview, foreign table, tabela particionada nem sequence — só as 12 tabelas.
-- O dono (postgres) e service_role não são tocados: o backend do WhatsApp segue
-- intacto, e ele nem dependeria disto (é dono E tem BYPASSRLS).
revoke all on all tables in schema public from anon, authenticated;

-- ── 2) O que fica de pé ──────────────────────────────────────────────────────
-- Só leitura, só nas três tabelas do portal do síndico. As outras 9 continuam
-- sem policy E agora sem grant: invisíveis por dois motivos independentes.
--
-- O GRANT é o portão 2 do RLS: sem ele a policy nem chega a ser avaliada — o
-- erro é `permission denied for table`, não zero linhas.
--
-- USAGE em `privado` e EXECUTE em meu_condominio() NÃO aparecem aqui de
-- propósito: nasceram na Etapa 1 (20260731205800) e o revoke acima não os toca
-- (ele é sobre tabelas de public). Sem eles a policy falha com ERRO, porque
-- "policy expressions are run ... with the privileges of the user running the
-- query" (ddl-rowsecurity.html).
grant select on public.condominios, public.areas_comuns, public.reservas
  to authenticated;

-- ── 3) As três policies ──────────────────────────────────────────────────────
-- `to authenticated` é obrigatório, não estilo: "the default is PUBLIC, which
-- will apply the policy to all roles" (sql-createpolicy.html). E é o único
-- elemento que o teste de comportamento NÃO consegue distinguir — MEDIDO: com
-- `to public` a bateria de isolamento fica VERDE, porque public inclui
-- authenticated. Quem guarda isto é a asserção de catálogo.
--
-- `for select` e não `for all`: a ausência de policy de UPDATE é deliberada
-- (C8) e é mais forte que qualquer policy restritiva — com o grant revogado, a
-- escrita para no portão 2. Uma policy SELECT não pode ter WITH CHECK de
-- qualquer forma (sql-createpolicy.html).
--
-- `(select privado.meu_condominio())` e não a chamada crua: o parêntese vira
-- InitPlan avaliado UMA vez, em vez de por linha — ~50× em 10 mil linhas,
-- medido na Etapa 1.
--
-- Permissivas (o default) e únicas por tabela: "all permissive policies which
-- are applicable to a given query will be combined together using the Boolean
-- OR" — com uma só, não há OR que afrouxe o predicado.

-- Esta é a que poderia recursionar: a policy de condominios chama uma função
-- que LÊ condominios. Não recursiona por DUAS condições, e a segunda falha
-- CALADA — ambas medidas em laboratório:
--   função INVOKER            → ERROR: stack depth limit exceeded (recursão real)
--   DEFINER + dono isento     → funciona
--   DEFINER + dono NÃO-isento → 0 linhas, EM SILÊNCIO (nenhuma policy se aplica
--                               ao dono, default deny, a função devolve null)
-- Na produção o dono é postgres, com BYPASSRLS (rolsuper é false) — isento por
-- atributo. Nenhuma constraint garante isso; quem guarda é a asserção de
-- catálogo, que pergunta "o dono é isento?", nunca "o dono é o postgres?".
--
-- Devolver exatamente 1 linha é garantia da Etapa 1: uq_condominios_sindico_user
-- torna ingravável o estado de dois condomínios para o mesmo síndico, que é o
-- estado em que uma função SQL escalar escolheria "the first row" de um
-- resultado multi-linha sem ORDER BY (xfunc-sql.html).
create policy sindico_le_o_proprio_condominio on public.condominios
  for select to authenticated
  using (id = (select privado.meu_condominio()));

create policy sindico_le_areas_do_proprio_condominio on public.areas_comuns
  for select to authenticated
  using (condominio_id = (select privado.meu_condominio()));

create policy sindico_le_reservas_do_proprio_condominio on public.reservas
  for select to authenticated
  using (condominio_id = (select privado.meu_condominio()));

-- ── 4) Para a próxima tabela não nascer aberta ───────────────────────────────
-- Sem isto o revoke acima protege só as 12 de hoje: MEDIDO — o projeto carrega
-- ALTER DEFAULT PRIVILEGES concedendo arwdDxtm a anon/authenticated/service_role
-- em public, então toda tabela nova nasceria com os grants de volta.
--
-- Fecha a metade que faltava de uma garantia que já existia pela outra: o event
-- trigger `ensure_rls` (baseline, confirmado ativo na produção) já liga RLS em
-- toda tabela nova de public. A partir daqui, tabela nova nasce com RLS ligado E
-- sem grants — default deny nos dois eixos.
--
-- Per-schema e não global, e isso importa: "you cannot revoke privileges
-- per-schema if they are granted globally ... per-schema REVOKE is only useful
-- to reverse the effects of a previous per-schema GRANT"
-- (sql-alterdefaultprivileges.html). VERIFICADO antes de escrever: não existe
-- nenhuma entrada global (defaclnamespace = 0) neste banco — as duas entradas de
-- public são per-schema. Se fossem globais, esta linha seria um no-op silencioso.
--
-- ALCANCE PARCIAL, e fica registrado: sem FOR ROLE, o comando muda os defaults
-- do role corrente ("the current role if unspecified"). Existem DUAS entradas
-- para public — uma de postgres e outra de supabase_admin. Esta migration roda
-- como postgres e só alcança a dele. É o suficiente para nós (migrations rodam
-- como postgres, logo nossas tabelas usam a entrada de postgres), mas objetos que
-- a infra do Supabase criar em public seguem herdando a outra. Não dá para
-- corrigir: postgres não é membro de supabase_admin (pg_has_role = false).
--
-- FUNÇÕES ficam de fora por decisão (31/07/2026): a entrada gêmea concede
-- EXECUTE a anon, mas das ~190 funções de public 188 são internas do btree_gist
-- e as nossas duas são rls_auto_enable (já revogada em 20260702171614) e
-- set_updated_at (função de trigger). Exposição real: nenhuma. A mitigação de
-- verdade é a convenção da Etapa 1 — função privilegiada mora em `privado` —
-- e ela é a guarda do aprovar_reserva da Etapa 6.
alter default privileges in schema public
  revoke all on tables from anon, authenticated;

-- ── DOWN (não roda por engano) ───────────────────────────────────────────────
-- Ordem inversa da de cima, para desfazer também fail-closed: os defaults
-- primeiro, as policies depois, e os grants por último.
--
-- alter default privileges in schema public
--   grant all on tables to anon, authenticated;
-- drop policy sindico_le_reservas_do_proprio_condominio on public.reservas;
-- drop policy sindico_le_areas_do_proprio_condominio on public.areas_comuns;
-- drop policy sindico_le_o_proprio_condominio on public.condominios;
-- grant all on all tables in schema public to anon, authenticated;
