const LOCAL = 'pt-BR'

type PartesLocais = { data: string; hora: string }

function partesLocais(iso: string, tz: string): PartesLocais {
  const partes = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(iso))

  const p: Record<string, string> = {}
  for (const parte of partes) p[parte.type] = parte.value

  return { data: `${p.year}-${p.month}-${p.day}`, hora: `${p.hour}:${p.minute}:${p.second}` }
}

function diaSeguinte(data: string): string {
  const d = new Date(`${data}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 10)
}

export function ehDiaInteiro(inicioISO: string, fimISO: string, tz: string): boolean {
  const inicio = partesLocais(inicioISO, tz)
  const fim = partesLocais(fimISO, tz)

  return (
    inicio.hora === '00:00:00' && fim.hora === '00:00:00' && fim.data === diaSeguinte(inicio.data)
  )
}

export function formatarDia(iso: string, tz: string): string {
  return new Intl.DateTimeFormat(LOCAL, {
    weekday: 'short',
    day: '2-digit',
    month: '2-digit',
    timeZone: tz,
  }).format(new Date(iso))
}

export function formatarHora(iso: string, tz: string): string {
  return new Intl.DateTimeFormat(LOCAL, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: tz,
  }).format(new Date(iso))
}

export function formatarPeriodo(inicioISO: string, fimISO: string, tz: string): string {
  if (ehDiaInteiro(inicioISO, fimISO, tz)) {
    return `${formatarDia(inicioISO, tz)} · dia inteiro`
  }

  if (partesLocais(inicioISO, tz).data === partesLocais(fimISO, tz).data) {
    return `${formatarDia(inicioISO, tz)} · ${formatarHora(inicioISO, tz)}–${formatarHora(fimISO, tz)}`
  }

  return `${formatarDia(inicioISO, tz)} ${formatarHora(inicioISO, tz)} – ${formatarDia(fimISO, tz)} ${formatarHora(fimISO, tz)}`
}

export function jaPassou(fimISO: string, agoraISO: string): boolean {
  return new Date(fimISO).getTime() <= new Date(agoraISO).getTime()
}

export function formatarTelefone(telefone: string | null): string {
  if (!telefone) return 'sem telefone'

  const digitos = telefone.replace(/\D/g, '')
  const corpo = digitos.startsWith('55') ? digitos.slice(2) : digitos

  if (corpo.length === 11) {
    return `+55 (${corpo.slice(0, 2)}) ${corpo.slice(2, 7)}-${corpo.slice(7)}`
  }
  if (corpo.length === 10) {
    return `+55 (${corpo.slice(0, 2)}) ${corpo.slice(2, 6)}-${corpo.slice(6)}`
  }

  return telefone
}
