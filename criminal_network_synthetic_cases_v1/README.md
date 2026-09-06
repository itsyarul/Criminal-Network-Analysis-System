# Synthetic Criminal Network Cases v1

Three independent synthetic multi-source investigation cases with a common schema.
The data are fictional and contain no real persons or police records.

Each case contains:
people_directory.csv, alias_map.csv, firs.csv, cdrs.csv, transactions.csv,
social_posts.csv, intelligence_reports.csv, surveillance_reports.csv, criminal_history.csv.

Ground truth is stored separately and must NEVER be supplied to the inference pipeline.
Recommended: develop on CASE_01/CASE_02 and hold CASE_03 out for final evaluation.
The same schemas across cases are intentional; network structures, people, anomalies,
burst days and noise differ so the pipeline cannot rely on a single fixed pattern.
