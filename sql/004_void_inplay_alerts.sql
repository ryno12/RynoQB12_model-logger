-- Week 5 cleanup (2026-10-13). Until the Oct 13 fix, alerts could fire on in-play lines after kickoff; the first
-- grading pass scored 19 of them as if they were pre-game bets. Void them, as logger/grading.py now does, so alert
-- stats only count lines that could have been bet before the game. Idempotent.

update alerts a
   set result = 'void', profit = 0
  from games g
 where g.id = a.event_id
   and a.graded_at is not null
   and a.sent_at >= g.commence_time
   and a.result is distinct from 'void';
