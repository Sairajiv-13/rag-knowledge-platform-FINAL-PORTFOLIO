# Chunk sizing

Documents are split into chunks of roughly four hundred tokens with sixty
tokens of overlap between neighbors. The overlap preserves context across
chunk boundaries so that a sentence split between two chunks is still fully
present in at least one of them.

Smaller chunks improve retrieval precision because each chunk is more focused,
but they fragment context and increase the number of vectors to index. Larger
chunks preserve context but dilute relevance, since a large chunk may match a
query on only one of its many sentences. Four hundred tokens is the tuned
balance for this corpus.
