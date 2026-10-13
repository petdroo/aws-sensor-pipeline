# Rôle d'exécution IAM pour la Lambda de traitement (handler)
resource "aws_iam_role" "lambda_handler" {
  name = "${var.project_name}-lambda-handler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Attachement pour l'intégration VPC (gestion des interfaces réseau ENI)
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda_handler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Politiques au moindre privilège : S3, SQS et RDS IAM Connect
resource "aws_iam_policy" "lambda_permissions" {
  name        = "${var.project_name}-lambda-policy"
  description = "Permissions for S3, SQS, and RDS IAM Auth"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3ReadAccess"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.landing.arn}/*"
      },
      {
        Sid    = "SQSConsumeAccess"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.main.arn
      },
      {
        Sid      = "RDSIAMConnect"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_db_instance.postgres.resource_id}/lambda_user"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_custom" {
  role       = aws_iam_role.lambda_handler.name
  policy_arn = aws_iam_policy.lambda_permissions.id
}

# Rôle d'exécution pour la Lambda d'initialisation de la DB (db_init)
resource "aws_iam_role" "lambda_db_init" {
  name = "${var.project_name}-lambda-db-init-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "db_init_vpc" {
  role       = aws_iam_role.lambda_db_init.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Archive ZIP du code de l'application
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../build"
  output_path = "${path.module}/../../lambda_bundle.zip"
}

# Lambda 1 : Initialisation de la base (db_init)
resource "aws_lambda_function" "db_init" {
  function_name    = "${var.project_name}-db-init"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  handler          = "src.db_init.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  role             = aws_iam_role.lambda_db_init.arn

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      DB_HOST     = aws_db_instance.postgres.address
      DB_NAME     = aws_db_instance.postgres.db_name
      DB_USER     = aws_db_instance.postgres.username
      DB_PASSWORD = random_password.master_password.result
      DB_PORT     = "5432"
    }
  }

  tags = {
    Name = "${var.project_name}-db-init"
  }

  depends_on = [aws_iam_role_policy_attachment.db_init_vpc]
}

# Lambda 2 : Traitement des données et détection d'anomalies (handler)
resource "aws_lambda_function" "processor" {
  function_name    = "${var.project_name}-processor"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  handler          = "src.handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  role             = aws_iam_role.lambda_handler.arn

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      DB_HOST = aws_db_instance.postgres.address
      DB_NAME = aws_db_instance.postgres.db_name
      DB_USER = "lambda_user"
      DB_PORT = "5432"
    }
  }

  tags = {
    Name = "${var.project_name}-processor"
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_vpc,
    aws_iam_role_policy_attachment.lambda_custom
  ]
}

# Déclencheur SQS -> Lambda avec gestion des échecs partiels
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn                   = aws_sqs_queue.main.arn
  function_name                      = aws_lambda_function.processor.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]

  depends_on = [
    aws_iam_role_policy_attachment.lambda_custom,
    aws_iam_role_policy_attachment.lambda_vpc
  ]
}
