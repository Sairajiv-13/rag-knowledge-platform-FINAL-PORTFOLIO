# Billing and metering

Every model call writes a usage record with the provider-reported input and
output token counts. Cost is computed only when per-million-token prices are
configured; otherwise the cost column stays null rather than recording an
invented number.

The usage endpoint rolls records up per model over a chosen window. A rollup
that would mix costed and uncosted rows reports the total cost as unknown
instead of presenting a partial sum as the truth.
