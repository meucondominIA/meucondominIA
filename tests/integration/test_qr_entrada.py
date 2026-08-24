"""Integração: a frase do QR resolvida no Postgres real (Etapa 7).

O que só o banco de verdade prova, e os fakes não: que a concatenação
`FRASE_QR || nome` reconstrói exatamente a string que a ferramenta imprime, que
o `where ativo` barra o tenant sintético do eval, e que homônimo — possível
porque `nome` não tem UNIQUE — devolve duas linhas em vez de sortear uma.

O teste de ida e volta é o que guarda o invariante mais perigoso da etapa: a
ferramenta GERA a frase e o resolvedor de produção a RESOLVE. Nenhuma string
literal é comparada, então um prefixo que divergir cai aqui em vez de cair no
mural.

Marcados 'integration' -> rode com `pytest -m integration` (precisa de Docker).
"""

import pytest

from condominios import FRASE_QR, buscar_elegivel_por_slug, resolver_por_frase

pytestmark = pytest.mark.integration

_SENTINELA = "Sentinela de Vazamento (eval)"


async def _inserir(conn, slug, nome, *, ativo=True):
    return await conn.fetchval(
        "insert into condominios (slug, nome, ativo) values ($1, $2, $3) returning id",
        slug,
        nome,
        ativo,
    )


def test_a_frase_resolve_o_condominio_impresso(rodar_tx):
    async def body(conn):
        gabro = await _inserir(conn, "res-gabro", "Gabro")
        await _inserir(conn, "res-solar", "Solar")

        achado = await resolver_por_frase(conn, f"{FRASE_QR}Gabro")

        assert achado is not None
        assert achado.id == gabro, "a frase do Gabro não pode resolver outro prédio"
        assert achado.nome == "Gabro"

    rodar_tx(body)


def test_ida_e_volta_da_ferramenta_ao_resolvedor(rodar_tx):
    """Anti-drift: nenhuma string literal é comparada — a frase é gerada a partir
    do banco e resolvida pelo código de produção."""

    async def body(conn):
        esperado = await _inserir(conn, "res-gabro", "Gabro")

        alvo = await buscar_elegivel_por_slug(conn, "res-gabro")
        frase = FRASE_QR + alvo.nome
        volta = await resolver_por_frase(conn, frase)

        assert alvo.id == esperado
        assert volta is not None and volta.id == alvo.id

    rodar_tx(body)


def test_sentinela_inativo_e_recusado_e_e_o_filtro_que_recusa(rodar_tx):
    """A prova dupla: sem o `where ativo` a frase casaria. É a cláusula que
    barra, não a ausência do dado."""

    async def body(conn):
        await _inserir(conn, "eval-sentinela", _SENTINELA, ativo=False)
        frase = f"{FRASE_QR}{_SENTINELA}"

        assert await resolver_por_frase(conn, frase) is None

        sem_filtro = await conn.fetch(
            "select id from condominios where $1 = $2 || nome", frase, FRASE_QR
        )
        assert len(sem_filtro) == 1, "o dado existe; quem recusa é o `ativo`"

    rodar_tx(body)


def test_ferramenta_nao_gera_qr_de_condominio_desativado(rodar_tx):
    async def body(conn):
        await _inserir(conn, "eval-sentinela", _SENTINELA, ativo=False)
        assert await buscar_elegivel_por_slug(conn, "eval-sentinela") is None

    rodar_tx(body)


def test_homonimos_ativos_caem_no_menu_em_vez_de_sortear(rodar_tx):
    """`nome` não tem UNIQUE — dois prédios podem se chamar igual, e aí a frase
    é ambígua. Ambiguidade é menu, nunca escolha."""

    async def body(conn):
        await _inserir(conn, "central-sp", "Edifício Central")
        await _inserir(conn, "central-rs", "Edifício Central")

        assert await resolver_por_frase(conn, f"{FRASE_QR}Edifício Central") is None

    rodar_tx(body)


@pytest.mark.parametrize(
    "texto",
    [
        "oi",
        "Gabro",
        "Sou morador do condomínio Gabro",
        "Olá! Sou morador do condomínio Gabro tem uma goteira",
        "Olá! Sou morador do condomínio Fantasma",
    ],
)
def test_texto_que_nao_e_a_frase_exata_nao_resolve(rodar_tx, texto):
    async def body(conn):
        await _inserir(conn, "res-gabro", "Gabro")
        assert await resolver_por_frase(conn, texto) is None

    rodar_tx(body)


def test_acento_do_prefixo_atravessa_o_banco(rodar_tx):
    """Os dois acentos do prefixo (á, í) são o que o match exato depende de
    preservar de ponta a ponta — inclusive no parâmetro que vai ao Postgres."""

    async def body(conn):
        await _inserir(conn, "res-gabro", "Gabro")
        frase = f"{FRASE_QR}Gabro"

        assert await resolver_por_frase(conn, frase) is not None
        assert len(frase.encode("utf-8")) == len(frase) + 2

    rodar_tx(body)
