import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import {
  ehDiaInteiro,
  formatarDia,
  formatarPeriodo,
  formatarTelefone,
  jaPassou,
} from './formato.ts'

const SP = 'America/Sao_Paulo'
const NY = 'America/New_York'

describe('o fuso do condomínio manda, não o do processo', () => {
  test('reserva noturna não escorrega para o dia seguinte', () => {
    assert.equal(formatarDia('2026-08-09T00:00:00Z', SP), 'sáb., 08/08')
    assert.equal(formatarDia('2026-08-09T02:00:00Z', SP), 'sáb., 08/08')
  })

  test('o mesmo instante em UTC cai noutro dia — a divergência que o bug produz', () => {
    assert.equal(formatarDia('2026-08-09T00:00:00Z', 'UTC'), 'dom., 09/08')
    assert.notEqual(
      formatarDia('2026-08-09T00:00:00Z', SP),
      formatarDia('2026-08-09T00:00:00Z', 'UTC'),
    )
  })

  test('o fuso vem do dado, não de constante no código', () => {
    const instante = '2026-08-09T01:00:00Z'
    assert.notEqual(formatarDia(instante, SP), formatarDia(instante, 'Asia/Tokyo'))
  })

  test('virada de ano erra dia, mês e ano de uma vez', () => {
    const reveillon = '2027-01-01T02:00:00Z'
    assert.match(formatarDia(reveillon, SP), /31\/12/)
    assert.match(formatarDia(reveillon, 'UTC'), /01\/01/)
  })

  test('ICU completo: pt-BR de verdade, não fallback em inglês', () => {
    assert.match(formatarDia('2026-08-09T03:00:00Z', SP), /^dom/)
    assert.doesNotMatch(formatarDia('2026-08-09T03:00:00Z', SP), /Sun/)
  })
})

describe('dia inteiro é derivado por fronteira, nunca por duração', () => {
  test('o que o wizard grava conta como dia inteiro', () => {
    assert.equal(ehDiaInteiro('2026-08-09T03:00:00Z', '2026-08-10T03:00:00Z', SP), true)
  })

  test('reserva parcial não é dia inteiro', () => {
    assert.equal(ehDiaInteiro('2026-08-09T19:00:00Z', '2026-08-09T23:00:00Z', SP), false)
  })

  test('dia inteiro medido no fuso errado deixa de ser dia inteiro', () => {
    assert.equal(ehDiaInteiro('2026-08-09T03:00:00Z', '2026-08-10T03:00:00Z', 'UTC'), false)
  })

  test('DST: dia de 23h continua sendo dia inteiro', () => {
    assert.equal(ehDiaInteiro('2026-03-08T05:00:00Z', '2026-03-09T04:00:00Z', NY), true)
  })

  test('DST: dia de 25h continua sendo dia inteiro', () => {
    assert.equal(ehDiaInteiro('2026-11-01T04:00:00Z', '2026-11-02T05:00:00Z', NY), true)
  })

  test('DST: 24h cravadas NÃO são dia inteiro no dia da transição', () => {
    assert.equal(ehDiaInteiro('2026-03-08T05:00:00Z', '2026-03-09T05:00:00Z', NY), false)
    assert.equal(ehDiaInteiro('2026-11-01T04:00:00Z', '2026-11-02T04:00:00Z', NY), false)
  })
})

describe('período impresso no cartão', () => {
  test('dia inteiro sai rotulado, sem horas', () => {
    assert.equal(
      formatarPeriodo('2026-08-09T03:00:00Z', '2026-08-10T03:00:00Z', SP),
      'dom., 09/08 · dia inteiro',
    )
  })

  test('reserva dentro do mesmo dia sai como faixa de horas', () => {
    assert.equal(
      formatarPeriodo('2026-08-09T19:00:00Z', '2026-08-09T23:00:00Z', SP),
      'dom., 09/08 · 16:00–20:00',
    )
  })

  test('reserva que cruza a meia-noite mostra os dois dias', () => {
    assert.equal(
      formatarPeriodo('2026-08-09T22:00:00Z', '2026-08-10T04:00:00Z', SP),
      'dom., 09/08 19:00 – seg., 10/08 01:00',
    )
  })
})

describe('reserva vencida olha o FIM, não o início', () => {
  const HOJE_MEIO_DIA = '2026-08-03T15:00:00Z'

  test('dia inteiro de hoje ainda NÃO passou — o síndico ainda decide', () => {
    assert.equal(jaPassou('2026-08-04T03:00:00Z', HOJE_MEIO_DIA), false)
  })

  test('dia inteiro de ontem já passou', () => {
    assert.equal(jaPassou('2026-08-03T03:00:00Z', HOJE_MEIO_DIA), true)
  })

  test('reserva parcial em andamento não é vencida', () => {
    assert.equal(jaPassou('2026-08-03T23:00:00Z', HOJE_MEIO_DIA), false)
  })

  test('compara instantes, não textos', () => {
    assert.equal(jaPassou('2026-08-01T12:00:00Z', '2026-08-02T00:00:00Z'), true)
    assert.equal(jaPassou('2026-08-03T12:00:00Z', '2026-08-02T00:00:00Z'), false)
  })
})

describe('telefone do solicitante', () => {
  test('treze dígitos com nono dígito', () => {
    assert.equal(formatarTelefone('5551999990001'), '+55 (51) 99999-0001')
  })

  test('doze dígitos, como chega do contact.number', () => {
    assert.equal(formatarTelefone('555192372732'), '+55 (51) 9237-2732')
  })

  test('formato desconhecido volta cru, sem quebrar', () => {
    assert.equal(formatarTelefone('123'), '123')
  })

  test('ausência de telefone é dita, não escondida', () => {
    assert.equal(formatarTelefone(null), 'sem telefone')
  })
})
