-- Fase 5 · Etapa 1: identidade verificada. Até aqui o síndico era um número de
-- telefone (sindico_telefone, 20260730093504) e nenhuma linha do banco sabia a
-- que PESSOA pertencia. Esta migration cria o vocabulário que a policy da Etapa
-- 2 vai exigir: policy é um predicado de dois lados, e o lado "quem está
-- pedindo" não existe hoje.
--
-- Forward-only, como as 14 anteriores. O DOWN vai comentado no rodapé, no
-- formato de 20260729121823, validado em Postgres 17.
--
-- Custo (sql-altertable.html): coluna nullable sem default não reescreve a
-- tabela; a FK exige validar as linhas existentes — 2, todas NULL. Instantâneo
-- aqui; a nota fica para quando não for.

-- ── 1) A identidade no tenant ────────────────────────────────────────────────
-- Espelho de sindico_telefone no eixo da identidade: mesma forma (coluna +
-- unique parcial), mesmo argumento (single-valued POR CONSTRUÇÃO). Um síndico
-- por condomínio é decisão de produto; se a fase pedir conselho, isto vira
-- tabela e o drop column é barato.
--
-- Nullable: existem 2 linhas e nenhum usuário para preenchê-las, e a
-- eval-sentinela nunca terá um de verdade. NOT NULL exigiria backfill do que
-- não existe.
--
-- ON DELETE RESTRICT e não SET NULL: MEDIDO em laboratório (31/07/2026) — com
-- SET NULL o delete do usuário PASSA e deixa o condomínio órfão sem erro
-- nenhum; como função SQL escalar devolve null quando a query não traz linha
-- (xfunc-sql.html), o síndico só para de ver tudo, indistinguível de "não há
-- nada". RESTRICT "prevents deletion of a referenced row"
-- (ddl-constraints.html) e o erro nomeia tabela e chave. Trocar de síndico
-- continua possível em dois passos: UPDATE aqui, DELETE do usuário depois.
--
-- Não CASCADE, embora seja o que a doc do Supabase recomenda para `profiles`
-- (https://supabase.com/docs/guides/auth/managing-user-data): lá a linha
-- PERTENCE ao usuário; aqui o usuário é ATRIBUTO do condomínio, e cascade
-- levaria junto conversas, mensagens, reservas e regras.
--
-- Referência só à PK, pela mesma doc: "Only use primary keys as foreign key
-- references for schemas and tables like auth.users which are managed by
-- Supabase" — o resto do schema auth "may change at any time".
alter table public.condominios
  add column sindico_user_id uuid
    references auth.users (id) on delete restrict;

-- O unique não é capricho: é o que impede o estado em que meu_condominio()
-- MENTE. MEDIDO — função SQL escalar com 2 linhas devolve "the first row of the
-- last query's result", e "'the first row' of a multirow result is not
-- well-defined unless you use ORDER BY" (xfunc-sql.html). Sem este índice, dois
-- condomínios para o mesmo síndico fazem a função escolher um em silêncio.
-- Parcial pelo mesmo motivo de uq_condominios_sindico_telefone: "enforces
-- uniqueness among the rows that satisfy the index predicate, without
-- constraining those that do not" (indexes-partial.html).
-- É também o índice do lookup `where sindico_user_id = auth.uid()`: um segundo
-- índice seria duplicata.
create unique index uq_condominios_sindico_user
  on public.condominios (sindico_user_id)
  where (sindico_user_id is not null);

-- ── 2) O schema fora da Data API ─────────────────────────────────────────────
-- VERIFICADO contra a API real (31/07/2026): um Accept-Profile desconhecido
-- responde PGRST106 "Only the following schemas are exposed: public,
-- graphql_public". Schema novo fica fora da Data API por DEFAULT — nada a
-- configurar, e é o padrão que a doc do Supabase nomeia: "The private schema is
-- used as it cannot be accessed over the API"
-- (https://supabase.com/docs/guides/api/securing-your-api).
--
-- Nome em PT-BR pela convenção de identificadores do projeto (F1).
-- O que ele NÃO faz: proteger do ROLE. authenticated precisa de USAGE aqui —
-- ver o grant no fim.
create schema privado;

-- ── 3) A função de identidade ────────────────────────────────────────────────
-- SEM PARÂMETRO, e isso é segurança, não estilo: a função roda com o privilégio
-- do dono (postgres, que tem BYPASSRLS), então um argumento `uid` deixaria
-- qualquer authenticated perguntar pelo condomínio de OUTRA pessoa. Sem
-- parâmetro, o único input é o claim `sub`, que o PostgREST verifica.
--
-- SECURITY DEFINER: MEDIDO — sob RLS sem policy, a mesma leitura devolve 0 como
-- INVOKER e 2 como DEFINER. É o que mantém a função enxergando condominios
-- quando a Etapa 2 ligar as policies, e o que evita a recursão (policy sobre
-- condominios que chamasse função INVOKER lendo condominios reentraria nela
-- mesma).
--
-- STABLE: lê tabela, logo não é IMMUTABLE; VOLATILE seria reavaliada por linha
-- (xfunc-volatility.html). auth.uid() também é STABLE.
--
-- search_path = '' e não 'public, pg_temp': MEDIDO em laboratório — com
-- `search_path = public` uma tabela TEMPORÁRIA do chamador SUBSTITUIU a tabela
-- real dentro da função. A doc pede excluir schemas graváveis por usuários não
-- confiáveis e forçar pg_temp por último (sql-createfunction.html), e
-- authenticated TEM privilégio TEMPORARY neste banco (datacl {=Tc/postgres}):
-- a ameaça tem permissão, falta-lhe canal. O vazio é mais forte que a ordem —
-- não depende de alguém manter pg_temp no fim quando mexer nisto. Mesmo fix que
-- 20260702171614 aplicou em set_updated_at (lint 0011).
-- Preço: qualificar tudo (public.condominios, auth.uid()).
create function privado.meu_condominio()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select id from public.condominios where sindico_user_id = auth.uid()
$$;

-- ── 4) Quem pode chamar ──────────────────────────────────────────────────────
-- CREATE FUNCTION concede EXECUTE a PUBLIC por padrão e a doc manda desfazer:
-- "REVOKE ALL ON FUNCTION ... FROM PUBLIC; GRANT EXECUTE ... TO admins"
-- (sql-createfunction.html). Vale mais ainda aqui: o projeto carrega o default
-- privilege legado do Supabase e toda função criada em public por postgres
-- nasce executável por anon (pg_default_acl, verificado 31/07/2026) — morar em
-- `privado` é o que mantém esta fora dessa herança.
revoke all on function privado.meu_condominio() from public;

-- MEDIDO: sem estes dois grants a policy da Etapa 2 falha com ERRO ("permission
-- denied for function meu_condominio"), não com zero linhas — a policy é
-- avaliada com o privilégio de quem pede. Por isso nascem AQUI, não lá.
-- Sem anon de propósito: anon não tem claim `sub` e a função devolveria null de
-- qualquer forma; a superfície fica menor por nada perdido.
grant usage on schema privado to authenticated;
grant execute on function privado.meu_condominio() to authenticated;

-- ── DOWN (validado em Postgres 17; não roda por engano) ──────────────────────
-- A ordem importa: a função depende da coluna, e o schema só cai vazio. Nenhuma
-- limpeza de dado antes (ao contrário do down de 20260729121823): a coluna sai
-- inteira e leva os vínculos junto. Os grants somem com os objetos.
--
-- drop function privado.meu_condominio();
-- drop schema privado;
-- drop index if exists uq_condominios_sindico_user;
-- alter table public.condominios drop column sindico_user_id;
