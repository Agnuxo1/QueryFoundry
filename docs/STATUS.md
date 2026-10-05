# Factual release status - 5 October 2026

Formal Kaggle submission completed and verified as Submitted on 5 October 2026. reports/kaggle-submission.json records the actual state, public URL, attachment verification and immutable source commit 32c283f32fe35e9b5bd0e538bd6631f305863fb3. No organizer score or ranking is claimed.

- 32 original files preserved. Primary payload_once with optional recovery; lazy_keys experimental.
- Final nine 100k GUI/SSH/Linux runs: medians original 16.045867 s, payload_once 15.447215 s, lazy_keys 12.792425 s; six outputs equivalent in every run. n=3 exploratory; resource observer disabled, peaks unavailable.
- Historical 1M pilot: n=1 per variant, all six outputs equivalent. No 300M validation.
- Actual SSH cuts at three boundaries recovered; clones removed, official source unchanged.
- Independent recovery audit completed and fixes reproduced. Two multi-error SQLSTATE differences remain unresolved for lazy_keys; no promotion to default.
- Disk sampler calibration and overhead control retained; no exact global peak or fixed observer overhead established.
- Signed-in deadline reverified: 12 October 2026, 04:59 Madrid. Rules previously accepted.

Consolidated report: WRITEUP.md. Reproduction: REPRODUCIBILITY.md. Historical and failed experiments retain their scope. Future research is not represented as implemented.
