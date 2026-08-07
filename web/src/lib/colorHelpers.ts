/**
 * Color helper utilities for consistent styling across the application.
 *
 * These functions provide standardized color schemes for status badges,
 * priority indicators, and other UI elements.
 */

/**
 * Get Tailwind CSS classes for status badges
 * Case-insensitive; supports both issue statuses (open, in_progress, closed, resolved)
 * and generic status values (active, healthy, offline, etc.)
 */
export const getStatusColor = (status: string): string => {
  const statusLower = status?.toLowerCase() || '';

  // Issue statuses (from issues module)
  switch (statusLower) {
    case 'open':
      return 'bg-green-500/20 text-green-400 border-green-500/30';
    case 'in_progress':
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    case 'resolved':
    case 'closed':
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }

  // Generic/system statuses
  switch (statusLower) {
    case 'active':
    case 'running':
    case 'healthy':
    case 'online':
    case 'connected':
    case 'up':
      return 'bg-green-500/20 text-green-400 border-green-500/30';

    case 'inactive':
    case 'stopped':
    case 'offline':
    case 'disconnected':
    case 'down':
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';

    case 'pending':
    case 'waiting':
    case 'queued':
      return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';

    case 'failed':
    case 'error':
    case 'critical':
    case 'unhealthy':
      return 'bg-red-500/20 text-red-400 border-red-500/30';

    case 'warning':
    case 'degraded':
      return 'bg-orange-500/20 text-orange-400 border-orange-500/30';

    case 'archived':
    case 'deprecated':
      return 'bg-gray-500/20 text-gray-400 border-gray-500/30';

    default:
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
  }
};

/**
 * Get Tailwind CSS classes for priority badges
 */
export const getPriorityColor = (priority: string): string => {
  const priorityLower = priority?.toLowerCase() || '';

  switch (priorityLower) {
    case 'critical':
    case 'urgent':
    case 'highest':
      return 'bg-red-500/20 text-red-400 border-red-500/30';

    case 'high':
      return 'bg-orange-500/20 text-orange-400 border-orange-500/30';

    case 'medium':
    case 'normal':
      return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';

    case 'low':
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';

    case 'lowest':
    case 'trivial':
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';

    default:
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }
};

/**
 * Get Tailwind CSS classes for entity/resource type badges
 */
export const getTypeColor = (type: string): string => {
  const typeLower = type?.toLowerCase() || '';

  switch (typeLower) {
    case 'server':
    case 'host':
    case 'vm':
    case 'virtual-machine':
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';

    case 'network':
    case 'router':
    case 'switch':
    case 'firewall':
      return 'bg-purple-500/20 text-purple-400 border-purple-500/30';

    case 'application':
    case 'app':
    case 'service':
      return 'bg-green-500/20 text-green-400 border-green-500/30';

    case 'database':
    case 'db':
      return 'bg-orange-500/20 text-orange-400 border-orange-500/30';

    case 'storage':
    case 'disk':
    case 'volume':
      return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';

    case 'container':
    case 'docker':
    case 'kubernetes':
      return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30';

    case 'identity':
    case 'user':
    case 'group':
      return 'bg-pink-500/20 text-pink-400 border-pink-500/30';

    default:
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }
};

/**
 * Get Tailwind CSS classes for deployment environment badges
 */
export const getEnvironmentColor = (environment: string): string => {
  const envLower = environment?.toLowerCase() || '';

  switch (envLower) {
    case 'production':
    case 'prod':
      return 'bg-red-500/20 text-red-400 border-red-500/30';

    case 'staging':
    case 'stage':
      return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';

    case 'development':
    case 'dev':
      return 'bg-green-500/20 text-green-400 border-green-500/30';

    case 'testing':
    case 'test':
    case 'qa':
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';

    default:
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }
};

/**
 * Get Tailwind CSS classes for severity badges
 */
export const getSeverityColor = (severity: string): string => {
  const severityLower = severity?.toLowerCase() || '';

  switch (severityLower) {
    case 'critical':
    case 'fatal':
      return 'bg-red-500/20 text-red-400 border-red-500/30';

    case 'high':
    case 'error':
      return 'bg-orange-500/20 text-orange-400 border-orange-500/30';

    case 'medium':
    case 'warning':
      return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';

    case 'low':
    case 'info':
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';

    default:
      return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }
};
