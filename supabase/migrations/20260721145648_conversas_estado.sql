-- Fase 3 · Passo 1 (Fundações): estado da conversa persistido (D1).
-- O processamento é stateless por mensagem — "onde a conversa está" só existe se estiver
-- gravado. 'menu' e 'duvidas' têm condominio_id igual: indistinguíveis por derivação.

-- O DEFAULT não é conveniência de backfill: mensagens.py faz
-- `insert into conversas (telefone) values ($1)` sem nomear colunas — sem DEFAULT o
-- caminho de entrada da Fase 1 quebra na primeira mensagem.
-- DEFAULT não-volátil não reescreve a tabela (sql-altertable, Notes).
alter table public.conversas
  add column estado text not null default 'identificacao';

-- Candidato ainda NÃO confirmado. Coluna própria para condominio_id significar uma coisa
-- só — "tenant confirmado" —, que é o valor que chega em buscar_por_similaridade.
-- on delete set null (e não cascade): apagar um condomínio candidato não pode apagar a
-- conversa do morador. Sobra uma conversa em aguardando_confirmacao sem candidato, e o
-- roteador volta a oferecer a lista.
alter table public.conversas
  add column condominio_pendente uuid
    references public.condominios (id) on delete set null;

alter table public.conversas
  add constraint chk_conversas_estado
    check (estado in ('identificacao', 'aguardando_confirmacao', 'menu', 'duvidas'));

-- Trava a FORMA da máquina, não só a lista de valores: impede que uma conversa não
-- identificada carregue condominio_id residual — o elo mais fraco do isolamento
-- multi-tenant, e a conversa não expira. Mesmo padrão de chk_regras_escopo.
-- Só IS NULL/IS NOT NULL nas colunas anuláveis: o CHECK é satisfeito quando a expressão
-- dá true OU null (ddl-constraints), então ela nunca pode avaliar para null.
-- Validada (sem NOT VALID) de propósito: o scan É a prova de que nenhuma linha existente
-- ficou incoerente.
alter table public.conversas
  add constraint chk_conversas_estado_coerente check (
       (estado = 'identificacao'
          and condominio_id is null and condominio_pendente is null)
    or (estado = 'aguardando_confirmacao'
          and condominio_id is null)
    or (estado in ('menu', 'duvidas')
          and condominio_id is not null and condominio_pendente is null)
  );
