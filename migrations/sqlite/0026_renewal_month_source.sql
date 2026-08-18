-- 0026: preserve whether the renewal month follows the join date or is a manual override.
ALTER TABLE members ADD COLUMN renewal_month_overridden INTEGER NOT NULL DEFAULT 0;
UPDATE members
SET renewal_month_overridden=CASE
  WHEN renewal_month IS NULL OR TRIM(renewal_month) = '' THEN 0
  WHEN join_date IS NOT NULL AND SUBSTR(renewal_month, 1, 7)=SUBSTR(join_date, 1, 7) THEN 0
  ELSE 1
END;
