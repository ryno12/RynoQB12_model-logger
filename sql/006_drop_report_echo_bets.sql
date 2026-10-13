-- 2026-10-13: the first weekly-report pushes were not recognised as the logger's own messages, so bet capture
-- parsed them, failed, and saved them for review (bets rows 9+). Fixed in logger/bets.py (is_system); remove the echoes.
delete from bets where status = 'needs_review' and raw_text like 'Bets %';
select id, status, raw_text from bets order by id;
