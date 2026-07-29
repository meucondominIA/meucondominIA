"""Testes do adapter de entrada do Z-PRO (camada anti-corrupção).

parse_zpro_webhook é síncrono e puro (dict -> IncomingMessage | IgnoreMessage);
os testes montam payloads crus e afirmam a conversão e os filtros. Cobre o que
os testes de webhook/sweeper só exercitam de passagem: fallback de telefone,
tipos sem texto, LID que não é telefone e os descartes explícitos.
"""

import base64
import copy
import hashlib
import json
import pathlib

import pytest

from zpro_models import (
    IgnoreMessage,
    MessageType,
    normalize_phone,
    parse_zpro_webhook,
)

# Payload REAL do Z-PRO, sem edição: capturado por endpoint-armadilha em
# 28/07/2026, com uma foto enviada pelo WhatsApp de verdade. É o que transforma
# os testes de mídia em contrato observado, e não em suposição sobre o provedor.
_IMAGEM = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "zpro_imagem.json").read_text(
        encoding="utf-8"
    )
)


def _imagem(*, caption=None, conteudo=None, sha=None) -> dict:
    """A captura real, com o mínimo alterado para cada caso."""
    raw = copy.deepcopy(_IMAGEM)
    im = raw["msg"]["message"]["imageMessage"]
    if caption is not None:
        im["caption"] = caption
    if conteudo is not None:
        raw["msg"]["base64"] = conteudo
    if sha is not None:
        im["fileSha256"] = sha
    return raw


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


def test_sem_texto_nem_midia_vira_unsupported():
    """Áudio/documento: message sem conversation, sem extendedTextMessage e sem
    imageMessage."""
    raw = _raw(message={"audioMessage": {"seconds": 3}})
    msg = parse_zpro_webhook(raw)
    assert msg.text is None
    assert msg.midia is None
    assert msg.message_type is MessageType.UNSUPPORTED


# ── mídia (Fase 4 · Etapa 4), contra o payload real ──────────────────────────


def test_foto_real_vira_image_com_os_bytes_decifrados():
    msg = parse_zpro_webhook(_imagem())
    assert msg.message_type is MessageType.IMAGE
    assert msg.midia is not None
    assert len(msg.midia.conteudo) == 111582
    assert msg.midia.conteudo[:3].hex() == "ffd8ff"  # JPEG
    assert msg.midia.mimetype == "image/jpeg"


def test_sha256_do_conteudo_bate_com_o_declarado_pelo_whatsapp():
    """A invariante que o adapter passa a exigir: o que o Z-PRO decifrou é o
    arquivo que o WhatsApp declarou. Se um dia parar de bater, é corrupção, e a
    gente descobre na borda."""
    msg = parse_zpro_webhook(_imagem())
    declarado = _IMAGEM["msg"]["message"]["imageMessage"]["fileSha256"]
    digest = hashlib.sha256(msg.midia.conteudo).digest()
    assert base64.b64encode(digest).decode() == declarado
    assert msg.midia.sha256 == digest.hex()


def test_sha256_em_hex_serve_de_nome_de_arquivo():
    """base64 tem '/' e '+' e quebraria o caminho no Storage."""
    sha = parse_zpro_webhook(_imagem()).midia.sha256
    assert len(sha) == 64 and sha.isalnum() and sha.islower()


def test_foto_sem_legenda_nao_tem_texto():
    msg = parse_zpro_webhook(_imagem())
    assert msg.message_type is MessageType.IMAGE
    assert msg.text is None


def test_legenda_da_foto_entra_como_texto():
    """Para o core a legenda é o que o morador escreveu; de onde veio não importa."""
    msg = parse_zpro_webhook(_imagem(caption="vazamento na garagem"))
    assert msg.message_type is MessageType.IMAGE
    assert msg.text == "vazamento na garagem"
    assert msg.midia is not None


def test_sha_divergente_descarta_a_midia_e_degrada_para_texto():
    """Corrupção não pode virar 200 mudo: sem a foto, a legenda ainda carrega a
    intenção e o wizard pede a imagem de novo."""
    raw = _imagem(caption="olha isso", sha="c2hhIGVycmFkbw==")
    msg = parse_zpro_webhook(raw)
    assert msg.midia is None
    assert msg.message_type is MessageType.TEXT
    assert msg.text == "olha isso"


def test_base64_ilegivel_descarta_a_midia():
    msg = parse_zpro_webhook(_imagem(conteudo="isto não é base64 !!!"))
    assert msg.midia is None
    assert msg.message_type is MessageType.UNSUPPORTED


def test_imagem_sem_base64_degrada_em_vez_de_perder_a_legenda():
    """Se o campo sumir do contrato, a legenda ainda chega — e o morador não fica
    ouvindo 'só entendo texto'."""
    raw = _imagem(caption="tem uma goteira")
    del raw["msg"]["base64"]
    msg = parse_zpro_webhook(raw)
    assert msg.midia is None
    assert msg.message_type is MessageType.TEXT
    assert msg.text == "tem uma goteira"


def test_texto_puro_nao_carrega_midia():
    """Verificado no payload real: msg.base64 só existe em mensagem de mídia."""
    msg = parse_zpro_webhook(_raw(message={"conversation": "Oi"}))
    assert msg.midia is None
    assert msg.message_type is MessageType.TEXT


def test_base64_torto_sem_sha_declarado_ainda_e_recusado():
    """Sem fileSha256 a conferência de digest não roda, então a validação do
    próprio base64 é a única defesa — decodificar "na marra" deixaria passar
    bytes truncados como se fossem a foto."""
    # Lixo ASCII de propósito: b64decode sem validate DESCARTA os caracteres
    # fora do alfabeto em silêncio e devolve 3 bytes truncados como se fossem
    # a foto. Com validate=True, levanta.
    raw = _imagem(conteudo="abcd!!!!")
    del raw["msg"]["message"]["imageMessage"]["fileSha256"]
    assert parse_zpro_webhook(raw).midia is None


def test_imagem_sem_mimetype_declarado_nao_quebra():
    raw = _imagem()
    del raw["msg"]["message"]["imageMessage"]["mimetype"]
    midia = parse_zpro_webhook(raw).midia
    assert midia is not None
    assert midia.mimetype == "application/octet-stream"


def test_encanamento_cifrado_do_baileys_nao_atravessa_a_fronteira():
    """url, mediaKey, directPath e fileEncSha256 são a rota do CDN que ignoramos:
    o core só conhece MidiaRecebida."""
    midia = parse_zpro_webhook(_imagem()).midia
    assert set(midia.model_dump()) == {"conteudo", "mimetype", "sha256"}


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
