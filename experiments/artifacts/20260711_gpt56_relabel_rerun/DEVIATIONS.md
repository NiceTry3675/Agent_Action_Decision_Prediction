# Realized-analysis clarifications and deviations

`DESIGN.md` is the frozen preregistration and was not edited after annotation
began. The following points clarify tensions discovered during independent
review.

## Formal population after the freshness exclusion

The design names all 28,782 Weak4 rows as the population and also excludes the
248 prior GPT-5.6 IDs. One of those 248 has recorded label `run_bash`; therefore
247 Weak4 rows are excluded and the realized probability-sampling frame is the
**28,535-row complement**. Primary estimates and eligible-frequency weights
formally apply to that complement.

To assess generalization to all 28,782 rows without assuming anything about the
excluded cases, `summary.json` now reports bounds that assign all 247 excluded
Weak4 rows either unacceptable or acceptable. Even the all-acceptable extreme
has macro acceptable 30.5% (bootstrap upper 35.8%) and natural-frequency
acceptable 37.3% (upper 43.6%), so the 50% verdict does not depend on the
exclusion.

## Unresolved rows

The frozen design says three-way top disagreements are excluded from consensus
endpoints. An initial analysis draft conservatively counted unresolved rows as
non-exact while allowing their independently valid acceptable-set majority; it
produced 61/240 exact and 73/240 acceptable. Independent review caught the
inconsistency before the final report.

The final primary analysis follows the frozen rule: three unresolved rows are
excluded from both consensus endpoints, giving 61/237 exact and 71/237
acceptable. The former all-row figures remain explicitly labeled as a
sensitivity analysis in `summary.json`; the verdict is unchanged.

## Source-family subgroup

The `sess_sim`/`sess_au` split was not preregistered. Because the core sample is
class-balanced rather than source-balanced, raw subgroup proportions are only
descriptive. The final report uses eligible-frame inverse-inclusion weights
across recorded-action strata and a corresponding within-stratum bootstrap.
Raw counts/Wilson intervals remain in `summary.json`. AU has only 14 sampled
rows and stays exploratory.

## Bootstrap scope

The bootstrap resamples session blocks **within each recorded-action stratum**.
Two sessions occur in two different strata and are treated independently across
those strata, so it is not a global session-cluster bootstrap. With 238 unique
sessions among 240 rows, the practical difference from a row-stratified
bootstrap is negligible, but the narrower implementation is stated explicitly.

## Integrity timing

The 48 blinded inputs were deterministically regenerated after annotation while
adding source-frame metadata to `design_snapshot.json`. Final bytes map exactly
to every output and selected source row, but there is no complete
pre-annotation 48-file checksum manifest. `PROVENANCE.md` records this gap;
final checksums are reproducibility seals, not proof of every byte at agent-read
time.

