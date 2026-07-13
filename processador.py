"""Miolo do processamento: entrada → eco → saída (Fase 1 · Parte 2).

Entrega at-least-once: webhook e sweeper podem reprocessar a mesma mensagem.
A guarda contra eco duplicado é NOSSA — o Z-PRO não deduplica por externalKey
(verificado 09/07/2026) — e mora no banco: entrada com ON CONFLICT no
message_id + checagem de saída existente + índice único uq_mensagens_em_resposta_a.
Janela residual aceita: crash entre o envio HTTP e o INSERT da saída faz o
reprocessamento reenviar o eco (duplica no WhatsApp, nunca no banco).

TX1 é curta (upsert conversa + INSERT entrada) e commita ANTES do envio;
nenhuma transação NEM conexão do pool atravessa I/O de rede — o envio roda
sem conexão presa e a saída é gravada numa segunda aquisição curta (o pool é
limitado pelo teto de conexões do Supabase, compartilhado; conexão parada
esperando o Z-PRO estrangularia o próprio caminho do ACK durável). Exceções
sobem: o chamador marca 'falhou'.
"""

import logging

from db import get_pool
from mensagens import (
    registrar_entrada,
    registrar_saida,
    saida_ja_existe,
    upsert_conversa_ativa,
)
from zpro_client import OutgoingMessage, enviar
from zpro_models import IncomingMessage, MessageType

logger = logging.getLogger(__name__)

TEXTO_UNSUPPORTED = "Recebi sua mensagem, mas por enquanto só entendo texto."


async def processar_mensagem(msg: IncomingMessage) -> None:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            conversa_id = await upsert_conversa_ativa(conn, msg.phone)
            entrada_id, nova = await registrar_entrada(conn, conversa_id, msg)

        if not nova and await saida_ja_existe(conn, entrada_id):
            logger.info(
                "entrada já respondida — não reenvia: message_id=%s", msg.message_id
            )
            return

    texto = (
        f"Eco: {msg.text}"
        if msg.message_type is MessageType.TEXT
        else TEXTO_UNSUPPORTED
    )

    await enviar(
        OutgoingMessage(phone=msg.phone, text=texto, external_key=msg.message_id)
    )

    async with get_pool().acquire() as conn:
        await registrar_saida(conn, conversa_id, texto, entrada_id)
