#!/usr/bin/env bash
# Trava da chave secreta. A chave `sb_secret_...` tem BYPASSRLS: se ela entrar no
# portal, as Etapas 1 e 2 viram decoracao e NADA muda na tela.
#
# Dois escopos, porque as bibliotecas mencionam os NOMES legitimamente:
#   fonte  -> nossos arquivos: proibe ate o nome (nada aqui precisa cita-lo)
#   build  -> .next: proibe so o VALOR, porque @supabase/ssr carrega o literal
#             "sb_secret_" no proprio codigo de validacao de chave
set -uo pipefail

cd "$(dirname "$0")/.."
falhou=0

reprovar() {
  echo "TRAVA: $1" >&2
  falhou=1
}

FONTE=(--exclude-dir=node_modules --exclude-dir=.next --exclude-dir=.git --exclude-dir=scripts)
VALOR_DA_CHAVE='sb_secret_[A-Za-z0-9_-]{16,}'
QUALQUER_JWT='eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}'

# (a) o ambiente do portal so pode conter variaveis NEXT_PUBLIC_
for arquivo in .env .env.local .env.development .env.production .env.example; do
  [ -f "$arquivo" ] || continue
  intrusas=$(grep -oE '^[A-Za-z_][A-Za-z0-9_]*' "$arquivo" | grep -v '^NEXT_PUBLIC_' || true)
  [ -n "$intrusas" ] && reprovar "$arquivo tem variavel sem prefixo NEXT_PUBLIC_: $(echo "$intrusas" | tr '\n' ' ')"
done

# (b) nenhuma mencao a chave secreta nos NOSSOS arquivos
achados=$(grep -rIlE "${VALOR_DA_CHAVE}|SUPABASE_SECRET_KEY|service_role" . "${FONTE[@]}" 2>/dev/null || true)
[ -n "$achados" ] && reprovar "referencia a chave secreta em: $(echo "$achados" | tr '\n' ' ')"

# (c) nenhum JWT colado no codigo (pega a service_role legada, formato eyJ...)
achados=$(grep -rIlE "$QUALQUER_JWT" . "${FONTE[@]}" 2>/dev/null || true)
[ -n "$achados" ] && reprovar "JWT hardcoded em: $(echo "$achados" | tr '\n' ' ')"

# (d) nada com prefixo publico carregando segredo
achados=$(grep -rIlE 'NEXT_PUBLIC_[A-Z0-9_]*(SECRET|SERVICE|PRIVATE|TOKEN)' . "${FONTE[@]}" 2>/dev/null || true)
[ -n "$achados" ] && reprovar "variavel NEXT_PUBLIC_ com nome de segredo em: $(echo "$achados" | tr '\n' ' ')"

# (e) o VALOR da chave nunca pode chegar ao build
if [ -d .next ]; then
  achados=$(grep -rIlE "$VALOR_DA_CHAVE" .next 2>/dev/null || true)
  [ -n "$achados" ] && reprovar "chave secreta no build: $(echo "$achados" | tr '\n' ' ')"
fi

[ "$falhou" -eq 0 ] && echo "trava da chave secreta: ok"
exit "$falhou"
