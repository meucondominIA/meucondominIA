"""Testes da costura roteador ↔ banco (Fase 3 · Passo 3).

_FakeConn programado: listar_elegiveis vira `fetch`, nome_por_id vira `fetchval`.
Provamos que a delegação do roteador vira a consulta certa, que a transição do
roteador passa intacta, e — o que importa de verdade — que o índice N indexa a
lista DEVOLVIDA, não uma segunda consulta.

A janela de sessão é testada com relógio real: ultima_interacao_em bem no
passado (expira) e recente (não expira). Os dois derivam de sessao_ttl_horas —
o corte é do config, e o que se prova aqui é a DIREÇÃO da decisão.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest


import atendimento
from atendimento import GeracaoPendente
from config import settings
from contexto import MAX_TROCAS, Troca
from roteador import Conversa, Estado, Transicao
from zpro_models import MessageType

C1, C2, C3 = uuid4(), uuid4(), uuid4()
CONVERSA_ID = uuid4()

# Derivados do corte, não fixos em horas: mudar sessao_ttl_horas não pode exigir
# reescrever teste. O que se prova aqui é a DIREÇÃO da decisão, não o número.
_VELHA = settings.sessao_ttl_horas * 2
_RECENTE = settings.sessao_ttl_horas / 2


class _FakeConn:
    """fetch devolve as linhas da lista; fetchval devolve o nome por id."""

    def __init__(self, lista=(), nomes=None):
        self._lista = list(lista)
        self._nomes = nomes or {}
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return [{"id": cid, "nome": nome} for cid, nome in self._lista]

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._nomes.get(args[0])


def _conversa(
    estado, *, condominio=None, pendente=None, idade_horas=0.0, rascunho=None
):
    return Conversa(
        id=CONVERSA_ID,
        estado=estado,
        condominio_id=condominio,
        condominio_pendente=pendente,
        ultima_interacao_em=datetime.now(timezone.utc)
        - timedelta(hours=idade_horas),
        telefone="5555999999999",
        rascunho=rascunho,
    )


def test_janela_usa_o_fuso_do_condominio_e_nao_utc():
    """23h30 em São Paulo já é o dia SEGUINTE em UTC. Se a janela usasse UTC, o
    morador perderia o dia de hoje da lista — todo dia, das 21h à meia-noite."""
    fim_do_dia_em_sp = datetime(2026, 8, 2, 2, 30, tzinfo=timezone.utc)
    de, _ = atendimento._janela(fim_do_dia_em_sp, "America/Sao_Paulo")

    assert de == date(2026, 8, 1)
    assert de != fim_do_dia_em_sp.date()


def test_janela_cobre_exatamente_a_decisao_de_produto():
    """14 dias contando hoje — inclusiva nos dois extremos, como dias_livres."""
    de, ate = atendimento._janela(
        datetime(2026, 8, 1, 12, tzinfo=timezone.utc), "America/Sao_Paulo"
    )
    assert (ate - de).days + 1 == settings.reserva_janela_dias


def _responder(
    conn, conversa, texto, tipo=MessageType.TEXT, entrada_id=None, nova=False
):
    return asyncio.run(
        atendimento.responder(
            conn,
            conversa,
            tipo=tipo,
            texto=texto,
            entrada_id=entrada_id or uuid4(),
            conversa_nova=nova,
        )
    )


# ── conversa reaberta com o tenant lembrado ──────────────────────────────────


def test_conversa_reaberta_pergunta_em_vez_de_corrigir():
    """Sem a guarda, o roteador cairia em _confirmacao e responderia "só preciso
    de 1 ou 2" — correção de uma pergunta que nunca foi feita."""
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=C1)

    texto, transicao = _responder(conn, conversa, "oi", nova=True)

    assert "É o Alfa?" in texto
    assert "só preciso" not in texto.lower()
    assert transicao is None, "o estado já nasceu em aguardando_confirmacao"


def test_conversa_reaberta_sem_memoria_segue_o_fluxo_normal():
    """Primeiro contato de todos: nasce em identificacao, sem candidato."""
    conn = _FakeConn(lista=LISTA3)
    conversa = _conversa(Estado.IDENTIFICACAO)

    texto, _ = _responder(conn, conversa, "oi", nova=True)

    assert "Alfa" in texto and "Beta" in texto


def test_a_guarda_so_vale_na_primeira_mensagem():
    """Na segunda, o 1 tem que confirmar de verdade — senão gira em círculo."""
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=C1)

    _, transicao = _responder(conn, conversa, "1", nova=False)

    assert transicao == Transicao.para_menu(C1)


LISTA3 = [(C1, "Alfa"), (C2, "Beta"), (C3, "Gama")]


# ── identificação: o índice indexa a lista devolvida ─────────────────────────


def test_indice_resolve_para_o_item_certo_da_lista():
    conn = _FakeConn(lista=LISTA3)
    texto, transicao = _responder(conn, _conversa(Estado.IDENTIFICACAO), "2")

    assert "Beta" in texto
    assert transicao == Transicao.para_confirmacao(C2)


def test_indice_alem_do_teto_pede_de_novo_sem_transicionar():
    conn = _FakeConn(lista=LISTA3)
    texto, transicao = _responder(conn, _conversa(Estado.IDENTIFICACAO), "9")

    assert transicao is None
    assert "Alfa" in texto and "Beta" in texto and "Gama" in texto


def test_lista_vazia_na_identificacao_avisa_sem_condominios():
    conn = _FakeConn(lista=[])
    texto, transicao = _responder(conn, _conversa(Estado.IDENTIFICACAO), "1")

    assert "não encontrei" in texto.lower()
    assert transicao is None


def test_uma_unica_consulta_de_lista_por_identificacao():
    """A invariante §3.5: mostrar e resolver leem a MESMA lista, não duas."""
    conn = _FakeConn(lista=LISTA3)
    _responder(conn, _conversa(Estado.IDENTIFICACAO), "2")

    fetches = [c for c in conn.calls if c[0] == "fetch"]
    assert len(fetches) == 1


# ── confirmação: a transição do roteador passa intacta ───────────────────────


def test_confirmacao_sim_vai_ao_menu():
    conn = _FakeConn()
    conversa = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=C1)
    texto, transicao = _responder(conn, conversa, "1")

    assert transicao == Transicao.para_menu(C1)
    assert "Como posso ajudar" in texto


def test_confirmacao_nao_entendida_cita_o_nome_do_pendente():
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=C1)
    texto, transicao = _responder(conn, conversa, "talvez")

    assert "Alfa" in texto
    assert transicao is None
    assert conn.calls[-1] == ("fetchval", conn.calls[-1][1], (C1,))


# ── dúvidas: a delegação vira pacote de geração ──────────────────────────────


def test_duvida_vira_geracao_pendente_com_historico(monkeypatch):
    trocas = [Troca(pergunta="Pode festa?", resposta="Até 22h.")]
    chamadas = []

    async def _trocas(conn, conversa_id, *, limite):
        chamadas.append((conn, conversa_id, limite))
        return trocas

    monkeypatch.setattr(atendimento, "ultimas_trocas", _trocas)
    conn = _FakeConn()
    conversa = _conversa(Estado.DUVIDAS, condominio=C1)
    pendente = _responder(conn, conversa, "Posso ter cachorro?")

    assert pendente == GeracaoPendente(
        pergunta="Posso ter cachorro?", condominio_id=C1, historico=trocas
    )
    assert chamadas == [(conn, CONVERSA_ID, MAX_TROCAS)]


# ── reconfirmação por sessão expirada ────────────────────────────────────────


def test_sessao_velha_reconfirma_o_condominio():
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=_VELHA)
    texto, transicao = _responder(conn, conversa, "1")

    assert "Alfa" in texto
    assert transicao == Transicao.para_confirmacao(C1)


def test_sessao_recente_nao_reconfirma():
    conn = _FakeConn()
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=_RECENTE)
    texto, transicao = _responder(conn, conversa, "1")

    assert transicao == Transicao.para_duvidas(C1)


@pytest.mark.parametrize(
    "idade_horas,expira", [(_RECENTE, False), (_VELHA, True)]
)
def test_a_fronteira_e_a_do_painel_do_zpro(idade_horas, expira):
    """O corte tem que ser o MESMO do "Resolver atendimento sem interação": se o
    nosso relógio for mais lento, o morador lê "atendimento encerrado" e o bot
    continua como se nada tivesse acontecido."""
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=idade_horas)
    assert atendimento._sessao_expirada(conversa) is expira


def test_sessao_expirada_no_meio_do_wizard_larga_o_rascunho():
    """Reserva é ESCRITA: sem isso, o morador voltaria depois da despedida do
    Z-PRO e confirmaria uma data de uma lista que já não vale."""
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(
        Estado.RESERVA,
        condominio=C1,
        idade_horas=_VELHA,
        rascunho={"passo": "confirmacao", "area_id": str(C2), "dia": "2026-08-01",
                  "pagina": 0},
    )
    texto, transicao = _responder(conn, conversa, "1")

    assert "Alfa" in texto
    assert transicao == Transicao.para_confirmacao(C1)
    assert transicao.rascunho is None


def test_reconfirmacao_usa_o_id_da_transicao_nao_o_pendente_antigo():
    """Na reconfirmação o pendente da conversa é None; o nome vem do id que a
    transição acabou de mover para condominio_pendente."""
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=_VELHA)
    _responder(conn, conversa, "1")

    assert conn.calls[-1] == ("fetchval", conn.calls[-1][1], (C1,))


# ── mídia ────────────────────────────────────────────────────────────────────


def test_audio_recebe_so_entendo_texto():
    conn = _FakeConn()
    conversa = _conversa(Estado.MENU, condominio=C1)
    texto, transicao = _responder(conn, conversa, None, tipo=MessageType.UNSUPPORTED)

    assert "texto" in texto.lower()
    assert transicao is None


# ── a corrida perdida (reserva automática) ───────────────────────────────────


class _ConnTx:
    """Conexão fake com transação: o `async with` precisa existir para provarmos
    que o except do _gravar fica FORA dele."""

    def __init__(self):
        self.saidas = []

    def transaction(self):
        registro = self.saidas

        class _Tx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, tipo, exc, tb):
                registro.append(tipo)
                return False

        return _Tx()


def test_23p01_vira_data_tomada_e_nao_engole_o_rollback(monkeypatch):
    """A perdedora da corrida não recebe erro: recebe a lista atualizada. E o
    except mora fora da transação, senão o rollback não teria acontecido."""
    import asyncpg

    from areas import AreaReservavel
    from reserva import Concluir

    conn = _ConnTx()
    area = AreaReservavel(id=C2, nome="Salão de Festas")

    async def _tz(conn, condominio_id):
        return "America/Sao_Paulo"

    async def _explode(conn, **kwargs):
        raise asyncpg.ExclusionViolationError("conflicting key value")

    async def _nunca(conn, **kwargs):
        raise AssertionError("aviso enfileirado para reserva que não existiu")

    async def _dias(conn, **kwargs):
        return [date(2026, 8, 20), date(2026, 8, 21)]

    monkeypatch.setattr(atendimento, "timezone_por_id", _tz)
    monkeypatch.setattr(atendimento, "confirmar_reserva", _explode)
    monkeypatch.setattr(atendimento, "enfileirar_aviso_reserva", _nunca)
    monkeypatch.setattr(atendimento, "dias_livres", _dias)

    conversa = _conversa(Estado.RESERVA, condominio=C1)
    texto, transicao = asyncio.run(
        atendimento._gravar(
            conn,
            conversa,
            Concluir(area_id=C2, dia=date(2026, 8, 12)),
            [area],
            uuid4(),
            0,
        )
    )

    assert "acabou de ser reservada por outro morador" in texto
    assert transicao.estado is Estado.RESERVA
    assert conn.saidas == [asyncpg.ExclusionViolationError]
