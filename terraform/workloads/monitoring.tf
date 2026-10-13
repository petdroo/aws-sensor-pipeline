resource "aws_cloudwatch_log_group" "processor_logs" {
  name              = "/aws/lambda/${aws_lambda_function.processor.function_name}"
  retention_in_days = 7

  tags = {
    Name = "${var.project_name}-processor-logs"
  }
}

# Filtre métrique basé sur le pattern JSON émis par src/handler.py
resource "aws_cloudwatch_log_metric_filter" "anomalies" {
  name           = "${var.project_name}-anomalies-filter"
  pattern        = "{ ($.severity = \"CRITICAL\") && ($.message = \"ANOMALY_DETECTED\") }"
  log_group_name = aws_cloudwatch_log_group.processor_logs.name

  metric_transformation {
    name          = "SensorAnomalyCount"
    namespace     = "SensorPipeline"
    value         = "1"
    default_value = "0"
  }
}

# Topic SNS pour notifier par email
resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"

  tags = {
    Name = "${var.project_name}-alerts"
  }
}

resource "aws_sns_topic_subscription" "email_sub" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Alarme CloudWatch : déclenchée si >= 1 anomalie détectée sur une période de 60s
resource "aws_cloudwatch_metric_alarm" "anomaly_alarm" {
  alarm_name          = "${var.project_name}-critical-anomaly-alarm"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = aws_cloudwatch_log_metric_filter.anomalies.metric_transformation[0].name
  namespace           = aws_cloudwatch_log_metric_filter.anomalies.metric_transformation[0].namespace
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Alarme déclenchée lorsqu'une anomalie statistique (|z| >= 3) est ingérée."
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${var.project_name}-anomaly-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_alarm" {
  alarm_name          = "${var.project_name}-dlq-not-empty"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${var.project_name}-dlq-alarm"
  }
}
