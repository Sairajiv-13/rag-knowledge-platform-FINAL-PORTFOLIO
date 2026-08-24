# Incident process

Failed ingestions are triaged from the documents table: permanent parse
failures carry the parser's reason, while transient failures that exhausted
their retries say so explicitly. Nothing is ever left in processing forever.

For a stuck queue, first check the worker logs for transient failure events
with backoff timings, then Redis connectivity. Re-enqueueing a completed
document is safe because processing is idempotent.
