// AWS Icon Adapter
// Wraps aws-react-icons components to conform to IconProps interface

import { createElement } from 'react';
import type { CSSProperties } from 'react';
import {
  ArchitectureServiceAmazonEC2,
  ArchitectureServiceAmazonSimpleStorageService,
  ArchitectureServiceAWSLambda,
  ArchitectureServiceAmazonRDS,
  ArchitectureServiceAmazonDynamoDB,
  ArchitectureServiceAmazonVirtualPrivateCloud,
  ArchitectureServiceAmazonCloudFront,
  ArchitectureServiceElasticLoadBalancing,
  ArchitectureServiceAmazonRoute53,
  ArchitectureServiceAWSIdentityandAccessManagement,
  ArchitectureServiceAmazonCloudWatch,
  ArchitectureServiceAmazonSimpleNotificationService,
  ArchitectureServiceAmazonSimpleQueueService,
  ArchitectureServiceAmazonElasticContainerService,
  ArchitectureServiceAmazonElasticKubernetesService,
  ArchitectureServiceAWSFargate,
  ArchitectureServiceAmazonAPIGateway,
  ArchitectureServiceAmazonCognito,
  ArchitectureServiceAWSSecretsManager,
  ArchitectureServiceAWSKeyManagementService,
} from 'aws-react-icons';

import type { IconProps, IconComponent } from '../types';

/**
 * Wrapper function to normalize AWS icon components to IconProps interface
 * Handles className, size (as number or string), and color props
 */
const createAwsIconWrapper = (
  AwsIconComponent: any
): IconComponent => {
  return ({ className, size, color }: IconProps) => {
    // Build style object from props
    const style: CSSProperties = {};

    // Handle color
    if (color) {
      style.color = color;
    }

    // Handle size - convert to CSS units if needed
    if (size) {
      const sizeStr = typeof size === 'number' ? `${size}px` : size;
      style.width = sizeStr;
      style.height = sizeStr;
    }

    return createElement(AwsIconComponent, {
      className,
      style,
    });
  };
};

// ============================================
// AWS ICONS MAP
// ============================================

export const awsIconMap: Record<string, IconComponent> = {
  // Compute Icons
  'aws-ec2': createAwsIconWrapper(ArchitectureServiceAmazonEC2),
  'aws-lambda': createAwsIconWrapper(ArchitectureServiceAWSLambda),
  'aws-ecs': createAwsIconWrapper(ArchitectureServiceAmazonElasticContainerService),
  'aws-eks': createAwsIconWrapper(ArchitectureServiceAmazonElasticKubernetesService),
  'aws-fargate': createAwsIconWrapper(ArchitectureServiceAWSFargate),

  // Storage Icons
  'aws-s3': createAwsIconWrapper(ArchitectureServiceAmazonSimpleStorageService),
  'aws-cloudfront': createAwsIconWrapper(ArchitectureServiceAmazonCloudFront),

  // Database Icons
  'aws-rds': createAwsIconWrapper(ArchitectureServiceAmazonRDS),
  'aws-dynamodb': createAwsIconWrapper(ArchitectureServiceAmazonDynamoDB),

  // Networking Icons
  'aws-vpc': createAwsIconWrapper(ArchitectureServiceAmazonVirtualPrivateCloud),
  'aws-elb': createAwsIconWrapper(ArchitectureServiceElasticLoadBalancing),
  'aws-route53': createAwsIconWrapper(ArchitectureServiceAmazonRoute53),
  'aws-api-gateway': createAwsIconWrapper(ArchitectureServiceAmazonAPIGateway),

  // Security Icons
  'aws-iam': createAwsIconWrapper(ArchitectureServiceAWSIdentityandAccessManagement),
  'aws-cognito': createAwsIconWrapper(ArchitectureServiceAmazonCognito),
  'aws-secrets-manager': createAwsIconWrapper(ArchitectureServiceAWSSecretsManager),
  'aws-kms': createAwsIconWrapper(ArchitectureServiceAWSKeyManagementService),

  // Integration & Monitoring Icons
  'aws-sns': createAwsIconWrapper(ArchitectureServiceAmazonSimpleNotificationService),
  'aws-sqs': createAwsIconWrapper(ArchitectureServiceAmazonSimpleQueueService),
  'aws-cloudwatch': createAwsIconWrapper(ArchitectureServiceAmazonCloudWatch),
};

// Export icon map for easy access
export type AwsIconId = keyof typeof awsIconMap;

/**
 * Get an AWS icon component by ID
 */
export const getAwsIcon = (id: AwsIconId): IconComponent | undefined => {
  return awsIconMap[id];
};

/**
 * Get all available AWS icon IDs
 */
export const getAwsIconIds = (): AwsIconId[] => {
  return Object.keys(awsIconMap) as AwsIconId[];
};
