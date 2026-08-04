import { redirect } from 'next/navigation'

import { carregarPainel, type Condominio, type Reserva } from '@/lib/consultas'
import { formatarPeriodo, formatarTelefone, jaPassou } from '@/lib/formato'
import { clienteServidor } from '@/lib/supabase/servidor'

import { sair } from './acoes'

export const dynamic = 'force-dynamic'

export default async function Painel() {
  const supabase = await clienteServidor()
  const { data, error } = await supabase.auth.getClaims()
  const claims = data?.claims

  if (error) {
    console.warn('[painel] sessao invalida:', error.name)
    redirect('/entrar')
  }

  if (!claims) {
    console.warn('[painel] sem sessao')
    redirect('/entrar')
  }

  const painel = await carregarPainel()
  const agora = new Date().toISOString()

  return (
    <main>
      <header>
        <h1>{painel.tipo === 'ok' ? painel.condominio.nome : 'Portal do síndico'}</h1>
        <form action={sair}>
          <button type="submit" className="secundario">
            Sair
          </button>
        </form>
      </header>

      {painel.tipo === 'sem_vinculo' ? (
        <p role="alert">
          Sua conta não está vinculada a nenhum condomínio. Procure o administrador com este
          identificador: <code>{String(claims.sub)}</code>
        </p>
      ) : null}

      {painel.tipo === 'erro' ? (
        <p role="alert">
          Não foi possível carregar as reservas (código {painel.codigo}). Recarregue a página;
          se persistir, avise o administrador.
        </p>
      ) : null}

      {painel.tipo === 'ok' ? (
        <>
          <section>
            <h2>Pendentes ({painel.pendentes.length})</h2>
            {painel.pendentes.length === 0 ? (
              <p className="nota">Nenhuma reserva aguardando decisão.</p>
            ) : (
              <ul>
                {painel.pendentes.map((reserva) => (
                  <Cartao
                    key={reserva.id}
                    reserva={reserva}
                    condominio={painel.condominio}
                    agora={agora}
                  />
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2>Últimas decisões</h2>
            {painel.decididas.length === 0 ? (
              <p className="nota">Nenhuma reserva decidida ainda.</p>
            ) : (
              <ul>
                {painel.decididas.map((reserva) => (
                  <Cartao
                    key={reserva.id}
                    reserva={reserva}
                    condominio={painel.condominio}
                    agora={agora}
                    decidida
                  />
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </main>
  )
}

function Cartao({
  reserva,
  condominio,
  agora,
  decidida = false,
}: {
  reserva: Reserva
  condominio: Condominio
  agora: string
  decidida?: boolean
}) {
  const tz = condominio.timezone
  const vencida = !decidida && jaPassou(reserva.fim, agora)

  return (
    <li className="cartao">
      <p className="area">
        {reserva.areas_comuns?.nome ?? 'Área desconhecida'}
        {decidida ? <span className="selo">{reserva.status}</span> : null}
      </p>

      <p>
        {formatarPeriodo(reserva.inicio, reserva.fim, tz)}
        {vencida ? <span className="selo alerta">já passou</span> : null}
      </p>

      {decidida ? null : <p className="nota">{formatarTelefone(reserva.telefone)}</p>}

      {reserva.observacao ? <p className="nota">{reserva.observacao}</p> : null}
    </li>
  )
}
