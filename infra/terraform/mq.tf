resource "aws_security_group" "mq" {
  name        = "${var.project}-${var.env}-mq-sg"
  description = "Amazon MQ RabbitMQ SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5671
    to_port         = 5671
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.project}-${var.env}-mq-sg" }
}

resource "aws_mq_broker" "main" {
  broker_name         = "${var.project}-${var.env}"
  engine_type         = "RabbitMQ"
  engine_version      = "3.13"
  host_instance_type  = var.mq_instance_type
  deployment_mode     = "SINGLE_INSTANCE"
  subnet_ids          = [aws_subnet.private[0].id]
  security_groups     = [aws_security_group.mq.id]
  publicly_accessible = false

  user {
    username = "ec_mq_admin"
    password = "CHANGE_ME_via_secret_then_ignore"
  }

  lifecycle {
    ignore_changes = [user]
  }
}
