# Backend config is supplied at init time via -backend-config flags in CI.
# To initialise locally:
#   terraform init \
#     -backend-config="bucket=tca-terraform-state-<your-account-id>" \
#     -backend-config="key=prod/terraform.tfstate" \
#     -backend-config="region=eu-west-1" \
#     -backend-config="dynamodb_table=tca-terraform-locks"
terraform {
  backend "s3" {}
}
