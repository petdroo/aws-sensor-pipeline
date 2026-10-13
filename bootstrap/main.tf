data "aws_caller_identity" "current" {}

# Utilisateur IAM dédié à la CI/CD GitHub Actions
resource "aws_iam_user" "ci_user" {
  name = "github-actions-ci"
}

# Clé d'accès pour GitHub Actions
resource "aws_iam_access_key" "ci_user_key" {
  user = aws_iam_user.ci_user.name
}

# Permissions d'administration pour déployer les workloads
resource "aws_iam_user_policy_attachment" "ci_admin" {
  user       = aws_iam_user.ci_user.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# Outputs des identifiants pour GitHub Secrets
output "ci_user_name" {
  value = aws_iam_user.ci_user.name
}

output "ci_access_key_id" {
  value = aws_iam_access_key.ci_user_key.id
}

output "ci_secret_access_key" {
  value     = aws_iam_access_key.ci_user_key.secret
  sensitive = true
}