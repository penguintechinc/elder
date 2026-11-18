/**
 * IBM Carbon Icon Adapter
 * Provides wrapper components for @carbon/icons-react icons normalized to IconProps interface
 */

import { createElement } from 'react';
import {
  Cloud,
  DataBase,
  Security,
  IbmSecurity,
  Locked,
  Deploy,
  ContainerServices,
  ChartLine,
  Ai,
  Code,
  Document,
  IbmCloud,
  Analytics,
  DataSet,
  ChartBar,
  Network_1,
  Time,
  Calculator,
  DecisionTree,
  ContainerRegistry,
  Model,
  CloudMonitoring,
  DataAnalytics,
  CloudDataOps,
  IbmDb2,
  IbmCloudSecurityGroups,
  IbmCloudKubernetesService,
} from '@carbon/icons-react';

import type { IconProps, IconComponent, IconMap } from '../types';

/**
 * Create a wrapper component that normalizes Carbon icons to IconProps interface
 */
const createIconWrapper = (
  CarbonIcon: React.ComponentType<any>,
  defaultSize: number = 20
): IconComponent => {
  return function WrappedIcon({ className = '', size = defaultSize, color }: IconProps) {
    const sizeNum = typeof size === 'string' ? parseInt(size, 10) : size;

    // Normalize size to Carbon's supported sizes
    let carbonSize: 16 | 20 | 24 | 32 = 20;
    if (sizeNum <= 16) carbonSize = 16;
    else if (sizeNum <= 20) carbonSize = 20;
    else if (sizeNum <= 24) carbonSize = 24;
    else carbonSize = 32;

    return createElement(CarbonIcon, {
      size: carbonSize,
      className,
      style: color ? { color } : undefined,
    });
  };
};

/**
 * Icon map with 30+ useful IBM Carbon icons
 */
export const ibmIconMap: IconMap = {
  'ibm-cloud': createIconWrapper(IbmCloud),
  'ibm-database': createIconWrapper(DataBase),
  'ibm-security': createIconWrapper(IbmSecurity),
  'ibm-network': createIconWrapper(Network_1),
  'ibm-analytics': createIconWrapper(Analytics),
  'ibm-dataset': createIconWrapper(DataSet),
  'ibm-ai': createIconWrapper(Ai),
  'ibm-code': createIconWrapper(Code),
  'ibm-locked': createIconWrapper(Locked),
  'ibm-deploy': createIconWrapper(Deploy),
  'ibm-container-services': createIconWrapper(ContainerServices),
  'ibm-container-registry': createIconWrapper(ContainerRegistry),
  'ibm-kubernetes': createIconWrapper(IbmCloudKubernetesService),
  'ibm-security-groups': createIconWrapper(IbmCloudSecurityGroups),
  'ibm-document': createIconWrapper(Document),
  'ibm-chart-bar': createIconWrapper(ChartBar),
  'ibm-chart-line': createIconWrapper(ChartLine),
  'ibm-monitor': createIconWrapper(CloudMonitoring),
  'ibm-time': createIconWrapper(Time),
  'ibm-calculate': createIconWrapper(Calculator),
  'ibm-decision-tree': createIconWrapper(DecisionTree),
  'ibm-model': createIconWrapper(Model),
  'ibm-cloud-monitoring': createIconWrapper(CloudMonitoring),
  'ibm-data-analytics': createIconWrapper(DataAnalytics),
  'ibm-cloud-data-ops': createIconWrapper(CloudDataOps),
  'ibm-db2': createIconWrapper(IbmDb2),
  'ibm-security-icon': createIconWrapper(Security),
  'ibm-cloud-security': createIconWrapper(IbmSecurity),
  'ibm-generic-cloud': createIconWrapper(Cloud),
};

export type { IconComponent, IconProps } from '../types';
