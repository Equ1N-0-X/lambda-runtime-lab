variable "aws_region" {
  description = "AWS region in which the benchmark resources are created."
  type        = string
  default     = "ap-northeast-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region name."
  }
}

variable "project_name" {
  description = "Lowercase project identifier used as a resource name prefix."
  type        = string
  default     = "lambda-runtime-lab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,28}[a-z0-9]$", var.project_name))
    error_message = "project_name must be 3-30 lowercase alphanumeric or hyphen characters and start with a letter."
  }
}

variable "lambda_memory_mb" {
  description = "Memory allocated equally to the Python and Rust Lambda functions."
  type        = number
  default     = 512

  validation {
    condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 10240 && floor(var.lambda_memory_mb) == var.lambda_memory_mb
    error_message = "lambda_memory_mb must be a whole number from 128 through 10240."
  }
}

variable "lambda_timeout_seconds" {
  description = "Timeout in seconds applied equally to the Python and Rust Lambda functions."
  type        = number
  default     = 30

  validation {
    condition     = var.lambda_timeout_seconds >= 1 && var.lambda_timeout_seconds <= 900 && floor(var.lambda_timeout_seconds) == var.lambda_timeout_seconds
    error_message = "lambda_timeout_seconds must be a whole number from 1 through 900."
  }
}

variable "architecture" {
  description = "Instruction set architecture used by both Lambda functions."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64."
  }
}

variable "python_image_uri" {
  description = "Python Lambda ECR image URI. It may be empty for terraform validate, but plan/apply requires an image URI from the managed Python repository."
  type        = string
  default     = ""

  validation {
    condition     = var.python_image_uri == "" || can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com(\\.cn)?/[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|@sha256:[0-9a-f]{64})$", var.python_image_uri))
    error_message = "python_image_uri must be empty or a complete private ECR image URI with a tag or sha256 digest."
  }
}

variable "rust_image_uri" {
  description = "Rust Lambda ECR image URI. It may be empty for terraform validate, but plan/apply requires an image URI from the managed Rust repository."
  type        = string
  default     = ""

  validation {
    condition     = var.rust_image_uri == "" || can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com(\\.cn)?/[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|@sha256:[0-9a-f]{64})$", var.rust_image_uri))
    error_message = "rust_image_uri must be empty or a complete private ECR image URI with a tag or sha256 digest."
  }
}

variable "environment" {
  description = "Lowercase environment identifier included in resource names and Lambda environment variables."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,10}[a-z0-9]$", var.environment))
    error_message = "environment must be 2-12 lowercase alphanumeric or hyphen characters and start with a letter."
  }
}
