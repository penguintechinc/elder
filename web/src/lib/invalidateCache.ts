/**
 * Cache invalidation utilities for React Query
 *
 * Usage:
 *   import { invalidateCache } from '@/lib/invalidateCache'
 *
 *   const queryClient = useQueryClient()
 *
 *   // After creating/updating/deleting an entity:
 *   await invalidateCache.entities(queryClient)
 *
 *   // After updating a specific issue:
 *   await invalidateCache.issue(queryClient, issueId)
 */

import { QueryClient } from "@tanstack/react-query";
import { queryKeys } from "./queryKeys";

export const invalidateCache = {
  /**
   * Invalidate all organization queries
   */
  organizations: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.organizations.all,
      refetchType: "all",
    });
    // Also invalidate dropdown since it's a separate query
    await queryClient.invalidateQueries({
      queryKey: queryKeys.organizations.dropdown,
      refetchType: "all",
    });
    // Dashboard stats may include org count
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dashboard.stats,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all entity queries
   */
  entities: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.entities.all,
      refetchType: "all",
    });
    // Dashboard stats may include entity count
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dashboard.stats,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all issue queries
   */
  issues: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.issues.all,
      refetchType: "all",
    });
    // Dashboard may show open issues count
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dashboard.stats,
      refetchType: "all",
    });
  },

  /**
   * Invalidate a specific issue and its related queries
   */
  issue: async (queryClient: QueryClient, issueId: number) => {
    // Invalidate the specific issue
    await queryClient.invalidateQueries({
      queryKey: queryKeys.issues.detail(issueId),
      refetchType: "all",
    });
    // Also invalidate the list since issue data may have changed
    await queryClient.invalidateQueries({
      queryKey: queryKeys.issues.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate issue comments
   */
  issueComments: async (queryClient: QueryClient, issueId: number) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.issues.comments(issueId),
      refetchType: "all",
    });
  },

  /**
   * Invalidate issue labels
   */
  issueLabels: async (queryClient: QueryClient, issueId: number) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.issues.labels(issueId),
      refetchType: "all",
    });
  },

  /**
   * Invalidate issue linked entities
   */
  issueEntities: async (queryClient: QueryClient, issueId: number) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.issues.entities(issueId),
      refetchType: "all",
    });
  },

  /**
   * Invalidate all project queries
   */
  projects: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.projects.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all label queries
   */
  labels: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.labels.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all milestone queries
   */
  milestones: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.milestones.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all software queries
   */
  software: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.software.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all service queries
   */
  services: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.services.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all data store queries
   */
  dataStores: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dataStores.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all dependency queries
   */
  dependencies: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dependencies.all,
      refetchType: "all",
    });
    // Dashboard may show dependency count
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dashboard.stats,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all identity queries
   */
  identities: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.identities.all,
      refetchType: "all",
    });
    // Dashboard may show identity count
    await queryClient.invalidateQueries({
      queryKey: queryKeys.dashboard.stats,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all key queries
   */
  keys: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.keys.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all secret queries
   */
  secrets: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.secrets.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate discovery queries
   */
  discovery: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.discovery.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate webhook queries
   */
  webhooks: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.webhooks.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate backup queries
   */
  backups: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.backups.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate network queries
   */
  network: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.network.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate IPAM queries
   */
  ipam: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.ipam.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate tenant queries (admin)
   */
  tenants: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.tenants.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate user profile
   */
  profile: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.profile.current,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all on-call rotation queries
   */
  onCall: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.onCall.all,
      refetchType: "all",
    });
    // Also invalidate organizations and services since they may show on-call badges
    await queryClient.invalidateQueries({
      queryKey: queryKeys.organizations.all,
      refetchType: "all",
    });
    await queryClient.invalidateQueries({
      queryKey: queryKeys.services.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate a specific on-call rotation and related queries
   */
  onCallRotation: async (queryClient: QueryClient, rotationId: number) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.onCall.detail(rotationId),
      refetchType: "all",
    });
    await queryClient.invalidateQueries({
      queryKey: queryKeys.onCall.participants(rotationId),
      refetchType: "all",
    });
    await queryClient.invalidateQueries({
      queryKey: queryKeys.onCall.overrides(rotationId),
      refetchType: "all",
    });
    await queryClient.invalidateQueries({
      queryKey: queryKeys.onCall.history(rotationId),
      refetchType: "all",
    });
    await queryClient.invalidateQueries({
      queryKey: queryKeys.onCall.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all AI resource queries
   */
  aiResources: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.aiResources.all,
      refetchType: "all",
    });
  },

  /**
   * Invalidate all queries (use sparingly)
   */
  all: async (queryClient: QueryClient) => {
    await queryClient.invalidateQueries();
  },
};
