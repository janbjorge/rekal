# rekal scoring

Source-of-truth reference for how `memory_search` ranks results. Covers
the three signals, their normalization, how candidates are retrieved and
merged, the four-layer weight resolution, and a tuning guide. Keep this
file in sync with `rekal/scoring.py` and the `search` / `resolve_weights`
methods in `rekal/adapters/sqlite_adapter.py`.

> **Why this exists.** "Hybrid search" is three independent signals
> squashed onto a common `[0, 1]` scale and summed with weights. Each
> piece is simple; the subtlety is in what happens when a signal is
> *missing*, how candidates are fetched before scoring, and which config
> layer wins. This doc is that detail.

---

## The formula

```
score = w_fts     × normalize_fts(bm25)
      + w_vec     × normalize_vec(cosine_distance)
      + w_recency × normalize_recency(age_days, half_life)
```

Defaults: `w_fts = 0.4`, `w_vec = 0.4`, `w_recency = 0.2`,
`half_life = 30.0` days (`ScoringWeights` in `scoring.py`).

Weights are not re-normalized. With the defaults they sum to 1.0, so
a perfect hit on every signal scores 1.0, but nothing stops you setting
`w_fts = 0.8, w_vec = 0.8`; the max score just becomes higher. Weights
are relative knobs, not probabilities.

---

## The three signals

| Signal | Catches | Misses | Default weight |
|---|---|---|---|
| **FTS (BM25)** | exact keywords, identifiers, rare tokens | synonyms, paraphrase | 0.4 |
| **Vector (cosine)** | semantic similarity, paraphrase | exact identifiers, typos | 0.4 |
| **Recency** | "what did we just decide" | nothing — it's a tiebreaker, not a matcher | 0.2 |

Keywords and vectors cover each other's blind spots; recency breaks ties
toward fresh knowledge without burying old facts (at 0.2 it can't
outvote a strong content match).

---

## Normalization

Each signal is squashed to `[0, 1]`, higher = better. Functions live in
`scoring.py`.

### `normalize_fts(score)` — BM25 → [0, 1]

```python
if score >= 0:
    return 0.0
return 1.0 / (1.0 + math.exp(score))
```

FTS5 returns BM25 as a **negative** number where *more negative = better
match*. The logistic `1 / (1 + e^score)` maps that to `(0, 1)`: a very
negative score → ~1.0, a score near 0 → ~0.5, and any non-negative score
(no real signal) is floored to 0.0.

### `normalize_vec(distance)` — cosine distance → similarity

```python
return max(0.0, 1.0 - distance)
```

sqlite-vec returns cosine **distance** (0 = identical). Similarity is
`1 - distance`, clamped at 0 so a distance > 1 can't go negative.

### `normalize_recency(days, half_life)` — exponential decay

```python
return math.exp(-0.693 * days / half_life)
```

`0.693 ≈ ln 2`, so a memory exactly `half_life` days old scores 0.5,
`2 × half_life` old scores 0.25, and a brand-new one scores ~1.0. Age is
days since `created_at`.

### `combine_scores(raw, weights)`

```python
fts     = normalize_fts(raw.fts_score)
vec     = normalize_vec(raw.vec_score)
recency = normalize_recency(raw.recency_days, w.half_life)
return w.w_fts * fts + w.w_vec * vec + w.w_recency * recency
```

---

## Retrieval → merge → score

`db.search` runs two lookups in parallel, unions the candidates, then
scores every survivor in Python.

1. **Vector lookup** — `memory_vec MATCH ? AND k = ?` with `k = limit × 3`,
   ordered by distance, excluding superseded rows. Yields `{id: distance}`.
2. **FTS lookup** — `quote_fts(query)`, then `memories_fts MATCH ?`
   ordered by rank, `LIMIT limit × 3`, excluding superseded. Yields
   `{id: bm25}`. Skipped entirely if the query has no usable tokens.
3. **Union** — `candidate_ids = vec_ids ∪ fts_ids`. Empty union returns
   `[]` immediately.
4. **Fetch + filter** — for each candidate, `db.get(id)`, then drop in
   Python on: expired (`expires_at <= now`), `project` (strict equality,
   see [gotchas](#gotchas)), `memory_type`, `tier`, `conversation_id`.
5. **Score** — `combine_scores`, write `mem.score`.
6. **Bump access** — `access_count += 1`, `last_accessed_at = now` for
   every scored row.
7. **Sort + slice** — descending by score, take `limit`.

### Why `k = limit × 3`

Both lookups over-fetch 3× because filtering happens *after* retrieval.
A heavily project- or tier-filtered query can still surface `limit`
results even when most of the raw top-`limit` get filtered out. It is not
a hard guarantee: filter aggressively enough and you get fewer than
`limit` back.

### Missing-signal defaults — the important subtlety

A candidate found by only one lookup still gets scored on both. The
defaults for the *absent* signal are chosen to contribute zero, not
to penalize:

| Candidate found by | `fts_score` default | `vec_score` default | Effect |
|---|---|---|---|
| vector only | `0.0` → `normalize_fts` = 0.0 | actual distance | FTS term contributes 0 |
| FTS only | actual BM25 | `1.0` → `normalize_vec` = 0.0 | vector term contributes 0 |

So a one-signal hit is never pushed *below* a row that simply lacked that
signal; the missing term is neutral. Recency always applies (every row
has `created_at`).

### FTS query handling — `quote_fts`

```python
tokens = query.replace('"', " ").replace("\x00", "").split()
return " ".join(f'"{t}"' for t in tokens)
```

Every whitespace token is wrapped in FTS5 phrase quotes, so user input is
always literal text, never FTS5 operators (`AND`, `*`, `NEAR`, column
filters). Multiple quoted tokens are implicitly AND-ed by FTS5: all
tokens must appear for an FTS hit. The vector side has no such
requirement, which is why a loosely-worded query can still match
semantically through the vector lookup alone.

---

## Weight resolution — four layers

`db.resolve_weights(project, *, w_fts=…, …, file_config=…)` builds the
`ScoringWeights` for a search. Precedence, highest first:

| Priority | Layer | Source | Persists? |
|---|---|---|---|
| 1 | Per-call overrides | explicit args to `memory_search` / `memory_build_context` | No — that query only |
| 2 | Project config | `project_config` table, via `memory_set_config` | Yes — SQLite, across sessions |
| 3 | File config | `.rekal/config.yml` in the project | Yes — version-controlled, shared |
| 4 | Hardcoded defaults | `ScoringWeights` field defaults | Always: 0.4 / 0.4 / 0.2 / 30.0 |

Implementation:

```python
merged = ChainMap(per_call, project_config, file_defaults)
return ScoringWeights.model_validate(merged)
```

Layers 1–3 merge through a `ChainMap`; pydantic supplies layer 4 for any
key still missing and coerces DB/YAML strings to floats. Resolution is
**per-key independent**: `.rekal/config.yml` can set `w_fts` while the
project config overrides only `half_life`, and each key takes its
highest-priority source.

### `.rekal/config.yml`

Loaded once at server start by `load_file_config(find_config_file())`
(`mcp_adapter.py`), which looks for `.rekal/config.yml` under the current
working directory and returns `{}` on any error (missing file, bad YAML).

```yaml
# .rekal/config.yml
scoring:
  w_fts: 0.6
  w_vec: 0.3
  w_recency: 0.1
  half_life: 14.0
```

### Per-project DB config

```
memory_set_config("w_fts", 0.6, project="my-project")
```

Stored as TEXT in `project_config`, coerced to float at resolve time.
Only applied when the search passes a `project`; a `project=None` search
ignores project config and falls through to file config + defaults.

---

## Tuning guide

Start from defaults. Change one knob at a time; weights are relative, so
only their ratios matter.

| Symptom | Try |
|---|---|
| Search misses obvious keyword/identifier matches | raise `w_fts` (e.g. 0.6) — favor exact tokens |
| Search misses paraphrases / synonyms | raise `w_vec` — favor semantics |
| Stale facts rank above current ones | raise `w_recency` and/or lower `half_life` |
| Fast-moving project (decisions churn weekly) | `half_life: 7–14` so week-old memories halve |
| Stable knowledge base (long-lived facts) | `half_life: 60–90`, keep `w_recency` low |
| Recency drowning out strong matches | lower `w_recency` (≤ 0.15) — it should tiebreak, not decide |

Set team-wide defaults in `.rekal/config.yml` (committed). Use
`memory_set_config` for a per-project override that shouldn't be in the
repo. Use per-call `w_*` args only to experiment with a single query.

---

## Gotchas

- **`project` filter is strict equality.** `project=None` matches only
  global (NULL-project) memories; `project="x"` matches only `"x"`. There
  is no "global + project" union and no fallback. This is deliberate, to
  stop cross-project bleed.
- **Recency uses `created_at` only.** `updated_at` and `last_accessed_at`
  are recorded but ignored by scoring. Editing a memory does not refresh
  its recency.
- **BM25 sign.** FTS5 ranks are negative (lower = better); any
  non-negative rank normalizes to 0.0. Don't expect raw BM25 to be a
  similarity.
- **Over-fetch is not a floor.** `k = limit × 3` mitigates post-filter
  shrinkage but doesn't guarantee `limit` results under heavy filtering.
- **Superseded rows never appear.** Both lookups exclude
  `to_id`s of `supersedes` links at the SQL layer, before scoring.
- **All FTS tokens must match.** `quote_fts` AND-s tokens; the vector
  lookup is what rescues loosely-worded queries.

---

## File map

- Signal functions + weights: `rekal/scoring.py`
- Retrieval / merge / scoring: `rekal/adapters/sqlite_adapter.py` → `search`
- Weight resolution: `rekal/adapters/sqlite_adapter.py` → `resolve_weights`
- File config loader: `rekal/adapters/mcp_adapter.py` → `load_file_config`, `find_config_file`
- FTS sanitizer: `rekal/adapters/sqlite_adapter.py` → `quote_fts`
- Schema (indexes, tiers, supersede links): [docs/data-model.md](data-model.md)
