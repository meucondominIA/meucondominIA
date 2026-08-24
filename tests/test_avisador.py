"""Testes do ciclo do avisador (Fase 4 · Etapa 5).

Sem banco e sem rede: prova-se a coreografia — que a conexão é DEVOLVIDA antes
do envio, que uma falha não derruba o lote, e que a marcação só acontece depois
do envio (é o que define a janela de duplicata). A query em si é do teste de
integração, onde há Postgres de verdade.
"""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import avisador
from avisos import AvisoPendente

UM = AvisoPendente(id=uuid4(), texto="Reserva #abc", telefone="5555990000000")
OUTRO = AvisoPendente(id=uuid4(), texto="Ocorrência #def", telefone="5511888887777")


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        self.pool.em_uso = True
        return _FakeConn(self.pool)

    async def __aexit__(self, *exc):
        self.pool.em_uso = False


class _FakePool:
    def __init__(self):
        self.em_uso = False

    def acquire(self):
        return _FakeAcquire(self)


@pytest.fixture
def cenario(monkeypatch):
    def _montar(lote, enviar=None, marcar=None):
        pool = _FakePool()
        monkeypatch.setattr(avisador, "get_pool", lambda: pool)
        monkeypatch.setattr(
            avisador, "reservar_lote", AsyncMock(return_value=list(lote))
        )
        monkeypatch.setattr(avisador, "enviar", enviar or AsyncMock())
        monkeypatch.setattr(avisador, "marcar_enviado", marcar or AsyncMock())
        return pool

    return _montar


def test_envia_e_marca_cada_aviso(cenario):
    enviar, marcar = AsyncMock(), AsyncMock()
    cenario([UM, OUTRO], enviar, marcar)

    assert asyncio.run(avisador.enviar_pendentes()) == 2
    assert enviar.await_count == 2
    assert marcar.await_count == 2


def test_endereca_o_sindico_certo_com_objeto_tipado(cenario):
    """O core só conhece OutgoingMessage — formato do Z-PRO não atravessa."""
    enviar = AsyncMock()
    cenario([UM, OUTRO], enviar)

    asyncio.run(avisador.enviar_pendentes())

    saidas = [chamada.args[0] for chamada in enviar.await_args_list]
    assert [s.phone for s in saidas] == ["5555990000000", "5511888887777"]
    assert [s.text for s in saidas] == ["Reserva #abc", "Ocorrência #def"]
    # external_key é o id do AVISO: correlação, não idempotência.
    assert [s.external_key for s in saidas] == [str(UM.id), str(OUTRO.id)]


def test_ciclo_vazio_nao_chama_a_rede(cenario):
    enviar = AsyncMock()
    cenario([], enviar)

    assert asyncio.run(avisador.enviar_pendentes()) == 0
    enviar.assert_not_awaited()


def test_envia_com_a_conexao_ja_devolvida(cenario):
    """A regra de ouro: rede nunca com conexão do pool na mão."""
    visto = []
    pool = None

    async def enviar(msg):
        visto.append(pool.em_uso)

    pool = cenario([UM, OUTRO], enviar)
    asyncio.run(avisador.enviar_pendentes())
    assert visto == [False, False]


def test_falha_de_envio_nao_marca_nem_derruba_o_lote(cenario):
    """O que falhou segue pendente e a lease o devolve; o resto sai."""
    marcados = []

    async def enviar(msg):
        if msg.text == UM.texto:
            raise RuntimeError("Z-PRO fora do ar")

    async def marcar(conn, aviso_id):
        marcados.append(aviso_id)

    cenario([UM, OUTRO], enviar, marcar)

    assert asyncio.run(avisador.enviar_pendentes()) == 1
    assert marcados == [OUTRO.id]


def test_marca_so_depois_de_enviar(cenario):
    """A ordem É a janela de duplicata: inverter faria perder o aviso em vez de
    duplicá-lo, e perder é pior."""
    ordem = []

    async def enviar(msg):
        ordem.append(("enviou", msg.external_key))

    async def marcar(conn, aviso_id):
        ordem.append(("marcou", str(aviso_id)))

    cenario([UM], enviar, marcar)
    asyncio.run(avisador.enviar_pendentes())

    assert ordem == [("enviou", str(UM.id)), ("marcou", str(UM.id))]


def test_lote_e_lease_saem_da_config(cenario, monkeypatch):
    """Guarda de dimensionamento: batch × latência tem que caber na lease."""
    pool = _FakePool()
    reservar = AsyncMock(return_value=[])
    monkeypatch.setattr(avisador, "get_pool", lambda: pool)
    monkeypatch.setattr(avisador, "reservar_lote", reservar)

    asyncio.run(avisador.enviar_pendentes())

    kwargs = reservar.await_args.kwargs
    assert kwargs["limite"] == avisador.settings.aviso_batch_size
    assert kwargs["lease_segundos"] == float(avisador.settings.aviso_lease_seconds)
    # ~1,1s por envio, medido em 29/07/2026 contra o Z-PRO real.
    assert avisador.settings.aviso_batch_size * 1.1 < (
        avisador.settings.aviso_lease_seconds
    )


def test_loop_sobrevive_a_um_ciclo_que_estoura(monkeypatch):
    ciclos = []

    async def ciclo():
        ciclos.append(1)
        if len(ciclos) == 1:
            raise RuntimeError("banco fora do ar")

    async def dormir(_):
        if len(ciclos) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(avisador, "enviar_pendentes", ciclo)
    monkeypatch.setattr(avisador.asyncio, "sleep", dormir)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(avisador.rodar_avisador())
    assert len(ciclos) == 2
