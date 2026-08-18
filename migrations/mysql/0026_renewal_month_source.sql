-- 0026: preserve whether the renewal month follows the join date or is a manual override.
ALTER TABLE members ADD COLUMN renewal_month_overridden BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE members
SET renewal_month_overridden=CASE
  WHEN renewal_month IS NULL OR TRIM(renewal_month) = '' THEN FALSE
  WHEN join_date IS NOT NULL AND LEFT(renewal_month, 7)=LEFT(join_date, 7) THEN FALSE
  ELSE TRUE
END;
