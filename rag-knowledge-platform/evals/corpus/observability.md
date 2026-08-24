# Metrics and tracing

Every HTTP request carries a request identifier, generated if the client did
not supply one, and bound into every log line for that request so a single
request can be traced across the logs. Metrics are exported in Prometheus
format, labeled by route template rather than raw path to keep cardinality
bounded.

Distributed tracing is optional and off by default. When enabled it exports
spans over OTLP to a collector, or prints them to standard output in the
console exporter mode used for local verification.
