import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  KeyRound,
  PlugZap,
  Plus,
  RefreshCw,
  Settings,
  Shield,
  ShieldCheck,
  Trash2,
  Upload,
  Webhook,
  X,
  XCircle,
} from "lucide-react";
import toast from "react-hot-toast";
import api, {
  type GoogleWorkspaceProviderItem,
  type GoogleWorkspaceTestResult,
  type IAMProviderItem,
  type IAMProviderSyncResult,
  type IAMProviderTestResult,
  type IAMProviderType,
} from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent, CardHeader } from "@/components/Card";
import { confirmDelete } from "@/lib/confirmActions";
import {
  extractOrganizations,
  getApiErrorMessage,
  isSecretConfigured,
  readUploadedTextFile,
} from "./shared";

interface GoogleWorkspaceFormState {
  name: string;
  organization_id: string;
  domain: string;
  customer_id: string;
  admin_email: string;
  service_account_json: string;
  description: string;
  enabled: boolean;
}

const INITIAL_GWS_FORM: GoogleWorkspaceFormState = {
  name: "",
  organization_id: "",
  domain: "",
  customer_id: "",
  admin_email: "",
  service_account_json: "",
  description: "",
  enabled: true,
};

interface IAMFormState {
  name: string;
  provider_type: IAMProviderType;
  organization_id: string;
  description: string;
  enabled: boolean;
  // AWS IAM
  aws_region: string;
  aws_access_key_id: string;
  aws_secret_access_key: string;
  aws_role_arn: string;
  // GCP IAM
  gcp_project_id: string;
  gcp_credentials_json: string;
  // Kubernetes RBAC
  k8s_mode: "kubeconfig" | "in_cluster";
  k8s_kubeconfig_path: string;
  k8s_context: string;
  k8s_namespace: string;
}

const INITIAL_IAM_FORM: IAMFormState = {
  name: "",
  provider_type: "aws_iam",
  organization_id: "",
  description: "",
  enabled: true,
  aws_region: "us-east-1",
  aws_access_key_id: "",
  aws_secret_access_key: "",
  aws_role_arn: "",
  gcp_project_id: "",
  gcp_credentials_json: "",
  k8s_mode: "kubeconfig",
  k8s_kubeconfig_path: "",
  k8s_context: "",
  k8s_namespace: "default",
};

const IAM_PROVIDER_LABELS: Record<string, string> = {
  aws_iam: "AWS IAM",
  gcp_iam: "GCP IAM",
  kubernetes: "Kubernetes RBAC",
};

function summarizeIAMConfig(provider: IAMProviderItem): string[] {
  const cfg = (provider.config_json || {}) as Record<string, unknown>;
  const parts: string[] = [];
  if (provider.provider_type === "aws_iam") {
    if (cfg.region) parts.push(`Region: ${String(cfg.region)}`);
    if (cfg.role_arn) parts.push(`Role ARN configured`);
  } else if (provider.provider_type === "gcp_iam") {
    if (cfg.project_id) parts.push(`Project: ${String(cfg.project_id)}`);
  } else if (provider.provider_type === "kubernetes") {
    parts.push(cfg.in_cluster ? "Mode: In-Cluster" : "Mode: Kubeconfig");
    parts.push(`Namespace: ${String(cfg.namespace || "default")}`);
    if (cfg.context) parts.push(`Context: ${String(cfg.context)}`);
  }
  return parts;
}

export interface IdentityIntegrationsPanelProps {
  openCreateRequest?: {
    nonce: number;
    kind?: "google_workspace" | IAMProviderType;
  };
}

export default function IdentityIntegrationsPanel({
  openCreateRequest,
}: IdentityIntegrationsPanelProps) {
  const queryClient = useQueryClient();

  // Google Workspace state
  const [showGwsModal, setShowGwsModal] = useState(false);
  const [editingGws, setEditingGws] =
    useState<GoogleWorkspaceProviderItem | null>(null);
  const [gwsForm, setGwsForm] =
    useState<GoogleWorkspaceFormState>(INITIAL_GWS_FORM);
  const [gwsError, setGwsError] = useState<string | null>(null);
  const [testingGwsId, setTestingGwsId] = useState<number | null>(null);
  const [gwsTestResults, setGwsTestResults] = useState<
    Record<number, GoogleWorkspaceTestResult>
  >({});
  const gwsFileInputRef = useRef<HTMLInputElement | null>(null);

  // IAM state
  const [showIamModal, setShowIamModal] = useState(false);
  const [editingIam, setEditingIam] = useState<IAMProviderItem | null>(null);
  const [iamForm, setIamForm] = useState<IAMFormState>(INITIAL_IAM_FORM);
  const [iamError, setIamError] = useState<string | null>(null);
  const [testingIamId, setTestingIamId] = useState<number | null>(null);
  const [syncingIamId, setSyncingIamId] = useState<number | null>(null);
  const [iamTestResults, setIamTestResults] = useState<
    Record<number, IAMProviderTestResult>
  >({});
  const [iamSyncResults, setIamSyncResults] = useState<
    Record<number, IAMProviderSyncResult>
  >({});
  const iamGcpFileRef = useRef<HTMLInputElement | null>(null);

  const { data: gwsData, isLoading: gwsLoading } = useQuery({
    queryKey: ["googleWorkspaceProviders"],
    queryFn: () => api.getGoogleWorkspaceProviders(),
  });

  const { data: iamData, isLoading: iamLoading } = useQuery({
    queryKey: ["iamProviders"],
    queryFn: () => api.getIAMProviders(),
  });

  const { data: orgsData } = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api.getOrganizations(),
  });

  const organizations = useMemo(
    () => extractOrganizations(orgsData),
    [orgsData],
  );
  const defaultOrgId = organizations[0] ? String(organizations[0].id) : "";

  const orgMap = useMemo(() => {
    const map = new Map<number, string>();
    for (const org of organizations) {
      map.set(org.id, org.name);
    }
    return map;
  }, [organizations]);

  const gwsProviders = gwsData?.providers ?? [];
  const iamProviders = iamData?.providers ?? [];

  const handleOpenCreateGws = useCallback(() => {
    setEditingGws(null);
    setGwsForm({
      ...INITIAL_GWS_FORM,
      organization_id: defaultOrgId,
    });
    setGwsError(null);
    setShowGwsModal(true);
  }, [defaultOrgId]);

  const handleOpenCreateIam = useCallback(
    (providerType: IAMProviderType = "aws_iam") => {
      setEditingIam(null);
      setIamForm({
        ...INITIAL_IAM_FORM,
        provider_type: providerType,
        organization_id: defaultOrgId,
      });
      setIamError(null);
      setShowIamModal(true);
    },
    [defaultOrgId],
  );

  const lastNonceRef = useRef(0);
  useEffect(() => {
    if (
      openCreateRequest &&
      openCreateRequest.nonce > 0 &&
      openCreateRequest.nonce !== lastNonceRef.current
    ) {
      lastNonceRef.current = openCreateRequest.nonce;
      const kind = openCreateRequest.kind ?? "google_workspace";
      if (kind === "google_workspace") {
        handleOpenCreateGws();
      } else {
        handleOpenCreateIam(kind);
      }
    }
  }, [openCreateRequest, handleOpenCreateGws, handleOpenCreateIam]);

  // Google Workspace mutations
  const createGwsMutation = useMutation({
    mutationFn: (payload: {
      name: string;
      organization_id: number;
      domain?: string;
      customer_id: string;
      admin_email: string;
      service_account_json: Record<string, unknown>;
      description?: string;
    }) => api.createGoogleWorkspaceProvider(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["googleWorkspaceProviders"],
      });
      toast.success("Google Workspace provider created");
      setShowGwsModal(false);
    },
    onError: (err) => {
      setGwsError(
        getApiErrorMessage(err, "Failed to create Google Workspace provider"),
      );
    },
  });

  const updateGwsMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: Partial<{
        name: string;
        domain: string;
        customer_id: string;
        admin_email: string;
        service_account_json: Record<string, unknown>;
        description: string;
        enabled: boolean;
      }>;
    }) => api.updateGoogleWorkspaceProvider(id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["googleWorkspaceProviders"],
      });
      toast.success("Google Workspace provider updated");
      setShowGwsModal(false);
      setEditingGws(null);
    },
    onError: (err) => {
      setGwsError(
        getApiErrorMessage(err, "Failed to update Google Workspace provider"),
      );
    },
  });

  const deleteGwsMutation = useMutation({
    mutationFn: (id: number) => api.deleteGoogleWorkspaceProvider(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["googleWorkspaceProviders"],
      });
      toast.success("Google Workspace provider deleted");
    },
    onError: (err) => {
      toast.error(
        getApiErrorMessage(err, "Failed to delete Google Workspace provider"),
      );
    },
  });

  // IAM mutations
  const createIamMutation = useMutation({
    mutationFn: (payload: {
      name: string;
      provider_type: string;
      organization_id: number;
      config: Record<string, unknown>;
      description?: string;
    }) => api.createIAMProvider(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["iamProviders"] });
      toast.success("IAM provider created");
      setShowIamModal(false);
    },
    onError: (err) => {
      setIamError(getApiErrorMessage(err, "Failed to create IAM provider"));
    },
  });

  const updateIamMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: Partial<{
        name: string;
        config: Record<string, unknown>;
        description: string;
        enabled: boolean;
      }>;
    }) => api.updateIAMProvider(id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["iamProviders"] });
      toast.success("IAM provider updated");
      setShowIamModal(false);
      setEditingIam(null);
    },
    onError: (err) => {
      setIamError(getApiErrorMessage(err, "Failed to update IAM provider"));
    },
  });

  const deleteIamMutation = useMutation({
    mutationFn: (id: number) => api.deleteIAMProvider(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["iamProviders"] });
      toast.success("IAM provider deleted");
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Failed to delete IAM provider"));
    },
  });

  const handleOpenEditGws = (provider: GoogleWorkspaceProviderItem) => {
    setEditingGws(provider);
    setGwsForm({
      name: provider.name,
      organization_id: String(provider.organization_id),
      domain: provider.domain || "",
      customer_id: provider.customer_id || "",
      admin_email: provider.admin_email || "",
      service_account_json: "",
      description: provider.description || "",
      enabled: provider.enabled,
    });
    setGwsError(null);
    setShowGwsModal(true);
  };

  const handleOpenEditIam = (provider: IAMProviderItem) => {
    const cfg = (provider.config_json || {}) as Record<string, unknown>;
    const providerType: IAMProviderType =
      provider.provider_type === "gcp_iam" ||
      provider.provider_type === "kubernetes"
        ? provider.provider_type
        : "aws_iam";
    setEditingIam(provider);
    setIamForm({
      name: provider.name,
      provider_type: providerType,
      organization_id: String(provider.organization_id),
      description: String(provider.description ?? cfg._description ?? ""),
      enabled: provider.enabled,
      aws_region: String(cfg.region || "us-east-1"),
      aws_access_key_id: "",
      aws_secret_access_key: "",
      aws_role_arn: String(cfg.role_arn || ""),
      gcp_project_id: String(cfg.project_id || ""),
      gcp_credentials_json: "",
      k8s_mode: cfg.in_cluster ? "in_cluster" : "kubeconfig",
      k8s_kubeconfig_path: String(cfg.kubeconfig_path || ""),
      k8s_context: String(cfg.context || ""),
      k8s_namespace: String(cfg.namespace || "default"),
    });
    setIamError(null);
    setShowIamModal(true);
  };

  const handleGwsFileUpload = async (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const content = await readUploadedTextFile(file);
      setGwsForm((p) => ({ ...p, service_account_json: content.trim() }));
      setGwsError(null);
    } catch {
      setGwsError("Unable to read the selected Google service account file.");
    }
  };

  const handleIamGcpFileUpload = async (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const content = await readUploadedTextFile(file);
      setIamForm((p) => ({ ...p, gcp_credentials_json: content.trim() }));
      setIamError(null);
    } catch {
      setIamError("Unable to read the selected GCP service account file.");
    }
  };

  const handleTestGws = async (provider: GoogleWorkspaceProviderItem) => {
    setTestingGwsId(provider.id);
    try {
      const res = await api.testGoogleWorkspaceProvider(provider.id);
      setGwsTestResults((prev) => ({ ...prev, [provider.id]: res }));
      if (res.success) {
        toast.success(`Google Workspace connected (${provider.name})`);
      } else {
        toast.error(res.error || "Google Workspace test failed");
      }
    } catch (err) {
      const maybeData = (
        err as { response?: { data?: GoogleWorkspaceTestResult } }
      )?.response?.data;
      const result: GoogleWorkspaceTestResult = maybeData?.error
        ? { ...maybeData, success: false }
        : {
            success: false,
            error: getApiErrorMessage(err, "Connection test failed"),
          };
      setGwsTestResults((prev) => ({ ...prev, [provider.id]: result }));
      toast.error(result.error || "Connection test failed");
    } finally {
      setTestingGwsId(null);
    }
  };

  const handleTestIam = async (provider: IAMProviderItem) => {
    setTestingIamId(provider.id);
    try {
      const res = await api.testIAMProvider(provider.id);
      setIamTestResults((prev) => ({ ...prev, [provider.id]: res }));
      if (res.success) {
        toast.success(`IAM connection verified (${provider.name})`);
      } else {
        toast.error(res.error || "IAM connection test failed");
      }
    } catch (err) {
      const msg = getApiErrorMessage(err, "IAM connection test failed");
      setIamTestResults((prev) => ({
        ...prev,
        [provider.id]: {
          provider_id: provider.id,
          success: false,
          tested_at: new Date().toISOString(),
          error: msg,
        },
      }));
      toast.error(msg);
    } finally {
      setTestingIamId(null);
    }
  };

  const handleSyncIam = async (provider: IAMProviderItem) => {
    setSyncingIamId(provider.id);
    try {
      const res = await api.syncIAMProvider(provider.id);
      setIamSyncResults((prev) => ({ ...prev, [provider.id]: res }));
      if (res.errors && res.errors.length > 0) {
        toast.error(res.errors[0]);
      } else {
        toast.success(
          `Synced ${res.users_synced ?? 0} users, ${res.roles_synced ?? 0} roles`,
        );
      }
    } catch (err) {
      toast.error(getApiErrorMessage(err, "Failed to sync IAM provider"));
    } finally {
      setSyncingIamId(null);
    }
  };

  const handleSubmitGws = (e: React.FormEvent) => {
    e.preventDefault();
    setGwsError(null);

    if (
      !gwsForm.name.trim() ||
      !gwsForm.customer_id.trim() ||
      !gwsForm.admin_email.trim()
    ) {
      setGwsError("Name, Customer ID, and Delegated Admin Email are required.");
      return;
    }

    const orgIdNum = Number(gwsForm.organization_id);
    if (!editingGws && (!orgIdNum || Number.isNaN(orgIdNum))) {
      setGwsError("Organization is required.");
      return;
    }

    let parsedSa: Record<string, unknown> | undefined;
    if (gwsForm.service_account_json.trim()) {
      try {
        const candidate = JSON.parse(gwsForm.service_account_json.trim());
        if (
          !candidate ||
          typeof candidate !== "object" ||
          Array.isArray(candidate)
        ) {
          setGwsError("Service Account JSON must be a JSON object.");
          return;
        }
        parsedSa = candidate as Record<string, unknown>;
      } catch {
        setGwsError("Service Account JSON is not valid JSON.");
        return;
      }
    } else if (!editingGws) {
      setGwsError("Service Account JSON credentials are required.");
      return;
    }

    if (editingGws) {
      const payload: Partial<{
        name: string;
        domain: string;
        customer_id: string;
        admin_email: string;
        service_account_json: Record<string, unknown>;
        description: string;
        enabled: boolean;
      }> = {
        name: gwsForm.name.trim(),
        domain: gwsForm.domain.trim() || undefined,
        customer_id: gwsForm.customer_id.trim(),
        admin_email: gwsForm.admin_email.trim(),
        description: gwsForm.description.trim(),
        enabled: gwsForm.enabled,
      };
      if (parsedSa) {
        payload.service_account_json = parsedSa;
      }
      updateGwsMutation.mutate({ id: editingGws.id, payload });
    } else {
      createGwsMutation.mutate({
        name: gwsForm.name.trim(),
        organization_id: orgIdNum,
        domain: gwsForm.domain.trim() || undefined,
        customer_id: gwsForm.customer_id.trim(),
        admin_email: gwsForm.admin_email.trim(),
        service_account_json: parsedSa!,
        description: gwsForm.description.trim() || undefined,
      });
    }
  };

  const handleSubmitIam = (e: React.FormEvent) => {
    e.preventDefault();
    setIamError(null);

    if (!iamForm.name.trim()) {
      setIamError("Provider name is required.");
      return;
    }

    const orgIdNum = Number(iamForm.organization_id);
    if (!editingIam && (!orgIdNum || Number.isNaN(orgIdNum))) {
      setIamError("Organization is required.");
      return;
    }

    const config: Record<string, unknown> = {};
    if (iamForm.provider_type === "aws_iam") {
      config.region = iamForm.aws_region.trim() || "us-east-1";
      if (iamForm.aws_access_key_id.trim()) {
        config.access_key_id = iamForm.aws_access_key_id.trim();
      }
      if (iamForm.aws_secret_access_key.trim()) {
        config.secret_access_key = iamForm.aws_secret_access_key.trim();
      }
      if (iamForm.aws_role_arn.trim()) {
        config.role_arn = iamForm.aws_role_arn.trim();
      }
    } else if (iamForm.provider_type === "gcp_iam") {
      if (!iamForm.gcp_project_id.trim()) {
        setIamError("GCP Project ID is required.");
        return;
      }
      config.project_id = iamForm.gcp_project_id.trim();
      if (iamForm.gcp_credentials_json.trim()) {
        config.credentials_json = iamForm.gcp_credentials_json.trim();
      }
    } else if (iamForm.provider_type === "kubernetes") {
      config.in_cluster = iamForm.k8s_mode === "in_cluster";
      config.namespace = iamForm.k8s_namespace.trim() || "default";
      if (iamForm.k8s_mode === "kubeconfig") {
        if (iamForm.k8s_kubeconfig_path.trim()) {
          config.kubeconfig_path = iamForm.k8s_kubeconfig_path.trim();
        }
        if (iamForm.k8s_context.trim()) {
          config.context = iamForm.k8s_context.trim();
        }
      }
    }

    if (editingIam) {
      updateIamMutation.mutate({
        id: editingIam.id,
        payload: {
          name: iamForm.name.trim(),
          config,
          description: iamForm.description.trim(),
          enabled: iamForm.enabled,
        },
      });
    } else {
      createIamMutation.mutate({
        name: iamForm.name.trim(),
        provider_type: iamForm.provider_type,
        organization_id: orgIdNum,
        config,
        description: iamForm.description.trim() || undefined,
      });
    }
  };

  const iamHasSavedSecret = useMemo(
    () =>
      isSecretConfigured(editingIam?.config_json, [
        "access_key_id",
        "secret_access_key",
        "credentials_json",
        "kubeconfig",
        "token",
      ]),
    [editingIam],
  );

  return (
    <div className="space-y-8">
      {/* Section 1: Google Workspace Directories */}
      <section className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">
              Google Workspace Directories
            </h2>
            <p className="mt-0.5 text-sm text-slate-400">
              Domain-wide delegation connectors for directory users, groups, and
              organizational units
            </p>
          </div>
          <Button
            onClick={handleOpenCreateGws}
            data-testid="add-google-workspace-btn"
          >
            <Plus className="w-4 h-4 mr-2" />
            Add Google Workspace
          </Button>
        </div>

        {gwsLoading ? (
          <Card>
            <CardContent className="p-6 text-center text-slate-400">
              Loading Google Workspace directories...
            </CardContent>
          </Card>
        ) : gwsProviders.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center space-y-3">
              <Building2 className="w-9 h-9 text-yellow-500 mx-auto" />
              <p className="text-sm text-slate-300 font-medium">
                No Google Workspace directories connected
              </p>
              <Button size="sm" onClick={handleOpenCreateGws}>
                <Plus className="w-3.5 h-3.5 mr-1.5" />
                Connect Google Workspace
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-4">
            {gwsProviders.map((provider) => {
              const testRes = gwsTestResults[provider.id];
              const orgName = orgMap.get(provider.organization_id);
              return (
                <Card key={provider.id}>
                  <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-lg font-semibold text-white">
                          {provider.name}
                        </h3>
                        <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-slate-800 text-yellow-400 border border-slate-700">
                          Google Workspace
                        </span>
                        <span
                          className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                            provider.enabled
                              ? "bg-emerald-950/60 text-emerald-300 border-emerald-700/60"
                              : "bg-slate-800 text-slate-400 border-slate-700"
                          }`}
                        >
                          {provider.enabled ? "Enabled" : "Paused"}
                        </span>
                        {(provider.has_credentials ||
                          Boolean(provider.credentials_json)) && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                            <ShieldCheck className="w-3 h-3" />
                            Credentials configured
                          </span>
                        )}
                      </div>

                      <div className="text-sm text-slate-400 flex flex-wrap items-center gap-x-4 gap-y-1">
                        <span>
                          Domain:{" "}
                          <code className="text-slate-200">
                            {provider.domain}
                          </code>
                        </span>
                        {provider.customer_id && (
                          <span>
                            Customer ID:{" "}
                            <code className="text-slate-200">
                              {provider.customer_id}
                            </code>
                          </span>
                        )}
                        <span>
                          Admin:{" "}
                          <span className="text-slate-200">
                            {provider.admin_email}
                          </span>
                        </span>
                        <span>
                          Org:{" "}
                          <span className="text-slate-200">
                            {orgName ?? `#${provider.organization_id}`}
                          </span>
                        </span>
                      </div>

                      {provider.description && (
                        <p className="text-xs text-slate-400">
                          {provider.description}
                        </p>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleTestGws(provider)}
                        isLoading={testingGwsId === provider.id}
                      >
                        <PlugZap className="w-3.5 h-3.5 mr-1.5" />
                        Test Connection
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleOpenEditGws(provider)}
                        aria-label={`Edit ${provider.name}`}
                      >
                        <Settings className="w-4 h-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          updateGwsMutation.mutate({
                            id: provider.id,
                            payload: { enabled: !provider.enabled },
                          })
                        }
                      >
                        {provider.enabled ? "Pause" : "Enable"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          confirmDelete(provider.name, () =>
                            deleteGwsMutation.mutate(provider.id),
                          )
                        }
                        aria-label={`Delete ${provider.name}`}
                      >
                        <Trash2 className="w-4 h-4 text-red-400" />
                      </Button>
                    </div>
                  </CardHeader>

                  {testRes && (
                    <CardContent className="pt-0 pb-4">
                      <div
                        className={`rounded-lg border px-3 py-2 text-xs flex items-center gap-2 ${
                          testRes.success
                            ? "border-emerald-700/60 bg-emerald-950/30 text-emerald-200"
                            : "border-red-700/60 bg-red-950/30 text-red-200"
                        }`}
                      >
                        {testRes.success ? (
                          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                        ) : (
                          <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                        )}
                        <span>
                          {testRes.success
                            ? testRes.message ||
                              "Successfully connected to Google Workspace"
                            : testRes.error ||
                              testRes.message ||
                              "Connection test failed"}
                        </span>
                      </div>
                    </CardContent>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* Section 2: Cloud IAM Providers */}
      <section className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">
              Cloud IAM Providers
            </h2>
            <p className="mt-0.5 text-sm text-slate-400">
              Synchronize IAM users, service accounts, roles, and policies from
              AWS IAM, GCP IAM, and Kubernetes RBAC
            </p>
          </div>
          <Button
            variant="secondary"
            onClick={() => handleOpenCreateIam("aws_iam")}
            data-testid="add-iam-provider-btn"
          >
            <Plus className="w-4 h-4 mr-2" />
            Add IAM Provider
          </Button>
        </div>

        {iamLoading ? (
          <Card>
            <CardContent className="p-6 text-center text-slate-400">
              Loading IAM providers...
            </CardContent>
          </Card>
        ) : iamProviders.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center space-y-3">
              <KeyRound className="w-9 h-9 text-yellow-500 mx-auto" />
              <p className="text-sm text-slate-300 font-medium">
                No Cloud IAM providers configured yet
              </p>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleOpenCreateIam("aws_iam")}
              >
                <Plus className="w-3.5 h-3.5 mr-1.5" />
                Connect IAM Provider
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-4">
            {iamProviders.map((provider) => {
              const cfg = (provider.config_json || {}) as Record<
                string,
                unknown
              >;
              const orgName = orgMap.get(provider.organization_id);
              const hasCreds = isSecretConfigured(cfg, [
                "access_key_id",
                "secret_access_key",
                "credentials_json",
                "kubeconfig",
                "token",
              ]);
              const summaryParts = summarizeIAMConfig(provider);
              const testRes = iamTestResults[provider.id];
              const syncRes = iamSyncResults[provider.id];
              const description =
                provider.description ?? (cfg._description as string);

              return (
                <Card key={provider.id}>
                  <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-lg font-semibold text-white">
                          {provider.name}
                        </h3>
                        <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-slate-800 text-yellow-400 border border-slate-700">
                          {IAM_PROVIDER_LABELS[provider.provider_type] ||
                            provider.provider_type}
                        </span>
                        <span
                          className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                            provider.enabled
                              ? "bg-emerald-950/60 text-emerald-300 border-emerald-700/60"
                              : "bg-slate-800 text-slate-400 border-slate-700"
                          }`}
                        >
                          {provider.enabled ? "Enabled" : "Paused"}
                        </span>
                        {hasCreds && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                            <ShieldCheck className="w-3 h-3" />
                            Credentials configured
                          </span>
                        )}
                      </div>

                      <div className="text-sm text-slate-400 flex flex-wrap items-center gap-x-4 gap-y-1">
                        {summaryParts.map((part) => (
                          <span key={part}>{part}</span>
                        ))}
                        <span>
                          Org:{" "}
                          <span className="text-slate-200">
                            {orgName ?? `#${provider.organization_id}`}
                          </span>
                        </span>
                        {provider.last_sync_at && (
                          <span>
                            Last sync{" "}
                            {new Date(provider.last_sync_at).toLocaleString()}
                          </span>
                        )}
                      </div>

                      {description && (
                        <p className="text-xs text-slate-400">{description}</p>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleTestIam(provider)}
                        isLoading={testingIamId === provider.id}
                      >
                        <PlugZap className="w-3.5 h-3.5 mr-1.5" />
                        Test Connection
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => handleSyncIam(provider)}
                        isLoading={syncingIamId === provider.id}
                      >
                        <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
                        Sync Now
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleOpenEditIam(provider)}
                        aria-label={`Edit ${provider.name}`}
                      >
                        <Settings className="w-4 h-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          updateIamMutation.mutate({
                            id: provider.id,
                            payload: { enabled: !provider.enabled },
                          })
                        }
                      >
                        {provider.enabled ? "Pause" : "Enable"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          confirmDelete(provider.name, () =>
                            deleteIamMutation.mutate(provider.id),
                          )
                        }
                        aria-label={`Delete ${provider.name}`}
                      >
                        <Trash2 className="w-4 h-4 text-red-400" />
                      </Button>
                    </div>
                  </CardHeader>

                  {(testRes || syncRes) && (
                    <CardContent className="pt-0 pb-4 space-y-2">
                      {testRes && (
                        <div
                          className={`rounded-lg border px-3 py-2 text-xs flex items-center gap-2 ${
                            testRes.success
                              ? "border-emerald-700/60 bg-emerald-950/30 text-emerald-200"
                              : "border-red-700/60 bg-red-950/30 text-red-200"
                          }`}
                        >
                          {testRes.success ? (
                            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                          ) : (
                            <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                          )}
                          <span>
                            {testRes.success
                              ? `IAM connection verified for "${provider.name}"`
                              : testRes.error || "IAM connection test failed"}
                          </span>
                        </div>
                      )}
                      {syncRes && (
                        <div className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 text-xs text-slate-300">
                          Synced {syncRes.users_synced ?? 0} users ·{" "}
                          {syncRes.roles_synced ?? 0} roles ·{" "}
                          {syncRes.policies_synced ?? 0} policies
                          {syncRes.errors && syncRes.errors.length > 0 && (
                            <span className="text-red-300 ml-2">
                              ({syncRes.errors[0]})
                            </span>
                          )}
                        </div>
                      )}
                    </CardContent>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* Section 3: Related Access & Event Integrations */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
          Related Access & Event Integrations
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Link
            to="/admin/sso"
            className="rounded-xl border border-slate-700 bg-slate-800/70 p-4 hover:border-yellow-500/60 hover:bg-slate-800 transition-colors flex items-start gap-3"
          >
            <Shield className="w-5 h-5 text-yellow-500 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-white">
                SSO / SAML / OIDC & SCIM
              </p>
              <p className="text-xs text-slate-400 mt-1">
                Configure enterprise SAML 2.0, OIDC identity providers, and SCIM
                2.0 user provisioning
              </p>
            </div>
          </Link>

          <Link
            to="/webhooks"
            className="rounded-xl border border-slate-700 bg-slate-800/70 p-4 hover:border-yellow-500/60 hover:bg-slate-800 transition-colors flex items-start gap-3"
          >
            <Webhook className="w-5 h-5 text-yellow-500 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-white">
                Inbound & Outbound Webhooks
              </p>
              <p className="text-xs text-slate-400 mt-1">
                Manage event delivery subscriptions, HMAC secrets, and scoped
                inbound webhook receivers
              </p>
            </div>
          </Link>
        </div>
      </section>

      {/* Google Workspace Create / Edit Modal */}
      {showGwsModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="gws-modal-title"
        >
          <div className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <h3
                id="gws-modal-title"
                className="text-xl font-semibold text-white"
              >
                {editingGws
                  ? `Edit ${editingGws.name}`
                  : "Connect Google Workspace"}
              </h3>
              <button
                type="button"
                onClick={() => setShowGwsModal(false)}
                aria-label="Close Google Workspace modal"
                className="text-slate-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form
              onSubmit={handleSubmitGws}
              className="p-6 space-y-4 max-h-[80vh] overflow-y-auto"
            >
              {gwsError && (
                <div
                  role="alert"
                  className="rounded-lg border border-red-700/70 bg-red-950/50 p-3 text-sm text-red-200 flex items-center gap-2"
                >
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{gwsError}</span>
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="gws-name"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Provider Name *
                  </label>
                  <input
                    id="gws-name"
                    type="text"
                    required
                    value={gwsForm.name}
                    onChange={(e) =>
                      setGwsForm((p) => ({ ...p, name: e.target.value }))
                    }
                    placeholder="Corporate Google Workspace"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="gws-org"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Organization *
                  </label>
                  <select
                    id="gws-org"
                    value={gwsForm.organization_id}
                    disabled={Boolean(editingGws)}
                    onChange={(e) =>
                      setGwsForm((p) => ({
                        ...p,
                        organization_id: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none disabled:opacity-60"
                  >
                    <option value="">Select organization</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={String(org.id)}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <label
                    htmlFor="gws-domain"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Workspace Domain
                  </label>
                  <input
                    id="gws-domain"
                    type="text"
                    value={gwsForm.domain}
                    onChange={(e) =>
                      setGwsForm((p) => ({ ...p, domain: e.target.value }))
                    }
                    placeholder="example.com"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="gws-customer-id"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Customer ID *
                  </label>
                  <input
                    id="gws-customer-id"
                    type="text"
                    required
                    value={gwsForm.customer_id}
                    onChange={(e) =>
                      setGwsForm((p) => ({ ...p, customer_id: e.target.value }))
                    }
                    placeholder="C01234567"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="gws-admin-email"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Delegated Admin Email *
                  </label>
                  <input
                    id="gws-admin-email"
                    type="email"
                    required
                    value={gwsForm.admin_email}
                    onChange={(e) =>
                      setGwsForm((p) => ({ ...p, admin_email: e.target.value }))
                    }
                    placeholder="admin@example.com"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label
                    htmlFor="gws-sa-json"
                    className="block text-sm font-medium text-yellow-500"
                  >
                    Service Account JSON {editingGws ? "" : "*"}
                  </label>
                  <div className="flex items-center gap-2">
                    {editingGws?.has_credentials && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                        <ShieldCheck className="w-3 h-3" />
                        Credentials configured
                      </span>
                    )}
                    <input
                      ref={gwsFileInputRef}
                      id="gws-json-upload"
                      type="file"
                      accept=".json,application/json"
                      aria-label="Upload Google service account JSON"
                      className="hidden"
                      onChange={handleGwsFileUpload}
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      onClick={() => gwsFileInputRef.current?.click()}
                    >
                      <Upload className="w-3.5 h-3.5 mr-1.5" />
                      Upload .json file
                    </Button>
                  </div>
                </div>
                <textarea
                  id="gws-sa-json"
                  rows={5}
                  value={gwsForm.service_account_json}
                  onChange={(e) =>
                    setGwsForm((p) => ({
                      ...p,
                      service_account_json: e.target.value,
                    }))
                  }
                  placeholder={
                    editingGws
                      ? "Leave blank to keep existing credentials"
                      : "Paste your Google Workspace domain-delegated service account JSON key object (project_id, client_email, private_key)"
                  }
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                />
              </div>

              <div>
                <label
                  htmlFor="gws-description"
                  className="block text-sm font-medium text-slate-300 mb-1.5"
                >
                  Description
                </label>
                <input
                  id="gws-description"
                  type="text"
                  value={gwsForm.description}
                  onChange={(e) =>
                    setGwsForm((p) => ({ ...p, description: e.target.value }))
                  }
                  placeholder="Primary company Google Workspace"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                />
              </div>

              {editingGws && (
                <label
                  htmlFor="gws-enabled"
                  className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                >
                  <input
                    id="gws-enabled"
                    type="checkbox"
                    checked={gwsForm.enabled}
                    onChange={(e) =>
                      setGwsForm((p) => ({ ...p, enabled: e.target.checked }))
                    }
                    className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                  />
                  Provider Enabled
                </label>
              )}

              <div className="flex justify-end gap-3 border-t border-slate-800 pt-4">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowGwsModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  isLoading={
                    createGwsMutation.isPending || updateGwsMutation.isPending
                  }
                >
                  {editingGws ? "Save Changes" : "Save Directory"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* IAM Create / Edit Modal */}
      {showIamModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="iam-modal-title"
        >
          <div className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <h3
                id="iam-modal-title"
                className="text-xl font-semibold text-white"
              >
                {editingIam ? `Edit ${editingIam.name}` : "Add IAM Provider"}
              </h3>
              <button
                type="button"
                onClick={() => setShowIamModal(false)}
                aria-label="Close IAM modal"
                className="text-slate-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form
              onSubmit={handleSubmitIam}
              className="p-6 space-y-4 max-h-[80vh] overflow-y-auto"
            >
              {iamError && (
                <div
                  role="alert"
                  className="rounded-lg border border-red-700/70 bg-red-950/50 p-3 text-sm text-red-200 flex items-center gap-2"
                >
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{iamError}</span>
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="iam-name"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Provider Name *
                  </label>
                  <input
                    id="iam-name"
                    type="text"
                    required
                    value={iamForm.name}
                    onChange={(e) =>
                      setIamForm((p) => ({ ...p, name: e.target.value }))
                    }
                    placeholder="Production AWS IAM"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="iam-type"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Provider Type *
                  </label>
                  <select
                    id="iam-type"
                    value={iamForm.provider_type}
                    disabled={Boolean(editingIam)}
                    onChange={(e) =>
                      setIamForm((p) => ({
                        ...p,
                        provider_type: e.target.value as IAMProviderType,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none disabled:opacity-60"
                  >
                    <option value="aws_iam">AWS IAM</option>
                    <option value="gcp_iam">GCP IAM</option>
                    <option value="kubernetes">Kubernetes RBAC</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="iam-org"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Organization *
                  </label>
                  <select
                    id="iam-org"
                    value={iamForm.organization_id}
                    disabled={Boolean(editingIam)}
                    onChange={(e) =>
                      setIamForm((p) => ({
                        ...p,
                        organization_id: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none disabled:opacity-60"
                  >
                    <option value="">Select organization</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={String(org.id)}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="iam-description"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Description
                  </label>
                  <input
                    id="iam-description"
                    type="text"
                    value={iamForm.description}
                    onChange={(e) =>
                      setIamForm((p) => ({ ...p, description: e.target.value }))
                    }
                    placeholder="IAM directory synchronization"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-white">
                    {IAM_PROVIDER_LABELS[iamForm.provider_type]} Settings
                  </h4>
                  {editingIam && iamHasSavedSecret && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                      <ShieldCheck className="w-3 h-3" />
                      Credentials configured
                    </span>
                  )}
                </div>

                {iamForm.provider_type === "aws_iam" && (
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="iam-aws-region"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        AWS Region
                      </label>
                      <input
                        id="iam-aws-region"
                        type="text"
                        value={iamForm.aws_region}
                        onChange={(e) =>
                          setIamForm((p) => ({
                            ...p,
                            aws_region: e.target.value,
                          }))
                        }
                        placeholder="us-east-1"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="iam-aws-role-arn"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        Role ARN (optional)
                      </label>
                      <input
                        id="iam-aws-role-arn"
                        type="text"
                        value={iamForm.aws_role_arn}
                        onChange={(e) =>
                          setIamForm((p) => ({
                            ...p,
                            aws_role_arn: e.target.value,
                          }))
                        }
                        placeholder="arn:aws:iam::123456789012:role/ElderIAM"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="iam-aws-access-key"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        Access Key ID
                      </label>
                      <input
                        id="iam-aws-access-key"
                        type="text"
                        value={iamForm.aws_access_key_id}
                        onChange={(e) =>
                          setIamForm((p) => ({
                            ...p,
                            aws_access_key_id: e.target.value,
                          }))
                        }
                        placeholder={
                          editingIam && iamHasSavedSecret
                            ? "Leave blank to keep existing secret"
                            : "AKIA..."
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="iam-aws-secret-key"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        Secret Access Key
                      </label>
                      <input
                        id="iam-aws-secret-key"
                        type="password"
                        value={iamForm.aws_secret_access_key}
                        onChange={(e) =>
                          setIamForm((p) => ({
                            ...p,
                            aws_secret_access_key: e.target.value,
                          }))
                        }
                        placeholder={
                          editingIam && iamHasSavedSecret
                            ? "Leave blank to keep existing secret"
                            : "Secret key"
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>
                )}

                {iamForm.provider_type === "gcp_iam" && (
                  <div className="space-y-4">
                    <div>
                      <label
                        htmlFor="iam-gcp-project-id"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        GCP Project ID *
                      </label>
                      <input
                        id="iam-gcp-project-id"
                        type="text"
                        value={iamForm.gcp_project_id}
                        onChange={(e) =>
                          setIamForm((p) => ({
                            ...p,
                            gcp_project_id: e.target.value,
                          }))
                        }
                        placeholder="my-gcp-project-123"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label
                          htmlFor="iam-gcp-sa-json"
                          className="block text-sm font-medium text-slate-300"
                        >
                          Service Account JSON
                        </label>
                        <div>
                          <input
                            ref={iamGcpFileRef}
                            id="iam-gcp-json-upload"
                            type="file"
                            accept=".json,application/json"
                            aria-label="Upload GCP IAM service account JSON"
                            className="hidden"
                            onChange={handleIamGcpFileUpload}
                          />
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            onClick={() => iamGcpFileRef.current?.click()}
                          >
                            <Upload className="w-3.5 h-3.5 mr-1.5" />
                            Upload .json file
                          </Button>
                        </div>
                      </div>
                      <textarea
                        id="iam-gcp-sa-json"
                        rows={4}
                        value={iamForm.gcp_credentials_json}
                        onChange={(e) =>
                          setIamForm((p) => ({
                            ...p,
                            gcp_credentials_json: e.target.value,
                          }))
                        }
                        placeholder={
                          editingIam && iamHasSavedSecret
                            ? "Leave blank to keep existing secret"
                            : "Paste your Google Cloud service account JSON key object (project_id, client_email, private_key)"
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>
                )}

                {iamForm.provider_type === "kubernetes" && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <div>
                        <label
                          htmlFor="iam-k8s-mode"
                          className="block text-sm font-medium text-yellow-500 mb-1.5"
                        >
                          Connection Mode
                        </label>
                        <select
                          id="iam-k8s-mode"
                          value={iamForm.k8s_mode}
                          onChange={(e) =>
                            setIamForm((p) => ({
                              ...p,
                              k8s_mode: e.target.value as
                                | "kubeconfig"
                                | "in_cluster",
                            }))
                          }
                          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                        >
                          <option value="kubeconfig">Kubeconfig File</option>
                          <option value="in_cluster">
                            In-Cluster Service Account
                          </option>
                        </select>
                      </div>

                      <div>
                        <label
                          htmlFor="iam-k8s-namespace"
                          className="block text-sm font-medium text-slate-300 mb-1.5"
                        >
                          Namespace
                        </label>
                        <input
                          id="iam-k8s-namespace"
                          type="text"
                          value={iamForm.k8s_namespace}
                          onChange={(e) =>
                            setIamForm((p) => ({
                              ...p,
                              k8s_namespace: e.target.value,
                            }))
                          }
                          placeholder="default"
                          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                        />
                      </div>
                    </div>

                    {iamForm.k8s_mode === "kubeconfig" && (
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="iam-k8s-kubeconfig-path"
                            className="block text-sm font-medium text-slate-300 mb-1.5"
                          >
                            Kubeconfig Path
                          </label>
                          <input
                            id="iam-k8s-kubeconfig-path"
                            type="text"
                            value={iamForm.k8s_kubeconfig_path}
                            onChange={(e) =>
                              setIamForm((p) => ({
                                ...p,
                                k8s_kubeconfig_path: e.target.value,
                              }))
                            }
                            placeholder="/etc/elder/kubeconfig"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor="iam-k8s-context"
                            className="block text-sm font-medium text-slate-300 mb-1.5"
                          >
                            Context (optional)
                          </label>
                          <input
                            id="iam-k8s-context"
                            type="text"
                            value={iamForm.k8s_context}
                            onChange={(e) =>
                              setIamForm((p) => ({
                                ...p,
                                k8s_context: e.target.value,
                              }))
                            }
                            placeholder="prod-cluster"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {editingIam && (
                <label
                  htmlFor="iam-enabled"
                  className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                >
                  <input
                    id="iam-enabled"
                    type="checkbox"
                    checked={iamForm.enabled}
                    onChange={(e) =>
                      setIamForm((p) => ({ ...p, enabled: e.target.checked }))
                    }
                    className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                  />
                  Provider Enabled
                </label>
              )}

              <div className="flex justify-end gap-3 border-t border-slate-800 pt-4">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowIamModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  isLoading={
                    createIamMutation.isPending || updateIamMutation.isPending
                  }
                >
                  {editingIam ? "Save Changes" : "Save Provider"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
