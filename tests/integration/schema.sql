-- Bootstrap de schema APENAS para os testes de integração (testcontainers).
-- Reproduz fielmente as tabelas sob teste (conversas, webhook_events, mensagens,
-- regras) e seus índices/constraints, copiados das migrations reais em
-- supabase/migrations/. Fora do escopo (não afetam estas constraints): pg_cron,
-- papéis do Supabase, RLS policies. O superusuário do container ignora RLS de
-- qualquer modo.
-- SE a DDL dessas tabelas mudar numa migration, ESTE arquivo precisa acompanhar.

-- pgvector no MESMO schema do Supabase (extensions), para o teste exercitar a
-- resolução do tipo fora do default 'public' — igual à produção.
create schema extensions;
create extension vector with schema extensions;

-- Mesmo search_path da produção (rolconfig do role postgres no Supabase,
-- verificado no banco real em 16/07/2026): sem 'extensions' no path, o
-- operador <=> não resolve. Vale para as PRÓXIMAS conexões (as dos testes).
alter role test set search_path = "$user", public, extensions;

-- Stubs mínimos: FKs + o slug consultado por condominios.py (UNIQUE e CHECK
-- como na produção; nullable AQUI, ao contrário da produção, para os inserts
-- `default values` dos outros testes continuarem válidos). `nome` entra pelo
-- mesmo motivo (NOT NULL na produção, nullable aqui); `ativo` é fiel — é ele
-- que tira o tenant sintético do menu do morador (D2).
create table public.condominios (
  id uuid primary key default gen_random_uuid(),
  slug text unique check (slug ~ '^[a-z0-9-]+$'),
  nome text,
  ativo boolean not null default true
);
create table public.moradores (id uuid primary key default gen_random_uuid());

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- conversas (baseline 20260701000000)
create table public.conversas (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid references public.condominios (id) on delete cascade,
  telefone text not null,
  morador_id uuid references public.moradores (id) on delete set null,
  status text not null default 'ativa' check (status in ('ativa', 'encerrada')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_conversas_condominio_id on public.conversas (condominio_id);
create index idx_conversas_telefone on public.conversas (telefone);
create trigger trg_conversas_updated_at
  before update on public.conversas
  for each row execute function public.set_updated_at();

-- webhook_events: base (baseline) + inbox durável (20260706191900, sem pg_cron)
create table public.webhook_events (
  message_id text primary key,
  recebido_em timestamptz not null default now()
);
alter table public.webhook_events
  add column payload jsonb,
  add column status text not null default 'pendente'
    constraint chk_webhook_events_status
      check (status in ('pendente', 'processado', 'falhou')),
  add column processado_em timestamptz;
alter table public.webhook_events
  alter column payload set not null;
create index idx_webhook_events_pendentes
  on public.webhook_events (recebido_em)
  where status = 'pendente';

-- mensagens + conversa ativa única (20260709193445)
create table public.mensagens (
  id uuid primary key default gen_random_uuid(),
  conversa_id uuid not null references public.conversas (id) on delete cascade,
  papel text not null check (papel in ('morador', 'assistente')),
  tipo text not null default 'text' check (tipo in ('text', 'unsupported')),
  conteudo text,
  message_id text,
  em_resposta_a uuid references public.mensagens (id) on delete set null,
  created_at timestamptz not null default now(),
  constraint chk_mensagens_assistente_conteudo
    check (papel <> 'assistente' or conteudo is not null)
);
create unique index uq_mensagens_message_id
  on public.mensagens (message_id) where (message_id is not null);
create unique index uq_mensagens_em_resposta_a
  on public.mensagens (em_resposta_a) where (em_resposta_a is not null);
create index idx_mensagens_conversa_created
  on public.mensagens (conversa_id, created_at);
create unique index uq_conversas_telefone_ativa
  on public.conversas (telefone) where (status = 'ativa');

-- regras: base (baseline) + fonte/documento obrigatórios (20260714172922)
create table public.regras (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid references public.condominios (id) on delete cascade,
  escopo text not null check (escopo in ('geral', 'especifico')),
  conteudo text not null,
  fonte text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  embedding extensions.vector(3072),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_regras_escopo check (
    (escopo = 'especifico' and condominio_id is not null)
    or (escopo = 'geral' and condominio_id is null)
  )
);
create index idx_regras_condominio_id on public.regras (condominio_id);
create trigger trg_regras_updated_at
  before update on public.regras
  for each row execute function public.set_updated_at();

alter table public.regras
  alter column fonte set not null;
alter table public.regras
  add constraint chk_regras_fonte_nao_vazia check (fonte ~ '\S');

alter table public.regras
  add column documento text;
alter table public.regras
  alter column documento set not null;
alter table public.regras
  add constraint chk_regras_documento_nao_vazio check (documento ~ '\S');

create index idx_regras_condominio_documento
  on public.regras (condominio_id, documento);

-- embedding obrigatório (20260716145702)
alter table public.regras
  alter column embedding set not null;

-- estado da conversa (20260721145648)
alter table public.conversas
  add column estado text not null default 'identificacao';
alter table public.conversas
  add column condominio_pendente uuid
    references public.condominios (id) on delete set null;
alter table public.conversas
  add constraint chk_conversas_estado
    check (estado in ('identificacao', 'aguardando_confirmacao', 'menu', 'duvidas'));
alter table public.conversas
  add constraint chk_conversas_estado_coerente check (
       (estado = 'identificacao'
          and condominio_id is null and condominio_pendente is null)
    or (estado = 'aguardando_confirmacao'
          and condominio_id is null)
    or (estado in ('menu', 'duvidas')
          and condominio_id is not null and condominio_pendente is null)
  );
