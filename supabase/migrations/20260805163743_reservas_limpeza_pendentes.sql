-- Reserva automática · o fim do estado 'pendente' como coisa viva.
--
-- A partir desta etapa a reserva NASCE 'aprovada' e ninguém mais produz
-- 'pendente'. As linhas que sobraram não são inofensivas: elas ocupam o dia em
-- dias_livres E no NOT EXISTS da escrita, e "Minhas reservas" filtra
-- status='aprovada' — ou seja, ficariam invisíveis para o morador e
-- incanceláveis para sempre. Ninguém no produto tem como tirá-las de lá.
--
-- DELETE e não UPDATE para 'cancelada' (decidido pelo dono em 05/08/2026): todos
-- os dados são de teste. O efeito colateral está medido e é aceito —
-- avisos_sindico_reserva_id_fkey é ON DELETE CASCADE, então os avisos já
-- enviados dessas reservas somem junto (verificado no container: avisos 1 -> 0).
--
-- 'pendente' CONTINUA no reservas_status_check de propósito: custo zero, e é a
-- porta de volta se a aprovação ressuscitar algum dia. O que morre é a produção
-- do valor, não o valor.
--
-- Em banco novo isto apaga zero linhas — a tabela nasce vazia. É one-shot por
-- natureza, não por guarda.

delete from public.reservas where status = 'pendente';

-- ── DOWN ─────────────────────────────────────────────────────────────────────
-- Não existe. As linhas apagadas não são reconstrutíveis a partir de nada que
-- reste no banco: o vínculo com a mensagem de origem some junto (o índice era
-- uq_reservas_origem_mensagem, na própria linha apagada). Registrar a ausência é
-- mais honesto do que fingir um desfazer.
