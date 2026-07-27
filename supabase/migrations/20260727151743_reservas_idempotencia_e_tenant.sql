-- Fase 4 · Etapa 2: as garantias de banco da escrita de reservas.

-- 1) Gate de idempotência do pedido pendente.
-- excl_reservas_sem_conflito é WHERE status='aprovada': um INSERT de 'pendente'
-- nunca a toca, então reprocessamento (reentrega do sweeper) duplicaria a
-- reserva — a guarda saida_ja_existe do processador só passa a valer depois da
-- TX2, e o crash mora justamente entre a escrita e ela.
-- Espelha uq_mensagens_em_resposta_a: uma reserva por mensagem de entrada, com o
-- vencedor da corrida decidido pelo banco (INSERT ... ON CONFLICT DO NOTHING).
-- on delete set null (não cascade): apagar a conversa não pode apagar a reserva.
alter table public.reservas
  add column origem_mensagem_id uuid
    references public.mensagens (id) on delete set null;

create unique index uq_reservas_origem_mensagem
  on public.reservas (origem_mensagem_id)
  where (origem_mensagem_id is not null);

-- 2) Coerência área ↔ condomínio como invariante do banco.
-- area_id e condominio_id eram FKs INDEPENDENTES: nada impedia gravar a área do
-- condomínio A com o tenant do B. Isso importa porque a excl_reservas_sem_conflito
-- chaveia só por area_id — ela só é garantia POR TENANT se a área implicar o
-- tenant. O unique redundante (id já é PK) existe porque a FK exige destino
-- unique/PK (ddl-constraints); on delete cascade preserva o comportamento atual.
alter table public.areas_comuns
  add constraint uq_areas_comuns_id_condominio unique (id, condominio_id);

alter table public.reservas
  drop constraint reservas_area_id_fkey;

alter table public.reservas
  add constraint fk_reservas_area_do_tenant
    foreign key (area_id, condominio_id)
    references public.areas_comuns (id, condominio_id)
    on delete cascade;
