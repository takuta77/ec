resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.env}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.env}-rds-sg"
  description = "RDS Postgres SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.project}-${var.env}-rds-sg" }
}

resource "aws_db_instance" "main" {
  identifier                  = "${var.project}-${var.env}"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_type                = "gp3"
  db_name                     = "ec"
  username                    = "ec_admin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.rds.id]
  multi_az                    = false
  backup_retention_period     = 7
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.project}-${var.env}-final"
  deletion_protection         = true
  storage_encrypted           = true

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}
