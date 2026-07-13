-- Fase 1 · Parte 2: log imutável de mensagens (sem updated_at — mensagem não se
-- edita) e unicidade de conversa ativa por telefone. Aplicada via MCP em 09/07/2026.

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

alter table public.mensagens enable row level security;

create unique index uq_conversas_telefone_ativa
  on public.conversas (telefone) where (status = 'ativa');
