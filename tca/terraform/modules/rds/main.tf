resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-rds-subnet"
  subnet_ids = var.subnet_ids
  tags       = merge(var.tags, { Name = "${var.name_prefix}-rds-subnet" })
}

resource "aws_db_parameter_group" "timescaledb" {
  name   = "${var.name_prefix}-pg16-timescaledb"
  family = "postgres16"

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-pg16-timescaledb" })
}

resource "aws_db_instance" "main" {
  identifier             = "${var.name_prefix}-postgres"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.instance_class
  db_name                = var.db_name
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.sg_id]
  parameter_group_name   = aws_db_parameter_group.timescaledb.name
  allocated_storage      = var.allocated_storage
  storage_type           = "gp2"
  storage_encrypted      = true
  backup_retention_period = 7
  skip_final_snapshot    = true
  deletion_protection    = false

  tags = merge(var.tags, { Name = "${var.name_prefix}-postgres" })
}
