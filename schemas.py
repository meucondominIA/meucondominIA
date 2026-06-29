"""
Fase 2.0 — Passo 1: Adapter de entrada do Z-PRO.

Transforma o JSON cru do webhook do Z-PRO num objeto interno limpo e tipado
(`IncomingMessage`), que é a única coisa que o resto do sistema conhece.
Quando migrarmos para o WhatsApp oficial (WABA), só este arquivo muda.

Baseado no payload real capturado em 28/06/2026 (canal type="baileys").
Doc oficial Pydantic v2:
  - Alias:   https://pydantic.dev/docs/validation/latest/concepts/alias/
  - Models:  https://pydantic.dev/docs/validation/latest/concepts/models/
  - Config:  https://pydantic.dev/docs/validation/latest/concepts/config/
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 1) Modelos do payload do Z-PRO (só os campos que usamos; o resto é ignorado)
# ---------------------------------------------------------------------------

class _ZproBase(BaseModel):
    # extra="ignore": o payload tem dezenas de campos (ticket.contact.tags, aiSummary,
    # messageContextInfo...) que não nos interessam — descartamos sem quebrar.
    # populate_by_name=True: permite construir os modelos pelo nome do atributo
    # (snake_case) nos testes, além do alias do JSON.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ZproKey(_ZproBase):
    id: str                                                      # ID da mensagem -> idempotência
    from_me: bool = Field(default=False, validation_alias="fromMe")
    remote_jid: str | None = Field(default=None, validation_alias="remoteJid")  # é LID, NÃO telefone
    sender_pn: str | None = None                                # "555592372732@s.whatsapp.net"


class ZproExtendedText(_ZproBase):
    text: str | None = None


class ZproMessageContent(_ZproBase):
    # Texto simples chega em `conversation`; texto com link/resposta em `extendedTextMessage.text`.
    conversation: str | None = None
    extended_text_message: ZproExtendedText | None = Field(
        default=None, validation_alias="extendedTextMessage"
    )


class ZproMsg(_ZproBase):
    key: ZproKey
    message_timestamp: int | None = Field(default=None, validation_alias="messageTimestamp")
    push_name: str | None = Field(default=None, validation_alias="pushName")
    message: ZproMessageContent | None = None


class ZproWhatsapp(_ZproBase):
    id: int | None = None
    name: str | None = None
    type: str | None = None      # "baileys" agora; "waba"/oficial depois -> self-describe p/ migração


class ZproContact(_ZproBase):
    id: int | None = None
    number: str | None = None    # já vem só dígitos: "555592372732"
    name: str | None = None
    pushname: str | None = None


class ZproTicket(_ZproBase):
    id: int | None = None
    is_group: bool = Field(default=False, validation_alias="isGroup")
    tenant_id: int | None = Field(default=None, validation_alias="tenantId")     # tenant Z-PRO (a empresa)
    whatsapp_id: int | None = Field(default=None, validation_alias="whatsappId") # canal -> futuro multi-tenant
    contact: ZproContact | None = None
    whatsapp: ZproWhatsapp | None = None


class ZproWebhookPayload(_ZproBase):
    method: str                  # "message" para mensagem; filtramos por isso
    msg: ZproMsg | None = None
    ticket: ZproTicket | None = None


# ---------------------------------------------------------------------------
# 2) Modelo interno normalizado (o que o resto do sistema enxerga)
# ---------------------------------------------------------------------------

class MessageType(str, Enum):
    TEXT = "text"
    UNSUPPORTED = "unsupported"   # áudio, imagem, etc. — tratamos na 2.x


class IncomingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str               # msg.key.id  -> dedup
    phone: str                    # só dígitos com DDI: "555592372732"
    text: str | None
    message_type: MessageType
    push_name: str | None

    timestamp: int | None
    # Contexto Z-PRO (guardado desde já p/ multi-tenant por canal no futuro)
    zpro_ticket_id: int | None
    zpro_whatsapp_id: int | None
    zpro_tenant_id: int | None
    channel_type: str | None      # "baileys" | "waba" | ...

    raw: dict                     # payload bruto, p/ auditoria/depuração


# ---------------------------------------------------------------------------
# 3) Adapter: payload cru -> IncomingMessage (com os filtros)
# ---------------------------------------------------------------------------

class IgnoreMessage(Exception):
    """Mensagem que não deve ser processada (eco, grupo, evento não-message, sem telefone)."""


def normalize_phone(value: str | None) -> str | None:
    """Mantém só os dígitos. '555592372732@s.whatsapp.net' -> '555592372732'."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def _extract_text(message: ZproMessageContent | None) -> str | None:
    if message is None:
        return None
    if message.conversation:
        return message.conversation
    if message.extended_text_message and message.extended_text_message.text:
        return message.extended_text_message.text
    return None


def parse_zpro_webhook(raw: dict) -> IncomingMessage:
    """Valida o corpo do webhook e converte no modelo interno.

    Levanta IgnoreMessage quando a mensagem não deve seguir adiante.
    """
    payload = ZproWebhookPayload.model_validate(raw)

    if payload.method != "message" or payload.msg is None:
        raise IgnoreMessage(f"evento ignorado: method={payload.method!r}")

    msg = payload.msg
    ticket = payload.ticket

    if msg.key.from_me:
        raise IgnoreMessage("eco da própria resposta (fromMe=true)")
    if ticket and ticket.is_group:
        raise IgnoreMessage("mensagem de grupo")

    # Telefone: prioriza ticket.contact.number (já limpo); fallback sender_pn.
    phone = normalize_phone(ticket.contact.number) if (ticket and ticket.contact) else None
    if not phone:
        phone = normalize_phone(msg.key.sender_pn)
    if not phone:
        raise IgnoreMessage("sem telefone identificável")

    text = _extract_text(msg.message)
    message_type = MessageType.TEXT if text else MessageType.UNSUPPORTED

    push_name = msg.push_name or (ticket.contact.name if ticket and ticket.contact else None)

    return IncomingMessage(
        message_id=msg.key.id,
        phone=phone,
        text=text,
        message_type=message_type,
        push_name=push_name,
        timestamp=msg.message_timestamp,
        zpro_ticket_id=ticket.id if ticket else None,
        zpro_whatsapp_id=ticket.whatsapp_id if ticket else None,
        zpro_tenant_id=ticket.tenant_id if ticket else None,
        channel_type=(ticket.whatsapp.type if ticket and ticket.whatsapp else None),
        raw=raw,
    )