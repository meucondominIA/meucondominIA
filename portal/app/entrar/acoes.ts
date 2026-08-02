'use server'

import { redirect } from 'next/navigation'

import { clienteServidor } from '@/lib/supabase/servidor'

export async function entrar(_anterior: string | null, formulario: FormData) {
  const email = String(formulario.get('email') ?? '').trim()
  const senha = String(formulario.get('senha') ?? '')

  if (!email || !senha) {
    return 'Preencha e-mail e senha.'
  }

  const supabase = await clienteServidor()
  const { error } = await supabase.auth.signInWithPassword({ email, password: senha })

  if (error) {
    console.warn('[entrar] login recusado:', error.code ?? error.name, `(${error.status})`)
    return 'E-mail ou senha incorretos.'
  }

  redirect('/painel')
}
