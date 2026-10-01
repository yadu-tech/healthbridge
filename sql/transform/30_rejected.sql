-- Step 3: keep a record of every row that did not make it into the fact table, and why.
INSERT INTO core.rejected_record (run_id, source, concept, source_file, row_num, reason, detail)
SELECT %(run_id)s, source, concept, source_file, row_num, reject_reason,
       jsonb_build_object('iso3', iso3, 'year', year, 'value', value, 'value_text', value_text,
                          'sex_raw', sex_raw, 'wealth_raw', wealth_raw, 'residence_raw', residence_raw)
FROM core_classified
WHERE reject_reason IS NOT NULL
