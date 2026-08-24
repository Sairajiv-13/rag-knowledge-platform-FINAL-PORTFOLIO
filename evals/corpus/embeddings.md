# Embedding model

Chunks are embedded with bge-small-en-v1.5, a 384-dimensional model that runs
locally on CPU. Queries are embedded with the model's recommended query
instruction prefix, which measurably improves retrieval for short questions.

The vector column dimension is fixed in the schema, so switching embedding
models requires a migration and a full re-embed of every chunk. Raw document
bytes are retained precisely so a re-embed never requires re-uploads.
