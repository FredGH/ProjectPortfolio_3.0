resource "aws_lb" "main" {
  name               = "${var.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.sg_alb_id]
  subnets            = var.subnet_ids

  tags = merge(var.tags, { Name = "${var.name_prefix}-alb" })
}

# ── Target groups ────────────────────────────────────────────────────────────

resource "aws_lb_target_group" "api" {
  name        = "${var.name_prefix}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/docs"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    timeout             = 10
    interval            = 30
    matcher             = "200"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-tg-api" })
}

resource "aws_lb_target_group" "mock" {
  name        = "${var.name_prefix}-mock"
  port        = 8001
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/docs"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    timeout             = 10
    interval            = 30
    matcher             = "200"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-tg-mock" })
}

resource "aws_lb_target_group" "airflow" {
  name        = "${var.name_prefix}-airflow"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    timeout             = 10
    interval            = 30
    matcher             = "200"
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-tg-airflow" })
}

# ── Listener and routing rules ───────────────────────────────────────────────

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not Found"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    path_pattern { values = ["/api/*"] }
  }
}

resource "aws_lb_listener_rule" "airflow" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.airflow.arn
  }
  condition {
    path_pattern { values = ["/airflow/*"] }
  }
}

resource "aws_lb_listener_rule" "mock" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 30

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mock.arn
  }
  condition {
    path_pattern { values = ["/mock/*"] }
  }
}
