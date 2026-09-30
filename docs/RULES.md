# Verified competition contract

Source: https://www.kaggle.com/competitions/python-database-performance-optimization/overview
Read from the live page on 2026-09-30 (Europe/Madrid). The page displayed
"12 days to go"; an exact deadline/time has not been verified.

- Performance: 60 points. Official metric: `MEASURED PROCESSING TOTAL` from
  the application's Expansion Performance Report. Organizers reproduce results.
- Recovery and consistency: 20 points. Resource efficiency: 20 points.
- Windows Manager, Linux PostgreSQL and SSH must remain available.
- First reproduce the original 100,000-row GUI baseline before promoting a change.
- Inputs must come from the unchanged official generator and fixed seed.
- Preserve validation, relational outputs, version registration, dump/version
  behavior and traceability. Removing checks is ineligible.
- Keep the six destinations in this order: table3, table4, table1, table5,
  table6, table2.
- Deliver a submitted Kaggle Writeup, public Git repository and exact commit.
- Target of 300 million rows is a scaling objective, not evidence of local capacity.

The live page contains both `kaggle_postgresql_challenge` and
`kaggle_postgre_challenge` spellings. The working source repository, linked by
the host and actually cloned, is https://github.com/igorsiecz/kaggle_postgre_challenge.

Local SQL diagnostics do not constitute an official baseline or score.
