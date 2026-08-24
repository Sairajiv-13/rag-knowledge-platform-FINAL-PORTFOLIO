output "url" {
  value = "${local.https_on ? "https" : "http"}://${aws_lb.main.dns_name}"
}

output "ecr_app_repository" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_web_repository" {
  value = aws_ecr_repository.web.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.main.endpoint # host:port
}

output "rds_master_secret_arn" {
  description = "RDS-managed master credentials (compose RAG_DATABASE_URL from this)"
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "database_url_secret_arn" {
  value = aws_secretsmanager_secret.app["database_url"].arn
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

# Everything the one-off `aws ecs run-task` commands (migrations, tenant
# bootstrap) need — see terraform/README.md.
output "private_subnets" {
  value = module.vpc.private_subnets
}

output "service_security_group_id" {
  value = aws_security_group.service.id
}
