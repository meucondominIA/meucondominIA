import { createServerClient, type CookieOptions } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'

type CookieParaGravar = { name: string; value: string; options: CookieOptions }

const ROTAS_PUBLICAS = ['/entrar']

const SEM_CACHE: Record<string, string> = {
  'Cache-Control': 'private, no-cache, no-store, must-revalidate, max-age=0',
  Expires: '0',
  Pragma: 'no-cache',
}

export async function proxy(request: NextRequest) {
  const cookiesRenovados: CookieParaGravar[] = []
  let cabecalhosDaSessao: Record<string, string> = {}

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll()
        },
        setAll(paraGravar, cabecalhos) {
          cabecalhosDaSessao = cabecalhos
          for (const cookie of paraGravar) {
            request.cookies.set(cookie.name, cookie.value)
            cookiesRenovados.push(cookie)
          }
        },
      },
    },
  )

  const { data } = await supabase.auth.getClaims()
  const autenticado = Boolean(data?.claims)
  const caminho = request.nextUrl.pathname
  const publica = ROTAS_PUBLICAS.some((rota) => caminho.startsWith(rota))

  let response: NextResponse

  if (!autenticado && !publica) {
    const destino = request.nextUrl.clone()
    destino.pathname = '/entrar'
    response = NextResponse.redirect(destino)
  } else if (autenticado && publica) {
    const destino = request.nextUrl.clone()
    destino.pathname = '/painel'
    response = NextResponse.redirect(destino)
  } else {
    response = NextResponse.next({ request })
  }

  for (const { name, value, options } of cookiesRenovados) {
    response.cookies.set(name, value, options)
  }

  for (const [chave, valor] of Object.entries({ ...SEM_CACHE, ...cabecalhosDaSessao })) {
    response.headers.set(chave, valor)
  }

  return response
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
