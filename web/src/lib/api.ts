import axios from "axios";
import type { AxiosInstance, AxiosError, AxiosRequestConfig } from "axios";

// Extend AxiosRequestConfig with _retry flag for token refresh logic
interface RetryableAxiosRequestConfig extends AxiosRequestConfig {
  _retry?: boolean;
}

// Use relative URL by default - nginx proxies /api/* to the API server
// Only use VITE_API_URL for local development outside Docker
const API_BASE_URL = import.meta.env.VITE_API_URL || "";

// gh security audit, High: the access + refresh JWT used to live in
// localStorage (XSS-exfiltratable). Both tokens are now HttpOnly cookies set
// by the backend (apps.api.auth.portal_cookies) -- this client never reads
// or writes them directly. AUTH_FLAG_KEY is a non-sensitive "was a login
// completed" boolean only, used to decide whether to attempt a cookie-based
// refresh on 401 and for route-guard checks (App.tsx) -- it carries no
// credential material, so it's safe to keep in localStorage.
const AUTH_FLAG_KEY = "elder_authenticated";
const CSRF_COOKIE_NAME = "elder_csrf_token";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const STATE_CHANGING_METHODS = new Set(["post", "put", "patch", "delete"]);

/** Read a single cookie value by name (the CSRF cookie is intentionally not
 * HttpOnly so the SPA can echo it back -- see portal_cookies.is_csrf_valid). */
function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

/** True once a login/refresh has completed this session. Non-sensitive --
 * never carries token material, safe for route guards (App.tsx, Layout.tsx). */
export function isAuthenticated(): boolean {
  return localStorage.getItem(AUTH_FLAG_KEY) === "1";
}

/** Set after a login/MFA-verify response whose cookies (not this call) are
 * the actual credential. Exported for the LoginPageBuilder onSuccess
 * handlers (Login.tsx, LoginPageWrapper.tsx), which bypass this client and
 * call fetch() directly against the same-origin login endpoint. */
export function markAuthenticated(): void {
  localStorage.setItem(AUTH_FLAG_KEY, "1");
}

function clearAuthenticated(): void {
  localStorage.removeItem(AUTH_FLAG_KEY);
}

export type SyncPlatform =
  | "github"
  | "jira"
  | "gitlab"
  | "trello"
  | "openproject";

export type SyncResourceType = "issue" | "milestone" | "project" | "label";
export type SyncDirection = "pull" | "push" | "bidirectional";
export type GitHubAuthMode = "github_app" | "pat";

export interface SyncConfigItem {
  id: number;
  name: string;
  platform: SyncPlatform;
  enabled: boolean;
  sync_interval: number;
  batch_fallback_enabled: boolean;
  batch_size: number;
  two_way_create: boolean;
  webhook_enabled: boolean;
  has_webhook_secret: boolean;
  last_sync_at: string | null;
  last_batch_sync_at: string | null;
  config_json: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SyncStatusSummary {
  configs: { total: number; enabled: number };
  recent_activity: { syncs_24h: number; failures_24h: number };
  conflicts: { unresolved: number };
  mappings: { total: number };
}

export interface SyncHistoryItem {
  id: number;
  sync_config_id: number;
  correlation_id: string | null;
  sync_type: string;
  items_synced: number;
  items_failed: number;
  started_at: string;
  completed_at: string | null;
  success: boolean;
  error_message: string | null;
  sync_metadata: Record<string, unknown> | null;
}

export interface SyncMappingItem {
  id: number;
  elder_type: string;
  elder_id: number;
  external_platform: string;
  external_id: string;
  sync_config_id: number;
  sync_status: string | null;
  sync_method: string | null;
  last_synced_at: string | null;
  elder_updated_at: string | null;
  external_updated_at: string | null;
}

export interface SyncConflictItem {
  id: number;
  mapping_id: number;
  conflict_type: string;
  elder_data: Record<string, unknown> | unknown[] | null;
  external_data: Record<string, unknown> | unknown[] | null;
  resolution_strategy: string | null;
  resolved: boolean;
  resolved_at: string | null;
  resolved_by_id: number | null;
  created_at: string | null;
}

export interface SyncTestResult {
  connected: boolean;
  success: boolean;
  platform: string;
  auth_mode?: GitHubAuthMode | string;
  repo_full_name?: string;
  token_expires_at?: string | null;
  permissions?: Record<string, string> | null;
  status_code?: number;
  error?: string;
  repository?: {
    full_name?: string;
    private?: boolean;
    default_branch?: string;
    html_url?: string;
    open_issues_count?: number;
  };
  project?: {
    id?: string;
    key?: string;
    name?: string;
    project_type?: string;
  };
}

export interface SyncTriggerResult {
  sync_config_id: number;
  platform: string;
  correlation_id: string;
  direction: SyncDirection;
  resource_types: SyncResourceType[];
  success: boolean;
  items_synced: number;
  items_failed: number;
  errors: string[];
  results: Record<string, unknown>;
}

export interface CreateSyncConfigPayload {
  name: string;
  platform: SyncPlatform;
  enabled?: boolean;
  sync_interval?: number;
  batch_fallback_enabled?: boolean;
  batch_size?: number;
  two_way_create?: boolean;
  webhook_enabled?: boolean;
  webhook_secret?: string;
  config_json: Record<string, unknown>;
}

export interface UpdateSyncConfigPayload {
  name?: string;
  enabled?: boolean;
  sync_interval?: number;
  batch_fallback_enabled?: boolean;
  batch_size?: number;
  two_way_create?: boolean;
  webhook_enabled?: boolean;
  webhook_secret?: string;
  clear_webhook_secret?: boolean;
  config_json?: Record<string, unknown>;
}

export type CloudDiscoveryProvider =
  | "aws"
  | "gcp"
  | "azure"
  | "kubernetes"
  | "vultr";

export type DiscoveryProviderType =
  | CloudDiscoveryProvider
  | "fleetdm"
  | "network"
  | "http_screenshot"
  | "banner";

export interface DiscoveryJobItem {
  id: number;
  name: string;
  provider: string;
  config_json: Record<string, unknown> | null;
  schedule_interval: number;
  enabled: boolean;
  organization_id?: number;
  description?: string | null;
  credential_type?: string | null;
  credential_id?: number | null;
  credential_mapping?: Record<string, unknown> | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DiscoveryJobTestResult {
  job_id: number;
  success: boolean;
  tested_at: string;
  auth_method?: string;
  identity?: {
    account?: string;
    arn?: string;
    user_id?: string;
  };
  error?: string;
}

export interface DiscoveryJobHistoryItem {
  id: number;
  job_id: number;
  started_at: string;
  completed_at?: string | null;
  status: string;
  entities_discovered: number;
  entities_created?: number;
  entities_updated?: number;
  error_message?: string | null;
  results_json?: Record<string, unknown> | null;
}

export type IAMProviderType = "aws_iam" | "gcp_iam" | "kubernetes";

export interface IAMProviderItem {
  id: number;
  name: string;
  provider_type: IAMProviderType | string;
  config_json: Record<string, unknown> | null;
  organization_id: number;
  enabled: boolean;
  description?: string | null;
  last_sync_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface IAMProviderTestResult {
  provider_id: number;
  success: boolean;
  tested_at: string;
  error?: string;
}

export interface IAMProviderSyncResult {
  users_synced?: number;
  roles_synced?: number;
  policies_synced?: number;
  errors?: string[];
}

export interface GoogleWorkspaceProviderItem {
  id: number;
  name: string;
  domain: string;
  admin_email: string;
  organization_id: number;
  enabled: boolean;
  customer_id?: string | null;
  description?: string | null;
  has_credentials?: boolean;
  credentials_json?: string;
  last_sync_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface GoogleWorkspaceTestResult {
  success: boolean;
  customer_id?: string;
  domains?: Array<{ domainName?: string }>;
  message?: string;
  error?: string;
}

class ApiClient {
  private client: AxiosInstance;
  private isRefreshing = false;
  private failedQueue: Array<{
    resolve: (value?: unknown) => void;
    reject: (reason?: unknown) => void;
  }> = [];
  private refreshTimer: number | null = null;

  constructor() {
    this.client = axios.create({
      baseURL: `${API_BASE_URL}/api/v1`,
      headers: {
        "Content-Type": "application/json",
      },
      timeout: 30000,
      // The access/refresh JWT now live in HttpOnly cookies (gh security
      // audit) -- withCredentials makes axios send/accept them even if
      // VITE_API_URL points at a cross-origin dev API instead of the
      // same-origin nginx/Express proxy.
      withCredentials: true,
    });

    // Request interceptor: echo the CSRF cookie on state-changing requests
    // (double-submit pattern -- see apps.api.main.enforce_csrf_protection).
    // No Authorization header injection here anymore: the browser attaches
    // the HttpOnly auth cookies automatically.
    this.client.interceptors.request.use(
      (config) => {
        const method = (config.method ?? "get").toLowerCase();
        if (STATE_CHANGING_METHODS.has(method)) {
          const csrfToken = readCookie(CSRF_COOKIE_NAME);
          if (csrfToken) {
            config.headers[CSRF_HEADER_NAME] = csrfToken;
          }
        }
        return config;
      },
      (error) => Promise.reject(error),
    );

    // Response interceptor for error handling with automatic token refresh
    this.client.interceptors.response.use(
      (response) => response,
      async (error: AxiosError) => {
        const originalRequest = error.config as
          | RetryableAxiosRequestConfig
          | undefined;

        // Redirect to login on 401 Unauthorized, but try refresh first
        if (error.response?.status === 401 && !originalRequest._retry) {
          const wasAuthenticated = isAuthenticated();
          const isLoginPage = window.location.pathname === "/login";

          // Only attempt refresh if a session was ever established and
          // we're not already on the login page
          if (wasAuthenticated && !isLoginPage) {
            if (this.isRefreshing) {
              // Queue this request to be retried after refresh completes
              return new Promise((resolve, reject) => {
                this.failedQueue.push({ resolve, reject });
              })
                .then(() => {
                  originalRequest._retry = true;
                  return this.client(originalRequest);
                })
                .catch((err) => Promise.reject(err));
            }

            originalRequest._retry = true;
            this.isRefreshing = true;

            try {
              // Refresh token comes from the HttpOnly cookie -- no body needed.
              const response = await this.client.post("/portal-auth/refresh");

              if (response.data.access_token) {
                markAuthenticated();

                // Schedule next proactive refresh
                this.scheduleTokenRefresh(response.data.expires_in);

                // Retry all queued requests with new token
                this.processQueue(null);

                // Retry the original request
                return this.client(originalRequest);
              }
            } catch (refreshError) {
              // Refresh failed - clear the auth flag and redirect to login
              this.processQueue(refreshError);
              clearAuthenticated();
              this.clearRefreshTimer();
              window.location.href = "/login";
              return Promise.reject(refreshError);
            } finally {
              this.isRefreshing = false;
            }

            // Refresh responded 200 but with no access_token (unexpected
            // shape) -- treat the same as a failed refresh.
            clearAuthenticated();
            this.clearRefreshTimer();
            window.location.href = "/login";
          }
        }
        return Promise.reject(error);
      },
    );
  }

  /**
   * Process queued requests after token refresh
   */
  private processQueue(error: unknown) {
    this.failedQueue.forEach((promise) => {
      if (error) {
        promise.reject(error);
      } else {
        promise.resolve();
      }
    });
    this.failedQueue = [];
  }

  /**
   * Schedule proactive token refresh before expiration
   */
  private scheduleTokenRefresh(expiresIn: number) {
    this.clearRefreshTimer();

    // Refresh 5 minutes before expiration (or at 75% of lifetime, whichever is sooner)
    const refreshDelay = Math.min(expiresIn * 0.75, expiresIn - 300) * 1000;

    if (refreshDelay > 0) {
      this.refreshTimer = window.setTimeout(() => {
        this.proactiveRefreshToken();
      }, refreshDelay);
    }
  }

  /**
   * Clear scheduled refresh timer
   */
  private clearRefreshTimer() {
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  /**
   * Proactively refresh token before expiration. The refresh token itself
   * is an HttpOnly cookie the browser attaches automatically -- nothing to
   * read from localStorage here.
   */
  private async proactiveRefreshToken() {
    if (!isAuthenticated()) return;

    try {
      const response = await this.client.post("/portal-auth/refresh");

      if (response.data.access_token) {
        // Schedule next refresh
        this.scheduleTokenRefresh(response.data.expires_in);
      }
    } catch (error) {
      console.error("[Elder] Proactive token refresh failed:", error);
      // Don't redirect here - let the next 401 handle it
    }
  }

  // Public API access for specific endpoints
  async getGuestEnabled() {
    const response = await this.client.get("/auth/guest-enabled");
    return response.data;
  }

  // Health check
  async health() {
    const response = await axios.get(`${API_BASE_URL}/healthz`);
    return response.data;
  }

  // Auth endpoints. The access/refresh JWT are HttpOnly cookies set directly
  // by the backend response -- nothing for this client to persist beyond the
  // non-sensitive "logged in" flag used by route guards.
  async login(username: string, password: string) {
    const response = await this.client.post("/auth/login", {
      username,
      password,
    });
    if (response.data.access_token) {
      markAuthenticated();
    }
    return response.data;
  }

  async register(data: {
    username: string;
    email: string;
    password: string;
    full_name: string;
  }) {
    const response = await this.client.post("/auth/register", data);
    return response.data;
  }

  async logout() {
    try {
      await this.client.post("/portal-auth/logout");
    } catch (error) {
      // Cookie clearing is best-effort from the client's perspective -- the
      // server-side cookies may already be expired/gone. Never block the
      // local logout on a network error.
      console.error("[Elder] Logout request failed:", error);
    } finally {
      clearAuthenticated();
      this.clearRefreshTimer();
    }
  }

  // Profile (Portal Users)
  // Updated: 2026-01-10 - All profile endpoints use /portal-auth/*
  async getProfile() {
    const response = await this.client.get("/portal-auth/me");
    return response.data;
  }

  async updateProfile(
    data: Partial<{
      email: string;
      full_name: string;
      organization_id: number | null;
    }>,
  ) {
    const response = await this.client.patch("/portal-auth/me", data);
    return response.data;
  }

  async changePassword(current_password: string, new_password: string) {
    const response = await this.client.post("/portal-auth/password/change", {
      current_password,
      new_password,
    });
    return response.data;
  }

  // Organizations
  async getOrganizations(params?: {
    page?: number;
    per_page?: number;
    search?: string;
    parent_id?: number;
  }) {
    const response = await this.client.get("/organizations", { params });
    return response.data;
  }

  async getOrganization(id: number) {
    const response = await this.client.get(`/organizations/${id}`);
    return response.data;
  }

  async createOrganization(data: {
    name: string;
    description?: string;
    parent_id?: number;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/organizations", data);
    return response.data;
  }

  async updateOrganization(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      metadata: Record<string, unknown>;
    }>,
  ) {
    const response = await this.client.put(`/organizations/${id}`, data);
    return response.data;
  }

  async deleteOrganization(id: number) {
    const response = await this.client.delete(`/organizations/${id}`);
    return response.data;
  }

  async getOrganizationTreeStats(id: number) {
    const response = await this.client.get(`/organizations/${id}/tree-stats`);
    return response.data;
  }

  async getOrganizationGraph(id: number, depth: number = 3) {
    const response = await this.client.get(`/organizations/${id}/graph`, {
      params: { depth },
    });
    return response.data;
  }

  // Entities
  async getEntities(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    entity_type?: string;
    sub_type?: string;
    parent_id?: number;
    search?: string;
  }) {
    const response = await this.client.get("/entities", { params });
    return response.data;
  }

  async getEntity(id: number) {
    const response = await this.client.get(`/entities/${id}`);
    return response.data;
  }

  async createEntity(data: {
    name: string;
    description?: string;
    entity_type: string;
    sub_type?: string;
    organization_id: number;
    parent_id?: number;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/entities", data);
    return response.data;
  }

  async updateEntity(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      metadata: Record<string, unknown>;
    }>,
  ) {
    const response = await this.client.put(`/entities/${id}`, data);
    return response.data;
  }

  async deleteEntity(id: number) {
    const response = await this.client.delete(`/entities/${id}`);
    return response.data;
  }

  async lookupEntity(uniqueId: string) {
    const response = await this.client.get(`/lookup/${uniqueId}`);
    return response.data;
  }

  // Entity Types
  async getEntityTypes(params?: { category?: string }) {
    const response = await this.client.get("/entity-types/", { params });
    return response.data;
  }

  async getEntityType(id: number) {
    const response = await this.client.get(`/entity-types/${id}`);
    return response.data;
  }

  // Dependencies
  // NOTE: backend only recognizes source_type/source_id/target_type/target_id
  // (see apps/api/modules/infrastructure/routes/dependencies.py) — do not add
  // *_entity_id aliases back, the backend silently ignores unknown params.
  async getDependencies(params?: {
    page?: number;
    per_page?: number;
    source_type?: string;
    source_id?: number;
    target_type?: string;
    target_id?: number;
    dependency_type?: string;
  }) {
    const response = await this.client.get("/dependencies", { params });
    return response.data;
  }

  async createDependency(data: {
    source_type: string;
    source_id: number;
    target_type: string;
    target_id: number;
    dependency_type: string;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/dependencies", data);
    return response.data;
  }

  async deleteDependency(id: number) {
    const response = await this.client.delete(`/dependencies/${id}`);
    return response.data;
  }

  async bulkDeleteDependencies(ids: number[]) {
    const response = await this.client.delete("/dependencies/bulk", {
      data: { ids },
    });
    return response.data;
  }

  // Graph
  async getGraph(params?: {
    organization_id?: number;
    entity_id?: number;
    depth?: number;
  }) {
    const response = await this.client.get("/graph", { params });
    return response.data;
  }

  async getMap(params?: {
    tenant_id?: number;
    organization_id?: number;
    resource_types?: string;
    entity_types?: string;
    include_hierarchical?: boolean;
    include_dependencies?: boolean;
    limit?: number;
  }) {
    const response = await this.client.get("/graph/map", { params });
    return response.data;
  }

  // Identities
  async getIdentities(params?: {
    page?: number;
    per_page?: number;
    search?: string;
    organization_id?: number;
    identity_type?: string;
  }) {
    const response = await this.client.get("/identities", { params });
    return response.data;
  }

  async getIdentity(id: number) {
    const response = await this.client.get(`/identities/${id}`);
    return response.data;
  }

  async createIdentity(data: {
    username: string;
    email: string;
    full_name: string;
    identity_type: string;
    auth_provider: string;
    password?: string;
  }) {
    const response = await this.client.post("/identities", data);
    return response.data;
  }

  async updateIdentity(
    id: number,
    data: Partial<{
      email: string;
      full_name: string;
      is_active: boolean;
    }>,
  ) {
    const response = await this.client.put(`/identities/${id}`, data);
    return response.data;
  }

  // Identity Groups
  async getIdentityGroups() {
    const response = await this.client.get("/identities/groups");
    return response.data;
  }

  async createIdentityGroup(data: { name: string; description?: string }) {
    const response = await this.client.post("/identities/groups", data);
    return response.data;
  }

  // ===========================
  // Group Membership Management (Enterprise)
  // ===========================

  // List groups with ownership/member info
  async getGroupMembershipGroups(params?: {
    include_members?: boolean;
    include_pending?: boolean;
    limit?: number;
    offset?: number;
  }) {
    const response = await this.client.get("/group-membership/groups", {
      params,
    });
    return response.data;
  }

  // Get group details
  async getGroupMembershipGroup(groupId: number) {
    const response = await this.client.get(
      `/group-membership/groups/${groupId}`,
    );
    return response.data;
  }

  // Update group ownership/settings
  async updateGroupMembershipGroup(
    groupId: number,
    data: Partial<{
      owner_identity_id: number;
      owner_group_id: number;
      approval_mode: "any" | "all" | "threshold";
      approval_threshold: number;
      provider: "internal" | "ldap" | "okta";
      provider_group_id: string;
      sync_enabled: boolean;
    }>,
  ) {
    const response = await this.client.patch(
      `/group-membership/groups/${groupId}`,
      data,
    );
    return response.data;
  }

  // Create access request for a group
  async createGroupAccessRequest(
    groupId: number,
    data: {
      reason?: string;
      expires_at?: string;
    },
  ) {
    const response = await this.client.post(
      `/group-membership/groups/${groupId}/requests`,
      data,
    );
    return response.data;
  }

  // List access requests for a group (owners only)
  async getGroupAccessRequests(
    groupId: number,
    params?: {
      status?: "pending" | "approved" | "denied" | "cancelled";
      limit?: number;
      offset?: number;
    },
  ) {
    const response = await this.client.get(
      `/group-membership/groups/${groupId}/requests`,
      { params },
    );
    return response.data;
  }

  // List pending requests for groups owned by current user
  async getPendingGroupAccessRequests(params?: {
    limit?: number;
    offset?: number;
  }) {
    const response = await this.client.get(
      "/group-membership/requests/pending",
      { params },
    );
    return response.data;
  }

  // Approve an access request
  async approveGroupAccessRequest(
    requestId: number,
    data?: { comment?: string },
  ) {
    const response = await this.client.post(
      `/group-membership/requests/${requestId}/approve`,
      data || {},
    );
    return response.data;
  }

  // Deny an access request
  async denyGroupAccessRequest(requestId: number, data?: { comment?: string }) {
    const response = await this.client.post(
      `/group-membership/requests/${requestId}/deny`,
      data || {},
    );
    return response.data;
  }

  // Cancel own access request
  async cancelGroupAccessRequest(requestId: number) {
    const response = await this.client.delete(
      `/group-membership/requests/${requestId}`,
    );
    return response.data;
  }

  // Bulk approve multiple requests
  async bulkApproveGroupAccessRequests(data: {
    request_ids: number[];
    comment?: string;
  }) {
    const response = await this.client.post(
      "/group-membership/requests/bulk-approve",
      data,
    );
    return response.data;
  }

  // List members of a group
  async getGroupMembers(
    groupId: number,
    params?: { limit?: number; offset?: number },
  ) {
    const response = await this.client.get(
      `/group-membership/groups/${groupId}/members`,
      { params },
    );
    return response.data;
  }

  // Add member to group (owner/admin only)
  async addGroupMember(
    groupId: number,
    data: {
      identity_id: number;
      expires_at?: string;
      provider_member_id?: string;
    },
  ) {
    const response = await this.client.post(
      `/group-membership/groups/${groupId}/members`,
      data,
    );
    return response.data;
  }

  // ===== Access Reviews (v3.1.0) =====

  // List access reviews
  async getAccessReviews(params?: {
    status?: string;
    group_id?: number;
    limit?: number;
    offset?: number;
  }) {
    const response = await this.client.get("/access-reviews", { params });
    return response.data;
  }

  // Get review details
  async getAccessReview(reviewId: number) {
    const response = await this.client.get(`/access-reviews/${reviewId}`);
    return response.data;
  }

  // Get review items (members to review)
  async getAccessReviewItems(reviewId: number) {
    const response = await this.client.get(`/access-reviews/${reviewId}/items`);
    return response.data;
  }

  // Submit review decision
  async submitReviewDecision(
    reviewId: number,
    data: {
      membership_id: number;
      decision: "keep" | "remove" | "extend";
      justification?: string;
      new_expiration?: string;
    },
  ) {
    const response = await this.client.post(
      `/access-reviews/${reviewId}/decisions`,
      data,
    );
    return response.data;
  }

  // Complete access review
  async completeAccessReview(reviewId: number) {
    const response = await this.client.post(
      `/access-reviews/${reviewId}/complete`,
    );
    return response.data;
  }

  // Get reviews assigned to current user
  async getMyAccessReviews(params?: { status?: string }) {
    const response = await this.client.get("/access-reviews/my-reviews", {
      params,
    });
    return response.data;
  }

  // Create ad-hoc review (admin only)
  async createAccessReview(data: {
    group_id: number;
    period_start: string;
    period_end: string;
    due_date: string;
    auto_apply?: boolean;
  }) {
    const response = await this.client.post("/access-reviews", data);
    return response.data;
  }

  // Remove member from group
  async removeGroupMember(groupId: number, identityId: number) {
    const response = await this.client.delete(
      `/group-membership/groups/${groupId}/members/${identityId}`,
    );
    return response.data;
  }

  // Issues
  async getIssues(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    entity_id?: number;
    project_id?: number;
    status?: string;
    priority?: string;
    issue_type?: string;
    assigned_to?: number;
    assignee_id?: number;
    search?: string;
  }) {
    const response = await this.client.get("/issues", { params });
    return response.data;
  }

  async getIssue(id: number) {
    const response = await this.client.get(`/issues/${id}`);
    return response.data;
  }

  async createIssue(data: {
    title: string;
    organization_id?: number;
    description?: string;
    status?: string;
    priority?: string;
    issue_type?: string;
    assignee_id?: number;
    assignee_type?: "identity" | "org_unit";
    is_incident?: number;
    channel?: string;
    category?: string;
    parent_issue_id?: number;
    start_date?: string;
    end_date?: string;
  }) {
    const response = await this.client.post("/issues", data);
    return response.data;
  }

  // Note: apps/api/modules/issues/routes/issues.py::update_issue only
  // registers PATCH for /issues/:id (no PUT route exists) — using PUT here
  // 405s every issue update.
  async updateIssue(
    id: number,
    data: Partial<{
      title: string;
      description: string;
      status: string;
      priority: string;
      issue_type: string;
      assignee_id: number;
      assignee_type: "identity" | "org_unit";
      organization_id: number;
      is_incident: number;
      channel: string;
      category: string;
      parent_issue_id: number;
      start_date: string | null;
      end_date: string | null;
    }>,
  ) {
    const response = await this.client.patch(`/issues/${id}`, data);
    return response.data;
  }

  async deleteIssue(id: number) {
    const response = await this.client.delete(`/issues/${id}`);
    return response.data;
  }

  // Issue Comments
  async getIssueComments(issueId: number) {
    const response = await this.client.get(`/issues/${issueId}/comments`);
    return response.data;
  }

  async createIssueComment(issueId: number, data: { body: string }) {
    const response = await this.client.post(
      `/issues/${issueId}/comments`,
      data,
    );
    return response.data;
  }

  async deleteIssueComment(issueId: number, commentId: number) {
    const response = await this.client.delete(
      `/issues/${issueId}/comments/${commentId}`,
    );
    return response.data;
  }

  // Issue Labels
  async getIssueLabels(issueId: number) {
    const response = await this.client.get(`/issues/${issueId}/labels`);
    return response.data;
  }

  async addIssueLabel(issueId: number, labelId: number) {
    const response = await this.client.post(
      `/issues/${issueId}/labels/${labelId}`,
    );
    return response.data;
  }

  async removeIssueLabel(issueId: number, labelId: number) {
    const response = await this.client.delete(
      `/issues/${issueId}/labels/${labelId}`,
    );
    return response.data;
  }

  // Issue Entity Links
  async getIssueEntities(issueId: number) {
    const response = await this.client.get(`/issues/${issueId}/links`);
    return response.data;
  }

  async linkIssueEntity(issueId: number, entityId: number) {
    const response = await this.client.post(`/issues/${issueId}/links`, {
      entity_id: entityId,
    });
    return response.data;
  }

  async unlinkIssueEntity(issueId: number, entityId: number) {
    const response = await this.client.delete(
      `/issues/${issueId}/links/by-entity/${entityId}`,
    );
    return response.data;
  }

  // Issue Resource Links
  async listIssueResources(issueId: number) {
    const response = await this.client.get(`/issues/${issueId}/resources`);
    return response.data;
  }

  async linkIssueResource(
    issueId: number,
    resourceType: string,
    resourceId: number,
  ) {
    const response = await this.client.post(`/issues/${issueId}/resources`, {
      resource_type: resourceType,
      resource_id: resourceId,
    });
    return response.data;
  }

  async unlinkIssueResource(issueId: number, linkId: number) {
    const response = await this.client.delete(
      `/issues/${issueId}/resources/${linkId}`,
    );
    return response.data;
  }

  // Labels
  async getLabels(params?: {
    page?: number;
    per_page?: number;
    search?: string;
  }) {
    const response = await this.client.get("/labels", { params });
    return response.data;
  }

  async getLabel(id: number) {
    const response = await this.client.get(`/labels/${id}`);
    return response.data;
  }

  async createLabel(data: {
    name: string;
    description?: string;
    color?: string;
  }) {
    const response = await this.client.post("/labels", data);
    return response.data;
  }

  async updateLabel(
    id: number,
    data: Partial<{ name: string; description: string; color: string }>,
  ) {
    const response = await this.client.put(`/labels/${id}`, data);
    return response.data;
  }

  async deleteLabel(id: number) {
    const response = await this.client.delete(`/labels/${id}`);
    return response.data;
  }

  // Projects
  async getProjects(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    status?: string;
    search?: string;
  }) {
    const response = await this.client.get("/projects", { params });
    return response.data;
  }

  async getProject(id: number) {
    const response = await this.client.get(`/projects/${id}`);
    return response.data;
  }

  async createProject(data: {
    name: string;
    organization_id: number;
    description?: string;
    status?: string;
    start_date?: string;
    end_date?: string;
  }) {
    const response = await this.client.post("/projects", data);
    return response.data;
  }

  async updateProject(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      status: string;
      start_date: string;
      end_date: string;
    }>,
  ) {
    const response = await this.client.put(`/projects/${id}`, data);
    return response.data;
  }

  async deleteProject(id: number) {
    const response = await this.client.delete(`/projects/${id}`);
    return response.data;
  }

  // Milestones
  async getMilestones(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    project_id?: number;
    status?: string;
    search?: string;
  }) {
    const response = await this.client.get("/milestones", { params });
    return response.data;
  }

  async getMilestone(id: number) {
    const response = await this.client.get(`/milestones/${id}`);
    return response.data;
  }

  async createMilestone(data: {
    title: string;
    organization_id: number;
    description?: string;
    status?: string;
    project_id?: number;
    due_date?: string;
  }) {
    const response = await this.client.post("/milestones", data);
    return response.data;
  }

  async updateMilestone(
    id: number,
    data: Partial<{
      title: string;
      description: string;
      status: string;
      project_id: number;
      due_date: string;
    }>,
  ) {
    const response = await this.client.put(`/milestones/${id}`, data);
    return response.data;
  }

  async deleteMilestone(id: number) {
    const response = await this.client.delete(`/milestones/${id}`);
    return response.data;
  }

  async getMilestoneIssues(id: number) {
    const response = await this.client.get(`/milestones/${id}/issues`);
    return response.data;
  }

  // Software
  async getSoftware(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    software_type?: string;
    search?: string;
  }) {
    const response = await this.client.get("/software", { params });
    return response.data;
  }

  async getSoftwareById(id: number) {
    const response = await this.client.get(`/software/${id}`);
    return response.data;
  }

  async createSoftware(data: {
    name: string;
    vendor?: string;
    software_type: string;
    version?: string;
    seats?: number;
    cost_monthly?: number;
    renewal_date?: string;
    license_url?: string;
    access_url?: string;
    parent_system_id?: number;
    git_url?: string;
    git_path?: string;
    release_shasum?: string;
    organization_id: number;
  }) {
    const response = await this.client.post("/software", data);
    return response.data;
  }

  async updateSoftware(
    id: number,
    data: Partial<{
      name: string;
      vendor: string;
      software_type: string;
      version: string;
      seats: number;
      cost_monthly: number;
      renewal_date: string;
      license_url: string;
      access_url: string;
      parent_system_id: number;
      git_url: string;
      git_path: string;
      release_shasum: string;
    }>,
  ) {
    const response = await this.client.put(`/software/${id}`, data);
    return response.data;
  }

  async deleteSoftware(id: number) {
    const response = await this.client.delete(`/software/${id}`);
    return response.data;
  }

  // AI Resources
  async getAIResources(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    resource_type?: string;
    status?: string;
    agreement_level?: string;
    search?: string;
  }) {
    const response = await this.client.get("/ai/resources", { params });
    return response.data;
  }

  async getAIResourceById(id: number) {
    const response = await this.client.get(`/ai/resources/${id}`);
    return response.data;
  }

  async createAIResource(data: {
    name: string;
    description?: string | null;
    organization_id: number;
    resource_type: string;
    provider?: string | null;
    model_name?: string | null;
    endpoint_url?: string | null;
    status?: string;
    agreement_level?: string | null;
    plan_info?: string | null;
    budget_monthly?: number | null;
    cost_per_10m_tokens?: number | null;
    seats?: number | null;
    parent_system_id?: number | null;
    owner_id?: number | null;
    notes?: string | null;
    tags?: string[] | null;
    metadata?: Record<string, unknown> | null;
    is_active?: boolean;
  }) {
    const response = await this.client.post("/ai/resources", data);
    return response.data;
  }

  async updateAIResource(
    id: number,
    data: Partial<{
      name: string;
      description: string | null;
      organization_id: number;
      resource_type: string;
      provider: string | null;
      model_name: string | null;
      endpoint_url: string | null;
      status: string;
      agreement_level: string | null;
      plan_info: string | null;
      budget_monthly: number | null;
      cost_per_10m_tokens: number | null;
      seats: number | null;
      parent_system_id: number | null;
      owner_id: number | null;
      notes: string | null;
      tags: string[] | null;
      metadata: Record<string, unknown> | null;
      is_active: boolean;
    }>,
  ) {
    const response = await this.client.put(`/ai/resources/${id}`, data);
    return response.data;
  }

  async deleteAIResource(id: number) {
    const response = await this.client.delete(`/ai/resources/${id}`);
    return response.data;
  }

  // Issue-Project Linking
  async linkIssueToProject(issueId: number, projectId: number) {
    const response = await this.client.post(`/issues/${issueId}/projects`, {
      project_id: projectId,
    });
    return response.data;
  }

  async unlinkIssueFromProject(issueId: number, projectId: number) {
    const response = await this.client.delete(
      `/issues/${issueId}/projects/${projectId}`,
    );
    return response.data;
  }

  // Issue-Milestone Linking
  async linkIssueToMilestone(issueId: number, milestoneId: number) {
    const response = await this.client.post(`/issues/${issueId}/milestones`, {
      milestone_id: milestoneId,
    });
    return response.data;
  }

  async unlinkIssueFromMilestone(issueId: number, milestoneId: number) {
    const response = await this.client.delete(
      `/issues/${issueId}/milestones/${milestoneId}`,
    );
    return response.data;
  }

  // Resource Roles
  async getResourceRoles(params?: {
    resource_type?: string;
    resource_id?: number;
    identity_id?: number;
  }) {
    const response = await this.client.get("/resource-roles", { params });
    return response.data;
  }

  async assignResourceRole(data: {
    resource_type: string;
    resource_id: number;
    identity_id: number;
    role: string;
  }) {
    const response = await this.client.post("/resource-roles", data);
    return response.data;
  }

  async revokeResourceRole(id: number) {
    const response = await this.client.delete(`/resource-roles/${id}`);
    return response.data;
  }

  // Organization Metadata
  async getOrganizationMetadata(id: number) {
    const response = await this.client.get(
      `/metadata/organizations/${id}/metadata`,
    );
    return response.data;
  }

  async createOrganizationMetadata(
    id: number,
    data: { key: string; field_type: string; value: unknown },
  ) {
    const response = await this.client.post(
      `/metadata/organizations/${id}/metadata`,
      data,
    );
    return response.data;
  }

  async updateOrganizationMetadata(
    id: number,
    key: string,
    data: { value: unknown; field_type?: string },
  ) {
    const response = await this.client.patch(
      `/metadata/organizations/${id}/metadata/${key}`,
      data,
    );
    return response.data;
  }

  async deleteOrganizationMetadata(id: number, key: string) {
    const response = await this.client.delete(
      `/metadata/organizations/${id}/metadata/${key}`,
    );
    return response.data;
  }

  // Entity Metadata
  async getEntityMetadata(id: number) {
    const response = await this.client.get(`/metadata/entities/${id}/metadata`);
    return response.data;
  }

  async createEntityMetadata(
    id: number,
    data: { key: string; field_type: string; value: unknown },
  ) {
    const response = await this.client.post(
      `/metadata/entities/${id}/metadata`,
      data,
    );
    return response.data;
  }

  async updateEntityMetadata(
    id: number,
    key: string,
    data: { value: unknown; field_type?: string },
  ) {
    const response = await this.client.patch(
      `/metadata/entities/${id}/metadata/${key}`,
      data,
    );
    return response.data;
  }

  async deleteEntityMetadata(id: number, key: string) {
    const response = await this.client.delete(
      `/metadata/entities/${id}/metadata/${key}`,
    );
    return response.data;
  }

  // ===========================
  // v1.2.0 Features
  // ===========================

  // Secrets Management
  async getSecretProviders(params?: { organization_id?: number }) {
    const response = await this.client.get("/secrets/providers", { params });
    return response.data;
  }

  async getSecretProvider(id: number) {
    const response = await this.client.get(`/secrets/providers/${id}`);
    return response.data;
  }

  async createSecretProvider(data: {
    name: string;
    provider_type: string;
    organization_id: number;
    config: Record<string, unknown>;
    description?: string;
  }) {
    const response = await this.client.post("/secrets/providers", data);
    return response.data;
  }

  async updateSecretProvider(
    id: number,
    data: Partial<{
      name: string;
      config: Record<string, unknown>;
      description: string;
      enabled: boolean;
    }>,
  ) {
    const response = await this.client.put(`/secrets/providers/${id}`, data);
    return response.data;
  }

  async deleteSecretProvider(id: number) {
    const response = await this.client.delete(`/secrets/providers/${id}`);
    return response.data;
  }

  async testSecretProvider(id: number) {
    const response = await this.client.post(`/secrets/providers/${id}/test`);
    return response.data;
  }

  async getSecret(
    providerId: number,
    secretName: string,
    params?: { version?: string },
  ) {
    const response = await this.client.get(`/secrets/${secretName}`, {
      params: { ...params, provider_id: providerId },
    });
    return response.data;
  }

  async listSecrets(providerId: number) {
    const response = await this.client.get("/secrets", {
      params: { provider_id: providerId },
    });
    return response.data;
  }

  // Keys Management
  async getKeyProviders(params?: { organization_id?: number }) {
    const response = await this.client.get("/keys/providers", { params });
    return response.data;
  }

  async getKeyProvider(id: number) {
    const response = await this.client.get(`/keys/providers/${id}`);
    return response.data;
  }

  async createKeyProvider(data: {
    name: string;
    provider_type: string;
    organization_id: number;
    config: Record<string, unknown>;
    description?: string;
  }) {
    const response = await this.client.post("/keys/providers", data);
    return response.data;
  }

  async updateKeyProvider(
    id: number,
    data: Partial<{
      name: string;
      config: Record<string, unknown>;
      description: string;
      enabled: boolean;
    }>,
  ) {
    const response = await this.client.put(`/keys/providers/${id}`, data);
    return response.data;
  }

  async deleteKeyProvider(id: number) {
    const response = await this.client.delete(`/keys/providers/${id}`);
    return response.data;
  }

  async testKeyProvider(id: number) {
    const response = await this.client.post(`/keys/providers/${id}/test`);
    return response.data;
  }

  async encryptData(
    providerId: number,
    keyId: string,
    data: { plaintext: string; context?: Record<string, unknown> },
  ) {
    const response = await this.client.post(`/keys/${keyId}/encrypt`, data);
    return response.data;
  }

  async decryptData(
    providerId: number,
    keyId: string,
    data: { ciphertext: string; context?: Record<string, unknown> },
  ) {
    const response = await this.client.post(`/keys/${keyId}/decrypt`, data);
    return response.data;
  }

  // IAM Integration
  async getIAMProviders(params?: {
    organization_id?: number;
    enabled_only?: boolean;
  }): Promise<{ providers: IAMProviderItem[]; count?: number }> {
    const response = await this.client.get("/iam/providers", { params });
    return response.data;
  }

  async getIAMProvider(id: number): Promise<IAMProviderItem> {
    const response = await this.client.get(`/iam/providers/${id}`);
    return response.data;
  }

  async createIAMProvider(data: {
    name: string;
    provider_type: string;
    organization_id: number;
    config: Record<string, unknown>;
    description?: string;
  }): Promise<IAMProviderItem> {
    const response = await this.client.post("/iam/providers", data);
    return response.data;
  }

  async updateIAMProvider(
    id: number,
    data: Partial<{
      name: string;
      config: Record<string, unknown>;
      description: string;
      enabled: boolean;
      organization_id: number;
    }>,
  ): Promise<IAMProviderItem> {
    const response = await this.client.put(`/iam/providers/${id}`, data);
    return response.data;
  }

  async deleteIAMProvider(id: number): Promise<{ message: string }> {
    const response = await this.client.delete(`/iam/providers/${id}`);
    return response.data;
  }

  async testIAMProvider(id: number): Promise<IAMProviderTestResult> {
    const response = await this.client.post(`/iam/providers/${id}/test`);
    return response.data;
  }

  async syncIAMProvider(id: number): Promise<IAMProviderSyncResult> {
    const response = await this.client.post(`/iam/providers/${id}/sync`);
    return response.data;
  }

  async listIAMUsers(providerId: number) {
    const response = await this.client.get(
      `/iam/providers/${providerId}/users`,
    );
    return response.data;
  }

  async listIAMRoles(providerId: number) {
    const response = await this.client.get(
      `/iam/providers/${providerId}/roles`,
    );
    return response.data;
  }

  async listIAMPolicies(providerId: number) {
    const response = await this.client.get(
      `/iam/providers/${providerId}/policies`,
    );
    return response.data;
  }

  // Cloud Discovery
  async getDiscoveryJobs(params?: {
    organization_id?: number;
    provider?: string;
    enabled?: boolean;
    status?: string;
  }): Promise<{ jobs: DiscoveryJobItem[]; count?: number }> {
    const response = await this.client.get("/discovery/jobs", { params });
    return response.data;
  }

  async getDiscoveryJob(id: number): Promise<DiscoveryJobItem> {
    const response = await this.client.get(`/discovery/jobs/${id}`);
    return response.data;
  }

  async createDiscoveryJob(data: {
    name: string;
    provider_type: string;
    organization_id: number;
    config: Record<string, unknown>;
    schedule?: string;
    schedule_interval?: number;
    description?: string;
    enabled?: boolean;
  }): Promise<DiscoveryJobItem> {
    // Map frontend field names to backend field names
    const parsedSchedule =
      data.schedule_interval !== undefined
        ? data.schedule_interval
        : data.schedule
          ? parseInt(data.schedule, 10)
          : undefined;
    const payload = {
      name: data.name,
      provider: data.provider_type, // Backend expects 'provider' not 'provider_type'
      organization_id: data.organization_id,
      config: data.config,
      schedule_interval:
        parsedSchedule !== undefined && !Number.isNaN(parsedSchedule)
          ? parsedSchedule
          : undefined,
      description:
        data.description !== undefined ? data.description : data.name,
      enabled: data.enabled,
    };
    const response = await this.client.post("/discovery/jobs", payload);
    return response.data;
  }

  async updateDiscoveryJob(
    id: number,
    data: Partial<{
      name: string;
      config: Record<string, unknown>;
      schedule_interval: number;
      description: string;
      enabled: boolean;
    }>,
  ): Promise<DiscoveryJobItem> {
    const response = await this.client.put(`/discovery/jobs/${id}`, data);
    return response.data;
  }

  async deleteDiscoveryJob(id: number): Promise<{ message: string }> {
    const response = await this.client.delete(`/discovery/jobs/${id}`);
    return response.data;
  }

  async testDiscoveryJob(id: number): Promise<DiscoveryJobTestResult> {
    const response = await this.client.post(`/discovery/jobs/${id}/test`);
    return response.data;
  }

  async runDiscoveryJob(id: number) {
    const response = await this.client.post(`/discovery/jobs/${id}/run`);
    return response.data;
  }

  async getDiscoveryJobHistory(jobId: number): Promise<{
    history: DiscoveryJobHistoryItem[];
    runs?: DiscoveryJobHistoryItem[];
    count?: number;
  }> {
    const response = await this.client.get(`/discovery/jobs/${jobId}/history`);
    return response.data;
  }

  // Google Workspace
  async getGoogleWorkspaceProviders(params?: {
    organization_id?: number;
  }): Promise<{ providers: GoogleWorkspaceProviderItem[]; count?: number }> {
    const response = await this.client.get("/google-workspace/providers", {
      params,
    });
    return response.data;
  }

  async getGoogleWorkspaceProvider(
    id: number,
  ): Promise<GoogleWorkspaceProviderItem> {
    const response = await this.client.get(`/google-workspace/providers/${id}`);
    return response.data;
  }

  async createGoogleWorkspaceProvider(data: {
    name: string;
    organization_id: number;
    domain?: string;
    customer_id: string;
    admin_email: string;
    service_account_json: Record<string, unknown> | string;
    description?: string;
  }): Promise<GoogleWorkspaceProviderItem> {
    const response = await this.client.post(
      "/google-workspace/providers",
      data,
    );
    return response.data;
  }

  async updateGoogleWorkspaceProvider(
    id: number,
    data: Partial<{
      name: string;
      domain: string;
      customer_id: string;
      admin_email: string;
      service_account_json: Record<string, unknown> | string;
      description: string;
      enabled: boolean;
    }>,
  ): Promise<GoogleWorkspaceProviderItem> {
    const response = await this.client.put(
      `/google-workspace/providers/${id}`,
      data,
    );
    return response.data;
  }

  async deleteGoogleWorkspaceProvider(
    id: number,
  ): Promise<{ message: string }> {
    const response = await this.client.delete(
      `/google-workspace/providers/${id}`,
    );
    return response.data;
  }

  async testGoogleWorkspaceProvider(
    id: number,
  ): Promise<GoogleWorkspaceTestResult> {
    const response = await this.client.post(
      `/google-workspace/providers/${id}/test`,
    );
    return response.data;
  }

  async listGoogleWorkspaceUsers(
    providerId: number,
    params?: { domain?: string; limit?: number },
  ) {
    const response = await this.client.get(
      `/google-workspace/providers/${providerId}/users`,
      { params },
    );
    return response.data;
  }

  async listGoogleWorkspaceGroups(
    providerId: number,
    params?: { domain?: string; limit?: number },
  ) {
    const response = await this.client.get(
      `/google-workspace/providers/${providerId}/groups`,
      { params },
    );
    return response.data;
  }

  // Webhooks
  async getWebhooks(params?: { organization_id?: number }) {
    const response = await this.client.get("/webhooks", { params });
    return response.data;
  }

  async getWebhook(id: number) {
    const response = await this.client.get(`/webhooks/${id}`);
    return response.data;
  }

  async createWebhook(data: {
    name: string;
    url: string;
    organization_id?: number;
    events: string[];
    secret?: string;
    is_active?: boolean;
    headers?: Record<string, string>;
    filter_issue_type?: string;
    filter_assignee_type?: "identity" | "org_unit";
    filter_assignee_id?: number;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/webhooks", data);
    return response.data;
  }

  async updateWebhook(
    id: number,
    data: Partial<{
      name: string;
      url: string;
      events: string[];
      secret: string;
      is_active: boolean;
      headers: Record<string, string>;
      filter_issue_type: string;
      filter_assignee_type: "identity" | "org_unit";
      filter_assignee_id: number;
      metadata: Record<string, unknown>;
    }>,
  ) {
    const response = await this.client.put(`/webhooks/${id}`, data);
    return response.data;
  }

  async deleteWebhook(id: number) {
    const response = await this.client.delete(`/webhooks/${id}`);
    return response.data;
  }

  async testWebhook(id: number) {
    const response = await this.client.post(`/webhooks/${id}/test`);
    return response.data;
  }

  async getWebhookDeliveries(webhookId: number) {
    const response = await this.client.get(`/webhooks/${webhookId}/deliveries`);
    return response.data;
  }

  // Inbound (Receiving) Webhooks
  async getInboundWebhookScopes() {
    const response = await this.client.get("/webhooks/inbound/scopes");
    return response.data;
  }

  async getInboundWebhooks(params?: { stream_id?: number }) {
    const response = await this.client.get("/webhooks/inbound", { params });
    return response.data;
  }

  async createInboundWebhook(data: {
    name: string;
    description?: string;
    auth_type?: "bearer" | "basic";
    bearer_token?: string;
    basic_username?: string;
    basic_password?: string;
    organization_id?: number | null;
    stream_id?: number;
    scopes?: string[];
    is_active?: boolean;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/webhooks/inbound", data);
    return response.data;
  }

  async updateInboundWebhook(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      auth_type: "bearer" | "basic";
      bearer_token: string;
      basic_username: string;
      basic_password: string;
      organization_id: number | null;
      stream_id: number | null;
      scopes: string[];
      is_active: boolean;
      metadata: Record<string, unknown>;
    }>,
  ) {
    const response = await this.client.put(`/webhooks/inbound/${id}`, data);
    return response.data;
  }

  async deleteInboundWebhook(id: number) {
    const response = await this.client.delete(`/webhooks/inbound/${id}`);
    return response.data;
  }

  async rotateInboundWebhookCredentials(id: number) {
    const response = await this.client.post(
      `/webhooks/inbound/${id}/rotate-credentials`,
    );
    return response.data;
  }

  async getInboundWebhookEvents(id: number) {
    const response = await this.client.get(`/webhooks/inbound/${id}/events`);
    return response.data;
  }

  // Backup Management
  async getBackupJobs(params?: { organization_id?: number }) {
    const response = await this.client.get("/backup/jobs", { params });
    return response.data;
  }

  async getBackupJob(id: number) {
    const response = await this.client.get(`/backup/jobs/${id}`);
    return response.data;
  }

  async createBackupJob(data: {
    name: string;
    schedule: string;
    organization_id?: number;
    retention_days: number;
    enabled?: boolean;
  }) {
    const response = await this.client.post("/backup/jobs", data);
    return response.data;
  }

  async updateBackupJob(
    id: number,
    data: Partial<{
      name: string;
      schedule: string;
      retention_days: number;
      enabled: boolean;
    }>,
  ) {
    const response = await this.client.put(`/backup/jobs/${id}`, data);
    return response.data;
  }

  async deleteBackupJob(id: number) {
    const response = await this.client.delete(`/backup/jobs/${id}`);
    return response.data;
  }

  async runBackupJob(id: number) {
    const response = await this.client.post(`/backup/jobs/${id}/run`);
    return response.data;
  }

  async getBackups(params?: { job_id?: number }) {
    const response = await this.client.get("/backup", { params });
    return response.data;
  }

  async getBackup(id: number) {
    const response = await this.client.get(`/backup/${id}`);
    return response.data;
  }

  async deleteBackup(id: number) {
    const response = await this.client.delete(`/backup/${id}`);
    return response.data;
  }

  async restoreBackup(id: number, data?: { dry_run?: boolean }) {
    const response = await this.client.post(`/backup/${id}/restore`, data);
    return response.data;
  }

  // v2.0.0 Networking Resources & Topology
  async listNetworks(params?: {
    organization_id?: number;
    network_type?: string;
    region?: string;
  }) {
    const response = await this.client.get("/networking/networks", { params });
    return response.data;
  }

  async getNetwork(id: number) {
    const response = await this.client.get(`/networking/networks/${id}`);
    return response.data;
  }

  async createNetwork(data: {
    name: string;
    network_type: string;
    organization_id: number;
    cidr?: string;
    gateway?: string;
    vlan_id?: number;
    mtu?: number;
    description?: string;
    region?: string;
    location?: string;
    parent_id?: number;
    poc?: string;
    organizational_unit?: string;
    attributes?: Record<string, unknown>;
    tags?: string[];
    overwrite?: boolean;
  }) {
    const response = await this.client.post("/networking/networks", data);
    return response.data;
  }

  async updateNetwork(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      region: string;
      location: string;
      attributes: Record<string, unknown>;
      tags: string[];
    }>,
  ) {
    const response = await this.client.put(`/networking/networks/${id}`, data);
    return response.data;
  }

  async deleteNetwork(id: number, hard?: boolean) {
    const response = await this.client.delete(`/networking/networks/${id}`, {
      params: { hard },
    });
    return response.data;
  }

  async listTopologyConnections(params?: {
    network_id?: number;
    connection_type?: string;
  }) {
    const response = await this.client.get("/networking/topology/connections", {
      params,
    });
    return response.data;
  }

  async getTopologyConnection(id: number) {
    const response = await this.client.get(
      `/networking/topology/connections/${id}`,
    );
    return response.data;
  }

  async createTopologyConnection(data: {
    source_network_id: number;
    target_network_id: number;
    connection_type: string;
    bandwidth?: string;
    latency?: number;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post(
      "/networking/topology/connections",
      data,
    );
    return response.data;
  }

  async deleteTopologyConnection(id: number) {
    const response = await this.client.delete(
      `/networking/topology/connections/${id}`,
    );
    return response.data;
  }

  async listEntityMappings(params?: {
    network_id?: number;
    entity_id?: number;
  }) {
    const response = await this.client.get("/networking/mappings", { params });
    return response.data;
  }

  async createEntityMapping(data: {
    network_id: number;
    entity_id: number;
    relationship_type: string;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/networking/mappings", data);
    return response.data;
  }

  async deleteEntityMapping(id: number) {
    const response = await this.client.delete(`/networking/mappings/${id}`);
    return response.data;
  }

  async getNetworkTopologyGraph(
    organizationId: number,
    includeEntities: boolean = false,
  ) {
    const response = await this.client.get("/networking/topology/graph", {
      params: {
        organization_id: organizationId,
        include_entities: includeEntities,
      },
    });
    return response.data;
  }

  // v2.0.0 Built-in Secrets
  async listBuiltinSecrets(params: {
    organization_id: number;
    prefix?: string;
  }) {
    const response = await this.client.get("/builtin-secrets", { params });
    return response.data;
  }

  async getBuiltinSecret(path: string, organizationId: number) {
    const response = await this.client.get(`/builtin-secrets/${path}`, {
      params: { organization_id: organizationId },
    });
    return response.data;
  }

  async createBuiltinSecret(data: {
    name: string;
    value: string;
    organization_id: number;
    description?: string;
    secret_type?: string;
    tags?: string[];
    expires_at?: string;
  }) {
    const response = await this.client.post("/builtin-secrets", data);
    return response.data;
  }

  async updateBuiltinSecret(
    path: string,
    organizationId: number,
    data: { value: string },
  ) {
    const response = await this.client.put(`/builtin-secrets/${path}`, data, {
      params: { organization_id: organizationId },
    });
    return response.data;
  }

  async deleteBuiltinSecret(path: string, organizationId: number) {
    const response = await this.client.delete(`/builtin-secrets/${path}`, {
      params: { organization_id: organizationId },
    });
    return response.data;
  }

  async testBuiltinSecretsConnection(organizationId: number) {
    const response = await this.client.post(
      "/builtin-secrets/test-connection",
      {
        organization_id: organizationId,
      },
    );
    return response.data;
  }

  // v2.2.0 Enterprise Edition - Portal Authentication. Tokens are HttpOnly
  // cookies set by the backend response -- only the "logged in" flag and the
  // proactive-refresh timer are managed client-side.
  async portalLogin(email: string, password: string, tenant?: string) {
    const response = await this.client.post("/portal-auth/login", {
      email,
      password,
      tenant: tenant || "system",
    });
    if (response.data.access_token) {
      markAuthenticated();
      // Schedule proactive token refresh
      if (response.data.expires_in) {
        this.scheduleTokenRefresh(response.data.expires_in);
      }
    }
    return response.data;
  }

  async portalRegister(data: {
    email: string;
    password: string;
    full_name?: string;
    tenant?: string;
  }) {
    const response = await this.client.post("/portal-auth/register", {
      ...data,
      tenant: data.tenant || "system",
    });
    return response.data;
  }

  async portalMfaVerify(code: string) {
    const response = await this.client.post("/portal-auth/mfa/verify", {
      code,
    });
    return response.data;
  }

  async portalMfaEnable() {
    const response = await this.client.post("/portal-auth/mfa/enable");
    return response.data;
  }

  async portalMfaDisable(code: string) {
    const response = await this.client.post("/portal-auth/mfa/disable", {
      code,
    });
    return response.data;
  }

  async portalRefreshToken() {
    // Refresh token is the HttpOnly cookie -- no body needed.
    const response = await this.client.post("/portal-auth/refresh");
    if (response.data.access_token) {
      markAuthenticated();
      // Schedule next proactive refresh
      if (response.data.expires_in) {
        this.scheduleTokenRefresh(response.data.expires_in);
      }
    }
    return response.data;
  }

  async getPortalProfile() {
    const response = await this.client.get("/portal-auth/me");
    return response.data;
  }

  // Enterprise MFA Enforcement & Google Authenticator (TOTP)
  async getMfaPolicy(): Promise<MfaPolicy> {
    const response = await this.client.get("/tenants/current/mfa-policy");
    return response.data;
  }

  async updateMfaPolicy(data: {
    enforced: boolean;
    provider?: string;
    grace_period_days?: number;
  }): Promise<MfaPolicy> {
    const response = await this.client.put("/tenants/current/mfa-policy", data);
    return response.data;
  }

  async enableMfa(): Promise<MfaSetupResponse> {
    const response = await this.client.post("/portal-auth/mfa/enable");
    return response.data;
  }

  async confirmMfa(totpCode: string): Promise<{ verified: boolean }> {
    const response = await this.client.post("/portal-auth/mfa/confirm", {
      totp_code: totpCode,
    });
    return response.data;
  }

  async disableMfa(): Promise<{ success: boolean }> {
    const response = await this.client.post("/portal-auth/mfa/disable");
    return response.data;
  }

  // v2.2.0 Enterprise Edition - Tenant Management
  async getTenants(params?: {
    is_active?: boolean;
    subscription_tier?: string;
  }) {
    const response = await this.client.get("/tenants", { params });
    return response.data;
  }

  async getTenant(id: number) {
    const response = await this.client.get(`/tenants/${id}`);
    return response.data;
  }

  async createTenant(data: {
    name: string;
    slug: string;
    domain?: string;
    subscription_tier?: string;
    license_key?: string;
    settings?: Record<string, unknown>;
    feature_flags?: Record<string, boolean>;
    data_retention_days?: number;
    storage_quota_gb?: number;
  }) {
    const response = await this.client.post("/tenants", data);
    return response.data;
  }

  async updateTenant(
    id: number,
    data: Partial<{
      name: string;
      slug: string;
      domain: string;
      subscription_tier: string;
      license_key: string;
      settings: Record<string, unknown>;
      feature_flags: Record<string, boolean>;
      data_retention_days: number;
      storage_quota_gb: number;
      is_active: boolean;
    }>,
  ) {
    const response = await this.client.put(`/tenants/${id}`, data);
    return response.data;
  }

  async deleteTenant(id: number) {
    const response = await this.client.delete(`/tenants/${id}`);
    return response.data;
  }

  async getTenantUsers(tenantId: number) {
    const response = await this.client.get(`/tenants/${tenantId}/users`);
    return response.data;
  }

  async updateTenantUser(
    tenantId: number,
    userId: number,
    data: Partial<{
      full_name: string;
      tenant_role: string;
      global_role: string;
      is_active: boolean;
    }>,
  ) {
    const response = await this.client.put(
      `/tenants/${tenantId}/users/${userId}`,
      data,
    );
    return response.data;
  }

  async deleteTenantUser(tenantId: number, userId: number) {
    const response = await this.client.delete(
      `/tenants/${tenantId}/users/${userId}`,
    );
    return response.data;
  }

  async getTenantStats(tenantId: number) {
    const response = await this.client.get(`/tenants/${tenantId}/stats`);
    return response.data;
  }

  // v2.2.0 Enterprise Edition - SSO/SAML/SCIM Configuration
  async getIdPConfigs(tenantId?: number) {
    const response = await this.client.get("/sso/idp", {
      params: tenantId ? { tenant_id: tenantId } : undefined,
    });
    return response.data;
  }

  async createIdPConfig(data: {
    name: string;
    idp_type?: string;
    tenant_id?: number;
    entity_id?: string;
    metadata_url?: string;
    sso_url?: string;
    slo_url?: string;
    certificate?: string;
    attribute_mappings?: Record<string, string>;
    jit_provisioning_enabled?: boolean;
    default_role?: string;
  }) {
    const response = await this.client.post("/sso/idp", data);
    return response.data;
  }

  async updateIdPConfig(
    id: number,
    data: Partial<{
      name: string;
      entity_id: string;
      metadata_url: string;
      sso_url: string;
      slo_url: string;
      certificate: string;
      attribute_mappings: Record<string, string>;
      jit_provisioning_enabled: boolean;
      default_role: string;
      is_active: boolean;
    }>,
  ) {
    const response = await this.client.put(`/sso/idp/${id}`, data);
    return response.data;
  }

  async deleteIdPConfig(id: number) {
    const response = await this.client.delete(`/sso/idp/${id}`);
    return response.data;
  }

  async getSCIMConfig(tenantId: number) {
    const response = await this.client.get(`/sso/scim/config/${tenantId}`);
    return response.data;
  }

  async createSCIMConfig(tenantId: number, endpointUrl?: string) {
    const response = await this.client.post("/sso/scim/config", {
      tenant_id: tenantId,
      endpoint_url: endpointUrl,
    });
    return response.data;
  }

  async regenerateSCIMToken(tenantId: number) {
    const response = await this.client.post(
      `/sso/scim/config/${tenantId}/regenerate-token`,
    );
    return response.data;
  }

  async getSPMetadata(tenantId?: number) {
    const response = await this.client.get("/sso/saml/metadata", {
      params: tenantId ? { tenant_id: tenantId } : undefined,
    });
    return response.data;
  }

  // v2.2.0 Enterprise Edition - Audit Logs & Compliance
  async getAuditLogs(params?: {
    tenant_id?: number;
    resource_type?: string;
    resource_id?: number;
    action?: string;
    category?: string;
    identity_id?: number;
    portal_user_id?: number;
    start_date?: string;
    end_date?: string;
    success?: boolean;
    limit?: number;
    offset?: number;
  }) {
    const response = await this.client.get("/audit-enterprise/logs", {
      params,
    });
    return response.data;
  }

  async getComplianceReport(
    tenantId: number,
    reportType: string,
    startDate: string,
    endDate: string,
  ) {
    const response = await this.client.get(
      "/audit-enterprise/reports/compliance",
      {
        params: {
          tenant_id: tenantId,
          report_type: reportType,
          start_date: startDate,
          end_date: endDate,
        },
      },
    );
    return response.data;
  }

  async getAuditRetentionPolicy(tenantId: number) {
    const response = await this.client.get("/audit-enterprise/retention", {
      params: { tenant_id: tenantId },
    });
    return response.data;
  }

  async cleanupAuditLogs(tenantId: number) {
    const response = await this.client.post("/audit-enterprise/cleanup", {
      tenant_id: tenantId,
    });
    return response.data;
  }

  async exportAuditLogs(params: {
    tenant_id?: number;
    start_date?: string;
    end_date?: string;
    format?: "json" | "csv";
  }) {
    const response = await this.client.get("/audit-enterprise/export", {
      params,
    });
    return response.data;
  }

  // IPAM - IP Address Management
  async getIpamPrefixes(params?: {
    organization_id?: number;
    parent_id?: number;
    status?: string;
    is_pool?: boolean;
    vrf_id?: number;
  }) {
    const response = await this.client.get("/ipam/prefixes", { params });
    return response.data;
  }

  async getIpamPrefixTree(id: number) {
    const response = await this.client.get(`/ipam/prefixes/${id}/tree`);
    return response.data;
  }

  async getIpamPrefix(id: number) {
    const response = await this.client.get(`/ipam/prefixes/${id}`);
    return response.data;
  }

  async createIpamPrefix(data: {
    prefix: string;
    organization_id: number;
    description?: string;
    status?: string;
    is_pool?: boolean;
    parent_id?: number;
    vrf_id?: number;
    vlan_id?: number;
    role?: string;
    tenant_id?: number;
    overwrite?: boolean;
  }) {
    const response = await this.client.post("/ipam/prefixes", data);
    return response.data;
  }

  async updateIpamPrefix(
    id: number,
    data: Partial<{
      prefix: string;
      description: string;
      status: string;
      is_pool: boolean;
      parent_id: number;
      vrf_id: number;
      vlan_id: number;
      role: string;
    }>,
  ) {
    const response = await this.client.put(`/ipam/prefixes/${id}`, data);
    return response.data;
  }

  async deleteIpamPrefix(id: number) {
    const response = await this.client.delete(`/ipam/prefixes/${id}`);
    return response.data;
  }

  async getIpamAddresses(params?: {
    organization_id?: number;
    prefix_id?: number;
    status?: string;
    dns_name?: string;
  }) {
    const response = await this.client.get("/ipam/addresses", { params });
    return response.data;
  }

  async getIpamAddress(id: number) {
    const response = await this.client.get(`/ipam/addresses/${id}`);
    return response.data;
  }

  async createIpamAddress(data: {
    address: string;
    organization_id: number;
    prefix_id?: number;
    description?: string;
    status?: string;
    dns_name?: string;
    tenant_id?: number;
    overwrite?: boolean;
  }) {
    const response = await this.client.post("/ipam/addresses", data);
    return response.data;
  }

  async updateIpamAddress(
    id: number,
    data: Partial<{
      address: string;
      description: string;
      status: string;
      dns_name: string;
      prefix_id: number;
    }>,
  ) {
    const response = await this.client.put(`/ipam/addresses/${id}`, data);
    return response.data;
  }

  async deleteIpamAddress(id: number) {
    const response = await this.client.delete(`/ipam/addresses/${id}`);
    return response.data;
  }

  async getIpamVlans(params?: {
    organization_id?: number;
    vid?: number;
    name?: string;
    status?: string;
    group_id?: number;
  }) {
    const response = await this.client.get("/ipam/vlans", { params });
    return response.data;
  }

  async getIpamVlan(id: number) {
    const response = await this.client.get(`/ipam/vlans/${id}`);
    return response.data;
  }

  async createIpamVlan(data: {
    vid: number;
    name: string;
    organization_id: number;
    description?: string;
    status?: string;
    group_id?: number;
    tenant_id?: number;
    overwrite?: boolean;
  }) {
    const response = await this.client.post("/ipam/vlans", data);
    return response.data;
  }

  async updateIpamVlan(
    id: number,
    data: Partial<{
      vid: number;
      name: string;
      description: string;
      status: string;
      group_id: number;
    }>,
  ) {
    const response = await this.client.put(`/ipam/vlans/${id}`, data);
    return response.data;
  }

  async deleteIpamVlan(id: number) {
    const response = await this.client.delete(`/ipam/vlans/${id}`);
    return response.data;
  }

  // ===========================
  // Data Stores (Community)
  // ===========================

  async getDataStores(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    data_classification?: string;
    storage_type?: string;
    location_region?: string;
    contains_pii?: boolean;
    contains_phi?: boolean;
    contains_pci?: boolean;
    compliance_framework?: string;
    poc_identity_id?: number;
    label_id?: number;
    label?: string;
    search?: string;
  }) {
    const response = await this.client.get("/data-stores", { params });
    return response.data;
  }

  async getDataStore(id: number) {
    const response = await this.client.get(`/data-stores/${id}`);
    return response.data;
  }

  async createDataStore(data: {
    name: string;
    description?: string;
    organization_id: number;
    data_classification: string;
    storage_type: string;
    location_region?: string;
    contains_pii?: boolean;
    contains_phi?: boolean;
    contains_pci?: boolean;
    compliance_framework?: string;
    poc_identity_id?: number;
    label_ids?: number[];
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/data-stores", data);
    return response.data;
  }

  async updateDataStore(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      data_classification: string;
      storage_type: string;
      location_region: string;
      contains_pii: boolean;
      contains_phi: boolean;
      contains_pci: boolean;
      compliance_framework: string;
      poc_identity_id: number;
      label_ids: number[];
      metadata: Record<string, unknown> | null;
    }>,
  ) {
    const response = await this.client.put(`/data-stores/${id}`, data);
    return response.data;
  }

  async deleteDataStore(id: number) {
    const response = await this.client.delete(`/data-stores/${id}`);
    return response.data;
  }

  async getDataStoreLabels(id: number) {
    const response = await this.client.get(`/data-stores/${id}/labels`);
    return response.data;
  }

  async addDataStoreLabel(id: number, labelId: number) {
    const response = await this.client.post(
      `/data-stores/${id}/labels/${labelId}`,
    );
    return response.data;
  }

  async removeDataStoreLabel(id: number, labelId: number) {
    const response = await this.client.delete(
      `/data-stores/${id}/labels/${labelId}`,
    );
    return response.data;
  }

  // Services (Microservice Tracking)
  async getServices(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    language?: string;
    deployment_method?: string;
    status?: string;
    search?: string;
  }) {
    const response = await this.client.get("/services", { params });
    return response.data;
  }

  async getServiceById(id: number) {
    const response = await this.client.get(`/services/${id}`);
    return response.data;
  }

  async createService(data: {
    name: string;
    organization_id: number;
    description?: string;
    language?: string;
    deployment_method?: string;
    is_public?: boolean;
    domains?: string[];
    paths?: string[];
    port?: number;
    status?: string;
  }) {
    const response = await this.client.post("/services", data);
    return response.data;
  }

  async updateService(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      language: string;
      deployment_method: string;
      is_public: boolean;
      domains: string[];
      paths: string[];
      port: number;
      status: string;
    }>,
  ) {
    const response = await this.client.put(`/services/${id}`, data);
    return response.data;
  }

  async deleteService(id: number) {
    const response = await this.client.delete(`/services/${id}`);
    return response.data;
  }

  // Certificates
  async getCertificates(params?: {
    page?: number;
    per_page?: number;
    search?: string;
    organization_id?: number;
  }) {
    const response = await this.client.get("/certificates", { params });
    return response.data;
  }

  async getCertificate(id: number) {
    const response = await this.client.get(`/certificates/${id}`);
    return response.data;
  }

  async createCertificate(data: {
    name: string;
    description?: string;
    organization_id: number;
    creator: string;
    cert_type: string;
    common_name?: string;
    issue_date: string;
    expiration_date: string;
    auto_renew?: boolean;
    certificate_pem?: string;
  }) {
    const response = await this.client.post("/certificates", data);
    return response.data;
  }

  async updateCertificate(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      organization_id: number;
      creator: string;
      cert_type: string;
      common_name: string;
      issue_date: string;
      expiration_date: string;
      auto_renew: boolean;
      certificate_pem: string;
      status: string;
    }>,
  ) {
    const response = await this.client.put(`/certificates/${id}`, data);
    return response.data;
  }

  async deleteCertificate(id: number) {
    const response = await this.client.delete(`/certificates/${id}`);
    return response.data;
  }

  // Village ID Resolution
  async resolveVillageId(villageId: string): Promise<{
    village_id: string;
    resource_type: string;
    resource_id: number | string;
    title?: string | null;
    module?: string | null;
    redirect_url: string;
    short_link?: string;
  }> {
    const response = await this.client.get(
      `/id/${encodeURIComponent(villageId)}`,
    );
    return response.data;
  }

  // Admin Logs (global admin only)
  async getLogs(): Promise<{
    lines: string[];
    total: number;
    log_file: string;
  }> {
    const response = await this.client.get("/logs");
    return response.data;
  }

  async searchLogs(query: string): Promise<{
    lines: string[];
    total_matches: number;
    query: string;
    log_file: string;
  }> {
    const response = await this.client.get("/logs/search", {
      params: { q: query },
    });
    return response.data;
  }

  // SBOM Schedule Management
  async getSBOMSchedules(params?: {
    parent_type?: string;
    parent_id?: number;
    is_active?: boolean;
  }) {
    const response = await this.client.get("/sbom/schedules", { params });
    return response.data;
  }

  async getSBOMSchedule(id: number) {
    const response = await this.client.get(`/sbom/schedules/${id}`);
    return response.data;
  }

  async createSBOMSchedule(data: {
    parent_type: string;
    parent_id: number;
    schedule_cron: string;
    is_active?: boolean;
    credential_type?: string;
    credential_id?: number;
    credential_mapping?: Record<string, string>;
  }) {
    const response = await this.client.post("/sbom/schedules", data);
    return response.data;
  }

  async updateSBOMSchedule(
    id: number,
    data: Partial<{
      schedule_cron: string;
      is_active: boolean;
      credential_type: string;
      credential_id: number;
      credential_mapping: Record<string, string>;
    }>,
  ) {
    const response = await this.client.put(`/sbom/schedules/${id}`, data);
    return response.data;
  }

  async deleteSBOMSchedule(id: number) {
    const response = await this.client.delete(`/sbom/schedules/${id}`);
    return response.data;
  }

  // Trigger immediate SBOM scan for a service/software
  async createSBOMScan(data: {
    parent_type: string;
    parent_id: number;
    scan_type: string;
    repository_url?: string;
    repository_branch?: string;
    credential_type?: string;
    credential_id?: number;
    credential_mapping?: Record<string, string>;
  }) {
    const response = await this.client.post("/sbom/scans", data);
    return response.data;
  }

  async getSBOMScans(params?: {
    parent_type?: string;
    parent_id?: number;
    status?: string;
    page?: number;
    per_page?: number;
  }) {
    const response = await this.client.get("/sbom/scans", { params });
    return response.data;
  }

  // License Policies
  async getLicensePolicies(params?: {
    page?: number;
    per_page?: number;
    search?: string;
  }) {
    const response = await this.client.get("/license-policies", { params });
    return response.data;
  }

  async getLicensePolicy(id: number) {
    const response = await this.client.get(`/license-policies/${id}`);
    return response.data;
  }

  async createLicensePolicy(data: {
    name: string;
    description?: string;
    allowed_patterns?: string;
    denied_patterns?: string;
    is_active?: boolean;
  }) {
    const response = await this.client.post("/license-policies", data);
    return response.data;
  }

  async updateLicensePolicy(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      allowed_patterns: string;
      denied_patterns: string;
      is_active: boolean;
    }>,
  ) {
    const response = await this.client.put(`/license-policies/${id}`, data);
    return response.data;
  }

  async deleteLicensePolicy(id: number) {
    const response = await this.client.delete(`/license-policies/${id}`);
    return response.data;
  }

  // Vulnerabilities
  async getVulnerabilities(params?: {
    page?: number;
    per_page?: number;
    search?: string;
    severity?: string;
    status?: string;
    owner_organization_id?: number;
    owner_entity_id?: number;
  }) {
    const response = await this.client.get("/vulnerabilities", { params });
    return response.data;
  }

  async getVulnerability(id: number) {
    const response = await this.client.get(`/vulnerabilities/${id}`);
    return response.data;
  }

  async createVulnerability(data: {
    cve_id: string;
    title: string;
    severity: string;
    cvss_score?: number | null;
    adjusted_risk_score?: string | null;
    owner_organization_id?: number | null;
    owner_entity_id?: number | null;
    remediation_issue_id?: number | null;
    description?: string;
    published_at?: string;
    source?: string;
  }) {
    const response = await this.client.post("/vulnerabilities", data);
    return response.data;
  }

  async updateVulnerability(
    id: number,
    data: {
      title?: string;
      severity?: string;
      description?: string | null;
      cvss_score?: number | null;
      adjusted_risk_score?: string | null;
      owner_organization_id?: number | null;
      owner_entity_id?: number | null;
      remediation_issue_id?: number | null;
      published_at?: string | null;
    },
  ) {
    const response = await this.client.patch(`/vulnerabilities/${id}`, data);
    return response.data;
  }

  async createVulnerabilityIssue(
    vulnId: number,
    data?: {
      organization_id?: number;
      title?: string;
      description?: string;
      priority?: string;
    },
  ) {
    const response = await this.client.post(
      `/vulnerabilities/${vulnId}/create-issue`,
      data || {},
    );
    return response.data;
  }

  // CSV Bulk Import & Templates
  async getCsvTemplates() {
    const response = await this.client.get("/import/templates");
    return response.data;
  }

  async downloadCsvTemplate(resource: string): Promise<string> {
    const response = await this.client.get(
      `/import/templates/${resource}/download`,
      { responseType: "text" },
    );
    return response.data;
  }

  async importCsvResource(
    resource: string,
    fileOrText: File | string,
  ): Promise<CsvImportResponse> {
    if (typeof fileOrText === "string") {
      const response = await this.client.post<CsvImportResponse>(
        `/import/${resource}`,
        {
          csv_text: fileOrText,
        },
      );
      return response.data;
    }
    const formData = new FormData();
    formData.append("file", fileOrText);
    const response = await this.client.post<CsvImportResponse>(
      `/import/${resource}`,
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
      },
    );
    return response.data;
  }

  async getCsvImportJobs(params?: {
    resource?: string;
    status?: CsvImportJobStatus;
    limit?: number;
    offset?: number;
  }): Promise<CsvImportJobsResponse> {
    const response = await this.client.get<CsvImportJobsResponse>(
      "/import/jobs",
      { params },
    );
    return response.data;
  }

  async getCsvImportJob(jobId: string): Promise<CsvImportJob> {
    const response = await this.client.get<CsvImportJob>(
      `/import/jobs/${encodeURIComponent(jobId)}`,
    );
    return response.data;
  }

  async updateVulnerabilityStatus(
    id: number,
    data: Partial<{ status: string }>,
  ) {
    const response = await this.client.patch(
      `/vulnerabilities/component-vulnerabilities/${id}`,
      data,
    );
    return response.data;
  }

  async assignVulnerability(
    vulnId: number,
    data: {
      parent_type: "service" | "software";
      parent_id: number;
      notes?: string;
    },
  ) {
    const response = await this.client.post(
      `/vulnerabilities/${vulnId}/assign`,
      data,
    );
    return response.data;
  }

  // ===========================
  // On-Call Rotations
  // ===========================

  async getOnCallRotations(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    service_id?: number;
    status?: string;
    search?: string;
    schedule_type?: string;
    scope_type?: string;
  }) {
    const response = await this.client.get("/on-call/rotations", { params });
    return response.data;
  }

  async getOnCallRotation(id: number) {
    const response = await this.client.get(`/on-call/rotations/${id}`);
    return response.data;
  }

  async createOnCallRotation(data: {
    name: string;
    organization_id: number;
    service_id?: number;
    description?: string;
    rotation_type: string;
    timezone?: string;
    enabled?: boolean;
    escalation_policy_id?: number;
    metadata?: Record<string, unknown>;
  }) {
    const response = await this.client.post("/on-call/rotations", data);
    return response.data;
  }

  async updateOnCallRotation(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      rotation_type: string;
      timezone: string;
      enabled: boolean;
      escalation_policy_id: number;
      metadata: Record<string, unknown>;
    }>,
  ) {
    const response = await this.client.put(`/on-call/rotations/${id}`, data);
    return response.data;
  }

  async deleteOnCallRotation(id: number) {
    const response = await this.client.delete(`/on-call/rotations/${id}`);
    return response.data;
  }

  async getCurrentOnCall(scopeType: string, scopeId: number) {
    const response = await this.client.get(
      `/on-call/rotations/current/${scopeType}/${scopeId}`,
    );
    return response.data;
  }

  async getOnCallParticipants(
    rotationId: number,
    params?: { page?: number; per_page?: number },
  ) {
    const response = await this.client.get(
      `/on-call/rotations/${rotationId}/participants`,
      { params },
    );
    return response.data;
  }

  async addOnCallParticipant(
    rotationId: number,
    data: {
      identity_id: number;
      order: number;
      start_date?: string;
      end_date?: string;
      metadata?: Record<string, unknown>;
    },
  ) {
    const response = await this.client.post(
      `/on-call/rotations/${rotationId}/participants`,
      data,
    );
    return response.data;
  }

  async updateOnCallParticipant(
    rotationId: number,
    participantId: number,
    data: Partial<{
      order: number;
      start_date: string;
      end_date: string;
      is_active: boolean;
      metadata: Record<string, unknown>;
    }>,
  ) {
    const response = await this.client.put(
      `/on-call/rotations/${rotationId}/participants/${participantId}`,
      data,
    );
    return response.data;
  }

  async removeOnCallParticipant(rotationId: number, participantId: number) {
    const response = await this.client.delete(
      `/on-call/rotations/${rotationId}/participants/${participantId}`,
    );
    return response.data;
  }

  async getOnCallOverrides(
    rotationId: number,
    params?: {
      page?: number;
      per_page?: number;
      status?: string;
      start_date?: string;
      end_date?: string;
    },
  ) {
    const response = await this.client.get(
      `/on-call/rotations/${rotationId}/overrides`,
      { params },
    );
    return response.data;
  }

  async createOnCallOverride(
    rotationId: number,
    data: {
      override_identity_id: number;
      original_identity_id?: number;
      start_datetime: string;
      end_datetime: string;
      reason?: string;
      created_by_id?: number;
    },
  ) {
    const response = await this.client.post(
      `/on-call/rotations/${rotationId}/overrides`,
      data,
    );
    return response.data;
  }

  async updateOnCallOverride(
    rotationId: number,
    overrideId: number,
    data: Partial<{
      override_participant_id: number;
      override_start: string;
      override_end: string;
      reason: string;
    }>,
  ) {
    const response = await this.client.put(
      `/on-call/rotations/overrides/${overrideId}`,
      data,
    );
    return response.data;
  }

  async deleteOnCallOverride(rotationId: number, overrideId: number) {
    const response = await this.client.delete(
      `/on-call/rotations/overrides/${overrideId}`,
    );
    return response.data;
  }

  async getOnCallHistory(
    rotationId: number,
    params?: {
      page?: number;
      per_page?: number;
      start_date?: string;
      end_date?: string;
      participant_id?: number;
    },
  ) {
    const response = await this.client.get(
      `/on-call/rotations/${rotationId}/history`,
      { params },
    );
    return response.data;
  }

  async getEscalationPolicies(params?: {
    page?: number;
    per_page?: number;
    organization_id?: number;
    rotation_id?: number;
    search?: string;
  }) {
    const response = await this.client.get(
      `/on-call/rotations/${params?.rotation_id || 1}/escalations`,
      { params },
    );
    return response.data;
  }

  async createEscalationPolicy(
    rotationId: number,
    data: {
      name: string;
      organization_id: number;
      description?: string;
      escalation_rules: Array<{
        level: number;
        delay_minutes: number;
        notification_targets: string[];
      }>;
      enabled?: boolean;
    },
  ) {
    const response = await this.client.post(
      `/on-call/rotations/${rotationId}/escalations`,
      data,
    );
    return response.data;
  }

  async updateEscalationPolicy(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      escalation_rules: Array<{
        level: number;
        delay_minutes: number;
        notification_targets: string[];
      }>;
      enabled: boolean;
    }>,
  ) {
    const response = await this.client.put(
      `/on-call/rotations/escalations/${id}`,
      data,
    );
    return response.data;
  }

  async deleteEscalationPolicy(id: number) {
    const response = await this.client.delete(
      `/on-call/rotations/escalations/${id}`,
    );
    return response.data;
  }

  // Cost tracking
  async getResourceCosts(resourceType: string, resourceId: number) {
    const response = await this.client.get(
      `/costs/${resourceType}/${resourceId}`,
    );
    return response.data;
  }

  async updateResourceCosts(
    resourceType: string,
    resourceId: number,
    data: Record<string, unknown>,
  ) {
    const response = await this.client.post(
      `/costs/${resourceType}/${resourceId}`,
      data,
    );
    return response.data;
  }

  // Module management (Phase 0)
  async getModules() {
    const response = await this.client.get("/modules");
    return response.data;
  }

  // Module management (Phase 2) - tenant per-module toggles
  async getTenantModules(tenantId: string) {
    const response = await this.client.get(`/tenants/${tenantId}/modules`);
    return response.data;
  }

  async setTenantModule(
    tenantId: string,
    data: {
      module_name: string;
      enabled: boolean;
      settings?: Record<string, unknown>;
    },
  ) {
    const response = await this.client.put(
      `/tenants/${tenantId}/modules`,
      data,
    );
    return response.data;
  }

  // ===========================
  // Diagrams (Phase 4c)
  // ===========================

  async getDiagrams(params?: {
    page?: number;
    per_page?: number;
    status?: string;
    q?: string;
    is_template?: boolean;
  }) {
    const response = await this.client.get("/diagrams", { params });
    return response.data;
  }

  async getDiagram(id: number) {
    const response = await this.client.get(`/diagrams/${id}`);
    return response.data;
  }

  async createDiagram(data: {
    title: string;
    description?: string;
    tags?: string[];
    is_public?: boolean;
    is_template?: boolean;
    status?: string;
    content?: { nodes: unknown[]; edges: unknown[] };
  }) {
    const response = await this.client.post("/diagrams", data);
    return response.data;
  }

  async updateDiagram(
    id: number,
    data: Partial<{
      title: string;
      description: string;
      tags: string[];
      status: string;
      is_public: boolean;
      is_template: boolean;
      thumbnail_url: string;
    }>,
  ) {
    const response = await this.client.patch(`/diagrams/${id}`, data);
    return response.data;
  }

  async deleteDiagram(id: number) {
    const response = await this.client.delete(`/diagrams/${id}`);
    return response.data;
  }

  async saveDiagramVersion(
    id: number,
    data: {
      content: { nodes: unknown[]; edges: unknown[] };
      change_summary?: string;
    },
  ) {
    const response = await this.client.post(`/diagrams/${id}/versions`, data);
    return response.data;
  }

  async getDiagramVersions(
    id: number,
    params?: { page?: number; per_page?: number },
  ) {
    const response = await this.client.get(`/diagrams/${id}/versions`, {
      params,
    });
    return response.data;
  }

  async getDiagramVersion(id: number, versionNumber: number) {
    const response = await this.client.get(
      `/diagrams/${id}/versions/${versionNumber}`,
    );
    return response.data;
  }

  async restoreDiagramVersion(id: number, versionNumber: number) {
    const response = await this.client.post(
      `/diagrams/${id}/versions/${versionNumber}/restore`,
    );
    return response.data;
  }

  // Diagram Collaboration (Phase 4d-2)
  async getCollabTicket(id: number) {
    const response = await this.client.post(`/diagrams/${id}/collab/ticket`);
    return response.data as { ticket: string; ws_path: string };
  }

  // ===========================
  // Documents (Phase 4e)
  // ===========================

  async getDocuments(params?: {
    page?: number;
    per_page?: number;
    status?: string;
    category?: string;
    q?: string;
  }) {
    const response = await this.client.get("/documents", { params });
    return response.data;
  }

  async getDocument(slug: string) {
    const response = await this.client.get(`/documents/${slug}`);
    return response.data;
  }

  async createDocument(data: {
    title: string;
    body: string;
    category?: string;
    tags?: string[];
    visibility?: "public" | "authenticated" | "roles" | "users";
    visibility_roles?: string[];
    visibility_users?: number[];
  }) {
    const response = await this.client.post("/documents", data);
    return response.data;
  }

  async updateDocument(
    docId: number,
    data: Partial<{
      title: string;
      body: string;
      category: string;
      tags: string[];
      visibility: "public" | "authenticated" | "roles" | "users";
      visibility_roles: string[];
      visibility_users: number[];
    }>,
  ) {
    const response = await this.client.patch(`/documents/${docId}`, data);
    return response.data;
  }

  async deleteDocument(docId: number) {
    const response = await this.client.delete(`/documents/${docId}`);
    return response.data;
  }

  async publishDocument(docId: number) {
    const response = await this.client.post(`/documents/${docId}/publish`);
    return response.data;
  }

  async getCollections() {
    const response = await this.client.get("/collections");
    return response.data;
  }

  async createCollection(data: {
    name: string;
    slug?: string;
    description?: string;
    parent_id?: number;
  }) {
    const response = await this.client.post("/collections", data);
    return response.data;
  }

  async updateCollection(
    collId: number,
    data: Partial<{
      name: string;
      slug: string;
      description: string;
      parent_id: number;
    }>,
  ) {
    const response = await this.client.patch(`/collections/${collId}`, data);
    return response.data;
  }

  async deleteCollection(collId: number) {
    const response = await this.client.delete(`/collections/${collId}`);
    return response.data;
  }

  async attachDocumentToCollection(collId: number, docId: number) {
    const response = await this.client.post(
      `/collections/${collId}/documents/${docId}`,
    );
    return response.data;
  }

  async detachDocumentFromCollection(collId: number, docId: number) {
    const response = await this.client.delete(
      `/collections/${collId}/documents/${docId}`,
    );
    return response.data;
  }

  async getDocumentVersions(
    docId: number,
    params?: { page?: number; per_page?: number },
  ) {
    const response = await this.client.get(`/documents/${docId}/versions`, {
      params,
    });
    return response.data;
  }

  async getDocumentVersion(docId: number, versionNumber: number) {
    const response = await this.client.get(
      `/documents/${docId}/versions/${versionNumber}`,
    );
    return response.data;
  }

  async restoreDocumentVersion(docId: number, versionNumber: number) {
    const response = await this.client.post(
      `/documents/${docId}/versions/${versionNumber}/restore`,
    );
    return response.data;
  }

  // ===========================
  // Pages (Phase 4f)
  // ===========================

  async getPages(params?: {
    page?: number;
    per_page?: number;
    status?: string;
    visibility?: string;
    search?: string;
  }) {
    const response = await this.client.get("/pages", { params });
    return response.data;
  }

  async getPage(slug: string) {
    const response = await this.client.get(`/pages/${slug}`);
    return response.data;
  }

  async createPage(data: {
    title: string;
    body_html: string;
    status?: "draft" | "published";
    visibility?: "public" | "authenticated" | "roles" | "users";
    visibility_roles?: string[];
    visibility_users?: number[];
    is_public?: boolean;
  }) {
    const response = await this.client.post("/pages", data);
    return response.data;
  }

  async updatePage(
    slug: string,
    data: Partial<{
      title: string;
      body_html: string;
      status: "draft" | "published";
      visibility: "public" | "authenticated" | "roles" | "users";
      visibility_roles: string[];
      visibility_users: number[];
      is_public: boolean;
    }>,
  ) {
    const response = await this.client.put(`/pages/${slug}`, data);
    return response.data;
  }

  async deletePage(slug: string) {
    const response = await this.client.delete(`/pages/${slug}`);
    return response.data;
  }

  async attachPageToCollection(slug: string, collectionId: number) {
    const response = await this.client.post(
      `/pages/${slug}/collections/${collectionId}`,
    );
    return response.data;
  }

  async detachPageFromCollection(slug: string, collectionId: number) {
    const response = await this.client.delete(
      `/pages/${slug}/collections/${collectionId}`,
    );
    return response.data;
  }

  async getPageBacklinks(slug: string) {
    const response = await this.client.get(`/pages/${slug}/backlinks`);
    return response.data;
  }

  // ===========================
  // Streams (Phase 4g)
  // ===========================

  async listStreams(params?: {
    page?: number;
    per_page?: number;
    search?: string;
  }) {
    const response = await this.client.get("/streams", { params });
    return response.data;
  }

  async getStream(id: number) {
    const response = await this.client.get(`/streams/${id}`);
    return response.data;
  }

  async createStream(data: {
    name: string;
    description?: string;
    trigger_type?: string;
    trigger_config?: Record<string, unknown>;
    is_enabled?: boolean;
    canvas_data?: {
      nodes: unknown[];
      edges: unknown[];
      viewport?: { x: number; y: number; zoom: number };
    };
  }) {
    const response = await this.client.post("/streams", data);
    return response.data;
  }

  async updateStream(
    id: number,
    data: Partial<{
      name: string;
      description: string;
      trigger_type: string;
      trigger_config: Record<string, unknown>;
      is_enabled: boolean;
      is_public: boolean;
      tags: string[];
      status: string;
      canvas_data: {
        nodes: unknown[];
        edges: unknown[];
        viewport?: { x: number; y: number; zoom: number };
      };
    }>,
  ) {
    const response = await this.client.put(`/streams/${id}`, data);
    return response.data;
  }

  async getStreamNodeCatalog() {
    const response = await this.client.get("/streams/node-catalog");
    return response.data;
  }

  async validateStream(
    id: number,
    data?: {
      canvas_data?: { nodes: unknown[]; edges: unknown[] };
      nodes?: unknown[];
      edges?: unknown[];
    },
  ) {
    const response = await this.client.post(
      `/streams/${id}/validate`,
      data || {},
    );
    return response.data;
  }

  async deleteStream(id: number) {
    const response = await this.client.delete(`/streams/${id}`);
    return response.data;
  }

  async duplicateStream(id: number, data?: { name?: string }) {
    const response = await this.client.post(
      `/streams/${id}/duplicate`,
      data || {},
    );
    return response.data;
  }

  async getStreamLock(id: number) {
    const response = await this.client.get(`/streams/${id}/lock`);
    return response.data;
  }

  async acquireStreamLock(id: number) {
    const response = await this.client.post(`/streams/${id}/lock`);
    return response.data;
  }

  async releaseStreamLock(id: number) {
    const response = await this.client.delete(`/streams/${id}/lock`);
    return response.data;
  }

  async executeStream(id: number, data?: Record<string, unknown>) {
    const response = await this.client.post(
      `/streams/${id}/execute`,
      data || {},
    );
    return response.data;
  }

  async listStreamExecutions(
    id: number,
    params?: { page?: number; per_page?: number },
  ) {
    const response = await this.client.get(`/streams/${id}/executions`, {
      params,
    });
    return response.data;
  }

  async listAllStreamExecutions(params?: {
    page?: number;
    per_page?: number;
    source?: "all" | "playbook" | "coderun" | "webhook";
    status?: string;
    search?: string;
    stream_id?: number;
  }) {
    const response = await this.client.get("/streams/executions", { params });
    return response.data;
  }

  async getStreamExecution(id: number, executionId: string) {
    const url = id
      ? `/streams/${id}/executions/${executionId}`
      : `/streams/executions/${executionId}`;
    const response = await this.client.get(url);
    return response.data;
  }

  async getExecutionById(executionId: string) {
    const response = await this.client.get(
      `/streams/executions/${executionId}`,
    );
    return response.data;
  }

  async listMyApprovals(params?: { page?: number; per_page?: number }) {
    const response = await this.client.get("/streams/my-approvals", { params });
    return response.data;
  }

  async approveExecution(executionId: string, data?: { comment?: string }) {
    const response = await this.client.post(
      `/streams/executions/${executionId}/approve`,
      data || {},
    );
    return response.data;
  }

  async rejectExecution(executionId: string, data?: { comment?: string }) {
    const response = await this.client.post(
      `/streams/executions/${executionId}/reject`,
      data || {},
    );
    return response.data;
  }

  async getApprovalStatus(executionId: string) {
    const response = await this.client.get(
      `/streams/executions/${executionId}/approval-status`,
    );
    return response.data;
  }

  async getApprovalHistory(params?: { limit?: number }) {
    const response = await this.client.get("/streams/approvals/history", {
      params,
    });
    return response.data;
  }

  // Geo Regions & Locations
  async getGeoRegions(params?: { provider?: string }) {
    const response = await this.client.get("/geo/regions", { params });
    return response.data;
  }

  async createGeoRegion(data: {
    code: string;
    name: string;
    provider?: string;
    latitude?: number;
    longitude?: number;
    description?: string;
    overwrite?: boolean;
  }) {
    const response = await this.client.post("/geo/regions", data);
    return response.data;
  }

  async updateGeoRegion(
    id: number,
    data: Partial<{
      code: string;
      name: string;
      provider: string;
      latitude: number;
      longitude: number;
      description: string;
    }>,
  ) {
    const response = await this.client.put(`/geo/regions/${id}`, data);
    return response.data;
  }

  async deleteGeoRegion(id: number) {
    const response = await this.client.delete(`/geo/regions/${id}`);
    return response.data;
  }

  async getGeoLocations(params?: { region_code?: string }) {
    const response = await this.client.get("/geo/locations", { params });
    return response.data;
  }

  async createGeoLocation(data: {
    name: string;
    region_code?: string;
    city?: string;
    state?: string;
    country?: string;
    address?: string;
    latitude?: number;
    longitude?: number;
    description?: string;
    overwrite?: boolean;
  }) {
    const response = await this.client.post("/geo/locations", data);
    return response.data;
  }

  async updateGeoLocation(
    id: number,
    data: Partial<{
      name: string;
      region_code: string;
      city: string;
      state: string;
      country: string;
      address: string;
      latitude: number;
      longitude: number;
      description: string;
    }>,
  ) {
    const response = await this.client.put(`/geo/locations/${id}`, data);
    return response.data;
  }

  async deleteGeoLocation(id: number) {
    const response = await this.client.delete(`/geo/locations/${id}`);
    return response.data;
  }

  async geocodeLocation(query: string) {
    const response = await this.client.post("/geo/geocode", { query });
    return response.data;
  }

  // External Issue/Milestone/Project/Label Sync API
  async getSyncConfigs(): Promise<{ configs: SyncConfigItem[] }> {
    const response = await this.client.get("/sync/configs");
    return response.data;
  }

  async getSyncConfig(id: number): Promise<{ config: SyncConfigItem }> {
    const response = await this.client.get(`/sync/configs/${id}`);
    return response.data;
  }

  async createSyncConfig(
    data: CreateSyncConfigPayload,
  ): Promise<{ config: SyncConfigItem }> {
    const response = await this.client.post("/sync/configs", data);
    return response.data;
  }

  async updateSyncConfig(
    id: number,
    data: UpdateSyncConfigPayload,
  ): Promise<{ config: SyncConfigItem }> {
    const response = await this.client.patch(`/sync/configs/${id}`, data);
    return response.data;
  }

  async deleteSyncConfig(id: number): Promise<{ message: string }> {
    const response = await this.client.delete(`/sync/configs/${id}`);
    return response.data;
  }

  async testSyncConfig(id: number): Promise<SyncTestResult> {
    const response = await this.client.post(`/sync/configs/${id}/test`);
    return response.data;
  }

  async triggerSyncConfig(
    id: number,
    data?: {
      resource_types?: SyncResourceType[];
      direction?: SyncDirection;
      since?: string;
    },
  ): Promise<SyncTriggerResult> {
    const response = await this.client.post(
      `/sync/configs/${id}/trigger`,
      data || {},
    );
    return response.data;
  }

  async getSyncStatus(): Promise<SyncStatusSummary> {
    const response = await this.client.get("/sync/status");
    return response.data;
  }

  async getSyncHistory(params?: {
    config_id?: number;
    sync_type?: string;
    page?: number;
    per_page?: number;
  }): Promise<{
    history: SyncHistoryItem[];
    pagination: {
      page: number;
      per_page: number;
      total: number;
      pages: number;
    };
  }> {
    const response = await this.client.get("/sync/history", { params });
    return response.data;
  }

  async getSyncMappings(params?: {
    config_id?: number;
    elder_type?: string;
  }): Promise<{ mappings: SyncMappingItem[] }> {
    const response = await this.client.get("/sync/mappings", { params });
    return response.data;
  }

  async getSyncConflicts(params?: {
    resolved?: boolean;
    config_id?: number;
  }): Promise<{ conflicts: SyncConflictItem[] }> {
    const response = await this.client.get("/sync/conflicts", { params });
    return response.data;
  }

  async resolveSyncConflict(
    id: number,
    resolution_strategy: string,
  ): Promise<{ conflict: SyncConflictItem }> {
    const response = await this.client.post(`/sync/conflicts/${id}/resolve`, {
      resolution_strategy,
    });
    return response.data;
  }

  async getAIResearcherConfig(): Promise<AIResearcherConfig> {
    const response = await this.client.get("/search/ai-research/config");
    return response.data;
  }

  async runAIResearch(payload: AIResearchRequest): Promise<AIResearchResponse> {
    const response = await this.client.post("/search/ai-research", payload, {
      timeout: 120000,
    });
    return response.data;
  }

  // Licensing
  async getLicenseStatus(): Promise<LicenseStatus> {
    const response = await this.client.get("/licensing/status");
    return response.data;
  }

  // Runs (Serverless Functions)
  async listRunFunctions(params?: {
    runtime?: string;
    search?: string;
    page?: number;
    per_page?: number;
  }): Promise<RunFunctionListResponse> {
    const response = await this.client.get("/runs", { params });
    const raw = response.data || {};
    const rows: RunFunction[] = Array.isArray(raw.items)
      ? raw.items
      : Array.isArray(raw.data)
        ? raw.data
        : [];
    return {
      ...raw,
      items: rows,
      data: rows,
      total: typeof raw.total === "number" ? raw.total : rows.length,
      page: typeof raw.page === "number" ? raw.page : (params?.page ?? 1),
      per_page:
        typeof raw.per_page === "number"
          ? raw.per_page
          : (params?.per_page ?? 50),
    };
  }

  async getRunFunction(id: number): Promise<RunFunction> {
    const response = await this.client.get(`/runs/${id}`);
    return response.data;
  }

  async createRunFunction(data: RunFunctionInput): Promise<RunFunction> {
    const response = await this.client.post("/runs", data);
    return response.data;
  }

  async updateRunFunction(
    id: number,
    data: Partial<RunFunctionInput>,
  ): Promise<RunFunction> {
    const response = await this.client.put(`/runs/${id}`, data);
    return response.data;
  }

  async deleteRunFunction(
    id: number,
  ): Promise<{ message?: string; deleted?: boolean }> {
    const response = await this.client.delete(`/runs/${id}`);
    return response.data;
  }

  async invokeRunFunction(
    id: number,
    payload?: Record<string, unknown>,
  ): Promise<RunExecution> {
    const response = await this.client.post(`/runs/${id}/invoke`, payload ?? {});
    return response.data;
  }

  async listRunExecutions(params?: {
    function_id?: number;
    status?: string;
    page?: number;
    per_page?: number;
  }): Promise<RunExecutionListResponse> {
    const response = await this.client.get("/runs/executions", { params });
    const raw = response.data || {};
    const rawRows: (RunExecution & {
      stdout_log?: string | null;
      stderr_log?: string | null;
    })[] = Array.isArray(raw.items)
      ? raw.items
      : Array.isArray(raw.data)
        ? raw.data
        : [];
    const rows: RunExecution[] = rawRows.map((row) => ({
      ...row,
      stdout: row.stdout ?? row.stdout_log ?? null,
      stderr: row.stderr ?? row.stderr_log ?? null,
    }));
    return {
      ...raw,
      items: rows,
      data: rows,
      total: typeof raw.total === "number" ? raw.total : rows.length,
      page: typeof raw.page === "number" ? raw.page : (params?.page ?? 1),
      per_page:
        typeof raw.per_page === "number"
          ? raw.per_page
          : (params?.per_page ?? 25),
    };
  }

  async getRunExecution(executionId: string): Promise<RunExecution> {
    const response = await this.client.get(`/runs/executions/${executionId}`);
    const row = response.data || {};
    return {
      ...row,
      stdout: row.stdout ?? row.stdout_log ?? null,
      stderr: row.stderr ?? row.stderr_log ?? null,
    };
  }

  async cancelRunExecution(executionId: string): Promise<RunExecutionDetail> {
    const response = await this.client.post(
      `/runs/executions/${executionId}/cancel`,
    );
    const row = response.data || {};
    return {
      ...row,
      stdout: row.stdout ?? row.stdout_log ?? null,
      stderr: row.stderr ?? row.stderr_log ?? null,
    };
  }

  async getRunPermissionsCatalog(): Promise<RunPermissionsCatalogResponse> {
    try {
      const response = await this.client.get("/runs/permissions/catalog");
      const raw = response.data as
        | {
            groups?: Array<{
              id?: string;
              key?: string;
              name?: string;
              label?: string;
              title?: string;
              description?: string;
              scopes?: Array<{
                scope?: string;
                id?: string;
                key?: string;
                label?: string;
                name?: string;
                description?: string;
                grantable?: boolean;
              }>;
            }>;
            scopes?: RunPermissionScopeItem[];
          }
        | undefined;

      if (raw && Array.isArray(raw.groups) && raw.groups.length > 0) {
        const groups: RunPermissionGroup[] = raw.groups.map((group, idx) => ({
          id: group.id ?? group.key ?? group.name ?? `group-${idx}`,
          label: group.label ?? group.title ?? group.name ?? `Group ${idx + 1}`,
          description: group.description,
          scopes: Array.isArray(group.scopes)
            ? group.scopes
                .filter((item) => Boolean(item.scope ?? item.id ?? item.key))
                .map((item) => {
                  const scopeId = (item.scope ?? item.id ?? item.key) as string;
                  return {
                    scope: scopeId,
                    label: item.label ?? item.name ?? scopeId,
                    description: item.description ?? "",
                    grantable: item.grantable !== false,
                  };
                })
            : [],
        }));
        return {
          groups,
          scopes: raw.scopes,
          source: "api",
        };
      }

      return DEFAULT_RUN_PERMISSIONS_CATALOG;
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response
        ?.status;
      if (status === 404) {
        return DEFAULT_RUN_PERMISSIONS_CATALOG;
      }
      throw err;
    }
  }

  // Admin: Remote Workers (svc-runworker & svc-scanner)
  async listRemoteWorkers(params?: {
    worker_type?: RemoteWorkerType;
    status?: RemoteWorkerStatus;
    cluster?: string;
  }): Promise<RemoteWorkerListResponse> {
    const response = await this.client.get("/admin/workers", { params });
    const raw = response.data || {};
    const rows: RemoteWorker[] = Array.isArray(raw)
      ? raw
      : Array.isArray(raw.workers)
        ? raw.workers
        : Array.isArray(raw.items)
          ? raw.items
          : Array.isArray(raw.data)
            ? raw.data
            : [];
    return {
      ...(Array.isArray(raw) ? {} : raw),
      workers: rows,
      items: rows,
      data: rows,
      total:
        !Array.isArray(raw) && typeof raw.total === "number"
          ? raw.total
          : rows.length,
    };
  }

  async getRemoteWorker(id: number | string): Promise<RemoteWorker> {
    const response = await this.client.get<RemoteWorker>(`/admin/workers/${id}`);
    return response.data;
  }

  async onboardRemoteWorker(
    data: OnboardRemoteWorkerInput,
  ): Promise<OnboardRemoteWorkerResponse> {
    const response = await this.client.post<OnboardRemoteWorkerResponse>(
      "/admin/workers",
      data,
    );
    return response.data;
  }

  async updateRemoteWorker(
    id: number | string,
    data: UpdateRemoteWorkerInput,
  ): Promise<RemoteWorker> {
    const response = await this.client.put<RemoteWorker>(
      `/admin/workers/${id}`,
      data,
    );
    return response.data;
  }

  async rotateRemoteWorkerToken(
    id: number | string,
  ): Promise<OnboardRemoteWorkerResponse> {
    const response = await this.client.post<OnboardRemoteWorkerResponse>(
      `/admin/workers/${id}/rotate-token`,
    );
    return response.data;
  }

  async offboardRemoteWorker(
    id: number | string,
  ): Promise<{ message?: string; status?: string }> {
    const response = await this.client.delete<{
      message?: string;
      status?: string;
    }>(`/admin/workers/${id}`);
    return response.data;
  }
}

export type RunRuntime = "nodejs" | "rust" | "python" | "golang" | "ruby";

export interface RunPermissionScopeItem {
  scope: string;
  label: string;
  description: string;
  grantable: boolean;
}

export interface RunPermissionGroup {
  id: string;
  label: string;
  description?: string;
  scopes: RunPermissionScopeItem[];
}

export interface RunPermissionsCatalogResponse {
  groups: RunPermissionGroup[];
  scopes?: RunPermissionScopeItem[];
  source?: "api" | "fallback";
}

export const DEFAULT_RUN_PERMISSIONS_CATALOG: RunPermissionsCatalogResponse = {
  source: "fallback",
  groups: [
    {
      id: "resources",
      label: "Resources",
      description:
        "Read and update Elder CMDB entities, metadata, and relationships.",
      scopes: [
        {
          scope: "entities:read",
          label: "Read Entities",
          description: "List and inspect entities, attributes, and metadata",
          grantable: true,
        },
        {
          scope: "entities:write",
          label: "Write Entities",
          description:
            "Create or update entity metadata, tags, and attributes",
          grantable: true,
        },
        {
          scope: "entities:delete",
          label: "Delete Entities",
          description: "Delete entities and managed resources",
          grantable: true,
        },
        {
          scope: "relationships:read",
          label: "Read Relationships",
          description: "Inspect entity relationships and dependency graphs",
          grantable: true,
        },
        {
          scope: "relationships:write",
          label: "Write Relationships",
          description: "Create or update relationships between entities",
          grantable: true,
        },
      ],
    },
    {
      id: "integrations",
      label: "Integrations",
      description:
        "Inspect connectors and trigger on-demand syncs (e.g. FleetDM).",
      scopes: [
        {
          scope: "integrations:read",
          label: "Read Integrations",
          description: "View configured integrations and connector sync status",
          grantable: true,
        },
        {
          scope: "integrations:sync",
          label: "Trigger Integration Sync",
          description:
            "Trigger on-demand syncs for connected integrations (e.g. FleetDM)",
          grantable: true,
        },
        {
          scope: "integrations:write",
          label: "Manage Integrations",
          description: "Create or modify integration configurations",
          grantable: true,
        },
      ],
    },
    {
      id: "workstreams_webhooks",
      label: "Workstreams & Webhooks",
      description: "Read or trigger workstreams and manage outbound webhooks.",
      scopes: [
        {
          scope: "workstreams:read",
          label: "Read Workstreams",
          description: "Inspect workstreams, pipelines, and routing rules",
          grantable: true,
        },
        {
          scope: "workstreams:write",
          label: "Manage Workstreams",
          description: "Update workstream definitions and trigger executions",
          grantable: true,
        },
        {
          scope: "webhooks:read",
          label: "Read Webhooks",
          description: "View registered outbound webhooks and delivery logs",
          grantable: true,
        },
        {
          scope: "webhooks:write",
          label: "Manage Webhooks",
          description: "Create, update, or dispatch outbound webhooks",
          grantable: true,
        },
      ],
    },
    {
      id: "work_management",
      label: "Work Management",
      description:
        "Access tickets, tasks, and programmatic CodeRun invocations.",
      scopes: [
        {
          scope: "tickets:read",
          label: "Read Tickets",
          description: "View tickets, tasks, and work items",
          grantable: true,
        },
        {
          scope: "tickets:write",
          label: "Manage Tickets",
          description: "Create, update, or comment on tickets and work items",
          grantable: true,
        },
        {
          scope: "runs:read",
          label: "Read CodeRuns",
          description: "Inspect CodeRun definitions and execution history",
          grantable: true,
        },
        {
          scope: "runs:invoke",
          label: "Invoke CodeRuns",
          description: "Trigger other CodeRuns programmatically",
          grantable: true,
        },
      ],
    },
  ],
};

export interface RunFunction {
  id: number;
  public_id?: string;
  name: string;
  description?: string | null;
  runtime: RunRuntime | string;
  code: string;
  storage_backend?: "database" | "s3";
  storage_key?: string | null;
  code_sha256?: string | null;
  timeout_seconds?: number;
  memory_mb?: number;
  schedule_cron?: string | null;
  execution_namespace?: string | null;
  worker_cluster?: string | null;
  env_vars?: Record<string, string> | null;
  granted_scopes?: string[];
  allowed_egress_domains?: string[];
  is_enabled?: boolean;
  village_id?: string | null;
  last_run_at?: string | null;
  execution_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface RunFunctionInput {
  name: string;
  description?: string;
  runtime: RunRuntime | string;
  code: string;
  timeout_seconds?: number;
  memory_mb?: number;
  schedule_cron?: string | null;
  execution_namespace?: string | null;
  worker_cluster?: string | null;
  env_vars?: Record<string, string>;
  granted_scopes?: string[];
  allowed_egress_domains?: string[];
  is_enabled?: boolean;
}

export interface RunFunctionListResponse {
  data: RunFunction[];
  items?: RunFunction[];
  total: number;
  page: number;
  per_page: number;
  pages?: number;
}

export interface RunExecution {
  id: number;
  execution_id: string;
  function_id: number;
  function_name?: string;
  runtime?: RunRuntime | string;
  status: string;
  trigger_type?: "manual" | "schedule" | "stream" | string;
  input_data?: Record<string, unknown> | null;
  output_data?: Record<string, unknown> | null;
  stdout?: string | null;
  stderr?: string | null;
  exit_code?: number | null;
  error_message?: string | null;
  duration_ms?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

export type RunExecutionDetail = RunExecution;

export interface RunExecutionListResponse {
  data: RunExecution[];
  items?: RunExecution[];
  total: number;
  page: number;
  per_page: number;
  pages?: number;
}

export interface LicenseLimits {
  max_global_admins: number | null;
  max_tenant_admins: number | null;
  max_teams: number | null;
  max_tenants: number | null;
  max_objects: number | null;
  max_nodes_per_type: number | null;
}

export interface LicenseFeatures {
  multi_tenancy: boolean;
  portal_users: boolean;
  saml_sso: boolean;
  scim_provisioning: boolean;
  audit_logging: boolean;
  compliance_reports: boolean;
  access_reviews: boolean;
  enterprise_integrations: boolean;
  google_oauth_sso: boolean;
}

export interface LicenseStatus {
  tier: "free" | "professional" | "enterprise" | string;
  status: string;
  domain_bypass: boolean;
  bypass_domain: string | null;
  limits: LicenseLimits;
  features: LicenseFeatures;
}

export interface MfaPolicy {
  enforced: boolean;
  provider: string;
  grace_period_days: number;
  enforced_at: string | null;
  mfa_enrolled: boolean;
  license_tier: string;
  can_manage: boolean;
}

export interface MfaSetupResponse {
  secret: string;
  provisioning_uri: string;
  backup_codes: string[];
}

export interface AIResearcherConfig {
  default_model: string;
  available_models: string[];
  mcp_tool_count: number;
  enabled_modules: string[];
  max_steps: number;
  ollama_reachable: boolean;
  ollama_error: string | null;
}

export interface AIResearchHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AIResearchRequest {
  query: string;
  model?: string;
  history?: AIResearchHistoryMessage[];
  max_steps?: number;
}

export interface AIResearchStep {
  step: number;
  tool: string;
  endpoint?: string | null;
  arguments: Record<string, unknown>;
  status: "ok" | "error";
  result_preview?: string;
  error?: string;
}

export interface AIResearchCitation {
  resource_type: string;
  id: number | string;
  name: string;
  url: string;
}

export interface AIResearchResponse {
  answer: string;
  model: string;
  steps: AIResearchStep[];
  citations: AIResearchCitation[];
  tool_count: number;
  enabled_modules: string[];
}

export type CsvImportJobStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed";

export type CsvImportJobError =
  | string
  | {
      row?: number;
      field?: string;
      message?: string;
      error?: string;
    };

export interface CsvImportJob {
  id?: number;
  job_id: string;
  resource: string;
  status: CsvImportJobStatus;
  filename?: string | null;
  storage_backend?: string | null;
  storage_key?: string | null;
  file_sha256?: string | null;
  total_rows?: number;
  imported_count?: number;
  skipped_count?: number;
  error_count?: number;
  errors?: CsvImportJobError[];
  error_message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
}

export interface CsvImportJobsResponse {
  items: CsvImportJob[];
  jobs?: CsvImportJob[];
  total?: number;
}

export interface CsvImportResponse {
  resource?: string;
  imported?: number;
  skipped?: number;
  job_id?: string;
  status?: CsvImportJobStatus;
  storage_backend?: string;
  storage_key?: string;
  file_sha256?: string;
  total_rows?: number;
  filename?: string | null;
  message?: string;
  errors?: CsvImportJobError[];
}

export type RemoteWorkerType = "runworker" | "scanner";

export type RemoteWorkerStatus = "online" | "stale" | "offline" | "revoked";

export interface RemoteWorkerConfig {
  poll_interval_seconds?: number;
  progress_interval_seconds?: number;
  log_flush_interval_seconds?: number;
  max_concurrent?: number;
  runner_backend?: string;
  [key: string]: unknown;
}

export interface RemoteWorker {
  id: number | string;
  public_id?: string;
  name: string;
  worker_type: RemoteWorkerType;
  cluster: string;
  status: RemoteWorkerStatus;
  hostname?: string | null;
  version?: string | null;
  token_prefix?: string | null;
  is_enabled: boolean;
  config?: RemoteWorkerConfig | null;
  last_heartbeat_at?: string | null;
  capabilities?: string[] | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RemoteWorkerListResponse {
  workers?: RemoteWorker[];
  data?: RemoteWorker[];
  items?: RemoteWorker[];
  total: number;
  counts?: {
    online?: number;
    stale?: number;
    offline?: number;
    revoked?: number;
  };
}

export interface OnboardRemoteWorkerInput {
  name: string;
  worker_type: RemoteWorkerType;
  cluster?: string;
  config?: RemoteWorkerConfig;
  is_enabled?: boolean;
}

export interface UpdateRemoteWorkerInput {
  name?: string;
  cluster?: string;
  config?: RemoteWorkerConfig;
  is_enabled?: boolean;
}

export interface OnboardRemoteWorkerResponse {
  worker: RemoteWorker;
  token: string;
  token_prefix?: string;
  env_var?: string;
}

export const api = new ApiClient();
export default api;
