"""Integração: criar_solicitacao contra Postgres real (Fase 4 · Etapa 4).

O que fake nenhum pega: o gate de idempotência no índice único parcial, o anexo
atravessando o codec de jsonb inteiro, e a descrição voltando byte a byte depois
de passar por SQL parametrizado.
"""

import pytest

from ocorrencia import Anexo, TipoSolicitacao
from solicitacoes import criar_solicitacao

pytestmark = pytest.mark.integration

ANEXO = Anexo(
    bucket="anexos",
    caminho="cond/c7a24f3c.jpg",
    mimetype="image/jpeg",
    bytes=111582,
    sha256="c7a24f3c",
)


async def _cond(conn, slug):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug,
        slug.upper(),
    )


async def _mensagem(conn, telefone, cond=None):
    """A mensagem de entrada que origina a solicitação — a chave da idempotência."""
    conversa = await conn.fetchval(
        "insert into conversas (telefone) values ($1) returning id", telefone
    )
    return await conn.fetchval(
        "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
        "values ($1, 'morador', 'text', '1', $2) returning id",
        conversa,
        f"M-{telefone}",
    )


def test_grava_aberta_no_tenant_certo(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "gabro")
        entrada = await _mensagem(conn, "5555")

        sid = await criar_solicitacao(
            conn,
            condominio_id=cond,
            tipo=TipoSolicitacao.RECLAMACAO,
            descricao="Vazamento no 3º andar",
            anexos=[],
            telefone="5555",
            origem_mensagem_id=entrada,
        )

        linha = await conn.fetchrow("select * from solicitacoes where id = $1", sid)
        assert linha["condominio_id"] == cond
        assert linha["status"] == "aberta"
        assert linha["tipo"] == "reclamacao"
        assert linha["telefone"] == "5555"
        assert linha["descricao"] == "Vazamento no 3º andar"
        # Identidade hoje é o telefone; o roster é da Fase 5.
        assert linha["morador_id"] is None and linha["unidade_id"] is None
        # titulo fica para o dashboard preencher quando houver quem o escreva.
        assert linha["titulo"] is None

    rodar_tx(corpo)


@pytest.mark.parametrize(
    "tipo", [TipoSolicitacao.RECLAMACAO, TipoSolicitacao.OCORRENCIA,
             TipoSolicitacao.MANUTENCAO]
)
def test_os_tres_tipos_passam_pelo_check(rodar_tx, tipo):
    """A guarda de drift compara os valores com a migration; aqui o BANCO
    confirma que ela não está comparando com ficção."""
    async def corpo(conn):
        cond = await _cond(conn, f"c-{tipo.value}")
        entrada = await _mensagem(conn, f"55-{tipo.value}")
        sid = await criar_solicitacao(
            conn, condominio_id=cond, tipo=tipo, descricao="x", anexos=[],
            telefone="5555", origem_mensagem_id=entrada,
        )
        assert await conn.fetchval(
            "select tipo from solicitacoes where id = $1", sid
        ) == tipo.value

    rodar_tx(corpo)


def test_descricao_volta_verbatim_do_banco(rodar_tx):
    """Emoji, quebra de linha, aspas e uma tentativa de injeção: o $1 nunca deixa
    o valor chegar ao parser como sintaxe."""
    hostil = (
        "Vazamento no 3º andar 💧\n"
        "Escorre desde ontem.\n"
        "'; drop table solicitacoes; -- \"aspas\" \\barra\\ %s {chave} $1\n"
        "à noite fica pior — 🏳️‍🌈"
    )

    async def corpo(conn):
        cond = await _cond(conn, "verbatim")
        entrada = await _mensagem(conn, "5556")
        sid = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.RECLAMACAO,
            descricao=hostil, anexos=[], telefone="5556", origem_mensagem_id=entrada,
        )
        lido = await conn.fetchval(
            "select descricao from solicitacoes where id = $1", sid
        )
        assert lido == hostil
        assert await conn.fetchval("select to_regclass('public.solicitacoes')")

    rodar_tx(corpo)


def test_anexo_atravessa_o_codec_de_jsonb(rodar_tx):
    """O codec do db.py já é json.dumps: serializar aqui gravaria a string do
    JSON DENTRO do JSON, e o anexo voltaria como texto."""
    async def corpo(conn):
        cond = await _cond(conn, "anexo")
        entrada = await _mensagem(conn, "5557")
        sid = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.OCORRENCIA,
            descricao="olha a foto", anexos=[ANEXO], telefone="5557",
            origem_mensagem_id=entrada,
        )
        lido = await conn.fetchval("select anexos from solicitacoes where id = $1", sid)
        assert isinstance(lido, list) and len(lido) == 1
        assert lido[0]["caminho"] == ANEXO.caminho
        assert lido[0]["sha256"] == ANEXO.sha256
        assert lido[0]["bytes"] == 111582
        assert await conn.fetchval(
            "select jsonb_typeof(anexos) from solicitacoes where id = $1", sid
        ) == "array"

    rodar_tx(corpo)


def test_sem_anexo_grava_array_vazio(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "vazio")
        entrada = await _mensagem(conn, "5558")
        sid = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.MANUTENCAO,
            descricao="portão", anexos=[], telefone="5558", origem_mensagem_id=entrada,
        )
        assert await conn.fetchval(
            "select anexos from solicitacoes where id = $1", sid
        ) == []

    rodar_tx(corpo)


def test_reprocessar_a_mesma_mensagem_nao_duplica(rodar_tx):
    """A janela de crash real: a solicitação é escrita na 1ª janela de conexão e a
    saída só na TX2. Sem este gate, a reentrega do sweeper criaria a segunda."""
    async def corpo(conn):
        cond = await _cond(conn, "idem")
        entrada = await _mensagem(conn, "5559")

        primeiro = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.RECLAMACAO,
            descricao="vazou", anexos=[ANEXO], telefone="5559",
            origem_mensagem_id=entrada,
        )
        segundo = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.RECLAMACAO,
            descricao="vazou", anexos=[ANEXO], telefone="5559",
            origem_mensagem_id=entrada,
        )

        assert primeiro == segundo
        assert await conn.fetchval(
            "select count(*) from solicitacoes where origem_mensagem_id = $1", entrada
        ) == 1

    rodar_tx(corpo)


def test_origens_diferentes_criam_solicitacoes_diferentes(rodar_tx):
    """O gate é por MENSAGEM, não por telefone: o mesmo morador abre quantas
    ocorrências quiser."""
    async def corpo(conn):
        cond = await _cond(conn, "duas")
        uma = await _mensagem(conn, "5560")
        outra = await _mensagem(conn, "5561")

        a = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.RECLAMACAO,
            descricao="primeira", anexos=[], telefone="5560", origem_mensagem_id=uma,
        )
        b = await criar_solicitacao(
            conn, condominio_id=cond, tipo=TipoSolicitacao.RECLAMACAO,
            descricao="segunda", anexos=[], telefone="5560", origem_mensagem_id=outra,
        )
        assert a != b
        assert await conn.fetchval("select count(*) from solicitacoes") == 2

    rodar_tx(corpo)


def test_tenant_inexistente_bate_na_fk(rodar_tx):
    """Isolamento garantido no banco: solicitação sem condomínio não entra."""
    import asyncpg

    async def corpo(conn):
        entrada = await _mensagem(conn, "5562")
        from uuid import uuid4

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await criar_solicitacao(
                conn, condominio_id=uuid4(), tipo=TipoSolicitacao.RECLAMACAO,
                descricao="x", anexos=[], telefone="5562",
                origem_mensagem_id=entrada,
            )

    rodar_tx(corpo)


# ── o descarte do base64 depois do upload (Fase 4 · Etapa 4) ─────────────────


async def _evento(conn, mid, *, com_foto: bool):
    msg = {"key": {"id": mid}, "message": {"conversation": "oi"}}
    if com_foto:
        # Alta entropia de propósito: base64 de foto real não comprime, e um
        # padrão repetido viraria ~200 bytes no TOAST, escondendo o peso.
        import base64
        import os

        msg["base64"] = base64.b64encode(os.urandom(30_000)).decode()
    await conn.execute(
        "insert into webhook_events (message_id, payload) values ($1, $2::jsonb)",
        mid, {"method": "message", "msg": msg},
    )


async def _payload(conn, mid):
    return await conn.fetchval(
        "select payload from webhook_events where message_id = $1", mid
    )


def test_processado_descarta_o_base64_e_preserva_o_resto(rodar_tx):
    """O staging cumpriu o papel: a foto já está no Storage."""
    from dedup import StatusEvento, marcar_status

    async def corpo(conn):
        await _evento(conn, "COM-FOTO", com_foto=True)
        antes = await _payload(conn, "COM-FOTO")
        assert "base64" in antes["msg"]

        await marcar_status(conn, "COM-FOTO", StatusEvento.PROCESSADO)

        depois = await _payload(conn, "COM-FOTO")
        assert "base64" not in depois["msg"]
        assert depois["msg"]["key"]["id"] == "COM-FOTO"
        assert depois["msg"]["message"]["conversation"] == "oi"
        assert await conn.fetchval(
            "select status from webhook_events where message_id = 'COM-FOTO'"
        ) == "processado"

    rodar_tx(corpo)


def test_falhou_PRESERVA_o_base64(rodar_tx):
    """É o que o sweeper precisa para reprocessar — o Z-PRO não reenvia."""
    from dedup import StatusEvento, marcar_status

    async def corpo(conn):
        await _evento(conn, "FALHOU", com_foto=True)
        await marcar_status(conn, "FALHOU", StatusEvento.FALHOU)
        assert "base64" in (await _payload(conn, "FALHOU"))["msg"]

    rodar_tx(corpo)


def test_mensagem_de_texto_atravessa_intacta(rodar_tx):
    from dedup import StatusEvento, marcar_status

    async def corpo(conn):
        await _evento(conn, "SO-TEXTO", com_foto=False)
        antes = await _payload(conn, "SO-TEXTO")
        await marcar_status(conn, "SO-TEXTO", StatusEvento.PROCESSADO)
        assert await _payload(conn, "SO-TEXTO") == antes

    rodar_tx(corpo)


def test_o_descarte_encolhe_a_linha_de_verdade(rodar_tx):
    """A medição que motivou o conserto: ~49 kB por foto no Postgres."""
    from dedup import StatusEvento, marcar_status

    async def corpo(conn):
        await _evento(conn, "PESO", com_foto=True)
        antes = await conn.fetchval(
            "select pg_column_size(payload) from webhook_events "
            "where message_id = 'PESO'"
        )
        await marcar_status(conn, "PESO", StatusEvento.PROCESSADO)
        depois = await conn.fetchval(
            "select pg_column_size(payload) from webhook_events "
            "where message_id = 'PESO'"
        )
        assert depois < antes / 4

    rodar_tx(corpo)
