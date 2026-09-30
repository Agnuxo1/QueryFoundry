# Verified competition contract

Source: https://www.kaggle.com/competitions/python-database-performance-optimization/overview
Read from the live page on 2026-09-30 (Europe/Madrid). The page displayed
"12 days to go". Rechecked in the authenticated live browser on 2026-09-30:
**final submission deadline 2026-10-12 04:59 Europe/Madrid (UTC+02:00),
equivalent to 2026-10-12 02:59 UTC**. The Overview submission notice explicitly
displays Oct12 at4:59AM GMT+2; the Close tooltip agrees. Dates may be changed by
the host, so recheck before submission.

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

## Participation and submission check — 2026-09-30

The live Rules tab confirms the signed-in account has already accepted the
competition rules. No acceptance or enrollment action was performed in this check.
Overview says no Writeup has been created yet; no accepted submission is established.
Maximum team size: five participants. One final submission per team, formally
submitted through Kaggle; drafts are ineligible. Public repository plus exact
commit are required before the deadline. Apache-2.0 satisfies the stated winner
license. AI coding assistants are permitted; they do not replace participant
responsibility or become separate Kaggle team members.

Source: https://www.kaggle.com/competitions/python-database-performance-optimization/rules
The specific rules reiterate the fixed generator, intact benchmark inputs,
equivalent validation/versioning and fair complete-workflow timing. Functional
mutation fixtures must be labelled separately from official benchmark workloads.
Development dependencies must be disclosed and reproducible; the submitted
runtime does not require Claude or JEV services.
