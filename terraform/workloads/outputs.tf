output "s3_landing_bucket" {
  value       = aws_s3_bucket.landing.id
  description = "Nom du bucket S3 de dépôt des fichiers CSV"
}

output "sqs_main_queue_url" {
  value       = aws_sqs_queue.main.url
  description = "URL de la file SQS principale"
}

output "sqs_dlq_url" {
  value       = aws_sqs_queue.dlq.url
  description = "URL de la file Dead-Letter Queue"
}

output "rds_endpoint" {
  value       = aws_db_instance.postgres.address
  description = "Endpoint de l'instance PostgreSQL privée"
}

output "lambda_processor_name" {
  value       = aws_lambda_function.processor.function_name
  description = "Nom de la fonction Lambda de traitement"
}

output "lambda_db_init_name" {
  value       = aws_lambda_function.db_init.function_name
  description = "Nom de la fonction Lambda d'initialisation de base"
}

output "sns_topic_arn" {
  value       = aws_sns_topic.alerts.arn
  description = "ARN du topic SNS des alertes d'anomalies"
}

output "lambda_query_name" {
  value       = aws_lambda_function.query.function_name
  description = "Nom de la fonction Lambda de consultation des données"
}