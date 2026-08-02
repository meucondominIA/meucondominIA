"""Integração: as policies do síndico MORDEM (Fase 5 · Etapa 2).

A diferença entre este arquivo e test_isolamento_tenant.py é a frase que cada um
prova. Lá: "o código filtrou" — a query da busca põe o condominio_id no WHERE.
Aqui: "o BANCO recusou" — a query não filtra nada e mesmo assim as linhas do
outro condomínio não vêm.

O RLS tem três portões e a policy é o último (ddl-rowsecurity.html):
  1. isenção de role — superusuário, BYPASSRLS, ou dono da tabela
  2. GRANT — falha aqui produz ERRO
  3. policy — o USING é avaliado por linha; linha reprovada some EM SILÊNCIO

`test` (o POSTGRES_USER do container) é superusuário E dono das tabelas: uma
asserção feita por ele para no portão 1 e passaria com `using (true)`, com
`using (false)` e sem policy nenhuma. Por isso todo teste de comportamento aqui
troca de papel com `set local role authenticated` antes de olhar.

MEDIDO em 31/07/2026: `SET LOCAL ROLE` a partir de um superusuário SUJEITA a
sessão ao RLS — é o current_user que conta, não o session_user. E por ser LOCAL,
o rollback do rodar_tx desfaz o papel junto com os dados.

O QUE ESTE ARQUIVO NÃO PROVA, de propósito:
- que o PostgREST põe o claim como auth.uid() espera: não há PostgREST aqui, o
  setting é escrito à mão. Se o Supabase renomear `request.jwt.claims`, estes
  testes seguem VERDES e a produção quebra;
- que o JWT é verificado: set_config aceita qualquer `sub`, sem assinatura;
- que a produção está no estado que as migrations descrevem — aqui o banco É as
  migrations. Isso é asserção de catálogo rodada contra o remoto, à mão;
- que o dono da função é isento NA PRODUÇÃO: no container ele é isento por ser
  SUPERUSER, na produção por ter BYPASSRLS. São isenções diferentes; o que se
  testa aqui é a propriedade ("é isento?"), não o valor;
- nada sobre desempenho: os ~50× do `(select ...)` vêm de 10 mil linhas, e aqui
  há cinco.
"""

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_UID_A = "aaaaaaaa-0000-0000-0000-00000000000a"
_UID_B = "bbbbbbbb-0000-0000-0000-00000000000b"

# Volumes DIFERENTES de propósito: com 1 e 1, "vê o que é seu" e "vê o total"
# seriam a mesma frase e a mutação `using (true)` passaria. Espelha as
# proporções do piloto (res-gabro 4, eval-sentinela 1).
_RESERVAS_A = 4
_RESERVAS_B = 1

_DO_PORTAL = ("condominios", "areas_comuns", "reservas")
_FORA_DO_RECORTE = (
    "avisos_sindico",
    "conversas",
    "encaminhamentos",
    "mensagens",
    "moradores",
    "regras",
    "solicitacoes",
    "unidades",
    "webhook_events",
)
# Os 8 do PG 17. MAINTAIN é o novo, invisível ao information_schema, e autoriza
# LOCK TABLE — por isso a asserção usa has_table_privilege (ddl-priv.html).
_PRIVILEGIOS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)


async def _semear(conn) -> tuple[str, str]:
    """Dois síndicos, dois condomínios, volumes diferentes. Devolve (cond_a, cond_b)."""
    conds = []
    for uid, slug, quantas in (
        (_UID_A, "tenant-a", _RESERVAS_A),
        (_UID_B, "tenant-b", _RESERVAS_B),
    ):
        await conn.execute("insert into auth.users (id) values ($1)", uid)
        cond = await conn.fetchval(
            "insert into condominios (slug, nome, sindico_user_id) "
            "values ($1, $2, $3) returning id",
            slug,
            slug.upper(),
            uid,
        )
        area = await conn.fetchval(
            "insert into areas_comuns (condominio_id, nome) values ($1, 'Salão') "
            "returning id",
            cond,
        )
        for dia in range(1, quantas + 1):
            await conn.execute(
                "insert into reservas (condominio_id, area_id, inicio, fim) "
                "values ($1, $2, now() + ($3 || ' days')::interval, "
                "        now() + ($3 || ' days')::interval + interval '2 hours')",
                cond,
                area,
                str(dia),
            )
        conds.append(cond)
    return conds[0], conds[1]


async def _como_sindico(conn, uid: str) -> None:
    """Assume o papel `authenticated` com o claim `sub` do síndico."""
    await conn.execute(
        "select set_config('request.jwt.claims', $1, true)", f'{{"sub":"{uid}"}}'
    )
    await conn.execute("set local role authenticated")


async def _como_authenticated_sem_identidade(conn) -> None:
    await conn.execute("select set_config('request.jwt.claims', '', true)")
    await conn.execute("set local role authenticated")


async def _voltar(conn) -> None:
    await conn.execute("reset role")


def test_sindico_ve_apenas_as_proprias_reservas(rodar_tx):
    """O coração da etapa, e a asserção é BILATERAL por necessidade.

    Um teste unilateral ("A não vê nada alheio") ficaria VERDE com o tenant
    chumbado na policy — MEDIDO. Só o lado do B mata essa mutação.
    """

    async def body(conn):
        cond_a, cond_b = await _semear(conn)

        # Controle: o dado dos DOIS existe. Sem isto, "A vê 0 de B" seria
        # compatível com "B não tem nada".
        assert await conn.fetchval("select count(*) from reservas") == (
            _RESERVAS_A + _RESERVAS_B
        )

        await _como_sindico(conn, _UID_A)
        assert await conn.fetchval("select count(*) from reservas") == _RESERVAS_A
        assert (
            await conn.fetchval(
                "select count(*) from reservas where condominio_id = $1", cond_b
            )
            == 0
        )
        await _voltar(conn)

        await _como_sindico(conn, _UID_B)
        assert await conn.fetchval("select count(*) from reservas") == _RESERVAS_B
        assert (
            await conn.fetchval(
                "select count(*) from reservas where condominio_id = $1", cond_a
            )
            == 0
        )

    rodar_tx(body)


def test_sindico_ve_apenas_o_proprio_condominio(rodar_tx):
    """A policy que poderia recursionar: o predicado lê a própria tabela."""

    async def body(conn):
        cond_a, cond_b = await _semear(conn)
        assert await conn.fetchval("select count(*) from condominios") == 2

        await _como_sindico(conn, _UID_A)
        assert await conn.fetchval("select slug from condominios") == "tenant-a"
        await _voltar(conn)

        await _como_sindico(conn, _UID_B)
        assert await conn.fetchval("select slug from condominios") == "tenant-b"

    rodar_tx(body)


def test_sindico_ve_apenas_as_proprias_areas(rodar_tx):
    async def body(conn):
        await _semear(conn)
        assert await conn.fetchval("select count(*) from areas_comuns") == 2

        await _como_sindico(conn, _UID_A)
        assert await conn.fetchval("select count(*) from areas_comuns") == 1
        await _voltar(conn)

        await _como_sindico(conn, _UID_B)
        assert await conn.fetchval("select count(*) from areas_comuns") == 1

    rodar_tx(body)


def test_sem_identidade_nao_ve_nada_e_a_causa_e_a_identidade(rodar_tx):
    """O falso verde desta etapa, nomeado e separado.

    Sem claim, meu_condominio() devolve NULL; `condominio_id = NULL` avalia como
    NULL (não false), a linha é reprovada e vêm zero linhas — assinatura
    IDÊNTICA à de `using (false)` e à da policy dropada. Por isso a asserção não
    para no zero: ela cobra que a CAUSA seja a identidade ausente.
    """

    async def body(conn):
        await _semear(conn)
        await _como_authenticated_sem_identidade(conn)

        assert await conn.fetchval("select privado.meu_condominio()") is None
        for tabela in _DO_PORTAL:
            assert await conn.fetchval(f"select count(*) from {tabela}") == 0

    rodar_tx(body)


def test_escrita_e_recusada_com_erro_nao_com_silencio(rodar_tx):
    """Portão 2, e a distinção importa: GRANT ausente ERRA, RLS SILENCIA.

    Não há policy de UPDATE/INSERT/DELETE, e isso é deliberado (C8): a ausência
    é mais forte que qualquer policy restritiva. O revoke é a segunda tranca.
    """

    async def body(conn):
        await _semear(conn)
        await _como_sindico(conn, _UID_A)

        for comando in (
            "insert into condominios (slug, nome) values ('invasor', 'X')",
            "update reservas set status = 'aprovada'",
            "delete from reservas",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(comando)

    rodar_tx(body)


def test_tabelas_fora_do_recorte_sao_invisiveis(rodar_tx):
    """As 9 restantes: sem policy E sem grant. Invisíveis por dois motivos
    independentes, e é o GRANT que fala primeiro."""

    async def body(conn):
        await _semear(conn)
        await _como_sindico(conn, _UID_A)

        for tabela in _FORA_DO_RECORTE:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(f"select 1 from {tabela}")

    rodar_tx(body)


def test_catalogo_das_policies(rodar_tx):
    """O que o teste de comportamento NÃO alcança.

    MEDIDO: trocar `to authenticated` por `to public` deixa a bateria de
    isolamento inteira VERDE, porque PUBLIC inclui authenticated — e o default
    do TO é justamente PUBLIC (sql-createpolicy.html). Quem guarda isto é aqui.
    """

    async def body(conn):
        policies = await conn.fetch(
            "select tablename, cmd, permissive, roles::text[] as roles "
            "from pg_policies where schemaname = 'public' order by tablename"
        )

        assert [p["tablename"] for p in policies] == sorted(_DO_PORTAL)
        for p in policies:
            assert p["cmd"] == "SELECT"
            assert p["permissive"] == "PERMISSIVE"
            assert p["roles"] == ["authenticated"]

    rodar_tx(body)


def test_catalogo_dos_grants(rodar_tx):
    """authenticated tem SELECT e SÓ SELECT nas 3; anon não tem nada em lugar
    nenhum; as 9 de fora estão fechadas para os dois.

    Privilégio a privilégio via has_table_privilege, e não pelo
    information_schema: MAINTAIN não aparece lá e autoriza LOCK TABLE.
    """

    async def body(conn):
        for tabela in _DO_PORTAL + _FORA_DO_RECORTE:
            for priv in _PRIVILEGIOS:
                esperado = tabela in _DO_PORTAL and priv == "SELECT"
                assert (
                    await conn.fetchval(
                        "select has_table_privilege('authenticated', $1, $2)",
                        f"public.{tabela}",
                        priv,
                    )
                    is esperado
                ), f"authenticated / {tabela} / {priv}"

                assert (
                    await conn.fetchval(
                        "select has_table_privilege('anon', $1, $2)",
                        f"public.{tabela}",
                        priv,
                    )
                    is False
                ), f"anon / {tabela} / {priv}"

    rodar_tx(body)


def test_as_doze_tabelas_tem_rls_ligado(rodar_tx):
    """A outra metade do default deny, e ela não é desta etapa: quem liga o RLS
    em tabela nova é o event trigger `ensure_rls`, desde a baseline."""

    async def body(conn):
        sem_rls = await conn.fetch(
            "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"
        )
        assert [r["relname"] for r in sem_rls] == []

    rodar_tx(body)


def test_tabela_nova_nasce_fechada_nos_dois_eixos(rodar_tx):
    """O único teste que cobre o ALTER DEFAULT PRIVILEGES — e ele precisou
    existir: sem esta função, remover aquela cláusula da migration não deixava
    NENHUM teste vermelho (o comando só afeta tabelas FUTURAS, e as outras
    asserções olham só as 12 que já existem).

    Prova o par que a etapa completa:
      RLS ligado    ← event trigger `ensure_rls`, da baseline (não é desta etapa)
      sem grants    ← o ALTER DEFAULT PRIVILEGES desta etapa
    Antes daqui o "default deny" existia pela metade.
    """

    async def body(conn):
        await conn.execute("create table public.tabela_recem_nascida (id int)")

        assert await conn.fetchval(
            "select relrowsecurity from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relname = 'tabela_recem_nascida'"
        )

        for papel in ("anon", "authenticated"):
            for priv in _PRIVILEGIOS:
                assert (
                    await conn.fetchval(
                        "select has_table_privilege($1, 'public.tabela_recem_nascida', $2)",
                        papel,
                        priv,
                    )
                    is False
                ), f"{papel} nasceu com {priv}"

    rodar_tx(body)


def test_dono_da_funcao_de_identidade_e_isento_de_rls(rodar_tx):
    """A segunda condição da não-recursão — a que falha CALADA.

    A policy de condominios chama uma função que LÊ condominios. Medido em
    laboratório:
      INVOKER                  → ERROR: stack depth limit exceeded
      DEFINER + dono isento    → funciona
      DEFINER + dono NÃO-isento → 0 linhas, em SILÊNCIO (nenhuma policy se aplica
                                  ao dono, default deny, a função devolve null)
    A pergunta é "o dono é isento?", nunca "o dono é o postgres?": no container a
    isenção vem de SUPERUSER, na produção de BYPASSRLS.
    """

    async def body(conn):
        dono = await conn.fetchrow(
            "select r.rolsuper, r.rolbypassrls, p.prosecdef "
            "from pg_proc p "
            "join pg_roles r on r.oid = p.proowner "
            "join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'privado' and p.proname = 'meu_condominio'"
        )

        assert dono["prosecdef"] is True
        assert dono["rolsuper"] or dono["rolbypassrls"]

    rodar_tx(body)
