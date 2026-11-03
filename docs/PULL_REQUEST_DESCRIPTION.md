## Why this change

The goal is for the 8s leaderboard to reflect estimated skill in a small group of 15–25 recurring players, even when people play different numbers of matches. The current public SR adds up points separately from TrueSkill and includes daily loss forgiveness, placement jumps, and inactivity deductions. Those rules can reward playing more without providing equivalent evidence of better skill.

## Rating changes

- Add an optional `v2` model that estimates ability from team wins/losses and keeps uncertainty between recurring teammates. It is a joint Gaussian team-probit approximation, not TrueSkill 2.
- Rank qualified players by estimated ability, not accumulated points or match count. Public SR is `max(0, round(350 + 20 * (mu - 25)))`.
- Show confidence separately. Players qualify after **20 v2 matches and sigma <= 6**; qualification never grants points or overwrites their skill estimate.
- Remove daily loss forgiveness, placement bonuses, and direct inactivity SR decay in v2. Inactivity gets a label after 28 days; elapsed time can increase uncertainty at the next result.
- Keep solo and premade-team ratings separate. Preserve the existing eight rank icons and SR bands, with continuous boundaries so fractional SR cannot accidentally receive the highest rank.
- Flag uncertain ordering between close players. Because estimates retain correlations, later results can affect previously related players, and an individual is not guaranteed an SR increase after every team win.

## Matchmaking and existing features

- Automatic teams evaluate all 35 unique 4v4 splits and use recent teammate repetition to break ties among similarly balanced options.
- Captain selection favors similar estimated skill and considers previous captain assignments.
- Preserve queues, waitlists, snake drafts, premade teams, brackets, leaderboard pagination, and map-host assignments.
- Require valid, unique rosters; reject stale buttons, invalid draft picks, and incorrect picking turns. Match buttons identify the specific match instead of only a reusable queue.
- Reserve premade teams atomically so concurrent starts cannot book the same team twice.
- Preserve upstream #84 and #85, including queue/results-channel repairs and saved per-map hosts. The branch is based on upstream master `956d11c`.

## Reliability and storage

- Store ratings in versioned, guild-specific snapshots with a permanent receipt for each completed match.
- Commit ratings, the receipt, and the queue transition together. Premade results also return both teams to idle in that transaction.
- Prevent retries, conflicting reports, and concurrent updates from rating one match twice. Bracket advancement retries reuse the saved result.
- Remove hardcoded guild overrides, follow database query pagination, and fix version handling after failed conditional saves.
- Keep legacy rating values intact for rollback.
- Record pending notifications when Discord delivery fails. Discord messages and tournament-wide championship side effects are still outside the rating transaction and may require reconciliation.

## Validation

- **230 local tests pass:** 176 existing/upstream tests and 54 new tests using real rating math and emulated DynamoDB.
- CI is configured for Python 3.9 and 3.12. Local execution used Python 3.12; Python 3.9 syntax was also checked.
- Compare legacy behavior, default TrueSkill, OpenSkill variants, and joint-model settings across **3,456 held-out model/scenario runs**.
- In **300 targeted simulations**, a strong player with 25 games ranked above an average player with 200 games every time. Ten forced wins remained provisional.
- The selected model had approximately **1.8% lower log loss** than default TrueSkill on the held-out synthetic set. It did not win every scenario, and its uncertainty intervals were conservative.
- Include all numerical results and reproduction scripts. These are synthetic findings, not proof of performance on the actual community; no production history was available.

## Activation and rollback

**Legacy remains the default. This PR does not automatically reset ratings or enable v2.**

Before activation, provision the dedicated DynamoDB rating table and permissions, finish/cancel active lobbies, and set `RATING_SYSTEM`, `RATING_TABLE`, and `RATING_SEASON` in the DEV/PROD GitHub environment. Both deployment workflows now preserve those settings.

Without trustworthy history, start v2 from the same prior for everyone. An offline replay tool can import reviewed chronological outcomes; existing SR alone cannot reconstruct that history. Switching back to legacy preserves the old values, but v2-only results are not automatically backfilled.

See `docs/RATING_V2.md` for the full audit, formulas, migration, and rollback procedure, and `docs/VALIDATION.md` for the comparisons, evidence archive, and limitations. Staging and authorized real-match validation remain before production activation.
