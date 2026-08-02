'use server'

import { redirect } from 'next/navigation'

import { clienteServidor } from '@/lib/supabase/servidor'

export async function sair() {
  const supabase = await clienteServidor()
  await supabase.auth.signOut()

  redirect('/entrar')
}
