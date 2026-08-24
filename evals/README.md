# Evaluation harness

Two harnesses, kept separate because they measure different things:

- **`run_eval.py`** — retrieval quality (hit@1/3/5, MRR@5) per mode, plus a
  chunk-size sweep and a retrieval-context-support proxy. No LLM needed.
- **`answer_eval.py`** — end-to-end answer correctness and faithfulness.
  Needs a real LLM to be meaningful.

Corpus: **19 documents, 38 labeled questions** (`corpus/`, `questions.jsonl`).
Each question carries an `expected_file` and an `expected_fact`. Every number
any harness prints is measured in that run against the configuration printed
alongside it — nothing here is hand-written.

```bash
# retrieval quality, all three modes + answer-context support:
python evals/run_eval.py

# how chunk size affects retrieval quality (re-ingests per size):
python evals/run_eval.py --chunk-sweep 200 400 800

# real semantic numbers (downloads bge-small on first use):
RAG_EMBEDDING_PROVIDER=local python evals/run_eval.py

# whether reranking earns its latency (ADR 0003):
RAG_EMBEDDING_PROVIDER=local RAG_RERANKER=cross_encoder python evals/run_eval.py

# end-to-end answer quality (needs a real LLM):
RAG_LLM_PROVIDER=anthropic RAG_ANTHROPIC_API_KEY=sk-... \
RAG_EMBEDDING_PROVIDER=local python evals/answer_eval.py
```

## Measured retrieval quality

Measured with `embedding_provider=fake, reranker=none, top_n=5` over 19 docs /
38 questions. **The fake embedder is a deterministic lexical hash — treat the
absolute values as a plumbing baseline, but the ORDERING is the real result:**

| mode    | hit@1 | hit@3 | hit@5 | MRR@5 |
|---------|-------|-------|-------|-------|
| keyword | 0.395 | 0.421 | 0.421 | 0.408 |
| dense   | 0.474 | 0.789 | 0.816 | 0.624 |
| hybrid  | 0.553 | 0.816 | 0.842 | 0.681 |

Hybrid > dense > keyword at every k. This is the empirical justification for
defaulting to hybrid retrieval (ADR 0003) — measured, not asserted. Run with
`RAG_EMBEDDING_PROVIDER=local` for semantically meaningful magnitudes; the
ordering holds because RRF can only help when the two signals disagree.

**Retrieval context support:** on 35/38 questions (0.921) the expected fact
appears in at least one retrieved chunk — i.e. retrieval puts the answer
within the model's reach. The remaining 3 are where retrieval, not
generation, is the ceiling.

## Chunk-size sweep (precision vs. context, measured)

`python evals/run_eval.py --chunk-sweep 200 400 800`:

| chunk_target_tokens | hit@1 | hit@3 | hit@5 | MRR@5 |
|--------------------:|-------|-------|-------|-------|
| 200 | 0.579 | 0.789 | 0.816 | 0.686 |
| 400 | 0.553 | 0.816 | 0.842 | 0.681 |
| 800 | 0.553 | 0.816 | 0.842 | 0.681 |

Smaller chunks (200) win **hit@1** — a tight chunk is more precise, so the
single best result is more often exactly right. Larger chunks (400) win
**hit@3/5** — more context per chunk means the right document appears
*somewhere* in the top-k more often. 400 is the configured default: it
maximizes the top-k recall that RRF and the reranker then refine. This is the
precision/context trade-off from theory, now measured on this corpus.

## Answer quality (`answer_eval.py`)

Measures the generated answer, not just retrieval:
- **correctness** — the expected fact appears in the answer text.
- **faithfulness** — that fact appears in a chunk the answer actually *cited*
  (a correct-looking answer citing an unsupporting chunk is the hallucination
  mode RAG exists to prevent).

Real numbers require `RAG_LLM_PROVIDER=anthropic`. With the fake LLM the
harness runs end to end (proving the plumbing) but reports correctness 0.0,
because the fake echoes context instead of composing answers — which is
exactly why those numbers are labeled plumbing-only rather than published.

## The keyword-mode finding

Keyword mode plateaus because `websearch_to_tsquery` ANDs every term: a
natural-language question like "how often are restore drills performed" cannot
match a document that says drills "run monthly" — one absent stem drops the
whole match. This is expected: keyword search is for exact-term/identifier
lookups, and hybrid's RRF inherits dense ordering wherever keyword returns
nothing. The eval framing to use: compare **hybrid vs dense** on your corpus,
and treat keyword as a precision aid for identifier queries, not a ranker.

## Notes

- Both harnesses recreate their eval tenant every run, so numbers are never
  contaminated by chunks embedded under a previous configuration.
- The corpus is deliberately small and auditable. Growing it is cheap: add a
  doc and a couple of labeled questions. For a real quality claim in a
  specific domain, replace the corpus with domain documents and label
  questions with per-chunk relevance.
