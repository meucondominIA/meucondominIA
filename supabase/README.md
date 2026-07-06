# Migrations do banco (Supabase)

Histórico versionado do schema. A fonte de verdade do que **já foi aplicado** no
projeto remoto é a tabela `supabase_migrations.schema_migrations`; estes arquivos
são o espelho em git.
Doc: https://supabase.com/docs/guides/deployment/database-migrations

## Como o histórico foi inaugurado (02/07/2026)

O schema da Fase 0 foi criado manualmente (SQL Editor), fora do sistema de
migrations. Para regularizar:

1. `20260701000000_baseline.sql` — captura fiel do schema existente. Foi
   **registrada como aplicada sem reexecutar** (insert direto em
   `supabase_migrations.schema_migrations`), o equivalente de
   `supabase migration repair --status applied 20260701000000`.
2. `20260702171614_advisor_security_fixes.sql` — primeira migration executada de
   verdade (via MCP `apply_migration`), com os fixes do Security Advisor.

Um banco novo que rode os arquivos em ordem chega ao mesmo estado do remoto.

## Regra a partir de agora

**Todo DDL entra por migration** — nunca mais SQL Editor/Table Editor direto no
remoto. Mudança direta dessincroniza o histórico e quebra `db push` (aviso da doc
oficial: "Making schema changes directly on your remote database bypasses the
migration history").

Fluxos possíveis:
- **Via Claude/MCP:** `apply_migration` (executa e registra). Depois, espelhar o
  arquivo aqui com o mesmo timestamp/nome que aparecer em `list_migrations`.
- **Via CLI (quando instalar):** `supabase link` → `supabase migration new <nome>`
  → editar o SQL → `supabase db push`. Conferir sincronia com
  `supabase migration list`; corrigir divergência com `supabase migration repair`.

## Débito conhecido e aceito

- **Lint 0014 (`extension_in_public`)**: `btree_gist` está no schema `public`.
  A remediação oficial (`alter extension btree_gist set schema extensions;`,
  https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0014_extension_in_public)
  falha porque a extensão pertence a `supabase_admin` e `ALTER EXTENSION` exige
  ser dono (https://www.postgresql.org/docs/current/sql-alterextension.html).
  Risco baixo: RLS-sem-policy nega tudo via API e a app usa conexão direta.
  Revisitar se o suporte do Supabase liberar ou ao recriar o projeto.
