# Hybrid Search Guide

This sample document exists so you can try ingestion and retrieval locally.

## Why hybrid retrieval

Dense vector search captures meaning: a query about resetting credentials will
match a passage about changing passwords even with zero shared words. Keyword
search captures exact tokens: error codes, product names, and identifiers that
embeddings often blur together.

## Reciprocal rank fusion

Each retriever produces a ranked list. RRF assigns every chunk a score of
1/(k + rank) per list and sums them, so agreement between retrievers is
rewarded without comparing raw scores across systems.

## Operational notes

Rebuilding an HNSW index on millions of rows takes real time, so plan index
changes as migrations. Keyword search here uses Postgres full text search,
which is not identical to BM25 ranking.
