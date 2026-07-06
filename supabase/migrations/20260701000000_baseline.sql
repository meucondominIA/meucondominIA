-- Baseline da Fase 0: captura o schema criado manualmente (SQL Editor) até 01/07/2026.
-- Já está aplicado no projeto remoto; foi registrado no histórico sem reexecução,
-- equivalente a `supabase migration repair --status applied 20260701000000`.
-- Doc: https://supabase.com/docs/guides/deployment/database-migrations

-- ── Extensões ────────────────────────────────────────────────────────────────
create extension if not exists vector with schema extensions;
-- btree_gist no public é o estado histórico real; mover exige ser dono da
-- extensão (supabase_admin) — ver supabase/README.md (lint 0014).
create extension if not exists btree_gist with schema public;

-- ── Funções utilitárias ──────────────────────────────────────────────────────
-- Sem SET search_path aqui de propósito: o fix (lint 0011) vem na migration seguinte.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Rede de segurança: habilita RLS automaticamente em toda tabela nova do public.
create or replace function public.rls_auto_enable()
returns event_trigger
language plpgsql
security definer
set search_path to 'pg_catalog'
as $$
declare
  cmd record;
begin
  for cmd in
    select *
    from pg_event_trigger_ddl_commands()
    where command_tag in ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      and object_type in ('table', 'partitioned table')
  loop
    if cmd.schema_name is not null
       and cmd.schema_name in ('public')
       and cmd.schema_name not in ('pg_catalog', 'information_schema')
       and cmd.schema_name not like 'pg_toast%'
       and cmd.schema_name not like 'pg_temp%' then
      begin
        execute format('alter table if exists %s enable row level security', cmd.object_identity);
        raise log 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      exception
        when others then
          raise log 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      end;
    else
      raise log 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)',
        cmd.object_identity, cmd.schema_name;
    end if;
  end loop;
end;
$$;

create event trigger ensure_rls
  on ddl_command_end
  when tag in ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
  execute function public.rls_auto_enable();

-- ── Tabelas (ordem de dependência das FKs) ───────────────────────────────────
create table public.condominios (
  id uuid primary key default gen_random_uuid(),
  nome text not null,
  slug text not null unique check (slug ~ '^[a-z0-9-]+$'),
  documento text,
  endereco text,
  cidade text,
  uf text check (uf is null or uf ~ '^[A-Z]{2}$'),
  timezone text not null default 'America/Sao_Paulo',
  ativo boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.unidades (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  bloco text,
  numero text not null,
  tipo text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.moradores (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  unidade_id uuid references public.unidades (id) on delete set null,
  nome text,
  telefone text check (telefone is null or telefone ~ '^[0-9]{10,15}$'),
  papel text check (papel in ('proprietario', 'inquilino', 'morador', 'outro')),
  consentimento_lgpd_em timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.areas_comuns (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  nome text not null,
  descricao text,
  capacidade integer check (capacidade is null or capacidade > 0),
  reservavel boolean not null default true,
  requer_aprovacao boolean not null default true,
  horario_abertura time,
  horario_fechamento time,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_areas_horario check (
    horario_abertura is null
    or horario_fechamento is null
    or horario_fechamento > horario_abertura
  )
);

create table public.reservas (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  area_id uuid not null references public.areas_comuns (id) on delete cascade,
  unidade_id uuid references public.unidades (id) on delete set null,
  morador_id uuid references public.moradores (id) on delete set null,
  telefone text,
  inicio timestamptz not null,
  fim timestamptz not null,
  status text not null default 'pendente'
    check (status in ('pendente', 'aprovada', 'recusada', 'cancelada')),
  observacao text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_reservas_periodo check (fim > inicio),
  -- Invariante central: duas reservas APROVADAS da mesma área nunca se sobrepõem.
  -- '[)' = início incluso, fim excluso (18h–19h e 19h–20h não conflitam).
  constraint excl_reservas_sem_conflito exclude using gist (
    area_id with =,
    tstzrange(inicio, fim, '[)') with &&
  ) where (status = 'aprovada')
);

create table public.solicitacoes (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  unidade_id uuid references public.unidades (id) on delete set null,
  morador_id uuid references public.moradores (id) on delete set null,
  telefone text,
  tipo text not null default 'ocorrencia'
    check (tipo in ('reclamacao', 'ocorrencia', 'manutencao', 'outro')),
  titulo text,
  descricao text,
  anexos jsonb not null default '[]'::jsonb check (jsonb_typeof(anexos) = 'array'),
  status text not null default 'aberta'
    check (status in ('aberta', 'em_andamento', 'resolvida', 'cancelada')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

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

create table public.conversas (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid references public.condominios (id) on delete cascade,
  telefone text not null,
  morador_id uuid references public.moradores (id) on delete set null,
  status text not null default 'ativa' check (status in ('ativa', 'encerrada')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.encaminhamentos (
  id uuid primary key default gen_random_uuid(),
  condominio_id uuid not null references public.condominios (id) on delete cascade,
  conversa_id uuid references public.conversas (id) on delete set null,
  telefone text not null,
  assunto text not null,
  status text not null default 'pendente' check (status in ('pendente', 'atendido')),
  atendido_em timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Dedup/idempotência do webhook (INSERT ... ON CONFLICT DO NOTHING).
create table public.webhook_events (
  message_id text primary key,
  recebido_em timestamptz not null default now()
);

-- ── Índices únicos (invariantes) e de apoio (FKs e buscas) ───────────────────
create unique index uq_condominios_documento
  on public.condominios (documento) where (documento is not null);
-- NULLS NOT DISTINCT: bloco nulo não abre brecha para unidades duplicadas.
create unique index uq_unidades_cond_bloco_numero
  on public.unidades (condominio_id, bloco, numero) nulls not distinct;
create unique index uq_moradores_cond_telefone
  on public.moradores (condominio_id, telefone) where (telefone is not null);
create unique index uq_areas_comuns_cond_nome
  on public.areas_comuns (condominio_id, nome);

create index idx_moradores_condominio_id on public.moradores (condominio_id);
create index idx_moradores_unidade_id on public.moradores (unidade_id);
create index idx_reservas_condominio_id on public.reservas (condominio_id);
create index idx_reservas_area_id on public.reservas (area_id);
create index idx_reservas_unidade_id on public.reservas (unidade_id);
create index idx_reservas_morador_id on public.reservas (morador_id);
create index idx_solicitacoes_condominio_id on public.solicitacoes (condominio_id);
create index idx_solicitacoes_unidade_id on public.solicitacoes (unidade_id);
create index idx_solicitacoes_morador_id on public.solicitacoes (morador_id);
create index idx_regras_condominio_id on public.regras (condominio_id);
create index idx_conversas_condominio_id on public.conversas (condominio_id);
create index idx_conversas_telefone on public.conversas (telefone);
create index idx_encaminhamentos_condominio_id on public.encaminhamentos (condominio_id);
create index idx_encaminhamentos_conversa_id on public.encaminhamentos (conversa_id);

-- ── Triggers de updated_at ───────────────────────────────────────────────────
create trigger trg_condominios_updated_at
  before update on public.condominios
  for each row execute function public.set_updated_at();
create trigger trg_unidades_updated_at
  before update on public.unidades
  for each row execute function public.set_updated_at();
create trigger trg_moradores_updated_at
  before update on public.moradores
  for each row execute function public.set_updated_at();
create trigger trg_areas_comuns_updated_at
  before update on public.areas_comuns
  for each row execute function public.set_updated_at();
create trigger trg_reservas_updated_at
  before update on public.reservas
  for each row execute function public.set_updated_at();
create trigger trg_solicitacoes_updated_at
  before update on public.solicitacoes
  for each row execute function public.set_updated_at();
create trigger trg_regras_updated_at
  before update on public.regras
  for each row execute function public.set_updated_at();
create trigger trg_conversas_updated_at
  before update on public.conversas
  for each row execute function public.set_updated_at();
create trigger trg_encaminhamentos_updated_at
  before update on public.encaminhamentos
  for each row execute function public.set_updated_at();

-- ── RLS (explícito, além do event trigger) ───────────────────────────────────
alter table public.condominios enable row level security;
alter table public.unidades enable row level security;
alter table public.moradores enable row level security;
alter table public.areas_comuns enable row level security;
alter table public.reservas enable row level security;
alter table public.solicitacoes enable row level security;
alter table public.regras enable row level security;
alter table public.conversas enable row level security;
alter table public.encaminhamentos enable row level security;
alter table public.webhook_events enable row level security;
