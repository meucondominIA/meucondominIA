"""Testes do repositório de regras (Fase 2 · Passo 4).

Sem banco real (molde do test_mensagens): _FakeConn grava cada chamada e
devolve resultados programados. O isolamento multi-tenant de verdade é provado
na integração (tests/integration/test_regras.py); aqui testamos o contrato —
guardas antes de tocar a conn, SQL certo, parâmetros na ordem certa, escopo
derivado de condominio_id.
"""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

import regras
from chunker import Chunk

CONDOMINIO_ID = uuid4()


def _chunk(artigo: int) -> Chunk:
    return Chunk(
        conteudo=f"Art. {artigo} Texto do artigo.",
        fonte=f"Regimento Teste, Art. {artigo}",
        documento="Regimento Teste",
        metadata={"artigo": str(artigo), "posicao": artigo - 1},
    )


class _FakeConn:
    def __init__(self, fetch_result=(), execute_result="DELETE 0"):
        self._fetch_result = list(fetch_result)
        self._execute_result = execute_result
        self.calls = []

    async def executemany(self, query, args):
        self.calls.append(("executemany", query, args))

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return self._execute_result

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self._fetch_result


def test_inserir_desalinhado_falha_antes_de_tocar_o_banco():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="desalinhados"):
        asyncio.run(
            regras.inserir_regras(
                conn, [_chunk(1), _chunk(2)], [[0.1]], condominio_id=CONDOMINIO_ID
            )
        )
    assert conn.calls == []


def test_inserir_vazio_e_noop():
    conn = _FakeConn()
    asyncio.run(regras.inserir_regras(conn, [], [], condominio_id=CONDOMINIO_ID))
    assert conn.calls == []


def test_inserir_deriva_escopo_especifico():
    conn = _FakeConn()
    chunk = _chunk(1)
    embedding = [0.5, 0.5]
    asyncio.run(
        regras.inserir_regras(conn, [chunk], [embedding], condominio_id=CONDOMINIO_ID)
    )
    assert len(conn.calls) == 1
    _, query, args = conn.calls[0]
    assert "insert into regras" in query.lower()
    assert args == [
        (
            CONDOMINIO_ID,
            "especifico",
            chunk.conteudo,
            chunk.fonte,
            chunk.documento,
            chunk.metadata,
            embedding,
        )
    ]


def test_inserir_sem_condominio_deriva_escopo_geral():
    conn = _FakeConn()
    asyncio.run(regras.inserir_regras(conn, [_chunk(1)], [[0.1]], condominio_id=None))
    _, _, args = conn.calls[0]
    assert args[0][0] is None
    assert args[0][1] == "geral"


def test_inserir_preserva_ordem_dos_pares():
    conn = _FakeConn()
    chunks = [_chunk(1), _chunk(2), _chunk(3)]
    embeddings = [[0.1], [0.2], [0.3]]
    asyncio.run(
        regras.inserir_regras(conn, chunks, embeddings, condominio_id=CONDOMINIO_ID)
    )
    _, _, args = conn.calls[0]
    assert [(linha[3], linha[6]) for linha in args] == [
        (chunk.fonte, embedding)
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]


def test_apagar_documento_em_branco_falha_antes_de_tocar_o_banco():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="documento em branco"):
        asyncio.run(
            regras.apagar_regras_do_documento(
                conn, condominio_id=CONDOMINIO_ID, documento="   "
            )
        )
    assert conn.calls == []


def test_apagar_usa_comparacao_null_safe_e_devolve_a_contagem_do_tag():
    conn = _FakeConn(execute_result="DELETE 3")
    apagadas = asyncio.run(
        regras.apagar_regras_do_documento(
            conn, condominio_id=CONDOMINIO_ID, documento="Regimento Teste"
        )
    )
    assert apagadas == 3
    [(tipo, query, args)] = conn.calls
    assert tipo == "execute"
    assert "condominio_id is not distinct from $1" in query.lower()
    assert "documento = $2" in query.lower()
    assert args == (CONDOMINIO_ID, "Regimento Teste")


def test_apagar_escopo_geral_passa_none_sem_traduzir():
    conn = _FakeConn()
    apagadas = asyncio.run(
        regras.apagar_regras_do_documento(
            conn, condominio_id=None, documento="Lei Exemplo"
        )
    )
    assert apagadas == 0
    [(_, _, args)] = conn.calls
    assert args[0] is None


def test_apagar_stripa_o_documento_como_o_chunker():
    conn = _FakeConn()
    asyncio.run(
        regras.apagar_regras_do_documento(
            conn, condominio_id=CONDOMINIO_ID, documento="  Regimento Teste  "
        )
    )
    [(_, _, args)] = conn.calls
    assert args[1] == "Regimento Teste"


def test_buscar_limite_invalido_falha_antes_de_tocar_o_banco():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="limite"):
        asyncio.run(
            regras.buscar_por_similaridade(conn, [0.1], CONDOMINIO_ID, limite=0)
        )
    assert conn.calls == []


def test_buscar_filtra_por_tenant_e_ordena_por_distancia():
    conn = _FakeConn(
        fetch_result=[
            {"conteudo": "Art. 1 ...", "fonte": "Regimento, Art. 1º", "distancia": 0.1},
            {"conteudo": "Art. 2 ...", "fonte": "Regimento, Art. 2º", "distancia": 0.4},
        ]
    )
    pergunta = [0.9, 0.1]
    resultado = asyncio.run(
        regras.buscar_por_similaridade(conn, pergunta, CONDOMINIO_ID, limite=5)
    )
    assert [tipo for tipo, _, _ in conn.calls] == ["fetch"]
    _, query, args = conn.calls[0]
    assert "where condominio_id = $2" in query.lower()
    assert "escopo" not in query.lower()
    assert "order by embedding <=> $1" in query.lower()
    assert "limit $3" in query.lower()
    assert args == (pergunta, CONDOMINIO_ID, 5)
    assert resultado == [
        regras.RegraEncontrada(
            conteudo="Art. 1 ...", fonte="Regimento, Art. 1º", distancia=0.1
        ),
        regras.RegraEncontrada(
            conteudo="Art. 2 ...", fonte="Regimento, Art. 2º", distancia=0.4
        ),
    ]


def test_regra_encontrada_e_imutavel():
    regra = regras.RegraEncontrada(conteudo="x", fonte="Doc, Art. 1º", distancia=0.2)
    with pytest.raises(ValidationError):
        regra.distancia = 0.9
