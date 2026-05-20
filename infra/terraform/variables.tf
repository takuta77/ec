variable "project" {
  type    = string
  default = "ec-api"
}

variable "env" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "mq_instance_type" {
  type    = string
  default = "mq.t3.micro"
}

variable "github_repo" {
  type        = string
  description = "owner/name for GitHub OIDC trust"
  default     = "takuta77/ec"
}
