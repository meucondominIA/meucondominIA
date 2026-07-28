"""Costura entre o roteador (puro) e o banco (Fase 3 · Passos 3 e 7).

O roteador decide sem I/O e por isso DELEGA o que precisa do banco: qual
condomínio é o item N, o nome do candidato a citar — e, na dúvida, o histórico
da conversa. Aqui isso vira consulta, e a decisão vira (texto, transição) para
o processador enviar e gravar — ou GeracaoPendente, a chamada de geração
adiada que o processador dispara depois de devolver a conexão ao pool.

Regra de ouro: só banco, nenhuma rede. Envio, geração e gravação da saída são
do processador, fora de qualquer conexão presa.

A invariante do isolamento mora aqui: o índice N é resolvido sobre a lista
DEVOLVIDA por listar_elegiveis, nunca por uma segunda consulta com OFFSET.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, ConfigDict, ValidationError

from areas import AreaReservavel, listar_areas_reservaveis
from condominios import (
    CondominioElegivel,
    listar_elegiveis,
    nome_por_id,
    timezone_por_id,
)
from config import settings
from contexto import MAX_TROCAS, Troca
from mensagens import ultimas_trocas
from reserva import (
    Concluir,
    Continuar,
    Encerrar,
    MensagemReserva,
    MostrarDias,
    PaginaDias,
    Rascunho,
    RascunhoArea,
    RascunhoConfirmacao,
    RascunhoDia,
    avancar,
    gravar,
    ler,
    montar_pagina,
)
from reservas import criar_reserva_pendente, dias_livres
from roteador import (
    Conversa,
    Decisao,
    DelegarDuvida,
    DelegarIdentificacao,
    DelegarReserva,
    Mensagem,
    Responder,
    Transicao,
    rotear,
)
from textos import MensagemAtendimento, renderizar
from zpro_models import MessageType

logger = logging.getLogger(__name__)

_EXIGEM_LISTA = frozenset(
    {Mensagem.PEDIR_CONDOMINIO, Mensagem.CONDOMINIO_NAO_ENTENDIDO}
)
_EXIGEM_NOME = frozenset(
    {Mensagem.CONFIRMACAO_NAO_ENTENDIDA, Mensagem.RECONFIRMAR_CONDOMINIO}
)


class GeracaoPendente(BaseModel):
    """A geração adiada: o processador a dispara fora da conexão."""

    model_config = ConfigDict(frozen=True)

    pergunta: str
    condominio_id: UUID
    historico: list[Troca]


def _sessao_expirada(conversa: Conversa) -> bool:
    idade = datetime.now(timezone.utc) - conversa.ultima_interacao_em
    return idade.total_seconds() > settings.sessao_ttl_horas * 3600


async def responder(
    conn: asyncpg.Connection,
    conversa: Conversa,
    *,
    tipo: MessageType,
    texto: str | None,
    entrada_id: UUID,
    conversa_nova: bool = False,
) -> tuple[str, Transicao | None] | GeracaoPendente:
    """A resposta ao morador e a transição a gravar (None = estado inalterado),
    ou o pacote de geração que o processador resolve fora da conexão.

    `entrada_id` é o gate de idempotência da reserva (uq_reservas_origem_mensagem):
    reprocessar a mesma mensagem devolve a reserva já gravada em vez de duplicar.
    """
    if conversa_nova and conversa.condominio_pendente is not None:
        return await _confirmar_lembrado(conn, conversa)

    decisao = rotear(
        conversa,
        tipo=tipo,
        texto=texto,
        precisa_reconfirmar=_sessao_expirada(conversa),
    )
    return await _resolver(conn, conversa, decisao, entrada_id)


async def _resolver(
    conn: asyncpg.Connection, conversa: Conversa, decisao: Decisao, entrada_id: UUID
) -> tuple[str, Transicao | None] | GeracaoPendente:
    match decisao:
        case DelegarIdentificacao(indice=indice):
            return await _identificar(conn, indice)
        case DelegarDuvida(pergunta=pergunta):
            return GeracaoPendente(
                pergunta=pergunta,
                condominio_id=conversa.condominio_id,
                historico=await ultimas_trocas(conn, conversa.id, limite=MAX_TROCAS),
            )
        case DelegarReserva(escolha=escolha):
            return await _reservar(conn, conversa, escolha, entrada_id)
        case Responder():
            return await _responder(conn, conversa, decisao)


async def _confirmar_lembrado(
    conn: asyncpg.Connection, conversa: Conversa
) -> tuple[str, Transicao | None]:
    """Primeira mensagem de uma conversa reaberta com o tenant lembrado.

    Sem isto o roteador cairia em _confirmacao e responderia "só preciso de 1 ou
    2" — correção de uma pergunta que nunca foi feita. Não transiciona: o estado
    já nasceu em aguardando_confirmacao com o candidato.
    """
    nome = await nome_por_id(conn, conversa.condominio_pendente)
    return (
        renderizar(MensagemAtendimento.CONFIRMAR_CONDOMINIO, nome_condominio=nome),
        None,
    )


async def _identificar(
    conn: asyncpg.Connection, indice: int
) -> tuple[str, Transicao | None]:
    """O morador escolheu o item N: se existir, entra em confirmação."""
    lista = await listar_elegiveis(conn)
    if not lista:
        return renderizar(MensagemAtendimento.SEM_CONDOMINIOS), None
    escolhido = _item(lista, indice)
    if escolhido is None:
        return renderizar(Mensagem.CONDOMINIO_NAO_ENTENDIDO, condominios=lista), None
    return (
        renderizar(
            MensagemAtendimento.CONFIRMAR_CONDOMINIO, nome_condominio=escolhido.nome
        ),
        Transicao.para_confirmacao(escolhido.id),
    )


def _item(lista: list[CondominioElegivel], indice: int) -> CondominioElegivel | None:
    """Indexa a lista já em mãos. O roteador barra indice < 1; o teto é daqui."""
    return lista[indice - 1] if 1 <= indice <= len(lista) else None


async def _responder(
    conn: asyncpg.Connection, conversa: Conversa, decisao: Responder
) -> tuple[str, Transicao | None]:
    """Uma decisão do roteador que já é resposta pronta. Só busca o contexto que
    a mensagem exige — a transição vem do roteador e passa intacta."""
    if decisao.mensagem in _EXIGEM_LISTA:
        lista = await listar_elegiveis(conn)
        if not lista:
            return renderizar(MensagemAtendimento.SEM_CONDOMINIOS), decisao.transicao
        return renderizar(decisao.mensagem, condominios=lista), decisao.transicao

    if decisao.mensagem in _EXIGEM_NOME:
        nome = await nome_por_id(conn, _id_do_candidato(conversa, decisao))
        return renderizar(decisao.mensagem, nome_condominio=nome), decisao.transicao

    return renderizar(decisao.mensagem), decisao.transicao


def _id_do_candidato(conversa: Conversa, decisao: Responder):
    """De onde vem o candidato a citar: a reconfirmação acabou de movê-lo para a
    transição; a repergunta da confirmação lê o pendente que já estava lá."""
    if decisao.mensagem is Mensagem.RECONFIRMAR_CONDOMINIO:
        return decisao.transicao.condominio_pendente
    return conversa.condominio_pendente


# ── wizard de reserva (Fase 4 · Etapa 3) ─────────────────────────────────────


async def _reservar(
    conn: asyncpg.Connection, conversa: Conversa, escolha: str, entrada_id: UUID
) -> tuple[str, Transicao | None]:
    """O sanduíche: lê o que o motor precisa, decide sem I/O, executa o descritor.

    `areas` é lido em toda mensagem porque a tela de confirmação cita o NOME e o
    rascunho guarda só o id.
    """
    areas = await listar_areas_reservaveis(conn, conversa.condominio_id)
    rascunho = _rascunho_de(conversa, areas)
    avanco = avancar(rascunho, escolha, areas=areas)

    match avanco:
        case Continuar():
            return _tela(avanco, areas, conversa)
        case MostrarDias():
            return await _dias(conn, conversa, avanco, areas)
        case Concluir():
            return await _gravar(
                conn, conversa, avanco, areas, entrada_id, rascunho.pagina
            )
        case Encerrar(mensagem=mensagem):
            return renderizar(mensagem), Transicao.para_menu(conversa.condominio_id)


def _rascunho_de(
    conversa: Conversa, areas: list[AreaReservavel]
) -> Rascunho | None:
    """Rascunho inutilizável reinicia o wizard em vez de prender o morador.

    Duas causas: forma ilegível (bug nosso) ou área que saiu do catálogo. Nada
    foi gravado até a confirmação, então reiniciar não perde nada.
    """
    if conversa.rascunho is None:
        return None

    try:
        rascunho = ler(conversa.rascunho)
    except ValidationError:
        logger.exception("rascunho ilegível — reiniciando: conversa=%s", conversa.id)
        return None

    area_id = getattr(rascunho, "area_id", None)
    if area_id is not None and _nome_area(areas, area_id) is None:
        logger.warning("área %s saiu do catálogo — reiniciando", area_id)
        return None
    return rascunho


def _nome_area(areas: list[AreaReservavel], area_id: UUID) -> str | None:
    return next((a.nome for a in areas if a.id == area_id), None)


def _janela(agora: datetime, tz: str) -> tuple[date, date]:
    """A janela civil ofertável, no fuso do CONDOMÍNIO.

    Recebe o instante em vez de perguntar ao relógio: assim o "às 21h em São
    Paulo já é amanhã em UTC" vira teste determinístico, e não um bug que só
    aparece à noite.
    """
    hoje = agora.astimezone(ZoneInfo(tz)).date()
    return hoje, hoje + timedelta(days=settings.reserva_janela_dias - 1)


def _tela(
    avanco: Continuar, areas: list[AreaReservavel], conversa: Conversa
) -> tuple[str, Transicao]:
    """Tela renderizável sem I/O novo: o rascunho já carrega o que ela cita."""
    rascunho = avanco.rascunho
    match rascunho:
        case RascunhoArea():
            texto = renderizar(avanco.mensagem, areas=areas)
        case RascunhoDia():
            texto = renderizar(
                avanco.mensagem,
                area=_nome_area(areas, rascunho.area_id),
                pagina=PaginaDias(
                    pagina=rascunho.pagina,
                    dias=rascunho.opcoes,
                    ultima=rascunho.ultima,
                ),
            )
        case RascunhoConfirmacao():
            texto = renderizar(
                avanco.mensagem,
                area=_nome_area(areas, rascunho.area_id),
                dia=rascunho.dia,
            )
    return texto, Transicao.para_reserva(conversa.condominio_id, gravar(rascunho))


async def _dias(
    conn: asyncpg.Connection,
    conversa: Conversa,
    pedido: MostrarDias,
    areas: list[AreaReservavel],
) -> tuple[str, Transicao]:
    """Lê a agenda e congela o mapeamento número→data da tela que vai sair."""
    tz = await timezone_por_id(conn, conversa.condominio_id)
    de, ate = _janela(datetime.now(timezone.utc), tz)
    livres = await dias_livres(
        conn,
        condominio_id=conversa.condominio_id,
        area_id=pedido.area_id,
        de=de,
        ate=ate,
        tz=tz,
    )
    pagina = montar_pagina(livres, pagina=pedido.pagina)
    nome = _nome_area(areas, pedido.area_id)

    if not pagina.dias:
        return (
            renderizar(MensagemReserva.SEM_DATAS, area=nome),
            Transicao.para_menu(conversa.condominio_id),
        )

    rascunho = RascunhoDia(
        area_id=pedido.area_id,
        pagina=pagina.pagina,
        opcoes=pagina.dias,
        ultima=pagina.ultima,
    )
    return (
        renderizar(
            MensagemReserva.LISTA_DIAS, area=nome, pagina=pagina, aviso=pedido.aviso
        ),
        Transicao.para_reserva(conversa.condominio_id, gravar(rascunho)),
    )


async def _gravar(
    conn: asyncpg.Connection,
    conversa: Conversa,
    pedido: Concluir,
    areas: list[AreaReservavel],
    entrada_id: UUID,
    pagina: int,
) -> tuple[str, Transicao]:
    """O único ponto de escrita do fluxo.

    None não é erro: é a corrida real (o dia saiu de baixo entre a tela e o "1").
    Volta para a lista atualizada, na página onde o morador estava. Quem decide
    quem venceu é o banco, como a Etapa 2 desenhou.
    """
    tz = await timezone_por_id(conn, conversa.condominio_id)
    reserva_id = await criar_reserva_pendente(
        conn,
        condominio_id=conversa.condominio_id,
        area_id=pedido.area_id,
        dia=pedido.dia,
        tz=tz,
        telefone=conversa.telefone,
        origem_mensagem_id=entrada_id,
    )

    if reserva_id is None:
        return await _dias(
            conn,
            conversa,
            MostrarDias(
                area_id=pedido.area_id,
                pagina=pagina,
                aviso=MensagemReserva.DATA_TOMADA,
            ),
            areas,
        )

    logger.info("reserva pendente: id=%s conversa=%s", reserva_id, conversa.id)
    return (
        renderizar(
            MensagemReserva.RESERVA_REGISTRADA,
            area=_nome_area(areas, pedido.area_id),
            dia=pedido.dia,
        ),
        Transicao.para_menu(conversa.condominio_id),
    )
