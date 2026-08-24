variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  description = "Resource name prefix"
  type        = string
  default     = "rag"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "image_tag" {
  description = "Tag of the images pushed to ECR (see DEPLOYMENT.md)"
  type        = string
  default     = "latest"
}

variable "certificate_arn" {
  description = "ACM cert for HTTPS. Null = HTTP only (fine for a demo, say so out loud)"
  type        = string
  default     = null
}

# --- database ---
variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_multi_az" {
  description = "Off by default: this is a portfolio deployment, not an SLA"
  type        = bool
  default     = false
}

variable "db_deletion_protection" {
  description = "Flip to true (and skip_final_snapshot to false) for real data"
  type        = bool
  default     = false
}

# --- redis ---
variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

# --- services ---
# api and worker both load the local embedding model (PyTorch): ~1-1.5GB RSS,
# so 3GB memory headroom. Fargate only allows specific cpu/memory combos.
variable "api_cpu" {
  type    = number
  default = 1024
}
variable "api_memory" {
  type    = number
  default = 3072
}
variable "api_desired_count" {
  type    = number
  default = 2
}
variable "worker_cpu" {
  type    = number
  default = 1024
}
variable "worker_memory" {
  type    = number
  default = 3072
}
variable "worker_desired_count" {
  type    = number
  default = 1
}
variable "web_cpu" {
  type    = number
  default = 256
}
variable "web_memory" {
  type    = number
  default = 512
}
