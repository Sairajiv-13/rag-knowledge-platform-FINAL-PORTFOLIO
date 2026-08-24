# Rate limits

Data-plane requests are limited per tenant per minute using a fixed window in
Redis. The known weakness of fixed windows is a burst of up to twice the limit
straddling a window boundary, accepted for simplicity at current limits.

The token endpoint has a separate, tighter limit keyed by client id, enforced
before any secret comparison so brute force attempts are throttled cheaply.
If Redis is unavailable the limiter fails open and logs a warning.
