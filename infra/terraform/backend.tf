# Bootstrap (run ONCE, manually, before `terraform init`):
#   aws s3api create-bucket --bucket <STATE_BUCKET> --region <REGION> \
#     --create-bucket-configuration LocationConstraint=<REGION>
#   aws dynamodb create-table --table-name <LOCK_TABLE> \
#     --attribute-definitions AttributeName=LockID,AttributeType=S \
#     --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST
#
# Then replace the placeholders below with your real bucket/table and run
# `terraform init`. Terraform backend blocks do NOT support variables.
terraform {
  backend "s3" {
    bucket         = "REPLACE_ME_ec-api-tfstate"
    key            = "ec-api/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "REPLACE_ME_ec-api-tflock"
    encrypt        = true
  }
}
