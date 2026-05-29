variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "log_group_name" {
  type        = string
  description = "CloudWatch log group shared by all ECS services."
}

variable "rds_instance_id" {
  type        = string
  description = "RDS DB instance identifier (not ARN)."
}

variable "alarm_email" {
  type        = string
  default     = ""
  description = "Email to notify on alarms. Leave empty to skip the SNS email subscription."
}

variable "cpu_alarm_threshold" {
  type    = number
  default = 80
}

variable "memory_alarm_threshold" {
  type    = number
  default = 80
}

variable "rds_connections_threshold" {
  type    = number
  default = 80
}

variable "rds_free_storage_bytes" {
  type        = number
  default     = 1073741824 # 1 GB
  description = "Alarm when RDS free storage drops below this value (bytes)."
}

variable "monthly_budget_limit_usd" {
  type        = number
  default     = 50
  description = "Monthly AWS spend limit in USD. Alarms fire at 80% actual and 100% forecasted."
}

variable "tags" {
  type    = map(string)
  default = {}
}
