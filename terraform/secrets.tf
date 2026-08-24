# Secret CONTAINERS only — values are written out-of-band with
# `aws secretsmanager put-secret-value` (terraform/README.md), so no secret
# value ever enters Terraform state or this repo.

locals {
  secret_names = {
    database_url    = "${var.name}/database-url"
    jwt_secret      = "${var.name}/jwt-secret"
    anthropic_key   = "${var.name}/anthropic-api-key"
    web_credentials = "${var.name}/web-ui-credentials" # JSON: {"client_id":..,"client_secret":..}
  }
}

resource "aws_secretsmanager_secret" "app" {
  for_each = local.secret_names
  name     = each.value
  # 0 = hard delete on destroy, so demo teardown/recreate doesn't collide with
  # the 7-30 day recovery window. Set a window for real deployments.
  recovery_window_in_days = 0
}
