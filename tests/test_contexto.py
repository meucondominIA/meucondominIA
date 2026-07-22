"""Testes da montagem de contexto (Fase 3 · Passo 5).

Função pura: sem rede, sem mock — assert sobre a lista de mensagens devolvida.
A QUALIDADE do que o modelo faz com essa lista é do Passo 8; aqui só a FORMA.
"""

from busca import RegraEncontrada
from chat import MensagemChat, PapelChat
from contexto import Troca, montar_mensagens


def _trecho(fonte: str, conteudo: str, distancia: float = 0.1) -> RegraEncontrada:
    return RegraEncontrada(conteudo=conteudo, fonte=fonte, distancia=distancia)


_UM = [_trecho("Regimento Gabro, Art. 15º", "Art. 15º - É permitida a permanência de animais.")]


def test_lista_minima_e_system_mais_turno_do_usuario():
    msgs = montar_mensagens("posso ter cachorro?", _UM, [])
    assert len(msgs) == 2
    assert msgs[0].papel is PapelChat.SISTEMA
    assert msgs[-1].papel is PapelChat.USUARIO
    assert all(isinstance(m, MensagemChat) for m in msgs)


def test_system_e_estatico_e_o_primeiro():
    a = montar_mensagens("p1", _UM, [])
    b = montar_mensagens("outra pergunta", _UM, [Troca(pergunta="x", resposta="y")])
    assert a[0].papel is PapelChat.SISTEMA
    assert a[0].conteudo == b[0].conteudo  # idêntico → prefixo cacheável


def test_pergunta_e_trecho_entram_no_turno_final():
    msgs = montar_mensagens("posso ter cachorro?", _UM, [])
    final = msgs[-1].conteudo
    assert "Pergunta: posso ter cachorro?" in final
    assert "[Regimento Gabro, Art. 15º]" in final
    assert "Art. 15º - É permitida a permanência de animais." in final


def test_trechos_etiquetados_com_a_fonte_na_ordem_da_busca():
    trechos = [
        _trecho("Regimento Gabro, Art. 15º", "regra dos animais", 0.20),
        _trecho("Regimento Gabro, Art. 22", "regra da piscina", 0.35),
    ]
    final = montar_mensagens("qualquer", trechos, [])[-1].conteudo
    assert final.index("[Regimento Gabro, Art. 15º]") < final.index("[Regimento Gabro, Art. 22]")
    assert "regra dos animais" in final
    assert "regra da piscina" in final


def test_distancia_nao_vaza_no_prompt():
    final = montar_mensagens("q", [_trecho("Regimento Gabro, Art. 15º", "texto", 0.4213)], [])[-1].conteudo
    assert "0.4213" not in final
    assert "distancia" not in final.lower()


def test_historico_vira_pares_usuario_assistente_na_ordem():
    hist = [Troca(pergunta="p1", resposta="r1"), Troca(pergunta="p2", resposta="r2")]
    msgs = montar_mensagens("atual", _UM, hist)
    meio = msgs[1:-1]
    assert [(m.papel, m.conteudo) for m in meio] == [
        (PapelChat.USUARIO, "p1"),
        (PapelChat.ASSISTENTE, "r1"),
        (PapelChat.USUARIO, "p2"),
        (PapelChat.ASSISTENTE, "r2"),
    ]


def test_historico_aparado_nas_ultimas_3_trocas():
    hist = [Troca(pergunta=f"p{i}", resposta=f"r{i}") for i in range(5)]
    msgs = montar_mensagens("atual", _UM, hist)
    meio = msgs[1:-1]
    assert len(meio) == 6  # 3 trocas × 2 mensagens
    assert "p0" not in [m.conteudo for m in meio]
    assert "p1" not in [m.conteudo for m in meio]
    assert meio[0].conteudo == "p2"  # a 3ª-a-última troca abre o histórico


def test_trechos_vazios_viram_marcador_de_nada_encontrado():
    final = montar_mensagens("posso ter cachorro?", [], [])[-1].conteudo
    assert "Nenhum trecho do regimento foi encontrado" in final
    assert "Pergunta: posso ter cachorro?" in final


def test_guardrails_estao_no_system_prompt():
    system = montar_mensagens("q", _UM, [])[0].conteudo
    assert "EXCLUSIVAMENTE" in system          # fidelidade só-fonte
    assert "não encontrou" in system           # admitir não-sei
    assert "leis gerais" in system             # não-lei-geral
    assert "português do Brasil" in system      # PT-BR
    assert "Fonte:" in system                  # citação (D4)
    assert "NÃO inclua a linha 'Fonte:'" in system  # omitir no não-sei
