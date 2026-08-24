# Ingestion failure handling

Document processing distinguishes permanent failures from transient ones. A
corrupt file that cannot be parsed is a permanent failure and is marked failed
immediately without retrying, because retrying will never succeed. A transient
failure such as a temporary database error is retried with exponential backoff.

When retries are exhausted the document is marked failed with a message
recording that the retries ran out. The document row is the single source of
truth for status, so there is no separate result store that could disagree
with it.
