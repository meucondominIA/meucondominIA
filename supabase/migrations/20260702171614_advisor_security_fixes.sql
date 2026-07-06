-- Correções do Security Advisor (Fase 0).
-- Aplicada no remoto em 02/07/2026 via MCP apply_migration (versão 20260702171614).

-- 0011_function_search_path_mutable: fixa o search_path da função de trigger.
-- https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0011_function_search_path_mutable
alter function public.set_updated_at() set search_path = '';

-- 0028/0029: revoga EXECUTE público da função de event trigger (SECURITY DEFINER).
-- https://supabase.com/docs/guides/database/database-advisors?queryGroups=lint&lint=0028_anon_security_definer_function_executable
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;

-- 0014_extension_in_public: NÃO aplicado. A extensão btree_gist pertence a
-- supabase_admin e ALTER EXTENSION ... SET SCHEMA exige ser dono
-- (https://www.postgresql.org/docs/current/sql-alterextension.html).
-- WARN de baixo risco, aceito e documentado em supabase/README.md.
