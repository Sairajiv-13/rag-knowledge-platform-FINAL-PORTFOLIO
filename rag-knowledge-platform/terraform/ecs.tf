resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled" # CloudWatch is the cloud-side metrics story (see gaps)
  }
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["api", "worker", "web"])
  name              = "/ecs/${var.name}-${each.key}"
  retention_in_days = 30
}

locals {
  redis_url = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"

  app_environment = [
    { name = "RAG_REDIS_URL", value = local.redis_url },
    { name = "RAG_ENVIRONMENT", value = "prod" },
    { name = "RAG_LLM_PROVIDER", value = "anthropic" },
    { name = "RAG_EMBEDDING_PROVIDER", value = "local" },
  ]

  app_secrets = [
    { name = "RAG_DATABASE_URL", valueFrom = aws_secretsmanager_secret.app["database_url"].arn },
    { name = "RAG_JWT_SECRET", valueFrom = aws_secretsmanager_secret.app["jwt_secret"].arn },
    { name = "RAG_ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.app["anthropic_key"].arn },
  ]

  log_config = { for k in ["api", "worker", "web"] : k => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.svc[k].name
      awslogs-region        = var.region
      awslogs-stream-prefix = k
    }
  } }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = local.app_environment
    secrets      = local.app_secrets
    # No container healthCheck: the slim runtime image ships no curl, and the
    # ALB's /readyz check is what actually gates traffic.
    logConfiguration = local.log_config["api"]
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true
    command = [
      "celery", "-A", "rag_platform.worker.celery_app",
      "worker", "--loglevel=INFO", "--concurrency=2",
    ]
    # jwt/anthropic secrets included because Settings validates the full
    # environment (documented trade-off of one Settings for all processes)
    environment      = local.app_environment
    secrets          = local.app_secrets
    logConfiguration = local.log_config["worker"]
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name         = "web"
    image        = "${aws_ecr_repository.web.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    environment = [
      # The BFF calls the API via the public ALB: simplest correct wiring.
      # ECS Service Connect (private mesh) is the documented refinement.
      { name = "RAG_API_URL", value = "http://${aws_lb.main.dns_name}" },
    ]
    secrets = [
      { name = "RAG_CLIENT_ID", valueFrom = "${aws_secretsmanager_secret.app["web_credentials"].arn}:client_id::" },
      { name = "RAG_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.app["web_credentials"].arn}:client_secret::" },
    ]
    logConfiguration = local.log_config["web"]
  }])
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = module.vpc.private_subnets
    security_groups = [aws_security_group.service.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  health_check_grace_period_seconds = 60
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = module.vpc.private_subnets
    security_groups = [aws_security_group.service.id]
  }
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = module.vpc.private_subnets
    security_groups = [aws_security_group.service.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }
}
