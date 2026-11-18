// AWS Icon Categories and Metadata
// Defines categories, icon definitions, and metadata for AWS icons

import type { IconCategory, IconDefinition } from '../types';

// AWS Brand Color
const AWS_ORANGE = '#FF9900';

// ============================================
// ICON DEFINITIONS
// ============================================

const computeIcons: IconDefinition[] = [
  {
    id: 'aws-ec2',
    label: 'EC2',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['compute', 'instance', 'server', 'virtual machine', 'vm'],
  },
  {
    id: 'aws-lambda',
    label: 'Lambda',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['compute', 'serverless', 'functions', 'function', 'event-driven'],
  },
  {
    id: 'aws-ecs',
    label: 'ECS',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['compute', 'container', 'docker', 'orchestration', 'elastic container service'],
  },
  {
    id: 'aws-eks',
    label: 'EKS',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['compute', 'kubernetes', 'container', 'orchestration', 'elastic kubernetes service'],
  },
  {
    id: 'aws-fargate',
    label: 'Fargate',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['compute', 'serverless', 'container', 'docker', 'ecs'],
  },
];

const storageIcons: IconDefinition[] = [
  {
    id: 'aws-s3',
    label: 'S3',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['storage', 'object', 'bucket', 'simple storage service', 'data'],
  },
  {
    id: 'aws-cloudfront',
    label: 'CloudFront',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['storage', 'cdn', 'distribution', 'content delivery', 'cache'],
  },
];

const databaseIcons: IconDefinition[] = [
  {
    id: 'aws-rds',
    label: 'RDS',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['database', 'relational', 'sql', 'mysql', 'postgres', 'aurora'],
  },
  {
    id: 'aws-dynamodb',
    label: 'DynamoDB',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['database', 'nosql', 'table', 'key-value', 'document'],
  },
];

const networkingIcons: IconDefinition[] = [
  {
    id: 'aws-vpc',
    label: 'VPC',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['networking', 'network', 'virtual private cloud', 'vpc', 'subnet'],
  },
  {
    id: 'aws-elb',
    label: 'Load Balancer',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['networking', 'load balancing', 'alb', 'nlb', 'elb', 'traffic', 'distribution'],
  },
  {
    id: 'aws-route53',
    label: 'Route 53',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['networking', 'dns', 'domain', 'routing', 'health check'],
  },
  {
    id: 'aws-api-gateway',
    label: 'API Gateway',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['networking', 'api', 'gateway', 'rest', 'websocket', 'integration'],
  },
];

const securityIcons: IconDefinition[] = [
  {
    id: 'aws-iam',
    label: 'IAM',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['security', 'access', 'identity', 'permission', 'role', 'user', 'policy'],
  },
  {
    id: 'aws-cognito',
    label: 'Cognito',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['security', 'authentication', 'authorization', 'user pool', 'identity pool', 'mfa'],
  },
  {
    id: 'aws-secrets-manager',
    label: 'Secrets Manager',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['security', 'secret', 'credential', 'password', 'key', 'encryption'],
  },
  {
    id: 'aws-kms',
    label: 'KMS',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['security', 'encryption', 'key', 'key management', 'cryptography'],
  },
];

const integrationIcons: IconDefinition[] = [
  {
    id: 'aws-sns',
    label: 'SNS',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['integration', 'messaging', 'notification', 'pubsub', 'topic', 'simple notification service'],
  },
  {
    id: 'aws-sqs',
    label: 'SQS',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['integration', 'messaging', 'queue', 'async', 'simple queue service'],
  },
];

const monitoringIcons: IconDefinition[] = [
  {
    id: 'aws-cloudwatch',
    label: 'CloudWatch',
    color: AWS_ORANGE,
    source: 'aws',
    tags: ['monitoring', 'logging', 'metrics', 'logs', 'dashboard', 'alerts'],
  },
];

// ============================================
// CATEGORIES
// ============================================

export const awsCategories: Record<string, IconCategory> = {
  compute: {
    label: 'Compute',
    source: 'aws',
    icons: computeIcons,
  },
  storage: {
    label: 'Storage',
    source: 'aws',
    icons: storageIcons,
  },
  database: {
    label: 'Database',
    source: 'aws',
    icons: databaseIcons,
  },
  networking: {
    label: 'Networking',
    source: 'aws',
    icons: networkingIcons,
  },
  security: {
    label: 'Security',
    source: 'aws',
    icons: securityIcons,
  },
  integration: {
    label: 'Integration',
    source: 'aws',
    icons: integrationIcons,
  },
  monitoring: {
    label: 'Monitoring',
    source: 'aws',
    icons: monitoringIcons,
  },
};

/**
 * Get all AWS icon definitions across all categories
 */
export const getAllAwsIcons = (): IconDefinition[] => {
  return Object.values(awsCategories).flatMap((category) => category.icons);
};

/**
 * Get AWS icon definition by ID
 */
export const getAwsIconDefinition = (id: string): IconDefinition | undefined => {
  return getAllAwsIcons().find((icon) => icon.id === id);
};

/**
 * Get all AWS icon IDs
 */
export const getAwsIconIds = (): string[] => {
  return getAllAwsIcons().map((icon) => icon.id);
};

/**
 * Get AWS category names
 */
export const getAwsCategoryNames = (): string[] => {
  return Object.keys(awsCategories);
};

/**
 * Get AWS icons by category key
 */
export const getAwsIconsByCategory = (categoryKey: string): IconDefinition[] => {
  return awsCategories[categoryKey]?.icons || [];
};
