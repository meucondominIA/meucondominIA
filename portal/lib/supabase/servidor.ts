import 'server-only'

import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'

export async function clienteServidor() {
  const jar = await cookies()

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll() {
          return jar.getAll()
        },
        setAll(paraGravar) {
          try {
            for (const { name, value, options } of paraGravar) {
              jar.set(name, value, options)
            }
          } catch {
            // Server Component não grava cookie. Quem grava é o proxy.ts.
          }
        },
      },
    },
  )
}
