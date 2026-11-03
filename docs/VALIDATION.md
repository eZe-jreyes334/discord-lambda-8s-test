# Validation record: CoD 8s rating overhaul

Recorded November 3, 2026. Source fork snapshot: `8715e2c8af661cc6469f3343a186b01a8b2c93a4`.
This validation was completed locally before repository publication. No production data was read or live Discord/AWS calls made during the tests.

## Result and interpretation

The central fix is to rank estimated ability instead of an accumulated points account.
Classic TrueSkill itself is a strong baseline: the original bot's custom SR rules were a major source of the grinding incentive.
The implementation selects a joint Gaussian team-probit approximation, with confidence shown separately from rank.

On the held-out synthetic set, the selected model's average log loss is **0.4454**, versus **0.4534** for default TrueSkill, a **1.77%** reduction. Final ability-rank correlation is **0.8868** versus **0.8760**. These are simulation results, not measured improvements in real Call of Duty matches.

The selected dynamics parameter was chosen on separate development seeds. A lower drift of 0.05 performs slightly better on the held-out average; the default was **not retuned after seeing the held-out results**. The selected model also loses to TrueSkill on farming log loss and has lower rank recovery in some scenarios. Its nominal 95% intervals cover 99.4% of simulated final abilities, so its uncertainty is conservative in this experiment, not calibrated to exactly 95%.

## Protocol

- Development: seeds 0-1, 200 matches per schedule, 3 pool sizes, 2 outcome links, 9 scenarios, 8 model configurations: **864 model/schedule runs and 172,800 rating updates**.
- Evaluation: seeds 100-107, 400 matches per schedule with the same grid: **3,456 model/schedule runs and 1,382,400 rating updates**. Each model sees 172,800 held-out outcomes on 432 schedules.
- Pools: 15, 20, and 25. Participation is unequal. Both normal-probit and variance-matched logistic outcomes are generated. Draft scenarios add unmodelled noise.
- Predictions precede each update. Every candidate sees identical rosters and realized outcomes for a given schedule. Roster construction does not use any candidate's estimates.
- Scenarios: random mixing, balanced rosters, noisy captain-style drafts, fixed pods, a persistent carry group, strongest-versus-weakest farming, ten forced early wins, a 28-day return, and changing ability.
- All scenarios receive equal weight. This is a chosen stress-test distribution, not an estimate of the community's actual mix of games.
- The TrueSkill/OpenSkill comparators use their documented defaults. Only the joint dynamics grid and public uncertainty penalty were explored; this is not exhaustive hyperparameter optimization for every model.
- The legacy replica reproduces customized hidden updates, public SR, daily loss forgiveness, placement, fractional rank-band gaps, and inactivity decay. Its prediction metrics use a team probability derived from the hidden ratings, since the original individual SR expectation is not a coherent single match forecast. Its ranking metric uses the actual accumulated/decayed public SR.

## Held-out aggregate results

Lower log loss/Brier is better; higher rank correlation is better. Rank recovery includes all simulated players, including people the UI would mark provisional. Coverage is a diagnostic, not a score to maximize.

| Model | Log loss | Brier | Rank correlation | 95% interval coverage |
|---|---:|---:|---:|---:|
| Legacy replica | 0.4652 | 0.1532 | -0.0277 | 87.2% |
| TrueSkill defaults | 0.4534 | 0.1511 | 0.8760 | 89.2% |
| OpenSkill PL | 0.5074 | 0.1596 | 0.8692 | 86.5% |
| OpenSkill BT full | 0.5074 | 0.1596 | 0.8692 | 86.5% |
| Joint, drift 0 | 0.4444 | 0.1477 | 0.8934 | 97.9% |
| Joint, drift 0.05 | 0.4442 | 0.1477 | 0.8927 | 98.3% |
| Joint, drift 0.25 (selected) | 0.4454 | 0.1482 | 0.8868 | 99.4% |
| Joint, drift 1.0 | 0.4521 | 0.1506 | 0.8686 | 99.5% |

Default TrueSkill and the selected model have accuracies of 77.0% and 77.6%. Their average ten-bin calibration errors are 0.0543 and 0.0517. The eight paired seed-level differences in mean log loss average -0.00804, with standard error 0.00089. Seeds within each scenario share generation structure; matches within a schedule are not independent experiments.

The near-zero/negative legacy public-SR rank recovery is specific to these balanced/unequal-participation synthetic schedules. It is not a claim that the current real leaderboard has negative correlation with player skill.

## Every scenario

| Scenario | Legacy log loss | TrueSkill log loss | Selected log loss | TrueSkill rank correlation | Selected rank correlation |
|---|---:|---:|---:|---:|---:|
| random | 0.4849 | 0.4721 | 0.4674 | 0.9472 | 0.9417 |
| balanced | 0.6333 | 0.6181 | 0.6000 | 0.9444 | 0.9456 |
| draft | 0.6706 | 0.6510 | 0.6369 | 0.9325 | 0.9370 |
| fixed_pods | 0.3888 | 0.3846 | 0.3716 | 0.5920 | 0.6851 |
| carry | 0.4667 | 0.4550 | 0.4410 | 0.7521 | 0.7565 |
| farming | 0.0640 | 0.0657 | 0.0718 | 0.8737 | 0.8848 |
| lucky | 0.5110 | 0.4959 | 0.4936 | 0.9538 | 0.9477 |
| returning | 0.4852 | 0.4721 | 0.4700 | 0.9467 | 0.9359 |
| changing | 0.4819 | 0.4664 | 0.4561 | 0.9416 | 0.9467 |

Fixed-pod rank recovery remains substantially weaker than mixed-team recovery. Keeping covariance preserves unresolved uncertainty; it cannot discover which inseparable teammate deserves credit.

## Public score policy

The public score uses **lambda = 0**, so **SR = max(0, round(350 + 20(mu - 25)))**. Qualified players are sorted by unrounded mu. Rank qualification requires **at least 20 v2 games and sigma <= 6**. Qualification does not modify mu or SR. The eight existing rank bands/icons are preserved.

This table varies only the public uncertainty deduction for the selected model; inference is identical in every row. The broad schedule's rare strong player does not have exactly 25 games, unlike the targeted test below.

| Lambda in mu - lambda sigma | Rank correlation | Rare strong above average grinder | Ability-error/activity correlation |
|---|---:|---:|---:|
| 0 | 0.8868 | 94.2% | 0.0574 |
| 0.25 | 0.8862 | 92.4% | 0.0705 |
| 0.5 | 0.8850 | 89.8% | 0.0834 |
| 1 | 0.8814 | 87.5% | 0.1098 |
| 2 | 0.8673 | 80.3% | 0.1656 |
| 3 | 0.8508 | 72.0% | 0.2218 |

Large uncertainty deductions increase the activity advantage here. Lambda 0 does not make participation irrelevant to statistical precision: more varied results can improve estimates, and qualification has a minimum evidence requirement. Residual-activity values in the raw legacy rows are not comparable to these values because legacy SR and latent skill have different scales; they were not used to select the policy.

## Exact 25-game versus 200-game stress test

Separate seeds 1000-1099 for each pool size give **300 runs**. A true-skill-39 player plays exactly 25 games; a true-skill-25 player plays all 200. Other players have varied strengths and randomized teams. The skill gap is deliberately large, so this does not establish reliability for nearly equal players.

| Pool | Strong player above grinder | Strong mean mu | Grinder mean mu | Strong mean sigma | Ten-win newcomers qualified |
|---|---:|---:|---:|---:|---:|
| 15 | 100/100 | 37.42 | 24.12 | 4.33 | 0/100 |
| 20 | 100/100 | 37.98 | 24.33 | 4.30 | 0/100 |
| 25 | 100/100 | 37.65 | 24.45 | 4.27 | 0/100 |

The strong occasional player qualifies and ranks above the average grinder in **300/300** runs. After each established pool, an average newcomer is given ten forced wins: **0/300 qualify**, but estimated mu is high (about 42). The qualification rule blocks premature leaderboard placement; it does not claim to recognize luck. Twenty wins can also be lucky, and collusion cannot be inferred reliably from outcome-only data.

## Executed checks

| Check | Result |
|---|---|
| Untouched fork baseline tests | 157 passed |
| Final legacy/regression suite, including upstream #84/#85 | 176 passed |
| Real-math and emulated-transaction suite | 54 passed |
| Python 3.9 syntax parse | 28 files passed |
| Optimized rank calculation versus reference | 500 tied/random cases matched |
| Git whitespace/error check | Passed |
| Remote GitHub Actions / real Lambda / real Discord | Not run |

Local runtime: Python 3.12.14. The new suite uses real TrueSkill, boto3 and Moto with fake credentials; it does not connect to live AWS. The inherited suite globally replaces libraries, so the suites run in separate Python processes. Dependency deprecation warnings remain (primarily botocore's utcnow calls); no test failed or was skipped. CI is configured for Python 3.9 and 3.12, but local syntax parsing is not a substitute for running the Python 3.9 CI job.

Coverage includes one-step equivalence to TrueSkill moments, fixed-teammate uncertainty, covariance positivity, extreme upsets, opponent-strength sensitivity, no read-time decay or game-count bonus, provisional/qualification behavior, rank boundaries, result replay, stale buttons, invalid/duplicate rosters, opposite outcomes, concurrent snapshot/queue updates, lost responses, guild isolation, immutable legacy rating fields, premade reservations/cancellation, separate team ratings, participant-only bracket recovery, failed Discord delivery, pagination, and displayed v2 stats.

## Changed areas

| Files | Purpose |
|---|---|
| rating/model.py, rating/__init__.py | Gaussian team model, public scores, confidence, team balancing, explicit mode switch |
| dao/RatingDao.py, core/RatingService.py | Atomic result receipts/snapshots/queue transitions and premade status changes |
| dao/PlayerDao.py, dao/TeamDao.py, dao/QueueDao.py | Guild isolation, compatible optional fields, version guards, pagination, rating views |
| core/QueueManager.py, core/TeamManager.py | v2 integration, match IDs, valid drafts, atomic premade reservations, consistent displays |
| core/BracketManager.py | Idempotent rating on bracket retries and participant-only recovery |
| core/RatingViews.py, core/LeaderboardManager.py, core/TeamLeaderboardManager.py | Skill order, provisional status, confidence, original rank icons and pagination |
| trueskillapi/__init__.py | Fail closed if the legacy update path is invoked in v2 |
| tools/benchmark_ratings.py, tools/stress_ratings.py, tools/replay_ratings.py | Reproducible comparisons, participation tests, offline chronological replay |
| tests_rating/ | Independent real-library/emulated-storage regression coverage |
| requirements*.txt, .github/workflows/test.yml, .env_sample | Pinned comparison/testing dependencies, separate suites, opt-in configuration |
| README.md, CLAUDE.md, ELO_SYSTEM_IMPROVEMENTS.md, docs/ | Audit, formulas, limitations, numerical evidence, migration and rollback |

## Reproduction and rollout

See [RATING_V2.md](RATING_V2.md) for formulas, cited research, the full migration/rollback procedure, and operator configuration. The code defaults to legacy mode. Enabling the new model requires a separate rating table and an explicit reviewed season; deployment alone does not activate it.

For faster benchmark execution, append `--workers 4` to the documented commands. The benchmark saves one row after every completed model/schedule run. With unchanged source and protocol, add `--resume` and reuse the output path to continue after interruption. Checkpoints are ignored by Git; final JSON contains every raw row. Do not resume across numerical code changes.

[Download the complete machine-readable results](rating-benchmark-data.zip).
The archive contains `benchmark-development.json`, `benchmark-evaluation.json`,
and `stress-results.json`, with every raw result. Extract beside this report to
inspect them. Reproduction commands regenerate the same JSON files locally;
generated JSON/checkpoints are ignored by Git to keep reviews readable.

The principal remaining validation is authorized chronological real-match replay and a staging Discord/Lambda trial. No trustworthy production history was available. Keep an immutable result archive from activation onward. Discord messages and tournament-wide championship side effects remain outside the rating transaction; pending notifications and interrupted bracket advancement require operator reconciliation as documented.
