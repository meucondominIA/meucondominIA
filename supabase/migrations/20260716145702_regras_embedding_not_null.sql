-- Fase 2 · Passo 4: embedding obrigatório. Sem isso, linha sem vetor entra de
-- carona na busca quando sobra vaga no LIMIT (NULL ordena por último em ASC —
-- queries-order.html) com distancia NULL. Nenhum fluxo legítimo grava regra sem
-- embedding: a ingestão escreve chunk + vetor juntos na mesma transação.
-- regras estava vazia — SET NOT NULL validou sem backfill. Aplicada via MCP em 16/07/2026.
alter table public.regras
  alter column embedding set not null;
