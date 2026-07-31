-- ═══════════════════════════════════════════════════════════════════════════
-- TEMPORÁRIO — Fase 5. Este arquivo MORRE quando as Etapas 3/4 estiverem
-- validadas. Ele existe só para o portal ter o que ler enquanto é construído.
--
-- DESMONTE COMPLETO, nesta ordem (a ordem não é gosto — ver nota do RESTRICT):
--   1. rodar SÓ a seção 1 (LIMPAR) deste arquivo;
--   2. apagar este arquivo num commit;
--   3. só então apagar os usuários no painel. Antes do passo 1 o banco RECUSA
--      apagá-los: a FK sindico_user_id é ON DELETE RESTRICT, e é ela que
--      transforma "apaguei o usuário errado" em erro em vez de portal vazio.
--
-- NÃO é o `supabase/seed.sql` do Supabase CLI: aquele nome é disparado por
-- `supabase db reset`, fluxo que este projeto não usa (não há config.toml nem
-- CLI instalado). Este é aplicado sob demanda, pelo agente do Supabase.
--
-- A seção 1 roda SEMPRE, antes de semear. Isso dá três coisas de uma vez:
-- idempotência sem ON CONFLICT (rodar duas vezes não duplica), datas sempre
-- frescas (nada de pedido no passado), e o mecanismo de remoção exercitado a
-- cada semeadura — ele nunca apodrece sem ninguém perceber.
--
-- O QUE ESTE ARQUIVO NUNCA TOCA:
--   - as regras da eval-sentinela (é o condominio_id delas que sustenta o eval
--     de vazamento: buscar_por_similaridade filtra por ele);
--   - o "Salão de Festas" real do res-gabro — ver a nota das áreas;
--   - as linhas de condominios: só a coluna sindico_user_id é escrita.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1) LIMPAR ───────────────────────────────────────────────────────────────
-- Prefixo `5eed` (lê-se "seed") em todo id: torna a remoção EXATA e visível a
-- olho nu em qualquer listagem.
--
-- Apagar a área leva as reservas junto por fk_reservas_area_do_tenant
-- (ON DELETE CASCADE) — MEDIDO em laboratório: 1 reserva -> 0 sem tocar nela.
-- Isso é garantia do banco, não disciplina nossa.
delete from public.areas_comuns where id::text like '5eed%';

-- Solicitações não penduram em área nenhuma, então aqui o prefixo é a única
-- força. O delete de reservas é cinto-e-suspensório: o cascade acima já as
-- levou, mas uma reserva `5eed` que fosse parar em área não-sintética (alguém
-- editando isto no futuro) ficaria para trás sem ele.
delete from public.solicitacoes where id::text like '5eed%';
delete from public.reservas     where id::text like '5eed%';

-- O vínculo de identidade também é seed. Some junto, e é o que destrava apagar
-- os usuários no painel (passo 3 do desmonte).
update public.condominios set sindico_user_id = null where sindico_user_id is not null;

-- ── 2) SEMEAR ───────────────────────────────────────────────────────────────

-- 2.1 Identidade. Resolvida por e-mail e não por uuid colado: o uuid nasce no
-- painel e muda a cada recriação do usuário; o e-mail é o que a pessoa digita
-- para entrar.
update public.condominios
   set sindico_user_id = (select id from auth.users where email = 'lzmichelotti@gmail.com')
 where slug = 'res-gabro';

update public.condominios
   set sindico_user_id = (select id from auth.users where email = 'palmabeats808@gmail.com')
 where slug = 'eval-sentinela';

-- A guarda checa o EFEITO, não a premissa: se o e-mail não casar, a subquery
-- devolve null e o UPDATE acima grava null em SILÊNCIO — o seed "passaria" e o
-- portal apareceria vazio na Etapa 3 sem ninguém saber por quê. Erro de
-- digitação tem que doer aqui, não três etapas adiante.
do $$
begin
  if (select sindico_user_id from public.condominios where slug = 'res-gabro') is null then
    raise exception 'síndico A não vinculou — confira o e-mail contra auth.users no painel';
  end if;
  if (select sindico_user_id from public.condominios where slug = 'eval-sentinela') is null then
    raise exception 'síndico B não vinculou — confira o e-mail contra auth.users no painel';
  end if;
end $$;

-- 2.2 Áreas sintéticas — por que NÃO o "Salão de Festas" real.
-- dias_livres conta 'pendente' E 'aprovada' como ocupado (reservas.py) e o
-- wizard pergunta pela janela hoje..hoje+13 (atendimento.py, com
-- reserva_janela_dias = 14). Uma reserva sintética no Salão faria dias SUMIREM
-- do WhatsApp que já roda no piloto.
-- `reservavel = false` é a defesa: listar_areas_reservaveis filtra
-- `and reservavel` (areas.py), então esta área nunca é oferecida ao morador — e
-- como dias_livres é sempre chamada com um area_id específico, reserva daqui
-- não interfere em área nenhuma. É mais forte que semear "fora da janela de 14
-- dias", que voltaria a interferir com o passar do tempo.
insert into public.areas_comuns (id, condominio_id, nome, reservavel, requer_aprovacao)
select '5eed0000-0000-0000-0000-0000000000a1', id, 'Churrasqueira (dados de teste)', false, true
  from public.condominios where slug = 'res-gabro';

insert into public.areas_comuns (id, condominio_id, nome, reservavel, requer_aprovacao)
select '5eed0000-0000-0000-0000-0000000000b1', id, 'Churrasqueira (dados de teste)', false, true
  from public.condominios where slug = 'eval-sentinela';

-- 2.3 Reservas. Três pendentes e uma aprovada no tenant A; uma pendente no B.
-- Assimétrico de propósito: na Etapa 4 "duas listas diferentes" fica evidente
-- pelo TAMANHO, não só pelo conteúdo.
-- Sem sobreposição entre aprovadas: excl_reservas_sem_conflito só morde em
-- 'aprovada', e duas no mesmo intervalo fariam o seed falhar.
insert into public.reservas (id, condominio_id, area_id, telefone, inicio, fim, status, observacao)
select v.id, c.id, v.area_id, v.telefone,
       date_trunc('day', now() + v.dias) + v.hora_ini,
       date_trunc('day', now() + v.dias) + v.hora_fim,
       v.status, v.observacao
  from public.condominios c
  cross join (values
    ('5eed0000-0000-0000-0000-00000000a101'::uuid, '5eed0000-0000-0000-0000-0000000000a1'::uuid,
     '5551999990001', interval '3 days',  interval '18 hours', interval '23 hours',
     'pendente', 'Aniversário — dados de teste'),
    ('5eed0000-0000-0000-0000-00000000a102'::uuid, '5eed0000-0000-0000-0000-0000000000a1'::uuid,
     '5551999990002', interval '5 days',  interval '12 hours', interval '16 hours',
     'pendente', 'Almoço de família — dados de teste'),
    ('5eed0000-0000-0000-0000-00000000a103'::uuid, '5eed0000-0000-0000-0000-0000000000a1'::uuid,
     '5551999990003', interval '9 days',  interval '19 hours', interval '23 hours',
     'pendente', 'Confraternização — dados de teste'),
    ('5eed0000-0000-0000-0000-00000000a104'::uuid, '5eed0000-0000-0000-0000-0000000000a1'::uuid,
     '5551999990004', interval '12 days', interval '14 hours', interval '18 hours',
     'aprovada', 'Chá de bebê — dados de teste')
  ) as v(id, area_id, telefone, dias, hora_ini, hora_fim, status, observacao)
 where c.slug = 'res-gabro';

insert into public.reservas (id, condominio_id, area_id, telefone, inicio, fim, status, observacao)
select '5eed0000-0000-0000-0000-00000000b101', c.id, '5eed0000-0000-0000-0000-0000000000b1',
       '5551999990101',
       date_trunc('day', now() + interval '4 days') + interval '18 hours',
       date_trunc('day', now() + interval '4 days') + interval '22 hours',
       'pendente', 'Pedido do vizinho — dados de teste'
  from public.condominios c
 where c.slug = 'eval-sentinela';

-- 2.4 Solicitações, em estados diferentes para o portal ter o que filtrar.
-- `anexos` fica no default '[]': semear anexo exigiria arquivo no Storage, e
-- nenhuma etapa desta fase o lê.
insert into public.solicitacoes (id, condominio_id, tipo, titulo, descricao, status, telefone)
select v.id, c.id, v.tipo, v.titulo, v.descricao, v.status, v.telefone
  from public.condominios c
  cross join (values
    ('5eed0000-0000-0000-0000-00000000a201'::uuid, 'manutencao', 'Lâmpada queimada na garagem',
     'Segunda vaga à direita, apagada há dois dias. — dados de teste', 'aberta', '5551999990001'),
    ('5eed0000-0000-0000-0000-00000000a202'::uuid, 'reclamacao', 'Barulho depois das 22h',
     'Som alto no bloco B na sexta. — dados de teste', 'aberta', '5551999990002'),
    ('5eed0000-0000-0000-0000-00000000a203'::uuid, 'ocorrencia', 'Vazamento no hall',
     'Poça perto do elevador. — dados de teste', 'em_andamento', '5551999990003'),
    ('5eed0000-0000-0000-0000-00000000a204'::uuid, 'manutencao', 'Portão travando',
     'Já normalizou. — dados de teste', 'resolvida', '5551999990004')
  ) as v(id, tipo, titulo, descricao, status, telefone)
 where c.slug = 'res-gabro';

insert into public.solicitacoes (id, condominio_id, tipo, titulo, descricao, status, telefone)
select '5eed0000-0000-0000-0000-00000000b201', c.id, 'ocorrencia', 'Ocorrência do vizinho',
       'Não deve aparecer para o síndico A. — dados de teste', 'aberta', '5551999990101'
  from public.condominios c
 where c.slug = 'eval-sentinela';

-- ── 3) CONFERIR ─────────────────────────────────────────────────────────────
-- "Duas listas diferentes" é o que a Etapa 4 promete; é aqui que isso nasce.
select c.slug,
       c.sindico_user_id is not null as tem_sindico,
       (select count(*) from public.reservas     r where r.condominio_id = c.id) as reservas,
       (select count(*) from public.solicitacoes s where s.condominio_id = c.id) as solicitacoes
  from public.condominios c
 where c.slug in ('res-gabro', 'eval-sentinela')
 order by c.slug;
