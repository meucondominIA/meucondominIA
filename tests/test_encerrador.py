"""Testes do encerrador (Fase 4) — a forma do ciclo, sem banco.

O comportamento contra o Postgres real (índice parcial, concorrência) vive em
tests/integration/test_encerrador.py. Aqui provamos o contrato: um statement só,
o corte vindo do settings, e o loop sobrevivendo a um ciclo que estoura.
"""

import asyncio
from uuid import uuid4

import pytest

import encerrador
from config import settings


class _FakeConn:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


@pytest.fixture
def conn(monkeypatch):
    fake = _FakeConn(rows=[{"id": uuid4()}, {"id": uuid4()}])
    monkeypatch.setattr(encerrador, "get_pool", lambda: _FakePool(fake))
    return fake


def test_um_statement_com_o_corte_do_settings(conn):
    assert asyncio.run(encerrador.encerrar_ociosas()) == 2

    (query, args), *resto = conn.calls
    assert not resto, "um UPDATE só"
    assert "set status = 'encerrada'" in query
    assert "status = 'ativa'" in query
    assert args == (settings.sessao_ttl_horas,)


def test_nao_usa_skip_locked(conn):
    """UPDATE é idempotente por construção: o segundo job reavalia o WHERE e não
    casa mais (READ COMMITTED). SKIP LOCKED aqui seria cerimônia."""
    asyncio.run(encerrador.encerrar_ociosas())
    query = conn.calls[0][0].lower()
    assert "skip locked" not in query
    assert "for update" not in query


def test_ciclo_vazio_devolve_zero(monkeypatch):
    vazio = _FakeConn(rows=[])
    monkeypatch.setattr(encerrador, "get_pool", lambda: _FakePool(vazio))
    assert asyncio.run(encerrador.encerrar_ociosas()) == 0


def test_loop_sobrevive_a_um_ciclo_que_estoura(monkeypatch):
    """Um ciclo ruim não pode matar o job — senão uma falha transitória de banco
    deixaria conversas abertas para sempre."""
    ciclos = []

    async def _ciclo():
        ciclos.append(1)
        if len(ciclos) == 1:
            raise RuntimeError("banco fora do ar")
        raise asyncio.CancelledError  # encerra o teste no 2º ciclo

    async def _sem_dormir(_):
        return None

    monkeypatch.setattr(encerrador, "encerrar_ociosas", _ciclo)
    monkeypatch.setattr(encerrador.asyncio, "sleep", _sem_dormir)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(encerrador.rodar_encerrador())

    assert len(ciclos) == 2, "o loop parou no primeiro erro"
