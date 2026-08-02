import { redirect } from 'next/navigation'

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

  return (
    <main>
      <h1>Painel do síndico</h1>
      <p>Sessão validada.</p>

      <dl>
        <dt>identificador</dt>
        <dd>
          <code>{claims.sub}</code>
        </dd>
        <dt>assinatura</dt>
        <dd>ES256, verificada localmente</dd>
      </dl>

      <p className="nota">A lista de reservas chega na Etapa 4.</p>

      <form action={sair}>
        <button type="submit" className="secundario">
          Sair
        </button>
      </form>
    </main>
  )
}
