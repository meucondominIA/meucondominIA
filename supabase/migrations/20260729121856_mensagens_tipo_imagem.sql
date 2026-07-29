-- Fase 4 · Etapa 4: 'image' vira um tipo de mensagem de verdade.
--
-- Até aqui MessageType tinha dois valores e o CHECK os espelhava: 'text' e
-- 'unsupported' (o balde de áudio/imagem/documento, tudo que a gente sabia
-- receber e não sabia usar). A foto da ocorrência tira a imagem desse balde.
--
-- Verificado no payload REAL do Z-PRO em 28/07/2026 (endpoint-armadilha + duas
-- fotos por WhatsApp): a mídia chega DECIFRADA e inline em msg.base64, e o
-- sha256 do conteúdo bate com o imageMessage.fileSha256 declarado pelo WhatsApp.
-- Não há download, não há mediaKey a usar — por isso 'image' pode ser um tipo
-- que o sistema PROCESSA, e não só um que ele reconhece e recusa.
--
-- Alargamento puro: nenhuma linha existente pode ficar inválida (os dois valores
-- antigos continuam aceitos). O scan da recriação prova.
alter table public.mensagens drop constraint mensagens_tipo_check;
alter table public.mensagens
  add constraint mensagens_tipo_check
    check (tipo in ('text', 'image', 'unsupported'));

-- ── DOWN ─────────────────────────────────────────────────────────────────────
-- Precisa da limpeza: mensagens gravadas como 'image' voltam ao balde de onde
-- saíram, senão o scan da recriação recusa. 'unsupported' é o destino honesto —
-- é literalmente o que elas seriam se esta migration nunca tivesse existido.
--
-- update public.mensagens set tipo = 'unsupported' where tipo = 'image';
-- alter table public.mensagens drop constraint mensagens_tipo_check;
-- alter table public.mensagens
--   add constraint mensagens_tipo_check
--     check (tipo in ('text', 'unsupported'));
