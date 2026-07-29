"""Miolo do processamento: entrada → decisão → saída (Fase 3 · Passos 3 e 7).

Substitui o eco pelo atendimento por menu. A idempotência é a mesma da Fase 1:
entrada com ON CONFLICT no message_id + checagem de saída existente + índice
único uq_mensagens_em_resposta_a. Janela residual aceita: crash entre o envio e
a TX2 reenvia (duplica no WhatsApp, nunca no banco).

Três janelas de conexão, e a decisão fica na PRIMEIRA — atendimento.responder só
toca banco, então roda com a conn já em mãos. A geração (duas redes) e o envio
ficam entre as janelas, sem conexão presa. A transição é gravada na TX2, junto
da saída e DEPOIS do envio: antes do envio, o estado mentiria sobre o que o
morador viu; separada da saída, histórico e estado poderiam discordar.

A contingência cobre só a falha ANTES do envio (a de rede não tem como avisar
pelo canal que caiu). Ela é best-effort: se o próprio banco caiu, nem ela sai —
e a exceção sobe do mesmo jeito para o chamador marcar 'falhou'.
"""

import logging

import anexos
from anexos import AnexoIndisponivelError, AnexoRecusadoError
from atendimento import (
    FotoPendente,
    GeracaoPendente,
    foto_falhou,
    responder,
    retomar_com_anexo,
)
from db import get_pool
from ocorrencia import MensagemOcorrencia
from geracao import responder_duvida
from mensagens import (
    aplicar_transicao,
    conversa_ativa,
    marcar_interacao,
    registrar_entrada,
    registrar_saida,
    saida_ja_existe,
)
from textos import MensagemAtendimento, renderizar
from zpro_client import OutgoingMessage, enviar
from zpro_models import IncomingMessage

logger = logging.getLogger(__name__)


async def processar_mensagem(msg: IncomingMessage) -> None:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            conversa, conversa_nova = await conversa_ativa(conn, msg.phone)
            entrada_id, nova = await registrar_entrada(conn, conversa.id, msg)

        if not nova and await saida_ja_existe(conn, entrada_id):
            logger.info(
                "entrada já respondida — não reenvia: message_id=%s", msg.message_id
            )
            return

        resultado = await _decidir(conn, conversa, msg, entrada_id, conversa_nova)

    match resultado:
        case GeracaoPendente():
            texto, transicao = await _gerar(resultado, msg), None
        case FotoPendente():
            texto, transicao = await _guardar_foto(resultado, msg)
        case (texto, transicao):
            pass

    await enviar(
        OutgoingMessage(phone=msg.phone, text=texto, external_key=msg.message_id)
    )

    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await registrar_saida(conn, conversa.id, texto, entrada_id)
            if transicao is not None:
                await aplicar_transicao(conn, conversa.id, transicao)
            await marcar_interacao(conn, conversa.id)


async def _decidir(conn, conversa, msg: IncomingMessage, entrada_id, conversa_nova):
    """A decisão do atendimento, com a contingência como rede: uma falha aqui
    (só banco) vira mensagem ao morador em vez de silêncio. A saída de erro NÃO
    transiciona — o estado fica onde estava."""
    try:
        return await responder(
            conn, conversa, tipo=msg.message_type, texto=msg.text,
            entrada_id=entrada_id, conversa_nova=conversa_nova, midia=msg.midia,
        )
    except Exception:
        logger.exception("atendimento falhou: message_id=%s", msg.message_id)
        return renderizar(MensagemAtendimento.CONTINGENCIA), None


async def _guardar_foto(pendente: FotoPendente, msg: IncomingMessage):
    """O upload, FORA da conexão. A retomada é síncrona e sem banco — o motor da
    ocorrência não lê nada —, então ela cabe aqui sem reabrir janela.

    As duas falhas do Storage viram telas diferentes de propósito: recusa do
    arquivo pede OUTRA foto, indisponibilidade pede a MESMA de novo.
    """
    try:
        anexo = await anexos.guardar(
            pendente.midia, condominio_id=pendente.condominio_id
        )
    except AnexoRecusadoError:
        logger.warning("anexo recusado: message_id=%s", msg.message_id, exc_info=True)
        return foto_falhou(pendente, MensagemOcorrencia.FOTO_RECUSADA)
    except AnexoIndisponivelError:
        logger.exception("anexo indisponível: message_id=%s", msg.message_id)
        return foto_falhou(pendente, MensagemOcorrencia.FOTO_FALHOU)

    return retomar_com_anexo(pendente, anexo)


async def _gerar(pendente: GeracaoPendente, msg: IncomingMessage) -> str:
    """A última rede da geração: responder_duvida engole falha de rede (B9);
    o que sobra é bug nosso, e bug também vira contingência."""
    try:
        return await responder_duvida(
            pendente.pergunta, pendente.condominio_id, pendente.historico
        )
    except Exception:
        logger.exception("geração falhou: message_id=%s", msg.message_id)
        return renderizar(MensagemAtendimento.CONTINGENCIA)
