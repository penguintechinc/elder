/**
 * IBM Carbon Icon Categories
 * Organizes IBM icons by category with metadata
 */

import type { IconCategory, IconDefinition } from '../types';

/**
 * IBM blue color (Carbon's primary brand color)
 */
const IBM_BLUE = '#0F62FE';

/**
 * Cloud and Infrastructure icons
 */
const cloudIcons: IconDefinition[] = [
  {
    id: 'ibm-cloud',
    label: 'IBM Cloud',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['cloud', 'computing', 'infrastructure', 'deployment', 'ibm cloud'],
  },
  {
    id: 'ibm-generic-cloud',
    label: 'Cloud',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['cloud', 'compute', 'infrastructure'],
  },
  {
    id: 'ibm-database',
    label: 'Database',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['database', 'storage', 'data', 'query', 'db'],
  },
  {
    id: 'ibm-db2',
    label: 'IBM DB2',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['db2', 'database', 'enterprise', 'ibm'],
  },
  {
    id: 'ibm-container-services',
    label: 'Container Services',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['container', 'services', 'docker', 'applications'],
  },
  {
    id: 'ibm-container-registry',
    label: 'Container Registry',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['registry', 'container', 'image', 'docker'],
  },
  {
    id: 'ibm-kubernetes',
    label: 'Kubernetes',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['kubernetes', 'k8s', 'container', 'orchestration', 'iks'],
  },
];

/**
 * Security and Access icons
 */
const securityIcons: IconDefinition[] = [
  {
    id: 'ibm-security',
    label: 'IBM Security',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['security', 'shield', 'protection', 'ibm', 'enterprise'],
  },
  {
    id: 'ibm-security-icon',
    label: 'Security',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['security', 'protect', 'safe', 'safety'],
  },
  {
    id: 'ibm-cloud-security',
    label: 'Cloud Security',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['security', 'cloud', 'protection', 'encryption'],
  },
  {
    id: 'ibm-security-groups',
    label: 'Security Groups',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['security groups', 'firewall', 'network', 'access control'],
  },
  {
    id: 'ibm-locked',
    label: 'Locked',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['locked', 'secure', 'encryption', 'access control'],
  },
];

/**
 * Network and Connectivity icons
 */
const networkIcons: IconDefinition[] = [
  {
    id: 'ibm-network',
    label: 'Network',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['network', 'connectivity', 'communication', 'topology'],
  },
  {
    id: 'ibm-deploy',
    label: 'Deploy',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['deploy', 'release', 'publish', 'launch', 'rollout'],
  },
];

/**
 * Data and Analytics icons
 */
const analyticsIcons: IconDefinition[] = [
  {
    id: 'ibm-analytics',
    label: 'Analytics',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['analytics', 'analysis', 'insights', 'metrics'],
  },
  {
    id: 'ibm-dataset',
    label: 'Dataset',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['dataset', 'data', 'collection', 'records'],
  },
  {
    id: 'ibm-chart-line',
    label: 'Chart Line',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['chart', 'graph', 'visualization', 'metrics', 'line', 'trends'],
  },
  {
    id: 'ibm-chart-bar',
    label: 'Chart Bar',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['chart', 'bar', 'graph', 'visualization', 'metrics'],
  },
  {
    id: 'ibm-data-analytics',
    label: 'Data Analytics',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['data analytics', 'analysis', 'insights', 'data', 'analytics'],
  },
  {
    id: 'ibm-cloud-data-ops',
    label: 'Cloud Data Ops',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['data ops', 'cloud', 'operations', 'data management'],
  },
];

/**
 * AI and Machine Learning icons
 */
const aiIcons: IconDefinition[] = [
  {
    id: 'ibm-ai',
    label: 'AI',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['ai', 'artificial intelligence', 'ml', 'machine learning'],
  },
  {
    id: 'ibm-decision-tree',
    label: 'Decision Tree',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['decision tree', 'model', 'classification', 'ml'],
  },
  {
    id: 'ibm-model',
    label: 'Model',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['model', 'ai', 'ml', 'algorithm', 'machine learning'],
  },
];

/**
 * Development and Code icons
 */
const developmentIcons: IconDefinition[] = [
  {
    id: 'ibm-code',
    label: 'Code',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['code', 'programming', 'development', 'script'],
  },
  {
    id: 'ibm-document',
    label: 'Document',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['document', 'file', 'pages', 'content'],
  },
];

/**
 * Monitoring and Operations icons
 */
const monitoringIcons: IconDefinition[] = [
  {
    id: 'ibm-monitor',
    label: 'Monitor',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['monitor', 'observe', 'dashboard', 'metrics', 'cloud monitoring'],
  },
  {
    id: 'ibm-time',
    label: 'Time',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['time', 'duration', 'schedule', 'latency', 'performance'],
  },
  {
    id: 'ibm-calculate',
    label: 'Calculate',
    color: IBM_BLUE,
    source: 'ibm',
    tags: ['calculate', 'compute', 'computation', 'calculator'],
  },
];

/**
 * IBM Categories organized by type
 */
export const ibmCategories: Record<string, IconCategory> = {
  'ibm-cloud': {
    label: 'Cloud & Infrastructure',
    source: 'ibm',
    icons: cloudIcons,
  },
  'ibm-security': {
    label: 'Security & Access',
    source: 'ibm',
    icons: securityIcons,
  },
  'ibm-network': {
    label: 'Network & Deployment',
    source: 'ibm',
    icons: networkIcons,
  },
  'ibm-analytics': {
    label: 'Data & Analytics',
    source: 'ibm',
    icons: analyticsIcons,
  },
  'ibm-ai': {
    label: 'AI & Machine Learning',
    source: 'ibm',
    icons: aiIcons,
  },
  'ibm-code': {
    label: 'Development & Code',
    source: 'ibm',
    icons: developmentIcons,
  },
  'ibm-monitor': {
    label: 'Monitoring & Operations',
    source: 'ibm',
    icons: monitoringIcons,
  },
};
