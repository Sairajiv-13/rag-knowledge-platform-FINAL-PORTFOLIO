# Cost accounting

Every model call records the number of input and output tokens it consumed.
When per-token prices are configured, the platform computes a cost for each
call; when prices are not configured, the cost field is left null rather than
recording a fabricated number.

The usage endpoint aggregates recorded token counts and costs per tenant over
a requested time window. Because cost is null when prices are unconfigured,
the aggregate distinguishes between zero cost and unknown cost.
