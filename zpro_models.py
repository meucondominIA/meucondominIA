"""Adapter de entrada do Z-PRO (camada anti-corrupção).

Transforma o JSON cru do webhook do Z-PRO num objeto interno tipado
(`IncomingMessage`) — a única coisa que o resto do sistema conhece.
Quando migrarmos para o WhatsApp oficial (WABA), só este arquivo muda.

Baseado no payload real capturado em 28/06/2026 (canal type="baileys").
Doc Pydantic v2 — alias: https://docs.pydantic.dev/latest/concepts/alias/

pydantic -> valida/coeage -> objeto tipado
"""

import base64
import hashlib
import logging
import re
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# Modelos do payload do Z-PRO, json_ZPRO tem o retorno json dele
# ____________________----------------_______________---------------________


#Zpro* espelham o payload
class _ZproBase(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_by_name=True)


class ZproKey(_ZproBase):
    id: str  # ID da mensagem -> idempotência/dedup
    from_me: bool = Field(default=False, validation_alias="fromMe") #camelCase -> snake_case
    remote_jid: str | None = Field(
        default=None, validation_alias="remoteJid"
    )  # é LID, NÃO telefone
    sender_pn: str | None = None  # "555590000000@s.whatsapp.net" (fallback)


class ZproExtendedText(_ZproBase):
    text: str | None = None


class ZproImage(_ZproBase):
    """O bloco de imagem do Baileys. Só os campos que usamos: url, mediaKey,
    directPath e fileEncSha256 são a rota CIFRADA do CDN, que ignoramos — o
    Z-PRO já entrega o arquivo decifrado em msg.base64.

    `fileLength` chega como string no payload real; o Pydantic coage.
    """

    mimetype: str | None = None
    caption: str | None = None
    file_sha256: str | None = Field(default=None, validation_alias="fileSha256")
    file_length: int | None = Field(default=None, validation_alias="fileLength")


class ZproMessageContent(_ZproBase):
    conversation: str | None = None
    extended_text_message: ZproExtendedText | None = Field(
        default=None, validation_alias="extendedTextMessage"
    )
    image_message: ZproImage | None = Field(
        default=None, validation_alias="imageMessage"
    )


class ZproMsg(_ZproBase):
    key: ZproKey
    message_timestamp: int | None = Field(
        default=None, validation_alias="messageTimestamp"
    )
    push_name: str | None = Field(default=None, validation_alias="pushName")
    message: ZproMessageContent | None = None
    # O arquivo DECIFRADO, inline. Verificado por envio real em 28/07/2026: só
    # aparece em mensagem de mídia, e o sha256 do conteúdo bate com fileSha256.
    base64: str | None = None


class ZproWhatsapp(_ZproBase):
    id: int | None = None
    name: str | None = None
    type: str | None = None


class ZproContact(_ZproBase):
    id: int | None = None
    number: str | None = None
    name: str | None = None
    pushname: str | None = None


class ZproTicket(_ZproBase):
    id: int | None = None
    is_group: bool = Field(default=False, validation_alias="isGroup")
    tenant_id: int | None = Field(
        default=None, validation_alias="tenantId"
    )  # tenant Z-PRO (a empresa)
    whatsapp_id: int | None = Field(
        default=None, validation_alias="whatsappId"
    )  # canal -> futuro multi-tenant
    contact: ZproContact | None = None
    whatsapp: ZproWhatsapp | None = None


class ZproWebhookPayload(_ZproBase):
    method: str
    msg: ZproMsg | None = None
    ticket: ZproTicket | None = None


# Modelo interno (o que o sisetma enxerga)
# _____________-----------------_________________-----------------------


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    UNSUPPORTED = "unsupported"  # áudio, documento — o que sabemos receber e não usar

class MidiaRecebida(BaseModel):
    """A mídia já traduzida: bytes decifrados, tipo e digest em hex.

    Hex e não o base64 do fileSha256 porque o digest vira nome de arquivo no
    Storage, e base64 tem '/' e '+'.
    """

    model_config = ConfigDict(frozen=True)

    conteudo: bytes
    mimetype: str
    sha256: str


#Contrato/Resultado
class IncomingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str  # msg.key.id  -> dedup
    phone: str  # só dígitos com DDI: "555590000000"
    text: str | None
    message_type: MessageType
    push_name: str | None
    midia: MidiaRecebida | None = None

    timestamp: int | None
    # Contexto Z-PRO (guardado desde já p/ multi-tenant por canal no futuro)
    zpro_ticket_id: int | None
    zpro_whatsapp_id: int | None
    zpro_tenant_id: int | None
    channel_type: str | None

    raw: dict


# payload cri -> IncomingMess(com os filtros)
# ___________-------------------________________-----------------------


class IgnoreMessage(Exception):
    """Mensagem que não deve ser processada (eco, grupo, evento não-message, sem telefone)."""
    """Se usasse um Exception genérico pro ignorar, ele cairia no mesmo balde do algo quebrou — e você não conseguiria distinguir pulei de propósito de deu ruim"""


def normalize_phone(value: str | None) -> str | None:
    """Mantém só os dígitos. '555590000000@s.whatsapp.net' -> '555590000000'."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def _extract_text(message: ZproMessageContent | None) -> str | None:
    """A legenda da foto entra como texto: para o core ela é o que o morador
    escreveu, e de onde veio não importa."""
    if message is None:
        return None
    if message.conversation:
        return message.conversation
    if message.extended_text_message and message.extended_text_message.text:
        return message.extended_text_message.text
    if message.image_message and message.image_message.caption:
        return message.image_message.caption
    return None


def _extract_midia(msg: ZproMsg) -> MidiaRecebida | None:
    """Decodifica e CONFERE o digest contra o que o WhatsApp declarou.

    Falha (base64 torto, sha divergente) devolve None em vez de levantar: a
    mensagem segue como texto, o wizard pede a foto de novo e o morador reenvia.
    Levantar aqui daria 200 mudo e deixaria a pessoa esperando.
    """
    imagem = msg.message.image_message if msg.message else None
    if imagem is None or not msg.base64:
        return None

    try:
        conteudo = base64.b64decode(msg.base64, validate=True)
    except (ValueError, TypeError):
        logger.error("mídia: base64 ilegível — message_id=%s", msg.key.id)
        return None

    digest = hashlib.sha256(conteudo).digest()
    if imagem.file_sha256 and base64.b64encode(digest).decode() != imagem.file_sha256:
        logger.error("mídia: sha256 não confere — message_id=%s", msg.key.id)
        return None

    return MidiaRecebida(
        conteudo=conteudo,
        mimetype=imagem.mimetype or "application/octet-stream",
        sha256=digest.hex(),
    )


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
    """morador "Oi" (fromMe=false) → bot responde "Olá!"
       → webhook da resposta (fromMe=true) → SEM filtro: bot responde de novo
       → webhook dessa (fromMe=true) → responde de novo → 🔁 loop"""
    if ticket and ticket.is_group:
        raise IgnoreMessage("mensagem de grupo")

    # Telefone: prioriza ticket.contact.number (já limpo); fallback sender_pn.
    phone = (
        normalize_phone(ticket.contact.number) if (ticket and ticket.contact) else None
    )
    if not phone:
        phone = normalize_phone(msg.key.sender_pn)
    if not phone:
        raise IgnoreMessage("sem telefone identificável")

    text = _extract_text(msg.message)
    midia = _extract_midia(msg)
    if midia is not None:
        message_type = MessageType.IMAGE
    else:
        message_type = MessageType.TEXT if text else MessageType.UNSUPPORTED

    push_name = msg.push_name or (
        ticket.contact.name if ticket and ticket.contact else None
    )

    return IncomingMessage(
        message_id=msg.key.id,
        phone=phone,
        text=text,
        message_type=message_type,
        push_name=push_name,
        midia=midia,
        timestamp=msg.message_timestamp,
        zpro_ticket_id=ticket.id if ticket else None,
        zpro_whatsapp_id=ticket.whatsapp_id if ticket else None,
        zpro_tenant_id=ticket.tenant_id if ticket else None,
        channel_type=(ticket.whatsapp.type if ticket and ticket.whatsapp else None),
        raw=raw,
    )
