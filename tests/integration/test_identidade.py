"""Integração: a identidade do síndico contra Postgres real (Fase 5 · Etapa 1).

O claim é posto à mão com set_config(..., is_local=true): amarrado à transação,
some no rollback da fixture. É assim que auth.uid() lê o `sub` na produção — o
corpo dela no schema.sql é cópia verbatim da função real (31/07/2026).

O QUE ESTE ARQUIVO NÃO PROVA, de propósito:
- que o PostgREST põe o claim: não há PostgREST aqui, o setting é escrito à mão.
  Se o Supabase renomear `request.jwt.claims`, estes testes seguem verdes e a
  produção quebra;
- que o JWT é verificado: set_config aceita qualquer `sub`;
- nada sobre RLS. Não existe policy (Etapa 2) e o superusuário do container
  ignoraria RLS de qualquer modo. "Síndico A não vê o B" é a Etapa 2 — aqui o
  que se prova é que A e B RESOLVEM para condomínios diferentes, que é o
  vocabulário de que aquela policy vai precisar.
"""

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_UID_A = "aaaaaaaa-0000-0000-0000-00000000000a"
_UID_B = "bbbbbbbb-0000-0000-0000-00000000000b"
_UID_SEM_VINCULO = "cccccccc-0000-0000-0000-00000000000c"


async def _sindico(conn, uid: str, *, slug: str) -> str:
    """Cria o usuário e o aponta como síndico do condomínio de `slug`."""
    await conn.execute("insert into auth.users (id) values ($1)", uid)
    return await conn.fetchval(
        "insert into condominios (slug, nome, sindico_user_id) values ($1, $2, $3) "
        "returning id",
        slug,
        f"Condomínio {slug}",
        uid,
    )


async def _logado_como(conn, uid: str | None) -> None:
    """Põe (ou limpa) o claim `sub` que auth.uid() lê."""
    claims = "" if uid is None else f'{{"sub":"{uid}"}}'
    await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)


def test_cada_sindico_resolve_para_o_proprio_condominio(rodar_tx):
    """O ponto da etapa: dois logins, dois condomínios DIFERENTES.

    Com um síndico só, "devolve o certo" e "devolve o único que existe" seriam a
    mesma frase, e o teste passaria nos dois casos.
    """

    async def body(conn):
        cond_a = await _sindico(conn, _UID_A, slug="tenant-a")
        cond_b = await _sindico(conn, _UID_B, slug="tenant-b")

        await _logado_como(conn, _UID_A)
        visto_por_a = await conn.fetchval("select privado.meu_condominio()")
        await _logado_como(conn, _UID_B)
        visto_por_b = await conn.fetchval("select privado.meu_condominio()")

        assert visto_por_a == cond_a
        assert visto_por_b == cond_b
        assert visto_por_a != visto_por_b

    rodar_tx(body)


def test_sem_claim_devolve_nulo(rodar_tx):
    """Sem login não há identidade — e NULL é o que a policy da Etapa 2 vai
    receber para negar tudo."""

    async def body(conn):
        await _sindico(conn, _UID_A, slug="tenant-a")
        await _logado_como(conn, None)

        assert await conn.fetchval("select privado.meu_condominio()") is None

    rodar_tx(body)


def test_usuario_sem_vinculo_devolve_nulo(rodar_tx):
    """Usuário existe no auth mas não é síndico de nada: NULL, não erro."""

    async def body(conn):
        await _sindico(conn, _UID_A, slug="tenant-a")
        await conn.execute("insert into auth.users (id) values ($1)", _UID_SEM_VINCULO)
        await _logado_como(conn, _UID_SEM_VINCULO)

        assert await conn.fetchval("select privado.meu_condominio()") is None

    rodar_tx(body)


def test_dois_condominios_para_o_mesmo_sindico_e_ingravavel(rodar_tx):
    """A garantia que sustenta o `returns uuid` escalar.

    Uma função SQL não-set-returning devolve "the first row of the last query's
    result", e essa primeira linha "is not well-defined unless you use ORDER BY"
    (xfunc-sql.html): com dois condomínios para o mesmo síndico, a função
    escolheria um em SILÊNCIO. O unique parcial torna esse estado ingravável.
    """

    async def body(conn):
        await _sindico(conn, _UID_A, slug="tenant-a")

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "insert into condominios (slug, nome, sindico_user_id) "
                "values ('tenant-c', 'Terceiro', $1)",
                _UID_A,
            )

    rodar_tx(body)


def test_varios_condominios_sem_sindico_convivem(rodar_tx):
    """O outro lado do índice PARCIAL: quem não tem síndico não disputa o
    índice — senão o segundo condomínio sem dono seria recusado."""

    async def body(conn):
        await conn.execute(
            "insert into condominios (slug, nome) values ('sem-um', 'Um'), "
            "('sem-dois', 'Dois')"
        )

        assert (
            await conn.fetchval(
                "select count(*) from condominios where sindico_user_id is null"
            )
            == 2
        )

    rodar_tx(body)


def test_apagar_o_usuario_do_sindico_e_recusado(rodar_tx):
    """RESTRICT, e não SET NULL: apagar o usuário no painel tem que DOER.

    Com SET NULL o delete passaria e o condomínio ficaria órfão sem erro; como
    a função devolve NULL quando não há linha, o síndico apenas pararia de ver
    tudo — indistinguível de "não há nada".
    """

    async def body(conn):
        await _sindico(conn, _UID_A, slug="tenant-a")

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute("delete from auth.users where id = $1", _UID_A)

    rodar_tx(body)
