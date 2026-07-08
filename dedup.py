from enum import Enum

import asyncpg


class StatusEvento(str, Enum):
    PENDENTE = "pendente"
    PROCESSADO = "processado"
    FALHOU = "falhou"


async def registrar_mensagem(
    conn: asyncpg.Connection, message_id: str, payload: dict
) -> bool:
    row = await conn.fetchrow(
        """
        insert into webhook_events (message_id, payload)
        values ($1, $2)
        on conflict (message_id) do nothing
        returning message_id
        """,
        message_id,
        payload,
    )
    return row is not None


async def marcar_status(
    conn: asyncpg.Connection, message_id: str, status: StatusEvento
) -> None:
    await conn.execute(
        """
        update webhook_events
        set status = $1, processado_em = now()
        where message_id = $2
        """,
        status.value,
        message_id,
    )
