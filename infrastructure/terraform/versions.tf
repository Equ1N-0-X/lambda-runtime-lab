# Terraform本体とAWS Providerの互換範囲を固定し、環境差による計画変更を防ぐ。
terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
