terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Remote state: uncomment after bootstrapping the bucket + lock table
  # (terraform/README.md). Shipping it commented beats shipping a backend
  # that fails on first `init` for everyone who clones this.
  #
  # backend "s3" {
  #   bucket         = "<your-tf-state-bucket>"
  #   key            = "rag-knowledge-platform/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "<your-lock-table>"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "rag-knowledge-platform"
      ManagedBy = "terraform"
    }
  }
}
