# Backups and restore

The database is backed up with nightly base backups plus continuous WAL
archiving. Restore drills run monthly: restore the latest base backup, replay
WAL to a chosen point in time, then run the smoke suite against the restored
instance.

Backups include uploaded document bytes, since raw payloads live in the
documents table. Dumps are therefore encrypted at rest and access to them is
audited the same way as access to the uploads themselves.
