-- Fase 2 · Passo 1 (Fundações do RAG): citabilidade como invariante do banco (D1)
-- e identidade de documento para reingestão por substituição atômica (D2).
-- regras estava vazia — SET NOT NULL validou sem backfill. Aplicada via MCP em 14/07/2026.

-- D1 ─ todo trecho tem fonte citável (não-nula e com conteúdo de verdade)
alter table public.regras
  alter column fonte set not null;
alter table public.regras
  add constraint chk_regras_fonte_nao_vazia check (fonte ~ '\S');

-- D2 ─ chave de reingestão: "qual documento originou este chunk".
-- Não é UNIQUE (vários chunks por documento); substituir = delete + insert numa transação.
alter table public.regras
  add column documento text;
alter table public.regras
  alter column documento set not null;
alter table public.regras
  add constraint chk_regras_documento_nao_vazio check (documento ~ '\S');

-- Cobre o delete da reingestão (where condominio_id = $1 and documento = $2).
create index idx_regras_condominio_documento
  on public.regras (condominio_id, documento);
