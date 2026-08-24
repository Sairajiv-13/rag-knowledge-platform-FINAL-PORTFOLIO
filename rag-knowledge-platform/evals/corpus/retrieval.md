# Hybrid retrieval

Search combines two signals. Dense retrieval embeds the query and finds the
nearest chunk vectors using an HNSW index over cosine distance. Keyword
retrieval matches the query against a generated tsvector column using
PostgreSQL full-text search. The two ranked lists are combined with reciprocal
rank fusion, which needs no score calibration because it uses only rank
position.

Reciprocal rank fusion assigns each chunk a score of one divided by a constant
plus its rank in each list, then sums across lists. A chunk ranked highly by
both dense and keyword search rises to the top even if neither list ranked it
first.
