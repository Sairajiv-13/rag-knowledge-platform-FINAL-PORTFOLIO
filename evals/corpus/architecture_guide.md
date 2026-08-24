# Platform architecture guide

## Request flow

A client authenticates at the token endpoint and receives a short-lived JWT.
Every subsequent request carries that token in the Authorization header. The
API validates the token, re-checks the credential row for revocation, and then
dispatches to the route handler. Read requests such as search and answer run
synchronously; write requests such as document upload enqueue background work.

## Ingestion path

An uploaded document is stored with its raw bytes and a content hash, then a
background task is enqueued. The worker parses the document into blocks,
splits the blocks into overlapping chunks, embeds each chunk, and writes the
chunks with their vectors. The document row tracks status through pending,
processing, and completed or failed. Because the raw bytes are retained, a
document can be re-embedded later without re-uploading it.

## Retrieval path

A search request embeds the query and runs two lookups in parallel: a vector
nearest-neighbor search over the chunk embeddings, and a full-text search over
the chunk text. Reciprocal rank fusion merges the two ranked lists into one.
An optional cross-encoder reranker can reorder the fused list for higher
accuracy when the evaluation harness justifies its latency cost.

## Answer generation

An answer request first retrieves relevant chunks, then constructs a prompt
that numbers each chunk as a potential citation. The language model generates
an answer that references chunks by number. The platform parses those
references back into structured citations, dropping any that the model
invented but that do not correspond to a retrieved chunk. Answers can stream
to the client so the first words appear within a second.

## Failure handling

Permanent failures such as an unparseable file are marked failed without
retrying. Transient failures are retried with exponential backoff, and after
retries are exhausted the document is marked failed with an explanatory
message. Nothing is left in the processing state indefinitely.
