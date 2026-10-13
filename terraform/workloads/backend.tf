terraform {
  backend "s3" {
    bucket       = "mon-tfstate-thomas-01" # Remplace par le nom exact de ton bucket si différent
    key          = "workloads/terraform.tfstate"
    region       = "eu-north-1"
    use_lockfile = true
  }
}