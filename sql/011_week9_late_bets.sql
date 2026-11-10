-- 2026-11-10: seven Week 9 bets Ryan placed but never sent to ntfy, added after the games and graded from the
-- 2026-11-10 dump with logger.grading (same math as the poller). received_at is the time they were added, not placed;
-- note says so. No alert linked (Ryan did not name one). Book MyBookie unless Ryan corrects it.
-- Also: price fixes from logger/prices.py (American prices across even money): bet 18's consensus close was stored as
-- -2 (median of +100 and -105), bets 23 and 37 had CLV of 205-220 cents that were really 5-20.

insert into bets (received_at,raw_text,event_id,matchup,kickoff,market,side,line,price,stake,tag,status,profit,result_units,settled_at,graded_at,note,close_seen_at,close_mb_point,close_mb_price,close_cons_point,close_cons_price,clv_points,clv_cents,clv_mb_points,clv_mb_cents) values
  (now(), 'Buf -3 (-115)', '17ad7eff2f7875e20a8109abe40470e3', 'Buffalo Bills @ Minnesota Vikings', '2026-11-10T01:15:00+00:00', 'spreads', 'Buffalo Bills', -3, -115, 5, 'own', 'won', 4.35, 0.87, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-10T01:11:26.362216+00:00', -3.0, -125, -3.25, -113, 0.25, null, 0.0, 10),
  (now(), 'GB +3 (-105)', '2922c573ec3df8466c4a34975e446898', 'Green Bay Packers @ New England Patriots', '2026-11-08T21:25:00+00:00', 'spreads', 'Green Bay Packers', 3, -105, 5, 'own', 'won', 4.76, 0.952, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-08T20:31:07.481196+00:00', 3.0, 100, 3.0, -102, 0.0, -3, 0.0, -5),
  (now(), 'Packers/Patriots Under 45 (-110)', '2922c573ec3df8466c4a34975e446898', 'Green Bay Packers @ New England Patriots', '2026-11-08T21:25:00+00:00', 'totals', 'Under', 45, -110, 5, 'own', 'lost', -5.0, -1.0, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-08T20:51:25.289532+00:00', 44.5, -110, 44.75, -110, 0.25, null, 0.5, null),
  (now(), 'Rams -5.5 (-110)', '89de07377e0aa8ffaa2ae6a7dc0855cc', 'Los Angeles Rams @ Washington Commanders', '2026-11-08T18:00:00+00:00', 'spreads', 'Los Angeles Rams', -5.5, -110, 5, 'own', 'won', 4.55, 0.91, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-08T17:38:50.316087+00:00', -5.0, -110, -4.5, -103, -1.0, null, -0.5, null),
  (now(), 'KC -9.5 (-123)', 'e354e6a04b1cb9c96e549b863debb92e', 'New York Jets @ Kansas City Chiefs', '2026-11-08T18:00:00+00:00', 'spreads', 'Kansas City Chiefs', -9.5, -123, 5, 'own', 'won', 4.07, 0.814, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-08T17:38:50.316087+00:00', -9.5, -110, -9.5, -110, 0.0, -13, 0.0, -13),
  (now(), 'Chi -6 (-110)', '9760d8f19f84bfee300a74ceb656c95d', 'Tampa Bay Buccaneers @ Chicago Bears', '2026-11-09T01:20:00+00:00', 'spreads', 'Chicago Bears', -6, -110, 5, 'own', 'lost', -5.0, -1.0, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-09T01:05:16.804172+00:00', -6.0, -110, -6.5, -113, 0.5, null, 0.0, 0),
  (now(), 'Cin -3 (-110)', 'dc56d980f527ffeef89940133b211b13', 'Cincinnati Bengals @ Atlanta Falcons', '2026-11-08T14:30:00+00:00', 'spreads', 'Cincinnati Bengals', -3, -110, 5, 'own', 'won', 4.55, 0.91, now(), now(), 'added 2026-11-10 after the games (placed before kickoff, not sent to ntfy); graded from the 2026-11-10 dump', '2026-11-08T13:45:06.795262+00:00', -3.5, -110, -3.25, -110, 0.25, null, 0.5, null);

update bets set close_cons_price = -102 where id = 18 and close_cons_price = -2;
update bets set clv_cents = 9, clv_mb_cents = 20 where id = 23 and clv_cents = 209;
update bets set clv_cents = 5 where id = 37 and clv_cents = 205;

select id, raw_text, status, profit, clv_points, clv_cents, clv_mb_points, clv_mb_cents from bets
 where id in (18, 23, 37) or note like 'added 2026-11-10%' order by id;
