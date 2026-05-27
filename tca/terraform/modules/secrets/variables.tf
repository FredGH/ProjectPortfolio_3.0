variable "name_prefix" {
  type = string
}

variable "db_username" {
  type      = string
  default   = "tca_user"
  sensitive = true
}

variable "db_password" {
  type      = string
  default   = "CHANGE_ME_BEFORE_DEPLOY"
  sensitive = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
