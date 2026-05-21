# Bootstrap (run ONCE, manually, before `terraform init`):
#   aws s3api create-bucket --bucket <STATE_BUCKET> --region <REGION> \
#     --create-bucket-configuration LocationConstraint=<REGION>
#   aws s3api put-bucket-versioning --bucket <STATE_BUCKET> \
#     --versioning-configuration Status=Enabled
#
# Then replace the placeholder below with your real bucket and run
# `terraform init`. Terraform backend blocks do NOT support variables.
#
# State locking uses S3 native locking (Terraform 1.10+), no DynamoDB.
terraform {
  backend "s3" {
    bucket       = "REPLACE_ME_ec-api-tfstate"
    key          = "ec-api/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
