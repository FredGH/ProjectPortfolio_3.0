output "dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.tca.dashboard_name}"
}

output "sns_topic_arn" {
  value = aws_sns_topic.alarms.arn
}
