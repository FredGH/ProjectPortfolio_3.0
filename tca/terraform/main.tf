# Root configuration — full production deployment.
# For LocalStack-only testing use environments/local.
# For prod plan/apply use environments/prod.

module "vpc" {
  source      = "./modules/vpc"
  name_prefix = local.name_prefix
  tags        = local.common_tags
}

module "iam" {
  source      = "./modules/iam"
  name_prefix = local.name_prefix
  tags        = local.common_tags
}

module "secrets" {
  source      = "./modules/secrets"
  name_prefix = local.name_prefix
  db_username = var.db_username
  db_password = var.db_password
  tags        = local.common_tags
}

module "ecr" {
  source      = "./modules/ecr"
  name_prefix = local.name_prefix
  tags        = local.common_tags
}

module "rds" {
  source      = "./modules/rds"
  name_prefix = local.name_prefix
  subnet_ids  = module.vpc.private_subnet_ids
  sg_id       = module.vpc.sg_rds_id
  db_username = var.db_username
  db_password = var.db_password
  tags        = local.common_tags
}

module "elasticache" {
  source      = "./modules/elasticache"
  name_prefix = local.name_prefix
  subnet_ids  = module.vpc.private_subnet_ids
  sg_id       = module.vpc.sg_redis_id
  tags        = local.common_tags
}

module "ecs_cluster" {
  source      = "./modules/ecs/cluster"
  name_prefix = local.name_prefix
  tags        = local.common_tags
}

module "alb" {
  source      = "./modules/alb"
  name_prefix = local.name_prefix
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.public_subnet_ids
  sg_alb_id   = module.vpc.sg_alb_id
  tags        = local.common_tags
}

module "cdn" {
  source       = "./modules/cdn"
  name_prefix  = local.name_prefix
  alb_dns_name = module.alb.dns_name
  tags         = local.common_tags
}

module "ecs_api" {
  source                     = "./modules/ecs/api"
  name_prefix                = local.name_prefix
  aws_region                 = var.aws_region
  cluster_arn                = module.ecs_cluster.cluster_arn
  image_url                  = "${local.ecr_registry}/tca-api:${var.image_tag}"
  execution_role_arn         = module.iam.execution_role_arn
  task_role_arn              = module.iam.task_role_arn
  subnet_ids                 = module.vpc.private_subnet_ids
  sg_id                      = module.vpc.sg_api_id
  target_group_arn           = module.alb.api_target_group_arn
  log_group_name             = module.ecs_cluster.log_group_name
  db_secret_arn              = module.secrets.db_credentials_arn
  redis_url_secret_arn       = module.secrets.redis_url_arn
  jwt_private_key_secret_arn = module.secrets.jwt_private_key_arn
  jwt_public_key_secret_arn  = module.secrets.jwt_public_key_arn
  tags                       = local.common_tags
}

module "ecs_mock_server" {
  source             = "./modules/ecs/mock_server"
  name_prefix        = local.name_prefix
  aws_region         = var.aws_region
  cluster_arn        = module.ecs_cluster.cluster_arn
  image_url          = "${local.ecr_registry}/tca-mock-server:${var.image_tag}"
  execution_role_arn = module.iam.execution_role_arn
  task_role_arn      = module.iam.task_role_arn
  subnet_ids         = module.vpc.private_subnet_ids
  sg_id              = module.vpc.sg_mock_id
  target_group_arn   = module.alb.mock_target_group_arn
  log_group_name     = module.ecs_cluster.log_group_name
  db_secret_arn      = module.secrets.db_credentials_arn
  redis_url_secret_arn = module.secrets.redis_url_arn
  tags               = local.common_tags
}

module "ecs_airflow_webserver" {
  source                  = "./modules/ecs/airflow_webserver"
  name_prefix             = local.name_prefix
  aws_region              = var.aws_region
  cluster_arn             = module.ecs_cluster.cluster_arn
  image_url               = "${local.ecr_registry}/tca-airflow:${var.image_tag}"
  execution_role_arn      = module.iam.execution_role_arn
  task_role_arn           = module.iam.task_role_arn
  subnet_ids              = module.vpc.private_subnet_ids
  sg_id                   = module.vpc.sg_airflow_id
  target_group_arn        = module.alb.airflow_target_group_arn
  log_group_name          = module.ecs_cluster.log_group_name
  db_secret_arn           = module.secrets.db_credentials_arn
  redis_url_secret_arn    = module.secrets.redis_url_arn
  airflow_secret_key_arn  = module.secrets.airflow_secret_key_arn
  tags                    = local.common_tags
}

module "ecs_airflow_scheduler" {
  source                  = "./modules/ecs/airflow_scheduler"
  name_prefix             = local.name_prefix
  aws_region              = var.aws_region
  cluster_arn             = module.ecs_cluster.cluster_arn
  image_url               = "${local.ecr_registry}/tca-airflow:${var.image_tag}"
  execution_role_arn      = module.iam.execution_role_arn
  task_role_arn           = module.iam.task_role_arn
  subnet_ids              = module.vpc.private_subnet_ids
  sg_id                   = module.vpc.sg_airflow_id
  log_group_name          = module.ecs_cluster.log_group_name
  db_secret_arn           = module.secrets.db_credentials_arn
  redis_url_secret_arn    = module.secrets.redis_url_arn
  airflow_secret_key_arn  = module.secrets.airflow_secret_key_arn
  tags                    = local.common_tags
}
