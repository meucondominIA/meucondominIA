import 'server-only'

import { clienteServidor } from '@/lib/supabase/servidor'

const COLUNAS =
  'id, inicio, fim, status, telefone, observacao, condominio_id, areas_comuns(nome)'

const DECIDIDAS = ['aprovada', 'recusada']
const TETO_PENDENTES = 200
const ULTIMAS_DECISOES = 5

export type Reserva = {
  id: string
  inicio: string
  fim: string
  status: string
  telefone: string | null
  observacao: string | null
  condominio_id: string
  areas_comuns: { nome: string } | null
}

export type Condominio = {
  id: string
  nome: string
  timezone: string
}

export type EstadoPainel =
  | { tipo: 'sem_vinculo' }
  | { tipo: 'erro'; codigo: string }
  | { tipo: 'ok'; condominio: Condominio; pendentes: Reserva[]; decididas: Reserva[] }

export async function carregarPainel(): Promise<EstadoPainel> {
  const supabase = await clienteServidor()

  const [condominios, pendentes, decididas] = await Promise.all([
    supabase.from('condominios').select('id, nome, timezone').overrideTypes<Condominio[], { merge: false }>(),
    supabase
      .from('reservas')
      .select(COLUNAS)
      .eq('status', 'pendente')
      .order('inicio', { ascending: true })
      .order('created_at', { ascending: true })
      .order('id', { ascending: true })
      .limit(TETO_PENDENTES)
      .overrideTypes<Reserva[], { merge: false }>(),
    supabase
      .from('reservas')
      .select(COLUNAS)
      .in('status', DECIDIDAS)
      // Ordenado sem ser selecionado: como texto mentiria ("última alteração",
      // não "decisão"), mas é a única chave que põe no topo o cartão recém-
      // decidido. Apagar não quebra build nem teste — só reembaralha, calado.
      .order('updated_at', { ascending: false })
      .order('id', { ascending: true })
      .limit(ULTIMAS_DECISOES)
      .overrideTypes<Reserva[], { merge: false }>(),
  ])

  const falha = condominios.error ?? pendentes.error ?? decididas.error
  if (falha) {
    console.error('[painel] leitura falhou:', falha.code, falha.message)
    return { tipo: 'erro', codigo: falha.code ?? 'desconhecido' }
  }

  const condominio = condominios.data?.[0]
  if (!condominio) {
    return { tipo: 'sem_vinculo' }
  }

  const linhas = [...(pendentes.data ?? []), ...(decididas.data ?? [])]
  conferirTenant(linhas, condominio.id)
  conferirArea(linhas)

  return {
    tipo: 'ok',
    condominio,
    pendentes: pendentes.data ?? [],
    decididas: decididas.data ?? [],
  }
}

function conferirTenant(linhas: Reserva[], esperado: string): void {
  const intrusas = linhas.filter((r) => r.condominio_id !== esperado)
  if (intrusas.length > 0) {
    console.error(
      '[painel] RLS FUROU: %d reserva(s) de outro condomínio; esperado %s, veio %s',
      intrusas.length,
      esperado,
      [...new Set(intrusas.map((r) => r.condominio_id))].join(', '),
    )
  }
}

function conferirArea(linhas: Reserva[]): void {
  const semArea = linhas.filter((r) => r.areas_comuns === null)
  if (semArea.length > 0) {
    console.error(
      '[painel] área nula em %d reserva(s) — bug de schema, não de RLS: %s',
      semArea.length,
      semArea.map((r) => r.id).join(', '),
    )
  }
}
