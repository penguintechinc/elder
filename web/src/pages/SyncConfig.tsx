import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Settings,
  Trash2,
  Play,
  PlugZap,
  Webhook,
  Copy,
  Check,
  X,
  GitBranch,
  History,
  Link2,
  ShieldCheck,
  Upload,
  KeyRound,
} from "lucide-react";
import api, {
  type SyncConfigItem,
  type SyncPlatform,
  type SyncResourceType,
  type SyncDirection,
  type GitHubAuthMode,
  type SyncTestResult,
  type SyncTriggerResult,
  type CreateSyncConfigPayload,
  type UpdateSyncConfigPayload,
} from "../lib/api";
import Button from "../components/Button";
import Card, { CardHeader, CardContent } from "../components/Card";

interface OrganizationOption {
  id: number;
  name: string;
}

const ALL_RESOURCE_TYPES: Array<{
  value: SyncResourceType;
  label: string;
  githubHint: string;
  jiraHint: string;
}> = [
  {
    value: "issue",
    label: "Issues",
    githubHint: "REST Issues (assignees, labels, milestone links)",
    jiraHint: "REST v3 Issues (/search/jql + ADF descriptions)",
  },
  {
    value: "milestone",
    label: "Milestones",
    githubHint: "Repository Milestones",
    jiraHint: "Project Fix Versions",
  },
  {
    value: "project",
    label: "Projects",
    githubHint: "Projects v2 (GraphQL API v4)",
    jiraHint: "Jira Projects",
  },
  {
    value: "label",
    label: "Labels",
    githubHint: "Repository Labels (#-stripped hex, 100-char desc)",
    jiraHint: "Jira Labels or Project Components",
  },
];

const PLATFORM_LABELS: Record<SyncPlatform, string> = {
  github: "GitHub",
  jira: "Jira Cloud",
  gitlab: "GitLab",
  trello: "Trello",
  openproject: "OpenProject",
};

interface FormState {
  name: string;
  platform: SyncPlatform;
  enabled: boolean;
  sync_interval: number;
  batch_fallback_enabled: boolean;
  batch_size: number;
  two_way_create: boolean;
  webhook_enabled: boolean;
  webhook_secret: string;
  elder_organization_id: string;
  resource_types: SyncResourceType[];
  direction: SyncDirection;
  // GitHub
  github_auth_mode: GitHubAuthMode;
  app_id: string;
  installation_id: string;
  private_key: string;
  owner: string;
  repo_name: string;
  base_url: string;
  graphql_url: string;
  // Jira Cloud
  jira_url: string;
  email: string;
  project_key: string;
  label_mode: "labels" | "components";
  project_lead_account_id: string;
  sync_all_projects: boolean;
  // Shared token / fallback
  api_token: string;
  project_id: string;
  api_key: string;
}

const INITIAL_FORM: FormState = {
  name: "",
  platform: "github",
  enabled: true,
  sync_interval: 300,
  batch_fallback_enabled: true,
  batch_size: 100,
  two_way_create: false,
  webhook_enabled: true,
  webhook_secret: "",
  elder_organization_id: "",
  resource_types: ["project", "milestone", "label", "issue"],
  direction: "bidirectional",
  github_auth_mode: "github_app",
  app_id: "",
  installation_id: "",
  private_key: "",
  owner: "",
  repo_name: "",
  base_url: "",
  graphql_url: "",
  jira_url: "",
  email: "",
  project_key: "",
  label_mode: "labels",
  project_lead_account_id: "",
  sync_all_projects: false,
  api_token: "",
  project_id: "",
  api_key: "",
};

function resolveGitHubAuthMode(json: Record<string, unknown>): GitHubAuthMode {
  const raw = String(json.auth_mode || "")
    .trim()
    .toLowerCase();
  if (raw === "pat") return "pat";
  if (raw === "github_app") return "github_app";
  if (json.app_id || json.installation_id || json.private_key) {
    return "github_app";
  }
  return "pat";
}

function configToFormState(cfg: SyncConfigItem): FormState {
  const json = (cfg.config_json || {}) as Record<string, unknown>;
  const rawResources = Array.isArray(json.resource_types)
    ? (json.resource_types.filter((r): r is SyncResourceType =>
        ["issue", "milestone", "project", "label"].includes(String(r)),
      ) as SyncResourceType[])
    : (["project", "milestone", "label", "issue"] as SyncResourceType[]);
  const rawDir = String(
    json.direction || (cfg.two_way_create ? "bidirectional" : "pull"),
  ).toLowerCase();
  const direction: SyncDirection =
    rawDir === "push" || rawDir === "bidirectional" ? rawDir : "pull";

  return {
    name: cfg.name,
    platform: cfg.platform,
    enabled: cfg.enabled,
    sync_interval: cfg.sync_interval,
    batch_fallback_enabled: cfg.batch_fallback_enabled,
    batch_size: cfg.batch_size,
    two_way_create: cfg.two_way_create,
    webhook_enabled: cfg.webhook_enabled,
    webhook_secret: "",
    elder_organization_id:
      json.elder_organization_id != null
        ? String(json.elder_organization_id)
        : "",
    resource_types:
      rawResources.length > 0
        ? rawResources
        : ["project", "milestone", "label", "issue"],
    direction,
    github_auth_mode:
      cfg.platform === "github" ? resolveGitHubAuthMode(json) : "github_app",
    app_id: json.app_id != null ? String(json.app_id) : "",
    installation_id:
      json.installation_id != null ? String(json.installation_id) : "",
    private_key: "",
    owner: String(json.owner || json.org_name || ""),
    repo_name: String(json.repo_name || ""),
    base_url: String(json.base_url || ""),
    graphql_url: String(json.graphql_url || ""),
    jira_url: String(json.jira_url || ""),
    email: String(json.email || ""),
    project_key: String(json.project_key || ""),
    label_mode:
      String(json.label_mode || "labels").toLowerCase() === "components"
        ? "components"
        : "labels",
    project_lead_account_id: String(json.project_lead_account_id || ""),
    sync_all_projects: Boolean(json.sync_all_projects),
    api_token: "",
    project_id: String(json.project_id || ""),
    api_key: "",
  };
}

function getApiErrorMessage(err: unknown, fallback: string): string {
  const maybeErr = err as {
    response?: { data?: { error?: string; message?: string } };
    message?: string;
  };
  return (
    maybeErr?.response?.data?.error ||
    maybeErr?.response?.data?.message ||
    maybeErr?.message ||
    fallback
  );
}

export interface SyncConfigProps {
  embedded?: boolean;
  openCreateSignal?: number;
}

export default function SyncConfig({
  embedded = false,
  openCreateSignal = 0,
}: SyncConfigProps) {
  const queryClient = useQueryClient();

  const [showModal, setShowModal] = useState(false);
  const [editingConfig, setEditingConfig] = useState<SyncConfigItem | null>(
    null,
  );
  const [formState, setFormState] = useState<FormState>(INITIAL_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const pemFileInputRef = useRef<HTMLInputElement | null>(null);

  const [selectedConfig, setSelectedConfig] = useState<SyncConfigItem | null>(
    null,
  );
  const [detailsTab, setDetailsTab] = useState<
    "webhooks" | "mappings" | "history" | "conflicts"
  >("webhooks");
  const [copiedWebhookId, setCopiedWebhookId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const [actionBanner, setActionBanner] = useState<{
    type: "success" | "error";
    title: string;
    detail?: string;
  } | null>(null);

  const [testingId, setTestingId] = useState<number | null>(null);
  const [triggeringId, setTriggeringId] = useState<number | null>(null);

  const {
    data: configsData,
    isLoading: configsLoading,
    error: configsError,
  } = useQuery({
    queryKey: ["sync-configs"],
    queryFn: () => api.getSyncConfigs(),
  });

  const { data: statusData } = useQuery({
    queryKey: ["sync-status"],
    queryFn: () => api.getSyncStatus(),
  });

  const { data: orgsData } = useQuery({
    queryKey: ["organizations-for-sync"],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  });

  const selectedId = selectedConfig?.id ?? null;

  const { data: mappingsData, isLoading: mappingsLoading } = useQuery({
    queryKey: ["sync-mappings", selectedId],
    queryFn: () => api.getSyncMappings({ config_id: selectedId ?? undefined }),
    enabled: selectedId !== null && detailsTab === "mappings",
  });

  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ["sync-history", selectedId],
    queryFn: () =>
      api.getSyncHistory({ config_id: selectedId ?? undefined, per_page: 50 }),
    enabled: selectedId !== null && detailsTab === "history",
  });

  const { data: conflictsData, isLoading: conflictsLoading } = useQuery({
    queryKey: ["sync-conflicts", selectedId],
    queryFn: () =>
      api.getSyncConflicts({
        resolved: false,
        config_id: selectedId ?? undefined,
      }),
    enabled: selectedId !== null && detailsTab === "conflicts",
  });

  const organizations: OrganizationOption[] = useMemo(() => {
    const raw = orgsData as
      | { organizations?: OrganizationOption[]; items?: OrganizationOption[] }
      | OrganizationOption[]
      | undefined;
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw?.organizations)) return raw.organizations;
    if (Array.isArray(raw?.items)) return raw.items;
    return [];
  }, [orgsData]);

  const orgMap = useMemo(() => {
    const map = new Map<number, string>();
    for (const org of organizations) {
      map.set(org.id, org.name);
    }
    return map;
  }, [organizations]);

  const configs = configsData?.configs ?? [];

  const createMutation = useMutation({
    mutationFn: (payload: CreateSyncConfigPayload) =>
      api.createSyncConfig(payload),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["sync-configs"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
      setShowModal(false);
      setFormError(null);
      setActionBanner({
        type: "success",
        title: `Created ${PLATFORM_LABELS[res.config.platform]} sync "${res.config.name}"`,
      });
    },
    onError: (err) => {
      setFormError(
        getApiErrorMessage(err, "Failed to create sync configuration"),
      );
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: UpdateSyncConfigPayload;
    }) => api.updateSyncConfig(id, payload),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["sync-configs"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
      setShowModal(false);
      setEditingConfig(null);
      setFormError(null);
      setActionBanner({
        type: "success",
        title: `Updated sync configuration "${res.config.name}"`,
      });
    },
    onError: (err) => {
      setFormError(
        getApiErrorMessage(err, "Failed to update sync configuration"),
      );
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSyncConfig(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync-configs"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
      setConfirmDeleteId(null);
      setActionBanner({
        type: "success",
        title: "Sync configuration deleted",
      });
    },
    onError: (err) => {
      setActionBanner({
        type: "error",
        title: "Failed to delete sync configuration",
        detail: getApiErrorMessage(err, "Unknown error"),
      });
    },
  });

  const resolveConflictMutation = useMutation({
    mutationFn: ({ id, strategy }: { id: number; strategy: string }) =>
      api.resolveSyncConflict(id, strategy),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["sync-conflicts", selectedId],
      });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
      setActionBanner({
        type: "success",
        title: "Conflict resolved",
      });
    },
    onError: (err) => {
      setActionBanner({
        type: "error",
        title: "Failed to resolve conflict",
        detail: getApiErrorMessage(err, "Unknown error"),
      });
    },
  });

  const defaultOrgId = organizations[0] ? String(organizations[0].id) : "";

  const handleOpenCreate = useCallback(
    (platform: SyncPlatform = "github") => {
      setEditingConfig(null);
      setFormState({
        ...INITIAL_FORM,
        platform,
        elder_organization_id: defaultOrgId,
      });
      setFormError(null);
      setShowModal(true);
    },
    [defaultOrgId],
  );

  const lastCreateSignalRef = useRef(0);
  useEffect(() => {
    if (
      openCreateSignal > 0 &&
      openCreateSignal !== lastCreateSignalRef.current
    ) {
      lastCreateSignalRef.current = openCreateSignal;
      handleOpenCreate("github");
    }
  }, [openCreateSignal, handleOpenCreate]);

  const handleOpenEdit = (cfg: SyncConfigItem) => {
    setEditingConfig(cfg);
    setFormState(configToFormState(cfg));
    setFormError(null);
    setShowModal(true);
  };

  const handleToggleResourceType = (rt: SyncResourceType) => {
    setFormState((prev) => {
      const exists = prev.resource_types.includes(rt);
      const next = exists
        ? prev.resource_types.filter((item) => item !== rt)
        : [...prev.resource_types, rt];
      return { ...prev, resource_types: next };
    });
  };

  const handlePemFileUpload = async (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const content =
        typeof file.text === "function"
          ? await file.text()
          : await new Promise<string>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result || ""));
              reader.onerror = () =>
                reject(reader.error || new Error("Failed to read PEM file"));
              reader.readAsText(file);
            });
      setFormState((p) => ({ ...p, private_key: content.trim() }));
      setFormError(null);
    } catch {
      setFormError("Unable to read the selected .pem private key file.");
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!formState.name.trim()) {
      setFormError("Configuration name is required.");
      return;
    }
    if (formState.resource_types.length === 0) {
      setFormError("Select at least one resource type to sync.");
      return;
    }

    const editingJson = (editingConfig?.config_json || {}) as Record<
      string,
      unknown
    >;
    const hasSavedPrivateKey = Boolean(
      editingConfig && editingJson.private_key,
    );
    const hasSavedApiToken = Boolean(editingConfig && editingJson.api_token);

    const configJson: Record<string, unknown> = {
      resource_types: formState.resource_types,
      direction: formState.direction,
    };

    if (formState.elder_organization_id.trim()) {
      configJson.elder_organization_id = Number(
        formState.elder_organization_id,
      );
    }

    if (formState.platform === "github") {
      if (!formState.owner.trim() || !formState.repo_name.trim()) {
        setFormError(
          "GitHub Owner/Organization and Repository Name are required.",
        );
        return;
      }
      configJson.owner = formState.owner.trim();
      configJson.repo_name = formState.repo_name.trim();
      configJson.auth_mode = formState.github_auth_mode;
      if (formState.base_url.trim()) {
        configJson.base_url = formState.base_url.trim();
      }
      if (formState.graphql_url.trim()) {
        configJson.graphql_url = formState.graphql_url.trim();
      }

      if (formState.github_auth_mode === "github_app") {
        if (!formState.app_id.trim()) {
          setFormError("GitHub App ID is required.");
          return;
        }
        const instTrimmed = formState.installation_id.trim();
        if (!/^\d+$/.test(instTrimmed) || Number(instTrimmed) <= 0) {
          setFormError(
            "GitHub App Installation ID must be a positive integer.",
          );
          return;
        }
        if (!hasSavedPrivateKey && !formState.private_key.trim()) {
          setFormError("GitHub App Private Key (PEM) is required.");
          return;
        }
        configJson.app_id = formState.app_id.trim();
        configJson.installation_id = instTrimmed;
        if (formState.private_key.trim()) {
          configJson.private_key = formState.private_key.trim();
        }
      } else {
        if (!hasSavedApiToken && !formState.api_token.trim()) {
          setFormError(
            "GitHub Personal Access Token is required when using PAT mode.",
          );
          return;
        }
        if (formState.api_token.trim()) {
          configJson.api_token = formState.api_token.trim();
        }
      }
    } else if (formState.platform === "jira") {
      if (
        !formState.jira_url.trim() ||
        !formState.email.trim() ||
        !formState.project_key.trim()
      ) {
        setFormError(
          "Jira Cloud URL, Atlassian Email, and Project Key are required.",
        );
        return;
      }
      if (!editingConfig && !formState.api_token.trim()) {
        setFormError(
          "Jira API Token is required when creating an integration.",
        );
        return;
      }
      configJson.jira_url = formState.jira_url.trim();
      configJson.email = formState.email.trim();
      configJson.project_key = formState.project_key.trim().toUpperCase();
      configJson.label_mode = formState.label_mode;
      configJson.sync_all_projects = formState.sync_all_projects;
      if (formState.project_lead_account_id.trim()) {
        configJson.project_lead_account_id =
          formState.project_lead_account_id.trim();
      }
      if (formState.api_token.trim()) {
        configJson.api_token = formState.api_token.trim();
      }
    } else {
      if (formState.base_url.trim()) {
        configJson.base_url = formState.base_url.trim();
      }
      if (formState.project_id.trim()) {
        configJson.project_id = formState.project_id.trim();
      }
      if (formState.api_token.trim()) {
        configJson.api_token = formState.api_token.trim();
      }
      if (formState.api_key.trim()) {
        configJson.api_key = formState.api_key.trim();
      }
    }

    if (editingConfig) {
      const payload: UpdateSyncConfigPayload = {
        name: formState.name.trim(),
        enabled: formState.enabled,
        sync_interval: Number(formState.sync_interval) || 300,
        batch_fallback_enabled: formState.batch_fallback_enabled,
        batch_size: Number(formState.batch_size) || 100,
        two_way_create:
          formState.two_way_create || formState.direction === "bidirectional",
        webhook_enabled: formState.webhook_enabled,
        config_json: configJson,
      };
      if (formState.webhook_secret.trim()) {
        payload.webhook_secret = formState.webhook_secret.trim();
      }
      updateMutation.mutate({ id: editingConfig.id, payload });
    } else {
      const payload: CreateSyncConfigPayload = {
        name: formState.name.trim(),
        platform: formState.platform,
        enabled: formState.enabled,
        sync_interval: Number(formState.sync_interval) || 300,
        batch_fallback_enabled: formState.batch_fallback_enabled,
        batch_size: Number(formState.batch_size) || 100,
        two_way_create:
          formState.two_way_create || formState.direction === "bidirectional",
        webhook_enabled: formState.webhook_enabled,
        webhook_secret: formState.webhook_secret.trim() || undefined,
        config_json: configJson,
      };
      createMutation.mutate(payload);
    }
  };

  const handleTestConnection = async (cfg: SyncConfigItem) => {
    setTestingId(cfg.id);
    try {
      const res: SyncTestResult = await api.testSyncConfig(cfg.id);
      if (res.connected) {
        const targetInfo = res.repository?.full_name
          ? `Connected to GitHub repository ${res.repository.full_name} (default branch: ${res.repository.default_branch ?? "unknown"})${res.auth_mode === "github_app" ? " via GitHub App" : ""}`
          : res.project?.key
            ? `Connected to Jira project ${res.project.key} (${res.project.name ?? ""})`
            : `Connected to ${PLATFORM_LABELS[cfg.platform]}`;
        setActionBanner({
          type: "success",
          title: `Connection test passed for "${cfg.name}"`,
          detail: targetInfo,
        });
      } else {
        setActionBanner({
          type: "error",
          title: `Connection test failed for "${cfg.name}"`,
          detail: res.error || "Unable to reach external platform",
        });
      }
    } catch (err) {
      setActionBanner({
        type: "error",
        title: `Connection test failed for "${cfg.name}"`,
        detail: getApiErrorMessage(err, "Request failed"),
      });
    } finally {
      setTestingId(null);
    }
  };

  const handleTriggerSync = async (cfg: SyncConfigItem) => {
    setTriggeringId(cfg.id);
    try {
      const json = (cfg.config_json || {}) as Record<string, unknown>;
      const resourceTypes = Array.isArray(json.resource_types)
        ? (json.resource_types as SyncResourceType[])
        : undefined;
      const direction =
        (json.direction as SyncDirection | undefined) || undefined;

      const res: SyncTriggerResult = await api.triggerSyncConfig(cfg.id, {
        resource_types: resourceTypes,
        direction,
      });

      queryClient.invalidateQueries({ queryKey: ["sync-configs"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
      queryClient.invalidateQueries({ queryKey: ["sync-history", cfg.id] });
      queryClient.invalidateQueries({ queryKey: ["sync-mappings", cfg.id] });

      if (res.success) {
        setActionBanner({
          type: "success",
          title: `Sync completed for "${cfg.name}"`,
          detail: `Direction: ${res.direction} · Synced ${res.items_synced} items (${res.resource_types.join(", ")})`,
        });
      } else {
        setActionBanner({
          type: "error",
          title: `Sync completed with errors for "${cfg.name}"`,
          detail:
            res.errors[0] ||
            `Synced ${res.items_synced}, Failed ${res.items_failed}`,
        });
      }
    } catch (err) {
      setActionBanner({
        type: "error",
        title: `Sync trigger failed for "${cfg.name}"`,
        detail: getApiErrorMessage(err, "Request failed"),
      });
    } finally {
      setTriggeringId(null);
    }
  };

  const handleToggleEnabled = (cfg: SyncConfigItem) => {
    updateMutation.mutate({
      id: cfg.id,
      payload: { enabled: !cfg.enabled },
    });
  };

  const getWebhookUrl = (cfg: SyncConfigItem) => {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}/api/v1/sync/webhooks/${cfg.platform}/${cfg.id}`;
  };

  const handleCopyWebhookUrl = async (cfg: SyncConfigItem) => {
    const url = getWebhookUrl(cfg);
    try {
      await navigator.clipboard.writeText(url);
      setCopiedWebhookId(cfg.id);
      setTimeout(() => setCopiedWebhookId(null), 2000);
    } catch {
      setActionBanner({
        type: "error",
        title: "Could not copy webhook URL to clipboard",
        detail: url,
      });
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      {embedded ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">
              Work Management Sync
            </h2>
            <p className="mt-0.5 text-sm text-slate-400">
              Two-way sync across Issues, Milestones, Projects, and Labels
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to="/issues"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Issues
            </Link>
            <Link
              to="/milestones"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Milestones
            </Link>
            <Link
              to="/projects"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Projects
            </Link>
            <Link
              to="/labels"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Labels
            </Link>
            <Button
              onClick={() => handleOpenCreate("github")}
              data-testid="add-sync-config-btn"
            >
              <Plus className="w-4 h-4 mr-2" />
              Add Integration
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white">
              GitHub & Jira Integrations
            </h1>
            <p className="mt-1 text-slate-400">
              Configure two-way sync across Issues, Milestones, Projects, and
              Labels
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link
              to="/issues"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Issues
            </Link>
            <Link
              to="/milestones"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Milestones
            </Link>
            <Link
              to="/projects"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Projects
            </Link>
            <Link
              to="/labels"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
            >
              Labels
            </Link>
            <Button
              onClick={() => handleOpenCreate("github")}
              data-testid="add-sync-config-btn"
            >
              <Plus className="w-4 h-4 mr-2" />
              Add Integration
            </Button>
          </div>
        </div>
      )}

      {/* Action Feedback Banner */}
      {actionBanner && (
        <div
          role="status"
          className={`flex items-start justify-between gap-3 rounded-lg border p-4 ${
            actionBanner.type === "success"
              ? "border-emerald-700/60 bg-emerald-950/40 text-emerald-200"
              : "border-red-700/60 bg-red-950/40 text-red-200"
          }`}
        >
          <div className="flex items-start gap-3">
            {actionBanner.type === "success" ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400 mt-0.5 shrink-0" />
            ) : (
              <XCircle className="w-5 h-5 text-red-400 mt-0.5 shrink-0" />
            )}
            <div>
              <p className="text-sm font-semibold">{actionBanner.title}</p>
              {actionBanner.detail && (
                <p className="text-xs opacity-90 mt-1">{actionBanner.detail}</p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setActionBanner(null)}
            aria-label="Dismiss notification"
            className="text-slate-400 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Status Summary Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
              Integrations
            </p>
            <p className="mt-2 text-2xl font-bold text-white">
              {statusData?.configs.enabled ?? 0}{" "}
              <span className="text-sm font-normal text-slate-400">
                / {statusData?.configs.total ?? configs.length} enabled
              </span>
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
              Synced Mappings
            </p>
            <p className="mt-2 text-2xl font-bold text-white">
              {statusData?.mappings.total ?? 0}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
              Sync Runs (24h)
            </p>
            <p className="mt-2 text-2xl font-bold text-white">
              {statusData?.recent_activity.syncs_24h ?? 0}{" "}
              <span
                className={`text-sm font-normal ${
                  (statusData?.recent_activity.failures_24h ?? 0) > 0
                    ? "text-red-400"
                    : "text-emerald-400"
                }`}
              >
                ({statusData?.recent_activity.failures_24h ?? 0} failed)
              </span>
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
              Unresolved Conflicts
            </p>
            <p
              className={`mt-2 text-2xl font-bold ${
                (statusData?.conflicts.unresolved ?? 0) > 0
                  ? "text-amber-400"
                  : "text-white"
              }`}
            >
              {statusData?.conflicts.unresolved ?? 0}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Configurations List */}
      {configsLoading ? (
        <Card>
          <CardContent className="p-8 text-center text-slate-400">
            Loading sync integrations...
          </CardContent>
        </Card>
      ) : configsError ? (
        <Card>
          <CardContent className="p-8 text-center text-red-400">
            Failed to load sync configurations:{" "}
            {getApiErrorMessage(configsError, "Request error")}
          </CardContent>
        </Card>
      ) : configs.length === 0 ? (
        <Card>
          <CardContent className="p-10 text-center space-y-4">
            <GitBranch className="w-10 h-10 text-yellow-500 mx-auto" />
            <div>
              <h2 className="text-lg font-semibold text-white">
                No external integrations configured yet
              </h2>
              <p className="text-sm text-slate-400 mt-1 max-w-xl mx-auto">
                Connect a GitHub repository (REST + Projects v2 GraphQL) or Jira
                Cloud project (REST v3 JQL + ADF) to synchronize issues,
                milestones, projects, and labels.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-3">
              <Button onClick={() => handleOpenCreate("github")}>
                <Plus className="w-4 h-4 mr-2" />
                Connect GitHub
              </Button>
              <Button
                variant="secondary"
                onClick={() => handleOpenCreate("jira")}
              >
                <Plus className="w-4 h-4 mr-2" />
                Connect Jira Cloud
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {configs.map((cfg) => {
            const json = (cfg.config_json || {}) as Record<string, unknown>;
            const orgId =
              json.elder_organization_id != null
                ? Number(json.elder_organization_id)
                : null;
            const orgName = orgId != null ? orgMap.get(orgId) : undefined;
            const resourceTypes = Array.isArray(json.resource_types)
              ? (json.resource_types as string[])
              : ["project", "milestone", "label", "issue"];
            const direction = String(
              json.direction || (cfg.two_way_create ? "bidirectional" : "pull"),
            );

            return (
              <Card key={cfg.id}>
                <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold text-white">
                        {cfg.name}
                      </h2>
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-slate-800 text-yellow-400 border border-slate-700">
                        {PLATFORM_LABELS[cfg.platform] ?? cfg.platform}
                      </span>
                      {cfg.platform === "github" &&
                        (resolveGitHubAuthMode(json) === "github_app" ? (
                          <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-emerald-950/60 text-emerald-300 border border-emerald-700/60">
                            GitHub App
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-amber-950/60 text-amber-300 border border-amber-700/60">
                            PAT (Legacy)
                          </span>
                        ))}
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                          cfg.enabled
                            ? "bg-emerald-950/60 text-emerald-300 border-emerald-700/60"
                            : "bg-slate-800 text-slate-400 border-slate-700"
                        }`}
                      >
                        {cfg.enabled ? "Active" : "Paused"}
                      </span>
                      <span className="px-2 py-0.5 text-xs rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                        {direction}
                      </span>
                    </div>

                    <div className="text-sm text-slate-400 flex flex-wrap items-center gap-x-4 gap-y-1">
                      {cfg.platform === "github" && (
                        <span>
                          Repo:{" "}
                          <code className="text-slate-200">
                            {String(json.owner || json.org_name || "?")}/
                            {String(json.repo_name || "?")}
                          </code>
                        </span>
                      )}
                      {cfg.platform === "jira" && (
                        <span>
                          Site:{" "}
                          <code className="text-slate-200">
                            {String(json.jira_url || "?")}
                          </code>{" "}
                          · Project:{" "}
                          <code className="text-slate-200">
                            {String(json.project_key || "?")}
                          </code>{" "}
                          · Labels:{" "}
                          <code className="text-slate-200">
                            {String(json.label_mode || "labels")}
                          </code>
                        </span>
                      )}
                      {orgId != null && (
                        <span>
                          Elder Org:{" "}
                          <span className="text-slate-200">
                            {orgName ?? `#${orgId}`}
                          </span>
                        </span>
                      )}
                      <span>
                        Interval: {cfg.sync_interval}s
                        {cfg.last_sync_at
                          ? ` · Last sync ${new Date(cfg.last_sync_at).toLocaleString()}`
                          : " · Never synced"}
                      </span>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => handleTestConnection(cfg)}
                      isLoading={testingId === cfg.id}
                    >
                      <PlugZap className="w-3.5 h-3.5 mr-1.5" />
                      Test Connection
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => handleTriggerSync(cfg)}
                      isLoading={triggeringId === cfg.id}
                    >
                      <Play className="w-3.5 h-3.5 mr-1.5" />
                      Sync Now
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setSelectedConfig(cfg);
                        setDetailsTab("webhooks");
                      }}
                    >
                      <Webhook className="w-3.5 h-3.5 mr-1.5" />
                      Details
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleOpenEdit(cfg)}
                      aria-label={`Edit ${cfg.name}`}
                    >
                      <Settings className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleToggleEnabled(cfg)}
                    >
                      {cfg.enabled ? "Pause" : "Enable"}
                    </Button>
                    {confirmDeleteId === cfg.id ? (
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => deleteMutation.mutate(cfg.id)}
                          isLoading={deleteMutation.isPending}
                        >
                          Confirm
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setConfirmDeleteId(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setConfirmDeleteId(cfg.id)}
                        aria-label={`Delete ${cfg.name}`}
                      >
                        <Trash2 className="w-4 h-4 text-red-400" />
                      </Button>
                    )}
                  </div>
                </CardHeader>

                <CardContent className="pt-0 pb-4">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span>Resources:</span>
                      {resourceTypes.map((rt) => (
                        <span
                          key={rt}
                          className="px-2 py-0.5 rounded bg-slate-800/80 text-slate-300 border border-slate-700/70"
                        >
                          {rt}
                        </span>
                      ))}
                    </div>
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="w-3.5 h-3.5 text-slate-400" />
                      <span>
                        Webhook:{" "}
                        {cfg.webhook_enabled
                          ? cfg.has_webhook_secret
                            ? "Enabled (HMAC secret configured)"
                            : "Enabled (add signing secret to accept inbound hooks)"
                          : "Disabled"}
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Create / Edit Integration Modal */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sync-modal-title"
        >
          <div className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <h2
                id="sync-modal-title"
                className="text-xl font-semibold text-white"
              >
                {editingConfig
                  ? `Edit ${PLATFORM_LABELS[editingConfig.platform]} Integration`
                  : "Add External Integration"}
              </h2>
              <button
                type="button"
                onClick={() => setShowModal(false)}
                aria-label="Close modal"
                className="text-slate-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form
              onSubmit={handleSubmit}
              className="p-6 space-y-5 max-h-[80vh] overflow-y-auto"
            >
              {formError && (
                <div
                  role="alert"
                  className="rounded-lg border border-red-700/70 bg-red-950/50 p-3 text-sm text-red-200 flex items-center gap-2"
                >
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{formError}</span>
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="sync-name"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Integration Name *
                  </label>
                  <input
                    id="sync-name"
                    type="text"
                    required
                    value={formState.name}
                    onChange={(e) =>
                      setFormState((p) => ({ ...p, name: e.target.value }))
                    }
                    placeholder="e.g., Core Platform GitHub"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="sync-platform"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Platform *
                  </label>
                  <select
                    id="sync-platform"
                    value={formState.platform}
                    disabled={Boolean(editingConfig)}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        platform: e.target.value as SyncPlatform,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none disabled:opacity-60"
                  >
                    <option value="github">GitHub (REST + Projects v2)</option>
                    <option value="jira">Jira Cloud (REST v3 + ADF)</option>
                    <option value="gitlab">GitLab</option>
                    <option value="trello">Trello</option>
                    <option value="openproject">OpenProject</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="sync-org"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Elder Organization
                  </label>
                  <select
                    id="sync-org"
                    value={formState.elder_organization_id}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        elder_organization_id: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  >
                    <option value="">Default Organization</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={String(org.id)}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="sync-direction"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Sync Direction
                  </label>
                  <select
                    id="sync-direction"
                    value={formState.direction}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        direction: e.target.value as SyncDirection,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  >
                    <option value="bidirectional">
                      Bidirectional (Pull & Push)
                    </option>
                    <option value="pull">Pull Only (External → Elder)</option>
                    <option value="push">Push Only (Elder → External)</option>
                  </select>
                </div>
              </div>

              {/* Resource Types */}
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-2.5">
                <p className="text-sm font-medium text-yellow-500">
                  Synchronized Resources
                </p>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {ALL_RESOURCE_TYPES.map((rt) => {
                    const checked = formState.resource_types.includes(rt.value);
                    return (
                      <div
                        key={rt.value}
                        className="flex items-start gap-2.5 rounded-md border border-slate-800 bg-slate-900/70 p-2.5 hover:border-slate-700"
                      >
                        <input
                          id={`sync-rt-${rt.value}`}
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleToggleResourceType(rt.value)}
                          className="mt-0.5 rounded border-slate-600 bg-slate-950 text-yellow-500 focus:ring-yellow-500 cursor-pointer"
                        />
                        <div>
                          <label
                            htmlFor={`sync-rt-${rt.value}`}
                            className="block text-sm font-medium text-white cursor-pointer"
                          >
                            {rt.label}
                          </label>
                          <span className="block text-xs text-slate-400">
                            {formState.platform === "jira"
                              ? rt.jiraHint
                              : rt.githubHint}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* GitHub Credentials & Target */}
              {formState.platform === "github" &&
                (() => {
                  const editingJson = (editingConfig?.config_json ||
                    {}) as Record<string, unknown>;
                  const hasSavedPrivateKey = Boolean(
                    editingConfig && editingJson.private_key,
                  );
                  const hasSavedApiToken = Boolean(
                    editingConfig && editingJson.api_token,
                  );

                  return (
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-semibold text-white">
                          GitHub REST (2022-11-28) & Projects v2 GraphQL
                        </h3>
                        <span className="text-xs text-slate-400">
                          {formState.github_auth_mode === "github_app"
                            ? "Installation-scoped RS256 App Auth"
                            : "Legacy Personal Access Token"}
                        </span>
                      </div>

                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="github-owner"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Owner / Organization *
                          </label>
                          <input
                            id="github-owner"
                            type="text"
                            value={formState.owner}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                owner: e.target.value,
                              }))
                            }
                            placeholder="e.g., penguintechinc"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor="github-repo"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Repository Name *
                          </label>
                          <input
                            id="github-repo"
                            type="text"
                            value={formState.repo_name}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                repo_name: e.target.value,
                              }))
                            }
                            placeholder="e.g., elder"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>
                      </div>

                      {/* GitHub Auth Mode Selector */}
                      <div className="space-y-2">
                        <p
                          id="github-auth-mode-label"
                          className="block text-sm font-medium text-yellow-500"
                        >
                          Authentication Method
                        </p>
                        <div
                          role="radiogroup"
                          aria-labelledby="github-auth-mode-label"
                          className="grid grid-cols-1 gap-2.5 sm:grid-cols-2"
                        >
                          <button
                            type="button"
                            role="radio"
                            aria-checked={
                              formState.github_auth_mode === "github_app"
                            }
                            data-testid="github-auth-mode-app"
                            onClick={() =>
                              setFormState((p) => ({
                                ...p,
                                github_auth_mode: "github_app",
                              }))
                            }
                            className={`text-left rounded-lg border p-3 transition-colors ${
                              formState.github_auth_mode === "github_app"
                                ? "border-emerald-500/70 bg-emerald-950/30 text-white"
                                : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-semibold flex items-center gap-1.5">
                                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                                GitHub App (Recommended)
                              </span>
                            </div>
                            <p className="mt-1 text-xs text-slate-400">
                              Short-lived installation tokens (1h), fine-grained
                              repo & Projects v2 permissions
                            </p>
                          </button>

                          <button
                            type="button"
                            role="radio"
                            aria-checked={formState.github_auth_mode === "pat"}
                            data-testid="github-auth-mode-pat"
                            onClick={() =>
                              setFormState((p) => ({
                                ...p,
                                github_auth_mode: "pat",
                              }))
                            }
                            className={`text-left rounded-lg border p-3 transition-colors ${
                              formState.github_auth_mode === "pat"
                                ? "border-amber-500/70 bg-amber-950/30 text-white"
                                : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-semibold flex items-center gap-1.5">
                                <KeyRound className="w-4 h-4 text-amber-400" />
                                Personal Access Token (Legacy / Not Recommended)
                              </span>
                            </div>
                            <p className="mt-1 text-xs text-slate-400">
                              Static user-bound token; use only for legacy
                              setups
                            </p>
                          </button>
                        </div>
                      </div>

                      {formState.github_auth_mode === "github_app" ? (
                        <div className="space-y-4">
                          <div className="rounded-lg border border-emerald-800/50 bg-emerald-950/20 p-3 text-xs text-slate-300 space-y-1.5">
                            <p className="font-semibold text-emerald-300">
                              Required GitHub App Permissions
                            </p>
                            <ul className="list-disc pl-5 space-y-0.5 text-slate-300">
                              <li>
                                <strong>Repository → Issues:</strong> Read &amp;
                                write (issues, labels, milestones)
                              </li>
                              <li>
                                <strong>Repository → Metadata:</strong>{" "}
                                Read-only
                              </li>
                              <li>
                                <strong>Organization → Projects:</strong> Read
                                &amp; write (for Projects v2)
                              </li>
                            </ul>
                          </div>

                          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                            <div>
                              <label
                                htmlFor="github-app-id"
                                className="block text-sm font-medium text-yellow-500 mb-1.5"
                              >
                                App ID *
                              </label>
                              <input
                                id="github-app-id"
                                type="text"
                                value={formState.app_id}
                                onChange={(e) =>
                                  setFormState((p) => ({
                                    ...p,
                                    app_id: e.target.value,
                                  }))
                                }
                                placeholder="e.g., 123456"
                                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                              />
                            </div>

                            <div>
                              <label
                                htmlFor="github-installation-id"
                                className="block text-sm font-medium text-yellow-500 mb-1.5"
                              >
                                Installation ID *
                              </label>
                              <input
                                id="github-installation-id"
                                type="text"
                                inputMode="numeric"
                                value={formState.installation_id}
                                onChange={(e) =>
                                  setFormState((p) => ({
                                    ...p,
                                    installation_id: e.target.value,
                                  }))
                                }
                                placeholder="e.g., 98765432"
                                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                              />
                            </div>
                          </div>

                          <div>
                            <div className="flex items-center justify-between mb-1.5">
                              <label
                                htmlFor="github-private-key"
                                className="block text-sm font-medium text-yellow-500"
                              >
                                Private Key (PEM){" "}
                                {hasSavedPrivateKey
                                  ? "(leave blank to keep saved)"
                                  : "*"}
                              </label>
                              <div>
                                <input
                                  ref={pemFileInputRef}
                                  id="github-pem-file"
                                  type="file"
                                  accept=".pem,application/x-pem-file,text/plain"
                                  aria-label="Upload .pem file"
                                  className="hidden"
                                  onChange={handlePemFileUpload}
                                />
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="secondary"
                                  onClick={() =>
                                    pemFileInputRef.current?.click()
                                  }
                                >
                                  <Upload className="w-3.5 h-3.5 mr-1.5" />
                                  Upload .pem file
                                </Button>
                              </div>
                            </div>
                            <textarea
                              id="github-private-key"
                              rows={4}
                              value={formState.private_key}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  private_key: e.target.value,
                                }))
                              }
                              placeholder={
                                hasSavedPrivateKey
                                  ? "***REDACTED***"
                                  : "Paste your GitHub App PEM private key (PKCS#1 or PKCS#8)"
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>
                        </div>
                      ) : (
                        <div className="space-y-4">
                          <div
                            role="note"
                            data-testid="github-pat-deprecation-banner"
                            className="rounded-lg border border-amber-600/60 bg-amber-950/40 p-3 text-xs text-amber-200 flex items-start gap-2.5"
                          >
                            <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
                            <div className="space-y-1">
                              <p className="font-semibold text-amber-300">
                                Personal Access Tokens are legacy and not
                                recommended
                              </p>
                              <p>
                                PATs are long-lived credentials tied to an
                                individual user account. Migrate to a{" "}
                                <button
                                  type="button"
                                  onClick={() =>
                                    setFormState((p) => ({
                                      ...p,
                                      github_auth_mode: "github_app",
                                    }))
                                  }
                                  className="underline font-medium text-amber-100 hover:text-white"
                                >
                                  GitHub App
                                </button>{" "}
                                for short-lived installation tokens and
                                fine-grained permissions.
                              </p>
                            </div>
                          </div>

                          <div>
                            <label
                              htmlFor="github-token"
                              className="block text-sm font-medium text-yellow-500 mb-1.5"
                            >
                              GitHub Personal Access Token{" "}
                              {hasSavedApiToken
                                ? "(leave blank to keep saved)"
                                : "*"}
                            </label>
                            <input
                              id="github-token"
                              type="password"
                              value={formState.api_token}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  api_token: e.target.value,
                                }))
                              }
                              placeholder={
                                hasSavedApiToken
                                  ? "***REDACTED***"
                                  : "github_pat_..."
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>
                        </div>
                      )}

                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="github-base-url"
                            className="block text-sm font-medium text-slate-300 mb-1.5"
                          >
                            API Base URL (optional, GHES)
                          </label>
                          <input
                            id="github-base-url"
                            type="text"
                            value={formState.base_url}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                base_url: e.target.value,
                              }))
                            }
                            placeholder="https://api.github.com"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor="github-graphql-url"
                            className="block text-sm font-medium text-slate-300 mb-1.5"
                          >
                            GraphQL URL (optional, Projects v2)
                          </label>
                          <input
                            id="github-graphql-url"
                            type="text"
                            value={formState.graphql_url}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                graphql_url: e.target.value,
                              }))
                            }
                            placeholder="https://api.github.com/graphql"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>
                      </div>
                    </div>
                  );
                })()}

              {/* Jira Cloud Credentials & Target */}
              {formState.platform === "jira" && (
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-white">
                      Jira Cloud REST API v3 Settings
                    </h3>
                    <span className="text-xs text-slate-400">
                      Uses /search/jql & Atlassian Document Format
                    </span>
                  </div>

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="jira-url"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        Jira Cloud Site URL *
                      </label>
                      <input
                        id="jira-url"
                        type="text"
                        value={formState.jira_url}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            jira_url: e.target.value,
                          }))
                        }
                        placeholder="your-domain.atlassian.net"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="jira-project-key"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        Project Key *
                      </label>
                      <input
                        id="jira-project-key"
                        type="text"
                        value={formState.project_key}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            project_key: e.target.value,
                          }))
                        }
                        placeholder="e.g., ENG"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="jira-email"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        Atlassian Account Email *
                      </label>
                      <input
                        id="jira-email"
                        type="email"
                        value={formState.email}
                        onChange={(e) =>
                          setFormState((p) => ({ ...p, email: e.target.value }))
                        }
                        placeholder="devops@company.com"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="jira-token"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        Jira API Token{" "}
                        {editingConfig ? "(leave blank to keep saved)" : "*"}
                      </label>
                      <input
                        id="jira-token"
                        type="password"
                        value={formState.api_token}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            api_token: e.target.value,
                          }))
                        }
                        placeholder={
                          editingConfig ? "***REDACTED***" : "ATATT3xFfGF0..."
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="jira-label-mode"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        Label Mapping Mode
                      </label>
                      <select
                        id="jira-label-mode"
                        value={formState.label_mode}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            label_mode: e.target.value as
                              | "labels"
                              | "components",
                          }))
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                      >
                        <option value="labels">
                          Jira Labels (pull + issue tagging)
                        </option>
                        <option value="components">
                          Jira Components (full CRUD)
                        </option>
                      </select>
                    </div>

                    <div>
                      <label
                        htmlFor="jira-lead-id"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        Project Lead Account ID (for creating Jira projects)
                      </label>
                      <input
                        id="jira-lead-id"
                        type="text"
                        value={formState.project_lead_account_id}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            project_lead_account_id: e.target.value,
                          }))
                        }
                        placeholder="5b10ac8d82e05b22cc7d4ef5"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <label
                    htmlFor="jira-sync-all-projects"
                    className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                  >
                    <input
                      id="jira-sync-all-projects"
                      type="checkbox"
                      checked={formState.sync_all_projects}
                      onChange={(e) =>
                        setFormState((p) => ({
                          ...p,
                          sync_all_projects: e.target.checked,
                        }))
                      }
                      className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                    />
                    Sync all accessible Jira projects (via /project/search)
                  </label>
                </div>
              )}

              {/* Other Platforms */}
              {formState.platform !== "github" &&
                formState.platform !== "jira" && (
                  <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                    <h3 className="text-sm font-semibold text-white">
                      {PLATFORM_LABELS[formState.platform]} Connection Settings
                    </h3>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <div>
                        <label
                          htmlFor="other-base-url"
                          className="block text-sm font-medium text-slate-300 mb-1.5"
                        >
                          Base URL
                        </label>
                        <input
                          id="other-base-url"
                          type="text"
                          value={formState.base_url}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              base_url: e.target.value,
                            }))
                          }
                          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label
                          htmlFor="other-project-id"
                          className="block text-sm font-medium text-slate-300 mb-1.5"
                        >
                          Project / Board ID
                        </label>
                        <input
                          id="other-project-id"
                          type="text"
                          value={formState.project_id}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              project_id: e.target.value,
                            }))
                          }
                          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                        />
                      </div>
                    </div>
                    <div>
                      <label
                        htmlFor="other-token"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        API Token / Key
                      </label>
                      <input
                        id="other-token"
                        type="password"
                        value={formState.api_token}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            api_token: e.target.value,
                          }))
                        }
                        placeholder={editingConfig ? "***REDACTED***" : ""}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>
                )}

              {/* Schedule & Webhooks */}
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                <h3 className="text-sm font-semibold text-white">
                  Scheduling & Inbound Webhooks
                </h3>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="sync-interval"
                      className="block text-sm font-medium text-slate-300 mb-1.5"
                    >
                      Batch Sync Interval (seconds)
                    </label>
                    <input
                      id="sync-interval"
                      type="number"
                      min={60}
                      value={formState.sync_interval}
                      onChange={(e) =>
                        setFormState((p) => ({
                          ...p,
                          sync_interval: Number(e.target.value),
                        }))
                      }
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="sync-webhook-secret"
                      className="block text-sm font-medium text-slate-300 mb-1.5"
                    >
                      Webhook Signing Secret{" "}
                      {editingConfig?.has_webhook_secret
                        ? "(leave blank to keep)"
                        : "(required for inbound hooks)"}
                    </label>
                    <input
                      id="sync-webhook-secret"
                      type="password"
                      value={formState.webhook_secret}
                      onChange={(e) =>
                        setFormState((p) => ({
                          ...p,
                          webhook_secret: e.target.value,
                        }))
                      }
                      placeholder={
                        editingConfig?.has_webhook_secret
                          ? "***REDACTED***"
                          : "HMAC-SHA256 shared secret"
                      }
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                    />
                  </div>
                </div>

                <div className="flex flex-wrap gap-5 pt-1">
                  <label
                    htmlFor="sync-enabled"
                    className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                  >
                    <input
                      id="sync-enabled"
                      type="checkbox"
                      checked={formState.enabled}
                      onChange={(e) =>
                        setFormState((p) => ({
                          ...p,
                          enabled: e.target.checked,
                        }))
                      }
                      className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                    />
                    Integration Enabled
                  </label>

                  <label
                    htmlFor="sync-two-way"
                    className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                  >
                    <input
                      id="sync-two-way"
                      type="checkbox"
                      checked={formState.two_way_create}
                      onChange={(e) =>
                        setFormState((p) => ({
                          ...p,
                          two_way_create: e.target.checked,
                        }))
                      }
                      className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                    />
                    Two-Way Create
                  </label>

                  <label
                    htmlFor="sync-webhook-enabled"
                    className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                  >
                    <input
                      id="sync-webhook-enabled"
                      type="checkbox"
                      checked={formState.webhook_enabled}
                      onChange={(e) =>
                        setFormState((p) => ({
                          ...p,
                          webhook_enabled: e.target.checked,
                        }))
                      }
                      className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                    />
                    Enable Inbound Webhook Receiver
                  </label>
                </div>
              </div>

              <div className="flex justify-end gap-3 border-t border-slate-800 pt-4">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  isLoading={
                    createMutation.isPending || updateMutation.isPending
                  }
                >
                  {editingConfig ? "Save Changes" : "Save Integration"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Details Modal (Webhook Setup / Mappings / History / Conflicts) */}
      {selectedConfig && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sync-details-title"
        >
          <div className="w-full max-w-4xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <div>
                <h2
                  id="sync-details-title"
                  className="text-xl font-semibold text-white"
                >
                  {selectedConfig.name} — Integration Details
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  {PLATFORM_LABELS[selectedConfig.platform]} · Config #
                  {selectedConfig.id}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedConfig(null)}
                aria-label="Close details"
                className="text-slate-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-slate-800 px-6 gap-4">
              <button
                type="button"
                onClick={() => setDetailsTab("webhooks")}
                className={`py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                  detailsTab === "webhooks"
                    ? "border-yellow-500 text-yellow-400"
                    : "border-transparent text-slate-400 hover:text-white"
                }`}
              >
                <Webhook className="w-4 h-4" />
                Webhook Setup
              </button>
              <button
                type="button"
                onClick={() => setDetailsTab("mappings")}
                className={`py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                  detailsTab === "mappings"
                    ? "border-yellow-500 text-yellow-400"
                    : "border-transparent text-slate-400 hover:text-white"
                }`}
              >
                <Link2 className="w-4 h-4" />
                Mappings
              </button>
              <button
                type="button"
                onClick={() => setDetailsTab("history")}
                className={`py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                  detailsTab === "history"
                    ? "border-yellow-500 text-yellow-400"
                    : "border-transparent text-slate-400 hover:text-white"
                }`}
              >
                <History className="w-4 h-4" />
                History
              </button>
              <button
                type="button"
                onClick={() => setDetailsTab("conflicts")}
                className={`py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                  detailsTab === "conflicts"
                    ? "border-yellow-500 text-yellow-400"
                    : "border-transparent text-slate-400 hover:text-white"
                }`}
              >
                <AlertTriangle className="w-4 h-4" />
                Conflicts
              </button>
            </div>

            <div className="p-6 max-h-[65vh] overflow-y-auto space-y-4">
              {detailsTab === "webhooks" && (
                <div className="space-y-4">
                  <div>
                    <p className="text-sm font-medium text-slate-200 mb-1.5">
                      Inbound Webhook Payload URL
                    </p>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-yellow-300 break-all">
                        {getWebhookUrl(selectedConfig)}
                      </code>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleCopyWebhookUrl(selectedConfig)}
                      >
                        {copiedWebhookId === selectedConfig.id ? (
                          <>
                            <Check className="w-3.5 h-3.5 mr-1 text-emerald-400" />
                            Copied
                          </>
                        ) : (
                          <>
                            <Copy className="w-3.5 h-3.5 mr-1" />
                            Copy
                          </>
                        )}
                      </Button>
                    </div>
                  </div>

                  {!selectedConfig.has_webhook_secret && (
                    <div className="rounded-lg border border-amber-700/60 bg-amber-950/30 p-3 text-xs text-amber-200">
                      Set a Webhook Signing Secret in Edit before enabling
                      remote webhooks — the receiver rejects unsigned requests
                      (HTTP 401).
                    </div>
                  )}

                  {selectedConfig.platform === "github" && (
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-sm text-slate-300 space-y-2">
                      <p className="font-semibold text-white">
                        GitHub Repository Webhook Setup
                      </p>
                      <ul className="list-disc pl-5 space-y-1 text-xs text-slate-300">
                        <li>
                          Content type: <code>application/json</code>
                        </li>
                        <li>
                          Secret: Must match your Webhook Signing Secret (
                          <code>X-Hub-Signature-256</code> HMAC-SHA256).
                        </li>
                        <li>
                          Events: Select <strong>Issues</strong>,{" "}
                          <strong>Milestones</strong>, and{" "}
                          <strong>Labels</strong>.
                        </li>
                      </ul>
                    </div>
                  )}

                  {selectedConfig.platform === "jira" && (
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-sm text-slate-300 space-y-2">
                      <p className="font-semibold text-white">
                        Jira Cloud System Webhook Setup
                      </p>
                      <ul className="list-disc pl-5 space-y-1 text-xs text-slate-300">
                        <li>
                          Navigate to Jira Settings → System → WebHooks and
                          paste the Payload URL above.
                        </li>
                        <li>
                          Secret: Configure the shared secret (sent via{" "}
                          <code>X-Hub-Signature: sha256=...</code>).
                        </li>
                        <li>
                          Events: Enable <strong>Issue</strong>{" "}
                          (created/updated/deleted), <strong>Version</strong>{" "}
                          (created/updated/deleted), and{" "}
                          <strong>Project</strong> (created/updated/deleted).
                        </li>
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {detailsTab === "mappings" && (
                <div>
                  {mappingsLoading ? (
                    <p className="text-sm text-slate-400">
                      Loading mappings...
                    </p>
                  ) : (mappingsData?.mappings.length ?? 0) === 0 ? (
                    <p className="text-sm text-slate-400">
                      No resource mappings recorded yet. Trigger a sync to
                      create links.
                    </p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs text-slate-300">
                        <thead className="border-b border-slate-800 text-slate-400">
                          <tr>
                            <th className="py-2 pr-4">Resource Type</th>
                            <th className="py-2 pr-4">Elder ID</th>
                            <th className="py-2 pr-4">External ID</th>
                            <th className="py-2 pr-4">Method</th>
                            <th className="py-2">Last Synced</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800">
                          {mappingsData?.mappings.map((m) => (
                            <tr key={m.id}>
                              <td className="py-2 pr-4 font-medium text-white">
                                {m.elder_type}
                              </td>
                              <td className="py-2 pr-4">#{m.elder_id}</td>
                              <td className="py-2 pr-4 font-mono text-yellow-300">
                                {m.external_id}
                              </td>
                              <td className="py-2 pr-4">
                                {m.sync_method || "—"}
                              </td>
                              <td className="py-2">
                                {m.last_synced_at
                                  ? new Date(m.last_synced_at).toLocaleString()
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {detailsTab === "history" && (
                <div>
                  {historyLoading ? (
                    <p className="text-sm text-slate-400">
                      Loading sync history...
                    </p>
                  ) : (historyData?.history.length ?? 0) === 0 ? (
                    <p className="text-sm text-slate-400">
                      No sync history recorded for this integration yet.
                    </p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs text-slate-300">
                        <thead className="border-b border-slate-800 text-slate-400">
                          <tr>
                            <th className="py-2 pr-4">Type</th>
                            <th className="py-2 pr-4">Status</th>
                            <th className="py-2 pr-4">Synced / Failed</th>
                            <th className="py-2 pr-4">Started</th>
                            <th className="py-2">Error</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800">
                          {historyData?.history.map((h) => (
                            <tr key={h.id}>
                              <td className="py-2 pr-4 font-medium text-white">
                                {h.sync_type}
                              </td>
                              <td className="py-2 pr-4">
                                <span
                                  className={
                                    h.success
                                      ? "text-emerald-400"
                                      : "text-red-400"
                                  }
                                >
                                  {h.success ? "Success" : "Failed"}
                                </span>
                              </td>
                              <td className="py-2 pr-4">
                                {h.items_synced} / {h.items_failed}
                              </td>
                              <td className="py-2 pr-4">
                                {new Date(h.started_at).toLocaleString()}
                              </td>
                              <td className="py-2 text-red-300">
                                {h.error_message || "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {detailsTab === "conflicts" && (
                <div>
                  {conflictsLoading ? (
                    <p className="text-sm text-slate-400">
                      Loading conflicts...
                    </p>
                  ) : (conflictsData?.conflicts.length ?? 0) === 0 ? (
                    <p className="text-sm text-slate-400">
                      No unresolved sync conflicts.
                    </p>
                  ) : (
                    <div className="space-y-3">
                      {conflictsData?.conflicts.map((c) => (
                        <div
                          key={c.id}
                          className="rounded-lg border border-amber-700/50 bg-slate-950 p-4 space-y-3"
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-semibold text-amber-300">
                              Conflict #{c.id} ({c.conflict_type}) · Mapping #
                              {c.mapping_id}
                            </span>
                            <div className="flex items-center gap-2">
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() =>
                                  resolveConflictMutation.mutate({
                                    id: c.id,
                                    strategy: "elder_wins",
                                  })
                                }
                              >
                                Keep Elder
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() =>
                                  resolveConflictMutation.mutate({
                                    id: c.id,
                                    strategy: "external_wins",
                                  })
                                }
                              >
                                Keep External
                              </Button>
                              <Button
                                size="sm"
                                onClick={() =>
                                  resolveConflictMutation.mutate({
                                    id: c.id,
                                    strategy: "manual",
                                  })
                                }
                              >
                                Mark Resolved
                              </Button>
                            </div>
                          </div>
                          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 text-xs">
                            <pre className="rounded bg-slate-900 p-2 text-slate-300 overflow-x-auto">
                              Elder: {JSON.stringify(c.elder_data, null, 2)}
                            </pre>
                            <pre className="rounded bg-slate-900 p-2 text-slate-300 overflow-x-auto">
                              External:{" "}
                              {JSON.stringify(c.external_data, null, 2)}
                            </pre>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
