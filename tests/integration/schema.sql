-- Bootstrap de schema APENAS para os testes de integração (testcontainers).
-- Reproduz fielmente as tabelas sob teste (conversas, webhook_events, mensagens)
-- e seus índices/constraints, copiados das migrations reais em supabase/migrations/.
-- Fora do escopo (não afetam estas constraints): pgvector, pg_cron, papéis do
-- Supabase, RLS policies. O superusuário do container ignora RLS de qualquer modo.
-- SE a DDL dessas tabelas mudar numa migration, ESTE arquivo precisa acompanhar.

-- Stubs mínimos só para as FKs de conversas resolverem (colunas reais irrelevantes aqui).
create table public.condominios (id uuid primary key default gen_random_uuid());
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
