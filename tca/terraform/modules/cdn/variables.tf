variable "name_prefix" {
  type = string
}

variable "alb_dns_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
