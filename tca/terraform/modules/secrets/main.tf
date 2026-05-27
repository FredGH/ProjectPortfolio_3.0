resource "aws_secretsmanager_secret" "db_credentials" {
  name                    = "tca/db-credentials"
  recovery_window_in_days = 0
  tags                    = merge(var.tags, { Name = "tca/db-credentials" })
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id     = aws_secretsmanager_secret.db_credentials.id
  # Plain URL — ECS valueFrom injects the entire string as DATABASE_URL.
  # The deploy workflow overwrites this with the real RDS endpoint after terraform apply.
  secret_string = "postgresql://${var.db_username}:${var.db_password}@PENDING_RDS_ENDPOINT:5432/tca_db"
}

resource "aws_secretsmanager_secret" "airflow_db" {
  name                    = "tca/airflow-db"
  recovery_window_in_days = 0
  tags                    = merge(var.tags, { Name = "tca/airflow-db" })
}

resource "aws_secretsmanager_secret_version" "airflow_db" {
  secret_id     = aws_secretsmanager_secret.airflow_db.id
  # SQLAlchemy format required by Airflow. Updated with real endpoint post-apply.
  secret_string = "postgresql+psycopg2://${var.db_username}:${var.db_password}@PENDING_RDS_ENDPOINT:5432/airflow_db"
}

resource "aws_secretsmanager_secret" "redis_url" {
  name                    = "tca/redis-url"
  recovery_window_in_days = 0
  tags                    = merge(var.tags, { Name = "tca/redis-url" })
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id     = aws_secretsmanager_secret.redis_url.id
  secret_string = "redis://PENDING_ELASTICACHE_ENDPOINT:6379/0"
}

resource "aws_secretsmanager_secret" "jwt_private_key" {
  name                    = "tca/jwt-private-key"
  recovery_window_in_days = 0
  tags                    = merge(var.tags, { Name = "tca/jwt-private-key" })
}

resource "aws_secretsmanager_secret_version" "jwt_private_key" {
  secret_id     = aws_secretsmanager_secret.jwt_private_key.id
  secret_string = "REPLACE_WITH_RSA_PRIVATE_KEY_PEM"
}

resource "aws_secretsmanager_secret" "jwt_public_key" {
  name                    = "tca/jwt-public-key"
  recovery_window_in_days = 0
  tags                    = merge(var.tags, { Name = "tca/jwt-public-key" })
}

resource "aws_secretsmanager_secret_version" "jwt_public_key" {
  secret_id     = aws_secretsmanager_secret.jwt_public_key.id
  secret_string = "REPLACE_WITH_RSA_PUBLIC_KEY_PEM"
}

resource "aws_secretsmanager_secret" "airflow_secret_key" {
  name                    = "tca/airflow-secret-key"
  recovery_window_in_days = 0
  tags                    = merge(var.tags, { Name = "tca/airflow-secret-key" })
}

resource "aws_secretsmanager_secret_version" "airflow_secret_key" {
  secret_id     = aws_secretsmanager_secret.airflow_secret_key.id
  secret_string = "REPLACE_WITH_RANDOM_SECRET_KEY"
}
