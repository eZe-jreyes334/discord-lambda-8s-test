# CoD 8s skill rating replacement

Source audited: `eZe-jreyes334/discord-lambda-8s-test`, GitHub commit
`8715e2c8af661cc6469f3343a186b01a8b2c93a4` (November 3, 2026).
Before opening the upstream PR, integrated Kumar's current master at
`956d11cec5915ee48a5dd669df54ae6b8abcc1ae`, preserving PRs #84 and #85
(queue/result-channel repairs, numeric normalization, and map-host assignments).
Development and validation used a local source snapshot. No production database,
credentials, or historical match results were accessed during that work.
Deploying this release uses the new SR system. Complete the migration steps below
before deploying; repository publication alone is not a live deployment.

## Decision

Use an outcome-only **joint Gaussian team-probit model**, with the same additive
team-performance likelihood as classic TrueSkill, retaining the covariance
between players instead of discarding it after each match. This is a small,
standard-library implementation of Gaussian assumed-density filtering, **not
TrueSkill 2**, and not an exact Bayesian posterior after a sequence of games.

For 15–25 recurring players, keeping a 25×25 covariance matrix is inexpensive.
It addresses an important weakness of independent player estimates: results
can reveal that a team is good while providing little evidence about which of
its members is better. This model preserves uncertainty in those unresolved
directions. It does not penalize someone simply for having regular teammates.

Public SR is a linear display of posterior mean ability. Confidence is shown
separately, and players qualify after **20 matches in the new season and sigma <= 6**. These
qualification thresholds are deliberate product choices, not universal statistical
constants. Ten forced wins against established opponents still produced high
estimated ability in testing, so a ten-game qualification rule was rejected.
Twenty wins can also be lucky: no outcome-only model can certify talent or
detect collusion from the same observations that would indicate genuine skill.

The skill model is the **only production rating system**, active without an opt-in
flag. `RATING_SYSTEM=legacy` is rejected instead of selecting old SR. The former
`v2` value is accepted only for configuration compatibility; it selects the same
required skill model. The internal schema version and existing snapshot keys stay
unchanged, so this default change does not discard a previously created snapshot.

The dedicated `RATING_TABLE` is required. Both deployment workflows validate its
existence, ACTIVE status, and String partition/sort keys before changing Lambda
dependencies or source. Missing runtime configuration fails explicitly and never
falls back to accumulated SR. Old rating fields are archival data for restoring a
previous application release, not a second selectable leaderboard.

## Confirmed audit findings

| Area | Confirmed behavior and consequence | Change |
|---|---|---|
| Leaderboard | Sorted accumulated, inactivity-adjusted `sr`, not estimated ability | v2 reads one authoritative skill snapshot |
| Free daily loss | At even odds, one win and one forgiven loss nets +10 SR | No points account or loss forgiveness in v2 |
| Inactivity | After seven days, subtracts 10 SR/day; returning matches commit the decay | No direct SR decay; show inactive after 28 days |
| Win odds | Each player compared to the average conservative rating of all eight players | Probability of one team's summed performance exceeding the other |
| Custom K | Multiplies updated mu but leaves sigma from a different posterior | No custom K or experience multiplier in v2 |
| Scale mismatch | `elo > 1800/2100` checks use raw mu, normally around 25 | Removed from v2 entirely |
| Placement | Tenth solo game/first premade game seeds a band midpoint with Diamond cap | Continuous SR; qualification never overwrites skill |
| Rank gaps | Inclusive ranges leave 99.5, 199.5, etc. unmatched and return the highest rank | Floor-based continuous bands; 700+ remains highest |
| Solo result | Clears queue before rating; rates even when the clear's conditional write fails | v2 snapshot, receipt and queue transition in one transaction |
| Player saves | Eight independent writes can partially succeed; conditional conflicts ignored | v2 never writes eight separate rating records |
| Team result | Rates before clearing, with separate team status writes | Team snapshot, receipt, queue and both statuses committed together |
| Bracket retry | Rates again inside the metadata retry loop | Permanent match receipt; bracket retries reuse the committed outcome |
| Server isolation | Player/queue DAOs silently rewrite every guild ID to one hardcoded ID | Use the requested guild; existing original-guild data stays there |
| Stale buttons | Match buttons identify a reusable queue, not a particular match | v2 result, cancel and draft buttons include a persisted match UUID |
| Draft validation | A forged/stale pick can repeat a player or pick someone outside the queue | Reject invalid picks, wrong turn, stale lobby and duplicate roster entries |
| Match start | Repeated starts can append teams; >8 players can be silently truncated | Require eight unique participants and an unused lobby |
| Premade start | Reused truncated team-based lobby ID, non-atomic team reservations | Unique lobby IDs; v2 reserves both teams atomically |
| DAO concurrency | Mutates the local version before a failed conditional save; version zero writes unconditionally | Advance version on success; new records use create-only conditions |
| Queries | Player/team guild-index queries stop after the first page | Follow DynamoDB pagination |
| Tests | Global mocks replace both TrueSkill and boto3 | Separate real-math/emulated-transaction test suite |

The old `ELO_SYSTEM_IMPROVEMENTS.md` presented unsupported research claims and
an incomplete, mathematically incorrect Glicko-2 sketch. It has been replaced.
The earlier assertions about top-rank exclusivity and 15–20-game convergence
were not evidence that the old implementation achieved those outcomes.

## Models considered

| Approach | Fit for this bot | Evaluation/decision |
|---|---|---|
| Original TrueSkill | Native changing-team outcome model; already a dependency | Benchmarked unmodified with its standard dynamics; strong simple baseline |
| TrueSkill 2 | Additional telemetry, squads, modes and fitted parameters can help | Research reviewed; not implemented without the required reliable data and estimation pipeline |
| OpenSkill Plackett–Luce | Practical Bayesian approximation, team support | Benchmarked official implementation; two-team results coincide with its Bradley–Terry full model here |
| OpenSkill Bradley–Terry full | Logistic performance comparison with regularized skill estimates | Benchmarked; not a separate advantage over PL in this two-team experiment |
| Glicko-2 | Well-defined head-to-head rating periods | Research reviewed; no native individual attribution for changing four-player teams. No fabricated "team Glicko-2" benchmark |
| Joint Bayesian team-probit | Same simple outcome likelihood; retains unresolved teammate correlations | Selected, with empirical sensitivity checks and analytical invariant tests |
| Learned hierarchical skill/squad effects | Could model role, persistent squad synergy or population hyperparameters | Too many weakly identified parameters for current data; defer until history supports validation |
| Ad hoc hybrid bonuses | Easy to build but changes the meaning of mu/sigma | Rejected experience multipliers, point grants, K/D bonuses and arbitrary confidence penalties |

Selection is based on synthetic development experiments, interpretability, the
fixed-teammate uncertainty invariant, and integration cost. Synthetic accuracy
is **not measured accuracy on this CoD community**. OpenSkill/default TrueSkill
were compared at documented defaults, not exhaustively tuned to these generators.
This is not proof that the selected model dominates every implementation/dataset.

## Final formulas and defaults

For a guild and rating namespace, keep a skill vector `m` and covariance `C`.
New players start with `mu=25`, `sigma=25/3`, independent of existing players.
The prior anchors the scale: results establish relative ability, not an external
worldwide skill standard.

For team A versus B, `x_i = +1` on A, `-1` on B, and `0` otherwise.
Performance noise is `beta=25/6` per player. With elapsed calendar days `dt`:

```text
C_prior = C + 0.25 * dt * I
c²      = xᵀ C_prior x + 8 beta²        (4v4 solo)
P(A wins) = Phi(xᵀ m / c)
```

Orient `x` toward the actual winning team for the update, and set:

```text
t = xᵀ m / c
v = phi(t) / Phi(t)
w = v * (v + t)
m_new = m + (C_prior x / c) * v
C_new = C_prior - ((C_prior x)(C_prior x)ᵀ / c²) * w
```

`phi`/`Phi` are the standard normal density/CDF. The one-observation Gaussian
posterior moments are exact given the current Gaussian approximation. Repeated
updates approximate the increasingly non-Gaussian posterior. Extreme upsets use
an asymptotic inverse-Mills-ratio calculation, with tests for numerical stability.

Each premade team is a separate rated entity in namespace `team`; its likelihood
uses `2 beta²` for two entities. It never changes solo ratings. A stable team ID
continues to represent that team when its roster is edited; a future roster-version
policy may be appropriate if teams replace most of their members.

```text
SkillScore = mu - lambda * sigma, with lambda = 0 by default
SR = max(0, round(350 + 20 * (SkillScore - 25)))
rank_index = clamp(floor(SR / 100), 0, 7)
```

The leaderboard sorts on unrounded `SkillScore`, not stored/cumulative SR and
not match count. Equal estimates share a position. Provisional/limited-evidence
players remain visible after qualified players, without a competitive position.
Qualification is at least 20 matches and sigma <=6. The original eight icons and
0–99, 100–199, …, 700+ bands remain. No percentile quotas or forced tier occupancy.
The center/scale are presentation choices; calibrate them only with a reviewed
season change, not to force someone into the top tier.

The development grid compared lambda in `{0,.25,.5,1,2,3}`. Large deductions
increased activity-related disadvantage. Ranking by mu avoids turning uncertainty
reduction into point earnings. Qualification and visible uncertainty handle the
small-sample tradeoff instead. A conservative score remains configurable in a
new state's `Config`, but is not the deployed default.

Read-only leaderboard views do not age/change ratings. At the next result,
elapsed-time uncertainty grows before conditioning on the result. This does not
reduce mu by itself or grant points for playing. Related players' means can change
without participating when later results provide information about earlier shared
results; this is a consequence of retaining correlations, not inactivity decay.
Long gaps can reduce qualification confidence. The "inactive" label is informational.
With correlated estimates, an individual need not gain SR after every team win:
the update increases the winning side's estimated advantage while attributing
evidence across the whole connected history. The displayed delta is from that
player's last played match, not every subsequent indirect inference update.

An "Order uncertain" label appears if another qualified player has a pairwise
posterior ordering probability between 20% and 80%. It uses
`Phi((mu_i-mu_j)/sqrt(C_ii+C_jj-2*C_ij))`; these are approximate model probabilities,
not guarantees about who wins a real-world 1v1 or who is best at a particular role.

## Matchmaking and competitive limits

Automatic teams examine the 35 unique 4v4 partitions, choose a near-best predicted
balance, and use recent teammate repetition as a tie-break within two percentage
points of the best probability. Team side is randomized. Captain selection uses
similar estimated mean ability, then fewer completed captain assignments among
near-equal candidates, with randomized ties and first side. The existing snake
draft order is preserved. Cancelling drafts does not earn rating or captain credit.

No individual K/D, objective, role or score modifiers are used because the bot
does not collect validated inputs for them. Win/loss evidence cannot separate
inseparable teammates, infer unobserved IGL/entry contributions, identify actual
match fixing, or remove all squad synergy bias. Verified match logs, mixing teams,
moderation, and accurate result reporting remain important. Repeated easy wins
produce diminishing evidence, but even an ideal outcome model cannot distinguish
deliberate loss trading from genuine surprise wins using outcomes alone.

## Storage, migration, deployment and rollback

1. Use a **test environment first**. Finish/cancel active lobbies before deploying
   the replacement. Old buttons lack a match UUID and are intentionally rejected.
2. Provision a dedicated DynamoDB table in `us-east-1` (the region used by the
   existing queue/team DAOs), in the same account as those tables,
   partition key `guild_id` (String), sort key `record_id` (String),
   on-demand capacity. Do not enable TTL on receipts. Enable backup/PITR according
   to the operator's normal policy. No infrastructure is created by this patch.
3. Grant `dynamodb:GetItem`, `dynamodb:PutItem`, and `dynamodb:UpdateItem` on
   the new rating table. The queue table also needs `dynamodb:ConditionCheckItem`
   for bracket guards; result transactions require queue `PutItem` and team
   `UpdateItem` alongside existing read permissions. AWS authorizes transactional
   operations through these underlying actions, not a `TransactWriteItems` IAM
   action. Scope permissions to the actual table ARNs. Existing leaderboard
   queries and other bot permissions still apply. The deployment identity also
   needs `dynamodb:DescribeTable` on the rating table for the pre-deployment check.
4. Set `RATING_TABLE=<dedicated table>` and a reviewed
   `RATING_SEASON` such as `v2-1` as GitHub environment variables in DEV/PROD.
   Both Lambda workflows now pass these variables into their explicit environment
   maps. There is no mode switch. Remove any old `RATING_SYSTEM=legacy` application
   setting. The deployment check reads the table's schema before either upload job
   runs; source deployment also waits for dependency deployment. Configure the
   Lambda role's permissions separately: the table check does not prove the runtime
   role has all permissions. A merge does not provision the table or IAM policy.
5. With no trustworthy history, start all v2 players from the same prior. Legacy
   `sr`, `elo`, `sigma`, wins/losses and championships are left intact. v2 games,
   wins, uncertainty, streaks and latest match deltas live in the snapshot.
   Do not convert accumulated SR or customized legacy mu/sigma into claimed
   accurate v2 estimates. Existing records need no new required fields.
6. If authorized historical results become available, run the offline replay
   below. Review dates, rosters, outcome semantics and IDs; a series should be
   recorded consistently as one result, not both a series and its maps.
   Replay refuses out-of-order history, conflicting IDs and SR-only records.
   It returns a review bundle containing the snapshot **and every permanent receipt**.
   An operator can install it into an empty new season while the bot is paused,
   with conditional create-only writes and receipt verification before activation.
   There is intentionally no automatic production importer in this patch.
7. Snapshot `schema_version=2` and the full numerical configuration are stored
   together. Loading an unsupported/corrupt snapshot fails closed; it never resets
   players silently. Runtime default changes do not reinterpret existing states.
   A 64-entity limit bounds item sizes; this implementation targets small pools.
8. Rollback: stop accepting results, restore the reviewed previous application
   release and its deployment configuration, and restore data if necessary.
   A mode flag cannot reactivate old SR in this release. Original values remain available,
   but results recorded only in v2 are not automatically backfilled into legacy
   SR. Keep receipts/snapshots for a later replay. Merely changing season cannot
   rate an old match twice because receipt keys are independent of the season.

All result retries use a permanent `MATCH#<UUID>` receipt, a versioned
`STATE#<season>#<namespace>` snapshot, and queue-version/match-ID conditions.
One transaction commits them together. A concurrent match or waitlist change
causes a reload/recompute; there are no partial eight-player rating writes.
Premade results also atomically return both teams to idle.

Discord notification delivery is **not exactly once**: DynamoDB cannot transact
with Discord. Ratings remain committed if posting fails, and receipts retain
`notification_status=pending`. An operator can inspect pending receipts and
refresh `/leaderboard` or repost the result after checking the channel. A response
can be lost after Discord accepted it, so automatic blind resend would duplicate
messages. No background notification worker is added here.

Bracket rating is idempotent through the same receipt, and the committed winning
side is stored on the match. Subsequent bracket metadata retries do not re-rate it.
Bracket advancement and tournament announcements retain the existing separate
workflow. A crash may require retrying the result/advancement; tournament-wide
championship awards and all bracket side effects are not made atomic by this patch.

## Reproduction and validation

```bash
python -m pip install -r requirements-testing.txt
python -m pytest tests -q
python -m pytest tests_rating -q

# Benchmarks require Python 3.10+ for the external OpenSkill comparator.
python -m pip install -r requirements-evaluation.txt
python -m tools.benchmark_ratings --seeds 2 --start-seed 0 --matches 200 --output docs/benchmark-development.json
python -m tools.benchmark_ratings --seeds 8 --start-seed 100 --matches 400 --output docs/benchmark-evaluation.json
python -m tools.stress_ratings

python -m tools.replay_ratings --input authorized-results.jsonl --output reviewed-new-season.json --guild YOUR_GUILD_ID --season reviewed-v2
```

Run the two test suites in **separate processes**. The inherited historical conftest
replaces third-party modules and explicitly selects historical reference paths
inside its test process; it does not enable a production legacy mode. Combining
suites would contaminate the real-library tests. The production-default suite runs
without `RATING_SYSTEM` and uses real boto3 with Moto, fake AWS credentials,
real TrueSkill for one-step moment equivalence, and NumPy only for a covariance
eigenvalue check. [GitHub Actions](https://github.com/eZe-jreyes334/discord-lambda-8s-test/actions/runs/41693948786)
passed all 240 tests (176 historical/reference and 64 production-default) on
Python 3.9.25 and 3.12.15 at commit `0f59e6f`. Earlier local execution used
Python 3.12.14, with a Python 3.9 syntax parse check.
OpenSkill is an evaluation-only dependency requiring Python 3.10+; it is excluded
from Lambda dependencies and from the Python 3.9 test dependency set.

The development set chooses the dynamics/default policy. Held-out seed numbers
are fixed separately and receive the same schedules/results across models.
Every prediction is made before updating on that outcome. Generators include
both probit and variance-matched logistic outcomes; models also face unmodelled
noise in draft scenarios. Full results, not only favorable scenarios, are retained.
Reported ranking correlations include all simulated players, including those
who would be provisional in the actual UI; they are model-recovery metrics.

The benchmark covers 15/20/25-player pools, weighted participation, balanced
rosters, captain-style snake drafts, fixed pods, carried teammates, strongest-vs-
weakest farming, forced early luck, a return after 28 days, and changing skill.
It reports prequential log loss, Brier score, accuracy, calibration bins/ECE,
approximate interval coverage, ranking movement, rank recovery, and residual
correlation with activity. Scenario averages and seed-level standard errors are
provided; match observations within a session are not independent samples.
See `VALIDATION.md` for the recorded run results.

## Sources and scope of evidence

- [Microsoft TrueSkill overview and FAQ](https://www.microsoft.com/en-us/research/project/trueskill-ranking-system/): additive team skill, uncertainty, and inability to separate players who always play together.
- [Original TrueSkill technical report](https://www.microsoft.com/en-us/research/wp-content/uploads/2006/01/TR-2006-80.pdf): Gaussian skill/performance likelihood and approximate inference.
- [TrueSkill Python documentation](https://trueskill.readthedocs.io/en/latest/): reference team win probability and implementation API.
- [TrueSkill 2 paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2018/03/trueskill2.pdf): richer telemetry and parameter estimation; its published game-specific gains are not transferred to this bot.
- [Weng and Lin, JMLR 2011](https://www.jmlr.org/papers/v12/weng11a.html): Bayesian approximations for team ranking, including Bradley–Terry and Plackett–Luce.
- [OpenSkill manual](https://openskill.me/en/v6.1.1/manual.html) and [project](https://github.com/vivekjoshy/openskill.py): supported models; benchmark pinned to 6.1.3.
- [Official Glicko-2 specification](https://www.glicko.net/glicko/glicko2.pdf): rating-period semantics and original equations; no invented team proxy claimed to be Glicko-2.
- [Probit posterior research](https://arxiv.org/abs/1802.09565): Gaussian-prior probit posteriors are generally skew-normal, supporting the explicit approximation caveat.
- [AWS DynamoDB transaction documentation](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html): atomic multi-item writes and conditional conflict handling.
- [IAM for DynamoDB transactions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html): underlying action permissions and `ConditionCheckItem`.

The full-covariance implementation, qualification policy, public scale, simulator
and measured comparisons here are this project's engineering decisions/results;
the sources do not endorse these particular parameters for Call of Duty.
