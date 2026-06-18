# UAT environment — used with:
#   terraform init -backend-config=environments/uat.backend
#   terraform plan  -var-file=environments/uat.tfvars
#   terraform apply -var-file=environments/uat.tfvars

snowflake_organization_name = "UFNDSPC"    # SELECT CURRENT_ORGANIZATION_NAME()
snowflake_account_name      = "GJ37236" # SELECT CURRENT_ACCOUNT_NAME()
snowflake_user             = "TERRAFORM_SVC"
snowflake_private_key_path = "~/.ssh/terraform_svc.p8"

environment = "uat"
