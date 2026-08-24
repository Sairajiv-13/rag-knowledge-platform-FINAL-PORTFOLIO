# Community VPC module over ~40 hand-rolled resources: boring, proven, and
# reviewable — the interesting engineering in this repo is not subnet math.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = var.name
  cidr = var.vpc_cidr

  azs              = ["${var.region}a", "${var.region}b"]
  public_subnets   = [cidrsubnet(var.vpc_cidr, 8, 0), cidrsubnet(var.vpc_cidr, 8, 1)]
  private_subnets  = [cidrsubnet(var.vpc_cidr, 8, 10), cidrsubnet(var.vpc_cidr, 8, 11)]
  database_subnets = [cidrsubnet(var.vpc_cidr, 8, 20), cidrsubnet(var.vpc_cidr, 8, 21)]

  create_database_subnet_group = true

  enable_nat_gateway = true
  # One NAT, not one per AZ: ~$32/mo each, and an AZ outage taking dev NAT
  # with it is an accepted risk here. Flip for production HA.
  single_nat_gateway = true

  enable_dns_hostnames = true
}
