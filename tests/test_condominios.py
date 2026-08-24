"""Testes do repositório de condomínios (Fase 2 · Passo 5; Fase 3 · Passo 3).

Molde do test_regras: _FakeConn grava as chamadas e devolve resultado
programado — aqui testamos o contrato (guarda antes da conn, SQL certo,
None repassado como None). O roundtrip real está na integração
(tests/integration/test_busca_slug.py e test_condominios_elegiveis.py).

O fake vê só o SQL que sai daqui. Que ele ordena igual nos dois provedores de
locale, só o Postgres real prova — na integração.
"""

import asyncio
from uuid import uuid4

import pytest

from condominios import (
    FRASE_QR,
    CondominioElegivel,
    buscar_elegivel_por_slug,
    buscar_id_por_slug,
    listar_elegiveis,
    nome_por_id,
    resolver_por_frase,
)


class _FakeConn:
    def __init__(self, fetchval_result=None, fetch_result=(), fetchrow_result=None):
        self._fetchval_result = fetchval_result
        self._fetch_result = fetch_result
        self._fetchrow_result = fetchrow_result
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._fetchval_result

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self._fetch_result

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self._fetchrow_result


def test_slug_em_branco_falha_antes_de_tocar_o_banco():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="slug em branco"):
        asyncio.run(buscar_id_por_slug(conn, "   "))
    assert conn.calls == []


def test_busca_parametrizada_devolve_o_id():
    esperado = uuid4()
    conn = _FakeConn(fetchval_result=esperado)
    assert asyncio.run(buscar_id_por_slug(conn, "res-gabro")) == esperado
    [(tipo, query, args)] = conn.calls
    assert tipo == "fetchval"
    assert "where slug = $1" in query.lower()
    assert args == ("res-gabro",)


def test_slug_inexistente_devolve_none():
    conn = _FakeConn(fetchval_result=None)
    assert asyncio.run(buscar_id_por_slug(conn, "nao-existe")) is None


def test_slug_e_stripado_antes_da_consulta():
    conn = _FakeConn()
    asyncio.run(buscar_id_por_slug(conn, "  res-gabro  "))
    [(_, _, args)] = conn.calls
    assert args == ("res-gabro",)


# ── lista do morador (Fase 3 · Passo 3) ──────────────────────────────────────


def test_listagem_filtra_ativo_e_ordena_com_desempate():
    conn = _FakeConn(fetch_result=[])
    asyncio.run(listar_elegiveis(conn))
    [(tipo, query, args)] = conn.calls
    assert tipo == "fetch"
    assert args == ()

    sql = " ".join(query.lower().split())
    assert "where ativo" in sql
    assert 'order by nome collate "pt-br-x-icu", id' in sql


def test_listagem_devolve_modelos_na_ordem_do_banco():
    linhas = [
        {"id": uuid4(), "nome": "Edifício Alfa"},
        {"id": uuid4(), "nome": "Edifício Beta"},
    ]
    conn = _FakeConn(fetch_result=linhas)
    lista = asyncio.run(listar_elegiveis(conn))

    assert lista == [CondominioElegivel.model_validate(linha) for linha in linhas]
    assert [c.nome for c in lista] == ["Edifício Alfa", "Edifício Beta"]


def test_listagem_vazia_devolve_lista_vazia():
    assert asyncio.run(listar_elegiveis(_FakeConn(fetch_result=[]))) == []


def test_nome_por_id_e_parametrizado_e_sem_filtro_de_ativo():
    procurado = uuid4()
    conn = _FakeConn(fetchval_result="Edifício Alfa")
    assert asyncio.run(nome_por_id(conn, procurado)) == "Edifício Alfa"

    [(tipo, query, args)] = conn.calls
    assert tipo == "fetchval"
    assert "where id = $1" in query.lower()
    assert "ativo" not in query.lower()  # desativado ainda tem nome a citar
    assert args == (procurado,)


def test_condominio_elegivel_e_imutavel():
    condominio = CondominioElegivel(id=uuid4(), nome="Edifício Alfa")
    with pytest.raises(Exception):
        condominio.nome = "Outro"


# ── entrada por QR (Etapa 7) ─────────────────────────────────────────────────

GABRO = {"id": uuid4(), "nome": "Gabro"}
FRASE_GABRO = f"{FRASE_QR}Gabro"


@pytest.mark.parametrize(
    "texto",
    [
        None,
        "",
        "   ",
        "oi",
        "Gabro",
        "Sou morador do condomínio Gabro",
        "Bom dia! Olá! Sou morador do condomínio Gabro",
    ],
)
def test_texto_que_nao_e_a_frase_nem_toca_o_banco(texto):
    """O prefixo é a peneira barata: mensagem comum não paga query."""
    conn = _FakeConn()
    assert asyncio.run(resolver_por_frase(conn, texto)) is None
    assert conn.calls == []


def test_a_frase_resolve_o_condominio_e_a_query_filtra_ativo():
    conn = _FakeConn(fetch_result=[GABRO])
    achado = asyncio.run(resolver_por_frase(conn, FRASE_GABRO))

    assert achado == CondominioElegivel.model_validate(GABRO)
    [(tipo, query, args)] = conn.calls
    assert tipo == "fetch"
    assert "where ativo" in " ".join(query.lower().split())
    assert args == (FRASE_GABRO, FRASE_QR)


def test_frase_com_espaco_sobrando_ainda_resolve():
    conn = _FakeConn(fetch_result=[GABRO])
    assert asyncio.run(resolver_por_frase(conn, f"  {FRASE_GABRO}  ")) is not None
    [(_, _, args)] = conn.calls
    assert args == (FRASE_GABRO, FRASE_QR)


def test_frase_com_pergunta_colada_cai_fora():
    """Quem digita a dúvida por cima do texto do cartaz vai para o menu."""
    conn = _FakeConn(fetch_result=[])
    assert asyncio.run(resolver_por_frase(conn, f"{FRASE_GABRO} tem goteira")) is None


def test_nome_desconhecido_devolve_none():
    conn = _FakeConn(fetch_result=[])
    assert asyncio.run(resolver_por_frase(conn, f"{FRASE_QR}Inexistente")) is None


def test_homonimos_viram_none_em_vez_de_predio_errado():
    """`nome` não tem UNIQUE: duas linhas é ambiguidade, e ambiguidade é menu."""
    conn = _FakeConn(fetch_result=[GABRO, {"id": uuid4(), "nome": "Gabro"}])
    assert asyncio.run(resolver_por_frase(conn, FRASE_GABRO)) is None


def test_caixa_diferente_nao_resolve():
    """Sem lower(): `nome` não tem CHECK de caixa, então dobrar a caixa criaria
    ambiguidade que o dado não tem."""
    conn = _FakeConn(fetch_result=[])
    assert asyncio.run(resolver_por_frase(conn, f"{FRASE_QR}GABRO")) is None
    assert conn.calls, "a caixa é decidida pelo banco, não pela peneira"


# ── a entrada da ferramenta de cartaz ────────────────────────────────────────


def test_slug_em_branco_falha_antes_do_banco_tambem_na_ferramenta():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="slug em branco"):
        asyncio.run(buscar_elegivel_por_slug(conn, "  "))
    assert conn.calls == []


def test_ferramenta_so_alcanca_condominio_ativo():
    conn = _FakeConn(fetchrow_result=GABRO)
    achado = asyncio.run(buscar_elegivel_por_slug(conn, "res-gabro"))

    assert achado == CondominioElegivel.model_validate(GABRO)
    [(tipo, query, args)] = conn.calls
    assert tipo == "fetchrow"
    assert "where ativo" in " ".join(query.lower().split())
    assert args == ("res-gabro",)


def test_slug_inativo_ou_inexistente_devolve_none_para_a_ferramenta():
    conn = _FakeConn(fetchrow_result=None)
    assert asyncio.run(buscar_elegivel_por_slug(conn, "eval-sentinela")) is None
