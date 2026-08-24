resource "aws_db_instance" "main" {
  identifier     = "${var.name}-postgres"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100 # storage autoscaling ceiling
  storage_type          = "gp3"

  db_name  = "rag"
  username = "rag"
  # Password managed by RDS in Secrets Manager — it never touches Terraform
  # state. The app's RAG_DATABASE_URL secret is composed from it out-of-band
  # (terraform/README.md).
  manage_master_user_password = true

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.db.id]
  multi_az               = var.db_multi_az

  backup_retention_period = 7
  # Demo-friendly defaults; terraform/README says loudly to flip both
  # before this holds data anyone cares about.
  deletion_protection = var.db_deletion_protection
  skip_final_snapshot = !var.db_deletion_protection

  performance_insights_enabled = true

  # pgvector: available on RDS Postgres 16; migration 0001 runs
  # CREATE EXTENSION IF NOT EXISTS vector, which the master user may do on RDS.
  # Using the master user as the app user is an accepted shortcut here —
  # a least-privilege app role is listed in the gaps.
}
