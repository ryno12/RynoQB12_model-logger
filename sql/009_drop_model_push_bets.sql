-- 2026-10-19: the "Model vs MyBookie" push was not recognised as the logger's own message, so bet capture read its
-- "Over" lines as bets: row 11 (Oct 13, CHI@ATL Over 40.5, graded won), row 14 (Oct 14, PIT@TB Over 39.5, graded won,
-- a duplicate of Ryan's real row 13) and row 20 (Oct 19, MIA@NYJ Over 39.5, open). None were Ryan's. Fixed in
-- logger/bets.py (title + system tags). The raw_text guard keeps this from touching anything Ryan typed.
delete from bets
 where id in (11, 14, 20)
   and (raw_text like '%model says%' or raw_text like '%fair %vs MB%');

-- Row 19 "Dal pk (-104) $5": "pk" was not parsed, so the line came from linked alert 124 (Dallas +1). Ryan bet a
-- pick'em. Result unchanged (Dallas lost 13-23); CLV is one point lower: 0 - (-1.25) and 0 - (-1).
update bets set line = 0, clv_points = 1.25, clv_mb_points = 1.0
 where id = 19 and raw_text = 'Dal pk (-104) $5' and line = 1;

select id, raw_text, status, side, line, price, profit, clv_points from bets order by id;
