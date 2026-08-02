-- Preâmbulo de compatibilidade dos testes de integração (Fase 5 · Etapa 2).
--
-- A partir daqui o schema de teste vem das supabase/migrations/ REAIS, aplicadas
-- em ordem pelo conftest.py. Este arquivo só cria o mundo do Supabase que um
-- Postgres pelado não tem, e roda ANTES delas.
--
-- Ele substitui o antigo schema.sql — um espelho de 11 tabelas mantido à mão que
-- já divergia da produção (faltava `encaminhamentos`; `condominios.slug` e
-- `.nome` mentiam a nulabilidade) sem que nada detectasse. MEDIDO em 31/07/2026:
-- as 15 migrations aplicam 15/15 num pgvector/pgvector:pg17 com este preâmbulo,
-- e produzem as mesmas 12 tabelas da produção, com RLS em 12/12.
--
-- REGRA DESTE ARQUIVO: ele reproduz o estado DE FÁBRICA do Supabase, INCLUSIVE
-- os grants excessivos. Não é descuido — é o que a migration das policies precisa
-- ter o que revogar. Se aqui já nascesse fechado, o revoke seria um no-op e o
-- teste ficaria verde provando nada.

-- ── extensions ───────────────────────────────────────────────────────────────
-- Só o schema: as extensões são criadas pela própria baseline (linhas 7 e 10,
-- `create extension if not exists ... with schema ...`), que exige o schema já
-- existindo. É a única linha sem a qual as migrations não sobem.
create schema extensions;

-- Mesmo search_path da produção (rolconfig do role `postgres` no Supabase,
-- verificado em 16/07/2026): sem `extensions` no path o operador <=> não resolve.
-- Vale para as PRÓXIMAS conexões — as dos testes.
alter role test set search_path = "$user", public, extensions;

-- ── auth ─────────────────────────────────────────────────────────────────────
-- auth.users com UMA coluna de propósito: a FK de condominios.sindico_user_id só
-- toca a PK, e a doc do Supabase avisa que o resto do schema auth "may change at
-- any time" (https://supabase.com/docs/guides/auth/managing-user-data).
create schema auth;
create table auth.users (id uuid primary key);

-- Corpo COPIADO VERBATIM da produção em 31/07/2026, via
--   select pg_get_functiondef('auth.uid'::regproc);
-- É cópia congelada de código que não é nosso: se o Supabase mudar a função,
-- este espelho diverge e NADA avisa. A conferência é manual.
create or replace function auth.uid() returns uuid language sql stable as $function$
  select
  coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$function$;

-- ── storage ──────────────────────────────────────────────────────────────────
-- Só as colunas que a faxina de anexos órfãos consulta. O Supabase cria esta
-- tabela sozinho — ela nunca esteve nas nossas migrations, e é por isso que mora
-- aqui e não lá: o preâmbulo é justamente o que o Postgres pelado não tem.
create schema storage;
create table storage.objects (
  id uuid primary key default gen_random_uuid(),
  bucket_id text,
  name text,
  created_at timestamptz not null default now()
);

-- ── Os papéis ────────────────────────────────────────────────────────────────
-- Atributos conferidos contra a produção em 31/07/2026 (pg_roles): anon e
-- authenticated NÃO têm bypassrls e herdam (rolinherit = true, o default do
-- CREATE ROLE); service_role tem bypassrls; postgres tem bypassrls e NÃO é
-- superusuário.
--
-- `postgres` existe aqui só porque 20260706191900 lhe concede privilégios no
-- schema cron. As tabelas seguem sendo de `test`, e não dele: emular o ownership
-- da produção não mudaria nada sob `set role` (MEDIDO) e custaria fidelidade
-- falsa em troca de nada. A consequência fica registrada — no container o dono
-- da função de identidade é isento por ser SUPERUSER, na produção por ter
-- BYPASSRLS. São isenções diferentes; a asserção de catálogo pergunta "é
-- isento?", nunca "é o postgres?".
create role postgres nologin bypassrls;
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;

-- ── Os grants de fábrica ─────────────────────────────────────────────────────
-- É este bloco que dá ao revoke da Etapa 2 o que revogar. `all on tables` são os
-- 8 privilégios do PG 17 (arwdDxtm) — inclusive MAINTAIN, que o
-- information_schema NÃO mostra e que autoriza LOCK TABLE
-- (https://www.postgresql.org/docs/17/ddl-priv.html).
grant usage on schema public, auth to anon, authenticated, service_role;

alter default privileges in schema public
  grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public
  grant execute on functions to anon, authenticated, service_role;
