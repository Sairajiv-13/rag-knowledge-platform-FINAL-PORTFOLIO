# Authentication model

Clients hold OAuth2 client credentials and exchange them at the token endpoint
for a thirty minute JWT. Every request re-checks the credential row, so
revoking a credential takes effect on the next request rather than at token
expiry.

Secrets are 256-bit random strings stored as SHA-256 hashes. Unknown client
identifiers and wrong secrets return the same error through the same code
path, so the endpoint cannot be used to enumerate which client ids exist.
