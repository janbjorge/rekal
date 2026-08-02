# Injection relevance calibration

Calibration of the recency-free `min_relevance` gate against the judged
pydantic and sqlmodel benchmark runs recorded on 2026-08-02.

## Decision

Keep `DEFAULT_MIN_RELEVANCE = 0.0` (off).

The two material content-harm cases do not separate from useful recalls.
sqlmodel has a useful held-out recall below its harmful one, so every
nonzero floor that removes the sqlmodel harm also removes a measured win.
Counterfactual sweeps over the recorded runs did not find a floor that
improved pooled median cost.

This is a calibrated default, not an uncalibrated placeholder. Revisit it
after the benchmark matrix covers more repositories.

## Method

For every `(repo, pair, role)`:

1. Recall from the frozen seed DB with `REKAL_MIN_RELEVANCE=0`.
2. Record the first result's hybrid score and recency-free relevance.
3. Compare median warm-seed cost with median warm-empty cost. This isolates
   the effect of recalled content from fixed hook overhead.
4. Recall every query again at each candidate floor and record which queries
   return no memories.
5. Replace those queries' warm-seed observations with the corresponding
   warm-empty observations. This estimates the no-injection outcome without
   paying to rerun the matrix.

The substitution is a counterfactual estimate, not a causal rerun. Before a
nonzero default ships, rerun candidate floors as real benchmark arms.

## Boundary cases

| repo | pair / role | relevance | hybrid | warm-seed - warm-empty | outcome |
|---|---|---:|---:|---:|---|
| sqlmodel | setattr-dual-write / heldout | 0.134 | 0.281 | -$0.076 | useful |
| sqlmodel | field-metadata / heldout | 0.138 | 0.284 | +$0.070 | harmful |
| sqlmodel | field-to-column / heldout | 0.142 | 0.287 | -$0.029 | useful |
| pydantic | validators / heldout | 0.161 | 0.295 | +$0.087 | harmful |
| pydantic | config / heldout | 0.166 | 0.299 | -$0.161 | useful |
| pydantic | serializers / heldout | 0.172 | 0.304 | -$0.022 | useful |

Negative cost deltas are wins: memory made the run cheaper. The overlap is
present in both recency-free relevance and the full hybrid score.

## Floor sweep

The table substitutes warm-empty observations for every query that returned
no memories at the tested floor, then pools all 120 warm observations from
both repositories. Quality is the mean judge score.

| floor | gated boundary cases | pooled median cost | mean quality |
|---:|---|---:|---:|
| 0.000 | none | $0.112 | 1.992 |
| 0.139 | sqlmodel setattr-dual-write + field-metadata | $0.117 | 1.992 |
| 0.143 | above + sqlmodel field-to-column | $0.115 | 1.992 |
| 0.162 | above + pydantic validators | $0.115 | 1.992 |
| 0.167 | above + pydantic config + sqlmodel init-lifecycle/select-dispatch | $0.117 | 1.992 |
| 0.173 | above + pydantic serializers + sqlmodel relationship-instrumentation | $0.127 | 1.992 |

The quality column is unchanged because the current judge is nearly
saturated at score 2; it does not establish that gating is quality-neutral.
Issue #78 tracks strengthening that signal.

## Recalibration bar

A future nonzero default needs:

- judged results from more than two repositories;
- a candidate floor that improves real (not substituted) warm-arm cost;
- no material quality regression under a discriminating judge; and
- a stable gap between harmful and useful top-hit relevance.
