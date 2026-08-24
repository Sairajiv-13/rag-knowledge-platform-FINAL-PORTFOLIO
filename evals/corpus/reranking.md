# Cross-encoder reranking

After hybrid retrieval returns a candidate set, an optional cross-encoder
reranker can reorder them. Unlike the bi-encoder used for dense retrieval,
a cross-encoder reads the query and a candidate chunk together, which produces
a more accurate relevance judgment at higher computational cost.

Reranking is disabled by default. It is enabled only once the evaluation
harness shows it improves ranking quality enough to justify the added latency,
because a reranker that reads every candidate is far slower than the vector
lookup that produced them.
