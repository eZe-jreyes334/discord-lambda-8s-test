# Rating-system audit and replacement

The previous document is superseded by [Skill rating design](docs/RATING_V2.md) and its reproducible validation results.

The old custom K-factor changes, placement caps, and illustrative Glicko-2 code were not a validated rating model. In particular, raw mu (normally around 25) was compared to thresholds of 1800/2100, and changing mu independently of its posterior uncertainty invalidated the claimed Bayesian interpretation.

The skill system derives SR from estimated ability, preserves teammate uncertainty, and records outcomes atomically. It replaces accumulated SR as the only production rating path. Provision the required rating table and review the migration before merging to a deployment branch. Rollback requires restoring the previous application release; legacy mode cannot be selected.
