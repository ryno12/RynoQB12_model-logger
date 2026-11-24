-- 2026-11-24: Ryan confirmed each of these was ONE bet, sent to ntfy more than once.
--   Texans/Browns under 40: #47 (sent 11-11, right after the Model edge push) kept; #54 (re-send 11-12) deleted.
--   Bengals -6.5: #60 kept; #63 (re-send) deleted.
--   Panthers ML: #56 ("Panthers -0", graded won) kept; #50 and #55 (unparsed re-sends) deleted.
--   Giants/Commanders over 40.5: #57 kept and graded; #58 (re-send after the game) deleted.
-- #57 was sent 6:11 PM MT, 4 min before the Thu Nov 12 kickoff; the poll that read it ran after kickoff, when the game
-- was no longer "upcoming", so it sat in needs_review (matcher now uses the message time, logger/bets.py).
-- Graded from the 2026-11-24 dump with logger.grading: WAS 24 @ NYG 21 = 45 > 40.5, won; MB close 40.5 -110,
-- consensus close 40.75 -111.

delete from bets where id in (50, 54, 55, 58, 63);

update bets set
  event_id = '74d39e19225caf211ad2c8eb4110179f', matchup = 'Washington Commanders @ New York Giants',
  kickoff = '2026-11-13T01:15:00+00:00', market = 'totals', side = 'Over', line = 40.5, price = -110, stake = 5,
  status = 'won', profit = 4.55, result_units = 0.91, settled_at = now(), graded_at = now(),
  close_seen_at = '2026-11-13T00:35:43.194519+00:00', close_mb_point = 40.5, close_mb_price = -110,
  close_cons_point = 40.75, close_cons_price = -111, clv_points = 0.25, clv_cents = null,
  clv_mb_points = 0.0, clv_mb_cents = 0,
  note = 'sent 4 min before kickoff, read after it; matched and graded by sql/013 (2026-11-24)'
where id = 57 and status = 'needs_review';

select id, raw_text, status, profit, clv_points, clv_mb_points from bets where id in (47, 56, 57, 60) order by id;
