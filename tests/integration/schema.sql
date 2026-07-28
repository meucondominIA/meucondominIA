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

-- btree_gist: exigido pela excl_reservas_sem_conflito (igualdade de area_id num
-- índice gist). No baseline vive em public; aqui o default basta (superusuário).
create extension btree_gist;

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
  ativo boolean not null default true,
  -- fiel à baseline: é ela que traduz "dia 25" em instantes (timezone_por_id).
  timezone text not null default 'America/Sao_Paulo'
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
  updated_at timestamptz not null default now(),
  -- sessão da conversa (20260721183000)
  ultima_interacao_em timestamptz not null default now()
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

-- estado da conversa (20260721145648) + estado do wizard de reserva
-- (Fase 4 · Etapa 1, 20260725211219): +reserva, +rascunho, +chk_conversas_rascunho.
-- Forma FINAL (schema.sql é construído do zero — sem o drop/recria da migration).
alter table public.conversas
  add column estado text not null default 'identificacao';
alter table public.conversas
  add column condominio_pendente uuid
    references public.condominios (id) on delete set null;
alter table public.conversas
  add column rascunho jsonb;
alter table public.conversas
  add constraint chk_conversas_estado
    check (estado in ('identificacao', 'aguardando_confirmacao', 'menu', 'duvidas', 'reserva'));
alter table public.conversas
  add constraint chk_conversas_estado_coerente check (
       (estado = 'identificacao'
          and condominio_id is null and condominio_pendente is null)
    or (estado = 'aguardando_confirmacao'
          and condominio_id is null)
    or (estado in ('menu', 'duvidas', 'reserva')
          and condominio_id is not null and condominio_pendente is null)
  );
alter table public.conversas
  add constraint chk_conversas_rascunho check (
       (estado = 'reserva'
          and rascunho is not null and jsonb_typeof(rascunho) = 'object')
    or (estado <> 'reserva' and rascunho is null)
  );

-- áreas comuns e reservas (baseline 20260701000000) — para as leituras da
-- Fase 4 · Etapa 1 (listar_areas_reservaveis, dias_livres). Cópia fiel das
-- constraints sob teste; unidades é stub mínimo (FK de reservas). Sem os
-- triggers de updated_at: as leituras não fazem UPDATE nessas tabelas.
create table public.unidades (id uuid primary key default gen_random_uuid());

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
create unique index uq_areas_comuns_cond_nome
  on public.areas_comuns (condominio_id, nome);

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
  constraint excl_reservas_sem_conflito exclude using gist (
    area_id with =,
    tstzrange(inicio, fim, '[)') with &&
  ) where (status = 'aprovada')
);

-- escrita de reservas (Fase 4 · Etapa 2, migration 20260727151743).

-- Gate de idempotência: a excl_reservas_sem_conflito é WHERE status='aprovada',
-- então um INSERT de pendente nunca a toca e o reprocessamento duplicaria a
-- reserva. Espelha uq_mensagens_em_resposta_a: uma reserva por mensagem de
-- entrada. on delete set null (não cascade): apagar a conversa não pode apagar
-- a reserva.
alter table public.reservas
  add column origem_mensagem_id uuid
    references public.mensagens (id) on delete set null;
create unique index uq_reservas_origem_mensagem
  on public.reservas (origem_mensagem_id)
  where (origem_mensagem_id is not null);

-- Coerência área ↔ condomínio. area_id e condominio_id eram FKs INDEPENDENTES:
-- nada impedia a área do condomínio A com o tenant do B. Isso importa porque a
-- excl_reservas_sem_conflito chaveia só por area_id — ela só é garantia por
-- tenant SE a área implicar o tenant. O unique redundante existe porque a FK
-- exige destino unique/PK (ddl-constraints).
alter table public.areas_comuns
  add constraint uq_areas_comuns_id_condominio unique (id, condominio_id);
alter table public.reservas
  drop constraint reservas_area_id_fkey;
alter table public.reservas
  add constraint fk_reservas_area_do_tenant
    foreign key (area_id, condominio_id)
    references public.areas_comuns (id, condominio_id)
    on delete cascade;
