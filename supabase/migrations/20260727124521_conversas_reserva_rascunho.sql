-- Fase 4 · Etapa 1 (Fundações): estado do wizard de reserva (D1 = Opção A).
-- A conversa precisa lembrar "esse morador está no meio de uma reserva, em tal
-- passo, com tais dados". Como o processamento é stateless por mensagem, essa
-- memória só existe se estiver GRAVADA na linha da conversa.
--
-- D1 escolheu a Opção A: o estado carrega o NOME do fluxo ('reserva', irmão de
-- 'duvidas') e o passo + dados parciais vivem numa coluna 'rascunho' jsonb.
-- Assim 'estado' segue legível/consultável (where estado='reserva') e mudar um
-- PASSO do wizard não exige migration — só um TIPO de fluxo novo (raro) exigiria.
-- A garantia do banco fica no CONTORNO (estado válido, tenant, existência do
-- rascunho); a FORMA de dentro ({passo, area, dia}) é validada no código
-- (Pydantic, Etapa 3), porque é rascunho descartável — o escape '0' do roteador
-- destrava uma conversa que corrompa.

-- 'reserva' entra na lista de estados válidos. CHECK não se edita: dropa e
-- recria. O scan da recriação é a prova de que nenhuma linha existente ficou
-- inválida (validada sem NOT VALID de propósito).
alter table public.conversas drop constraint chk_conversas_estado;
alter table public.conversas
  add constraint chk_conversas_estado
    check (estado in ('identificacao', 'aguardando_confirmacao', 'menu', 'duvidas', 'reserva'));

-- Coerência: 'reserva' entra no MESMO ramo de menu/duvidas — exige tenant
-- confirmado e proíbe candidato pendente. É o que mantém o isolamento
-- multi-tenant garantido NO BANCO também durante a reserva.
alter table public.conversas drop constraint chk_conversas_estado_coerente;
alter table public.conversas
  add constraint chk_conversas_estado_coerente check (
       (estado = 'identificacao'
          and condominio_id is null and condominio_pendente is null)
    or (estado = 'aguardando_confirmacao'
          and condominio_id is null)
    or (estado in ('menu', 'duvidas', 'reserva')
          and condominio_id is not null and condominio_pendente is null)
  );

-- A sacola do wizard. jsonb, nullable, SEM default: default nulo não reescreve a
-- tabela (sql-altertable, Notes), então a migration é barata mesmo viva.
alter table public.conversas
  add column rascunho jsonb;

-- Coerência da sacola: existe SE E SÓ SE estado='reserva', e é um objeto.
-- Duas garantias num CHECK só: (1) fora da reserva não sobra rascunho órfão;
-- (2) dentro da reserva sempre há sacola, e ela é objeto (não array/escalar).
-- Consequência p/ a Etapa 3: a transição p/ dentro/fora de 'reserva' terá que
-- gravar 'rascunho' no MESMO UPDATE — como a coerência já obriga com a trinca.
alter table public.conversas
  add constraint chk_conversas_rascunho check (
       (estado = 'reserva'
          and rascunho is not null and jsonb_typeof(rascunho) = 'object')
    or (estado <> 'reserva' and rascunho is null)
  );
