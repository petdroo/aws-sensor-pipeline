variable "aws_region" {
  type        = string
  default     = "eu-north-1"
  description = "Région AWS de déploiement"
}

variable "project_name" {
  type        = string
  default     = "sensor-pipeline"
  description = "Préfixe des ressources du projet"
}

variable "alert_email" {
  type        = string
  description = "Email destinataire des alertes (à fournir via TF_VAR_alert_email, jamais en dur)"
}
