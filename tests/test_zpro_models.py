"""Testes do adapter de entrada do Z-PRO (camada anti-corrupção).

parse_zpro_webhook é síncrono e puro (dict -> IncomingMessage | IgnoreMessage);
os testes montam payloads crus e afirmam a conversão e os filtros. Cobre o que
os testes de webhook/sweeper só exercitam de passagem: fallback de telefone,
tipos sem texto, LID que não é telefone e os descartes explícitos.
"""

import pytest

from zpro_models import (
    IgnoreMessage,
    MessageType,
    normalize_phone,
    parse_zpro_webhook,
)


def _raw(
    *,
    method="message",
    include_msg=True,
    msg_id="MSG-1",
    from_me=False,
    remote_jid=None,
    sender_pn="555592372732@s.whatsapp.net",
    push_name="Lorenzo",
    message=None,
    is_group=False,
    include_contact=True,
    number="555592372732",
    contact_name="Lorenzo",
    channel_type="baileys",
) -> dict:
    raw: dict = {"method": method}
    if include_msg:
        key: dict = {"id": msg_id, "fromMe": from_me}
        if remote_jid is not None:
            key["remoteJid"] = remote_jid
        if sender_pn is not None:
            key["sender_pn"] = sender_pn
        raw["msg"] = {
            "key": key,
            "messageTimestamp": 123,
            "pushName": push_name,
            "message": {"conversation": "Oi"} if message is None else message,
        }
    ticket: dict = {
        "id": 1,
        "isGroup": is_group,
        "tenantId": 8,
        "whatsappId": 45,
        "whatsapp": {"id": 45, "type": channel_type},
    }
    if include_contact:
        contact: dict = {"id": 1, "name": contact_name}
        if number is not None:
            contact["number"] = number
        ticket["contact"] = contact
    raw["ticket"] = ticket
    return raw


def test_extrai_campos_basicos():
    raw = _raw(msg_id="ABC-1")
    msg = parse_zpro_webhook(raw)
    assert msg.message_id == "ABC-1"
    assert msg.phone == "555592372732"
    assert msg.text == "Oi"
    assert msg.message_type is MessageType.TEXT
    assert msg.push_name == "Lorenzo"
    assert msg.timestamp == 123
    assert msg.zpro_ticket_id == 1
    assert msg.zpro_whatsapp_id == 45
    assert msg.zpro_tenant_id == 8
    assert msg.channel_type == "baileys"
    assert msg.raw == raw


def test_texto_via_extended_text_message():
    raw = _raw(message={"extendedTextMessage": {"text": "resposta citando"}})
    msg = parse_zpro_webhook(raw)
    assert msg.text == "resposta citando"
    assert msg.message_type is MessageType.TEXT


def test_sem_texto_vira_unsupported():
    # imagem/áudio: message sem conversation e sem extendedTextMessage.
    raw = _raw(message={"imageMessage": {"url": "..."}})
    msg = parse_zpro_webhook(raw)
    assert msg.text is None
    assert msg.message_type is MessageType.UNSUPPORTED


def test_telefone_prioriza_contact_number():
    raw = _raw(number="555511112222", sender_pn="555599998888@s.whatsapp.net")
    assert parse_zpro_webhook(raw).phone == "555511112222"


def test_telefone_fallback_para_sender_pn():
    raw = _raw(include_contact=False, sender_pn="555592372732@s.whatsapp.net")
    assert parse_zpro_webhook(raw).phone == "555592372732"


def test_sem_telefone_levanta_ignore():
    raw = _raw(include_contact=False, sender_pn=None)
    with pytest.raises(IgnoreMessage):
        parse_zpro_webhook(raw)


def test_remote_jid_lid_nao_vira_telefone():
    # remoteJid é LID, não telefone: sem contact.number nem sender_pn -> ignora.
    raw = _raw(include_contact=False, sender_pn=None, remote_jid="99999999999999@lid")
    with pytest.raises(IgnoreMessage):
        parse_zpro_webhook(raw)


def test_from_me_levanta_ignore():
    with pytest.raises(IgnoreMessage):
        parse_zpro_webhook(_raw(from_me=True))


def test_grupo_levanta_ignore():
    with pytest.raises(IgnoreMessage):
        parse_zpro_webhook(_raw(is_group=True))


def test_evento_nao_message_levanta_ignore():
    with pytest.raises(IgnoreMessage):
        parse_zpro_webhook({"method": "presence"})


def test_msg_ausente_levanta_ignore():
    with pytest.raises(IgnoreMessage):
        parse_zpro_webhook(_raw(include_msg=False))


def test_push_name_fallback_para_contact_name():
    raw = _raw(push_name=None, contact_name="Fulano da Silva")
    assert parse_zpro_webhook(raw).push_name == "Fulano da Silva"


def test_normalize_phone():
    assert normalize_phone("555592372732@s.whatsapp.net") == "555592372732"
    assert normalize_phone("555592372732") == "555592372732"
    assert normalize_phone(None) is None
    assert normalize_phone("sem digitos") is None
