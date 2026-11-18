/**
 * Azure icon categories and metadata
 * Defines all Azure service icons with their properties for filtering and search
 */

import type { IconCategory, IconDefinition } from '../types';

// Azure brand color
const AZURE_BLUE = '#0078D4';

/**
 * Cloud and Infrastructure icons
 */
const cloudIcons: IconDefinition[] = [
  {
    id: 'azure-cloud',
    label: 'Cloud',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['cloud', 'infrastructure', 'azure'],
  },
  {
    id: 'azure-server',
    label: 'Server',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['server', 'compute', 'infrastructure'],
  },
  {
    id: 'azure-virtual-machine',
    label: 'Virtual Machine',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['vm', 'virtual machine', 'compute', 'infrastructure'],
  },
  {
    id: 'azure-network',
    label: 'Network',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['network', 'connectivity', 'infrastructure'],
  },
  {
    id: 'azure-virtual-network',
    label: 'Virtual Network',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['vnet', 'virtual network', 'network', 'infrastructure'],
  },
  {
    id: 'azure-vpn',
    label: 'VPN',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['vpn', 'security', 'network', 'connection'],
  },
  {
    id: 'azure-load-balancer',
    label: 'Load Balancer',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['load balancer', 'networking', 'distribution', 'infrastructure'],
  },
  {
    id: 'azure-cdn',
    label: 'CDN',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['cdn', 'content delivery', 'network', 'performance'],
  },
  {
    id: 'azure-front-door',
    label: 'Front Door',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['front door', 'global routing', 'network', 'distribution'],
  },
  {
    id: 'azure-traffic-manager',
    label: 'Traffic Manager',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['traffic manager', 'routing', 'network', 'performance'],
  },
];

/**
 * Data and Storage icons
 */
const dataStorageIcons: IconDefinition[] = [
  {
    id: 'azure-database',
    label: 'Database',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['database', 'storage', 'data'],
  },
  {
    id: 'azure-storage',
    label: 'Storage',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['storage', 'blob', 'data', 'infrastructure'],
  },
  {
    id: 'azure-sql-database',
    label: 'SQL Database',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['sql', 'database', 'relational', 'data'],
  },
  {
    id: 'azure-sql-server',
    label: 'SQL Server',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['sql server', 'database', 'server', 'data'],
  },
  {
    id: 'azure-cosmos-db',
    label: 'Cosmos DB',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['cosmos db', 'nosql', 'database', 'data', 'distributed'],
  },
  {
    id: 'azure-data-lake',
    label: 'Data Lake',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['data lake', 'big data', 'storage', 'analytics'],
  },
  {
    id: 'azure-synapse',
    label: 'Synapse',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['synapse', 'analytics', 'data warehouse', 'big data'],
  },
  {
    id: 'azure-blob',
    label: 'Blob Storage',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['blob', 'storage', 'object storage', 'data'],
  },
  {
    id: 'azure-file-share',
    label: 'File Share',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['file share', 'storage', 'file', 'data'],
  },
];

/**
 * Security and Compliance icons
 */
const securityIcons: IconDefinition[] = [
  {
    id: 'azure-security',
    label: 'Security',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['security', 'protection', 'compliance'],
  },
  {
    id: 'azure-firewall',
    label: 'Firewall',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['firewall', 'security', 'network', 'protection'],
  },
  {
    id: 'azure-key-vault',
    label: 'Key Vault',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['key vault', 'secrets', 'security', 'encryption'],
  },
  {
    id: 'azure-lock',
    label: 'Lock',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['lock', 'security', 'encryption', 'protection'],
  },
  {
    id: 'azure-compliance',
    label: 'Compliance',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['compliance', 'security', 'audit', 'governance'],
  },
];

/**
 * Application and Code icons
 */
const applicationIcons: IconDefinition[] = [
  {
    id: 'azure-app-service',
    label: 'App Service',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['app service', 'application', 'web', 'compute'],
  },
  {
    id: 'azure-aks',
    label: 'AKS',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['aks', 'kubernetes', 'container', 'orchestration'],
  },
  {
    id: 'azure-functions',
    label: 'Functions',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['functions', 'serverless', 'compute', 'code'],
  },
  {
    id: 'azure-logic-apps',
    label: 'Logic Apps',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['logic apps', 'workflow', 'integration', 'automation'],
  },
  {
    id: 'azure-code',
    label: 'Code',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['code', 'development', 'programming'],
  },
  {
    id: 'azure-devops',
    label: 'DevOps',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['devops', 'development', 'ci/cd', 'pipeline'],
  },
  {
    id: 'azure-pipeline',
    label: 'Pipeline',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['pipeline', 'ci/cd', 'automation', 'deployment'],
  },
  {
    id: 'azure-container-registry',
    label: 'Container Registry',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['container registry', 'registry', 'container', 'docker'],
  },
];

/**
 * Integration and Messaging icons
 */
const integrationIcons: IconDefinition[] = [
  {
    id: 'azure-service-bus',
    label: 'Service Bus',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['service bus', 'messaging', 'queue', 'integration'],
  },
  {
    id: 'azure-event-hub',
    label: 'Event Hub',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['event hub', 'events', 'streaming', 'messaging'],
  },
  {
    id: 'azure-api-management',
    label: 'API Management',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['api management', 'api', 'integration', 'gateway'],
  },
];

/**
 * Data and Analytics icons
 */
const analyticsIcons: IconDefinition[] = [
  {
    id: 'azure-data-factory',
    label: 'Data Factory',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['data factory', 'etl', 'data integration', 'analytics'],
  },
  {
    id: 'azure-monitor',
    label: 'Monitor',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['monitor', 'monitoring', 'observability', 'analytics'],
  },
  {
    id: 'azure-application-insights',
    label: 'Application Insights',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['application insights', 'monitoring', 'apm', 'analytics'],
  },
  {
    id: 'azure-log-analytics',
    label: 'Log Analytics',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['log analytics', 'logging', 'analytics', 'monitoring'],
  },
];

/**
 * Documents and Files icons
 */
const documentsIcons: IconDefinition[] = [
  {
    id: 'azure-documents',
    label: 'Documents',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['documents', 'files', 'storage'],
  },
  {
    id: 'azure-image',
    label: 'Image',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['image', 'media', 'files'],
  },
  {
    id: 'azure-video',
    label: 'Video',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['video', 'media', 'files'],
  },
];

/**
 * Utility icons
 */
const utilityIcons: IconDefinition[] = [
  {
    id: 'azure-settings',
    label: 'Settings',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['settings', 'configuration', 'options'],
  },
  {
    id: 'azure-search',
    label: 'Search',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['search', 'find', 'lookup'],
  },
  {
    id: 'azure-filter',
    label: 'Filter',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['filter', 'search', 'query'],
  },
  {
    id: 'azure-warning',
    label: 'Warning',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['warning', 'alert', 'notification'],
  },
  {
    id: 'azure-error',
    label: 'Error',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['error', 'alert', 'failure'],
  },
  {
    id: 'azure-accessibility',
    label: 'Accessibility',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['accessibility', 'wcag', 'a11y'],
  },
  {
    id: 'azure-tags',
    label: 'Tags',
    color: AZURE_BLUE,
    source: 'azure',
    tags: ['tags', 'labels', 'metadata'],
  },
];

/**
 * All Azure icon categories
 */
export const azureCategories: Record<string, IconCategory> = {
  'cloud-infrastructure': {
    label: 'Cloud & Infrastructure',
    source: 'azure',
    icons: cloudIcons,
  },
  'data-storage': {
    label: 'Data & Storage',
    source: 'azure',
    icons: dataStorageIcons,
  },
  'security-compliance': {
    label: 'Security & Compliance',
    source: 'azure',
    icons: securityIcons,
  },
  'application-code': {
    label: 'Application & Code',
    source: 'azure',
    icons: applicationIcons,
  },
  'integration-messaging': {
    label: 'Integration & Messaging',
    source: 'azure',
    icons: integrationIcons,
  },
  'analytics': {
    label: 'Data & Analytics',
    source: 'azure',
    icons: analyticsIcons,
  },
  'documents-files': {
    label: 'Documents & Files',
    source: 'azure',
    icons: documentsIcons,
  },
  'utilities': {
    label: 'Utilities',
    source: 'azure',
    icons: utilityIcons,
  },
};

export default azureCategories;
