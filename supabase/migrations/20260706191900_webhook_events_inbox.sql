-- Fase 2.1: webhook_events vira inbox (payload cru, status, recovery e retenção).
-- Aplicada manualmente no SQL Editor em 06/07/2026; registrada aqui sem reexecução,
-- mesmo tratamento da baseline (supabase/README.md).

alter table public.webhook_events
  add column payload jsonb,
  add column status text not null default 'pendente'
    constraint chk_webhook_events_status
      check (status in ('pendente', 'processado', 'falhou')),
  add column processado_em timestamptz;

update public.webhook_events
   set payload = '{}'::jsonb, status = 'processado', processado_em = now()
 where payload is null;

alter table public.webhook_events
  alter column payload set not null;

create index idx_webhook_events_pendentes
  on public.webhook_events (recebido_em)
  where status = 'pendente';

create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

select cron.schedule(
  'limpeza_webhook_events',
  '15 6 * * *',
  $$delete from public.webhook_events
     where status = 'processado'
       and processado_em < now() - interval '30 days'$$
);
