'use client'

import { useActionState } from 'react'

import { entrar } from './acoes'

export default function Entrar() {
  const [erro, acao, pendente] = useActionState(entrar, null)

  return (
    <main>
      <h1>Portal do síndico</h1>

      <form action={acao}>
        <label htmlFor="email">E-mail</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          autoCapitalize="none"
          required
        />

        <label htmlFor="senha">Senha</label>
        <input
          id="senha"
          name="senha"
          type="password"
          autoComplete="current-password"
          required
        />

        <button type="submit" disabled={pendente}>
          {pendente ? 'Entrando…' : 'Entrar'}
        </button>
      </form>

      {erro ? <p role="alert">{erro}</p> : null}
    </main>
  )
}
