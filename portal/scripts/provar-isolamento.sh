#!/usr/bin/env bash
# Prova da Fase 5 · Etapa 4: o RLS morde na cadeia HTTP inteira.
#
# A bateria da Etapa 2 prova o predicado NO BANCO (asyncpg, sem PostgREST).
# Esta prova o caminho que a tela usa: token -> role authenticated -> PostgREST
# -> policy -> linha.
#
# Bilateral e com contagem de propósito: uma asserção só de "B não vê o que é de
# A" fica VERDE se B não vir nada, inclusive o que é dele. Aqui os dois lados
# precisam ser não-vazios E disjuntos.
#
# Não deixa rastro: cada login cria linha em auth.sessions e auth.refresh_tokens,
# e o logout no fim as apaga (MEDIDO). Senha nunca vai em argumento nem em
# arquivo — só por prompt sem eco.
set -uo pipefail

cd "$(dirname "$0")/.."
[ -f .env.local ] || { echo "faltou .env.local" >&2; exit 1; }
set -a && . ./.env.local && set +a

U="$NEXT_PUBLIC_SUPABASE_URL"
K="$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"
falhou=0

reprovar() { echo "  REPROVOU: $1" >&2; falhou=1; }
aprovar()  { echo "  ok: $1"; }

api() { curl -s -H "apikey: $K" -H "Authorization: Bearer $1" "$U/rest/v1/$2"; }

json() { python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }

entrar() {
  curl -s -X POST "$U/auth/v1/token?grant_type=password" \
    -H "apikey: $K" -H "Content-Type: application/json" \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"email":sys.argv[1],"password":sys.argv[2]}))' "$1" "$2")" \
    | json 'print(d.get("access_token",""))'
}

sair() { curl -s -o /dev/null -X POST "$U/auth/v1/logout" -H "apikey: $K" -H "Authorization: Bearer $1"; }

# Os ids saem por arquivo, não por $(...): captura em subshell perderia o
# `falhou=1` das asserções internas e a prova sairia com exit 0 reprovando.
conferir_sindico() {
  local rotulo="$1" token="$2" destino="$3"
  : > "$destino"
  echo "== $rotulo"

  local cond
  cond=$(api "$token" "condominios?select=id,nome")
  local n_cond
  n_cond=$(echo "$cond" | json 'print(len(d))')
  [ "$n_cond" = "1" ] || { reprovar "esperava 1 condomínio, veio $n_cond"; return; }
  aprovar "vê exatamente 1 condomínio: $(echo "$cond" | json 'print(d[0]["nome"])')"

  local cid
  cid=$(echo "$cond" | json 'print(d[0]["id"])')

  local reservas
  reservas=$(api "$token" "reservas?select=id,condominio_id,areas_comuns(nome)&order=inicio")
  local n
  n=$(echo "$reservas" | json 'print(len(d))')
  [ "$n" -gt 0 ] || { reprovar "0 reservas — tela vazia não é prova de isolamento"; return; }
  aprovar "vê $n reserva(s), e não zero"

  local tenants
  tenants=$(echo "$reservas" | json 'print(len({r["condominio_id"] for r in d}))')
  [ "$tenants" = "1" ] || reprovar "reservas de $tenants condomínios diferentes"
  [ "$tenants" = "1" ] && aprovar "todas do mesmo condomínio"

  local sem_area
  sem_area=$(echo "$reservas" | json 'print(sum(1 for r in d if not r.get("areas_comuns")))')
  [ "$sem_area" = "0" ] || reprovar "$sem_area reserva(s) com área nula"
  [ "$sem_area" = "0" ] && aprovar "nenhuma área nula no embed"

  # A decisão de NÃO repetir o filtro de tenant no cliente, provada: a policy
  # sozinha já produz o mesmo resultado que o filtro explícito produziria.
  local sem com
  sem=$(api "$token" "reservas?select=id&order=id" | json 'print(d)')
  com=$(api "$token" "reservas?select=id&condominio_id=eq.$cid&order=id" | json 'print(d)')
  [ "$sem" = "$com" ] || reprovar "filtro redundante MUDOU o resultado"
  [ "$sem" = "$com" ] && aprovar "com e sem filtro de condominio_id: idêntico"

  local negativo
  negativo=$(api "$token" "moradores?select=id" | json 'print(d.get("code",""))')
  [ "$negativo" = "42501" ] || reprovar "tabela sem grant devia dar 42501, deu '$negativo'"
  [ "$negativo" = "42501" ] && aprovar "tabela sem policy falha ALTO (42501), não em silêncio"

  echo "$reservas" | json 'print(",".join(sorted(r["id"] for r in d)))' > "$destino"
}

echo "Prova de isolamento — dois síndicos, a mesma consulta."
read -rp "e-mail do síndico A: " EMAIL_A
read -rsp "senha de A: " SENHA_A; echo
read -rp "e-mail do síndico B: " EMAIL_B
read -rsp "senha de B: " SENHA_B; echo
echo

TOKEN_A=$(entrar "$EMAIL_A" "$SENHA_A"); unset SENHA_A
TOKEN_B=$(entrar "$EMAIL_B" "$SENHA_B"); unset SENHA_B
[ -n "$TOKEN_A" ] || { echo "login de A falhou" >&2; exit 1; }
[ -n "$TOKEN_B" ] || { echo "login de B falhou" >&2; exit 1; }

TMP_A=$(mktemp); TMP_B=$(mktemp)
trap 'rm -f "$TMP_A" "$TMP_B"' EXIT

conferir_sindico "SÍNDICO A" "$TOKEN_A" "$TMP_A"
echo
conferir_sindico "SÍNDICO B" "$TOKEN_B" "$TMP_B"
echo

IDS_A=$(cat "$TMP_A"); IDS_B=$(cat "$TMP_B")

echo "== DISJUNÇÃO"
if python3 - "$IDS_A" "$IDS_B" <<'PY'
import sys
a = set(filter(None, sys.argv[1].split(',')))
b = set(filter(None, sys.argv[2].split(',')))
print(f"  |A|={len(a)}  |B|={len(b)}  |A∩B|={len(a & b)}")
sys.exit(0 if a and b and not (a & b) else 1)
PY
then echo "  ok: conjuntos não-vazios e disjuntos"
else echo "  REPROVOU: interseção não-vazia ou lado vazio" >&2; falhou=1
fi

sair "$TOKEN_A"; sair "$TOKEN_B"
echo
echo "  sessões revogadas"

[ "$falhou" -eq 0 ] && echo "PROVA DE ISOLAMENTO: ok"
exit "$falhou"
