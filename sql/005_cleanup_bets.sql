-- One-off cleanup Ryan asked for on 2026-10-13: two test messages (1, 6), the duplicate Baltimore row (3) and the
-- duplicate Seattle repost (7). Rows 4 (BAL -4.5, Week 6) and 8 (SEA -1.5, Thu Oct 15) stay. Idempotent.
delete from bets where id in (1, 3, 6, 7);
select id, status, raw_text, event_id is not null as matched from bets order by id;
