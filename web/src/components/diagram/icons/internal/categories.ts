import type { IconCategory } from '../types';

export const internalCategories: Record<string, IconCategory> = {
  cloud: {
    label: 'Cloud Providers',
    source: 'internal',
    icons: [
      { id: 'aws', label: 'AWS', color: '#FF9900', source: 'internal' },
      { id: 'azure', label: 'Azure', color: '#0078D4', source: 'internal' },
      { id: 'gcp', label: 'GCP', color: '#4285F4', source: 'internal' },
      { id: 'digitalocean', label: 'DigitalOcean', color: '#0080FF', source: 'internal' },
      { id: 'oracle', label: 'Oracle', color: '#F80000', source: 'internal' },
    ],
  },
  containers: {
    label: 'Containers & Orchestration',
    source: 'internal',
    icons: [
      { id: 'kubernetes', label: 'Kubernetes', color: '#326CE5', source: 'internal' },
      { id: 'docker', label: 'Docker', color: '#2496ED', source: 'internal' },
      { id: 'container', label: 'Container', color: '#6B7280', source: 'internal' },
    ],
  },
  compute: {
    label: 'Compute',
    source: 'internal',
    icons: [
      { id: 'server', label: 'Server', color: '#6B7280', source: 'internal' },
      { id: 'vm', label: 'Virtual Machine', color: '#6B7280', source: 'internal' },
      { id: 'lambda', label: 'Lambda/Function', color: '#FF9900', source: 'internal' },
    ],
  },
  database: {
    label: 'Database',
    source: 'internal',
    icons: [
      { id: 'database', label: 'Database', color: '#6B7280', source: 'internal' },
      { id: 'postgres', label: 'PostgreSQL', color: '#336791', source: 'internal' },
      { id: 'mysql', label: 'MySQL', color: '#4479A1', source: 'internal' },
      { id: 'mongodb', label: 'MongoDB', color: '#47A248', source: 'internal' },
      { id: 'redis', label: 'Redis', color: '#DC382D', source: 'internal' },
    ],
  },
  networking: {
    label: 'Networking',
    source: 'internal',
    icons: [
      { id: 'network', label: 'Network', color: '#6B7280', source: 'internal' },
      { id: 'router', label: 'Router', color: '#6B7280', source: 'internal' },
      { id: 'switch', label: 'Switch', color: '#6B7280', source: 'internal' },
      { id: 'firewall', label: 'Firewall', color: '#EF4444', source: 'internal' },
      { id: 'loadbalancer', label: 'Load Balancer', color: '#8B5CF6', source: 'internal' },
      { id: 'vpn', label: 'VPN', color: '#10B981', source: 'internal' },
      { id: 'dns', label: 'DNS', color: '#3B82F6', source: 'internal' },
      { id: 'cdn', label: 'CDN', color: '#F59E0B', source: 'internal' },
    ],
  },
};
