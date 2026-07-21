"""Integração: prova que as constraints da Fase 1 barram de verdade no Postgres.

Cada teste roda via `rodar_tx` (conftest): transação revertida no fim
(isolamento) e os mesmos codecs do db.py, então os repositórios reais rodam
como em produção. A violação esperada é envolta num savepoint
(async with conn.transaction()) para não abortar a transação externa.

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import asyncpg
import pytest

from dedup import registrar_mensagem
from mensagens import aplicar_transicao, registrar_saida, upsert_conversa_ativa
from roteador import Estado, Transicao

pytestmark = pytest.mark.integration


def test_uq_mensagens_message_id_bloqueia_duplicata(rodar_tx):
    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990001")).id
        await conn.execute(
            "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
            "values ($1, 'morador', 'text', 'oi', 'M-DUP')",
            cid,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
                    "values ($1, 'morador', 'text', 'de novo', 'M-DUP')",
                    cid,
                )
        # message_id NULL fica FORA do índice parcial -> pode repetir à vontade
        await conn.execute(
            "insert into mensagens (conversa_id, papel, conteudo) values ($1, 'assistente', 'a')",
            cid,
        )
        await conn.execute(
            "insert into mensagens (conversa_id, papel, conteudo) values ($1, 'assistente', 'b')",
            cid,
        )

    rodar_tx(body)


def test_uq_mensagens_em_resposta_a_bloqueia_segunda_saida(rodar_tx):
    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990002")).id
        entrada = await conn.fetchval(
            "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
            "values ($1, 'morador', 'text', 'oi', 'M2') returning id",
            cid,
        )
        await registrar_saida(conn, cid, "Eco: oi", entrada)

        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into mensagens (conversa_id, papel, conteudo, em_resposta_a) "
                    "values ($1, 'assistente', 'Eco: oi 2', $2)",
                    cid,
                    entrada,
                )

        # registrar_saida de novo (ON CONFLICT DO NOTHING): não estoura nem duplica
        await registrar_saida(conn, cid, "Eco: oi 3", entrada)
        n = await conn.fetchval(
            "select count(*) from mensagens where em_resposta_a = $1", entrada
        )
        assert n == 1

    rodar_tx(body)


def test_uq_conversas_telefone_ativa_uma_por_vez(rodar_tx):
    async def body(conn):
        id1 = (await upsert_conversa_ativa(conn, "5511999990003")).id
        id2 = (await upsert_conversa_ativa(conn, "5511999990003")).id
        assert id1 == id2  # ON CONFLICT devolveu a MESMA conversa ativa

        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into conversas (telefone, status) values ($1, 'ativa')",
                    "5511999990003",
                )

        # 'encerrada' fica FORA do índice parcial (where status='ativa') -> coexiste
        await conn.execute(
            "insert into conversas (telefone, status) values ($1, 'encerrada')",
            "5511999990003",
        )
        await conn.execute(
            "insert into conversas (telefone, status) values ($1, 'encerrada')",
            "5511999990003",
        )

    rodar_tx(body)


def test_chk_mensagens_assistente_exige_conteudo(rodar_tx):
    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990004")).id

        # assistente SEM conteúdo -> CHECK barra
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into mensagens (conversa_id, papel, conteudo) "
                    "values ($1, 'assistente', null)",
                    cid,
                )

        # morador SEM conteúdo -> OK (mídia 'unsupported' chega sem texto)
        await conn.execute(
            "insert into mensagens (conversa_id, papel, tipo, conteudo) "
            "values ($1, 'morador', 'unsupported', null)",
            cid,
        )

    rodar_tx(body)


async def _condominio(conn, slug: str):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug,
        slug.upper(),
    )


def test_conversa_nasce_em_identificacao(rodar_tx):
    """Contrato do DEFAULT com o código da Fase 1: upsert_conversa_ativa não
    nomeia `estado`, então sem DEFAULT o caminho de entrada quebraria."""

    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990010")).id
        row = await conn.fetchrow(
            "select estado, condominio_id, condominio_pendente from conversas where id = $1",
            cid,
        )
        assert row["estado"] == "identificacao"
        assert row["condominio_id"] is None
        assert row["condominio_pendente"] is None

    rodar_tx(body)


def test_chk_conversas_estado_recusa_valor_fora_da_lista(rodar_tx):
    async def body(conn):
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into conversas (telefone, estado) values ($1, 'esperando_pizza')",
                    "5511999990011",
                )

    rodar_tx(body)


def test_estado_coerente_recusa_tenant_residual(rodar_tx):
    """O elo mais fraco do isolamento: conversa NÃO identificada não pode
    carregar condomínio nenhum — nem confirmado, nem pendente."""

    async def body(conn):
        cond = await _condominio(conn, "res-teste-a")

        for coluna in ("condominio_id", "condominio_pendente"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(
                        f"insert into conversas (telefone, estado, {coluna}) "
                        "values ($1, 'identificacao', $2)",
                        "5511999990012",
                        cond,
                    )

    rodar_tx(body)


def test_estado_coerente_exige_condominio_em_menu_e_duvidas(rodar_tx):
    async def body(conn):
        cond = await _condominio(conn, "res-teste-b")

        for estado in ("menu", "duvidas"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(
                        "insert into conversas (telefone, estado) values ($1, $2)",
                        "5511999990013",
                        estado,
                    )
            # com condomínio confirmado, o mesmo estado passa
            await conn.execute(
                "insert into conversas (telefone, estado, condominio_id, status) "
                "values ($1, $2, $3, 'encerrada')",
                "5511999990013",
                estado,
                cond,
            )

    rodar_tx(body)


def test_estado_coerente_recusa_pendente_depois_de_confirmado(rodar_tx):
    """Transição que muda `estado` sem limpar o candidato é bug: vira erro."""

    async def body(conn):
        cond = await _condominio(conn, "res-teste-c")
        conversa = (await upsert_conversa_ativa(conn, "5511999990014")).id

        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "update conversas set estado = 'menu', condominio_id = $2, "
                    "condominio_pendente = $2 where id = $1",
                    conversa,
                    cond,
                )

    rodar_tx(body)


def test_aguardando_confirmacao_aceita_com_e_sem_candidato(rodar_tx):
    """Sem candidato é estado válido de propósito: o ON DELETE SET NULL da FK
    pode produzi-lo, e o roteador volta a oferecer a lista."""

    async def body(conn):
        cond = await _condominio(conn, "res-teste-d")
        conversa = (await upsert_conversa_ativa(conn, "5511999990015")).id

        await conn.execute(
            "update conversas set estado = 'aguardando_confirmacao', "
            "condominio_pendente = $2 where id = $1",
            conversa,
            cond,
        )
        await conn.execute(
            "update conversas set condominio_pendente = null where id = $1", conversa
        )

        # o que NÃO passa é chegar aqui já com condomínio confirmado
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "update conversas set condominio_id = $2 where id = $1",
                    conversa,
                    cond,
                )

    rodar_tx(body)


def test_apagar_condominio_candidato_preserva_a_conversa(rodar_tx):
    """on delete set null, e não cascade: sumir o candidato não pode sumir com
    a conversa do morador."""

    async def body(conn):
        cond = await _condominio(conn, "res-teste-e")
        conversa = (await upsert_conversa_ativa(conn, "5511999990016")).id
        await conn.execute(
            "update conversas set estado = 'aguardando_confirmacao', "
            "condominio_pendente = $2 where id = $1",
            conversa,
            cond,
        )

        await conn.execute("delete from condominios where id = $1", cond)

        row = await conn.fetchrow(
            "select estado, condominio_pendente from conversas where id = $1", conversa
        )
        assert row is not None
        assert row["estado"] == "aguardando_confirmacao"
        assert row["condominio_pendente"] is None

    rodar_tx(body)


def test_toda_transicao_do_roteador_e_aceita_pelo_check(rodar_tx):
    """Fecha o laço entre o passo 2 e o passo 1: o roteador não consegue produzir
    uma trinca que o banco recuse. Os construtores de Transicao e o
    chk_conversas_estado_coerente têm que dizer a MESMA coisa."""

    async def body(conn):
        cond = await _condominio(conn, "res-teste-f")
        conversa = await upsert_conversa_ativa(conn, "5511999990020")

        for transicao in (
            Transicao.para_confirmacao(cond),
            Transicao.para_menu(cond),
            Transicao.para_duvidas(cond),
            Transicao.para_identificacao(),
        ):
            await aplicar_transicao(conn, conversa.id, transicao)
            row = await conn.fetchrow(
                "select estado, condominio_id, condominio_pendente "
                "from conversas where id = $1",
                conversa.id,
            )
            assert row["estado"] == transicao.estado.value
            assert row["condominio_id"] == transicao.condominio_id
            assert row["condominio_pendente"] == transicao.condominio_pendente

    rodar_tx(body)


def test_upsert_le_a_trinca_gravada(rodar_tx):
    """A leitura que alimenta o roteador vê o que a transição escreveu."""

    async def body(conn):
        cond = await _condominio(conn, "res-teste-g")
        conversa = await upsert_conversa_ativa(conn, "5511999990021")
        assert conversa.estado is Estado.IDENTIFICACAO

        await aplicar_transicao(conn, conversa.id, Transicao.para_duvidas(cond))

        relida = await upsert_conversa_ativa(conn, "5511999990021")
        assert relida.id == conversa.id
        assert relida.estado is Estado.DUVIDAS
        assert relida.condominio_id == cond
        assert relida.condominio_pendente is None

    rodar_tx(body)


def test_webhook_events_dedup_atomico(rodar_tx):
    async def body(conn):
        assert await registrar_mensagem(conn, "W-1", {"a": 1}) is True
        assert await registrar_mensagem(conn, "W-1", {"a": 2}) is False  # ON CONFLICT

        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into webhook_events (message_id, payload) values ('W-1', '{}')"
                )

        # o payload volta como dict (codec jsonb), não como texto
        payload = await conn.fetchval(
            "select payload from webhook_events where message_id = 'W-1'"
        )
        assert payload == {"a": 1}

    rodar_tx(body)
