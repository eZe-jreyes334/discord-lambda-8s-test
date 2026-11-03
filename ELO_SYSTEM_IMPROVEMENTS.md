# Rating-system audit and replacement

The previous document is superseded by [Rating v2](docs/RATING_V2.md) and its reproducible validation results.

The old custom K-factor changes, placement caps, and illustrative Glicko-2 code were not a validated rating model. In particular, raw mu (normally around 25) was compared to thresholds of 1800/2100, and changing mu independently of its posterior uncertainty invalidated the claimed Bayesian interpretation.

The implemented v2 system derives SR from estimated ability, preserves teammate uncertainty, and records outcomes atomically. It is an explicit, new-season opt-in; see the migration and rollback instructions before activation. No deployment has been performed.
