async def registrar_mensagem(conn, message_id: str) -> bool:
    row = await conn.fetchrow(
        """
        insert into webhook_events (message_id)
        values ($1)
        on conflict (message_id) do nothing
        returning message_id
        """,
        message_id,
    )
    return row is not None