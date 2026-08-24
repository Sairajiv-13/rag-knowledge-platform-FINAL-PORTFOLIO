resource "aws_ecr_repository" "app" {
  name = "${var.name}-app" # one image for api + worker (no drift between them)
  # demo-friendly: allow `terraform destroy` with images present
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "web" {
  name         = "${var.name}-web"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}
