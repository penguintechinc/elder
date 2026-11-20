import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cloud,
  Code2,
  History,
  Play,
  PlugZap,
  Plus,
  Settings,
  ShieldCheck,
  Trash2,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import toast from "react-hot-toast";
import api, {
  type CloudDiscoveryProvider,
  type DiscoveryJobItem,
  type DiscoveryJobTestResult,
} from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent, CardHeader } from "@/components/Card";
import { confirmDelete } from "@/lib/confirmActions";
import DiscoveryHistoryModal from "./DiscoveryHistoryModal";
import {
  SCHEDULE_PRESETS,
  extractOrganizations,
  formatScheduleInterval,
  getApiErrorMessage,
  isSecretConfigured,
  readUploadedTextFile,
  resolveScheduleIntervalNumber,
  resolveScheduleState,
} from "./shared";

const CLOUD_PROVIDERS: Array<{
  value: CloudDiscoveryProvider;
  label: string;
}> = [
  { value: "aws", label: "AWS" },
  { value: "gcp", label: "GCP" },
  { value: "azure", label: "Azure" },
  { value: "kubernetes", label: "Kubernetes" },
  { value: "vultr", label: "Vultr" },
];

const CLOUD_PROVIDER_SET = new Set<string>(CLOUD_PROVIDERS.map((p) => p.value));

const AWS_SERVICES = ["ec2", "rds", "s3", "lambda"] as const;

type AWSAuthMethod =
  | "static_credentials"
  | "iam_role"
  | "web_identity"
  | "assume_role";

interface CloudFormState {
  name: string;
  provider: CloudDiscoveryProvider;
  organization_id: string;
  schedule_preset: string;
  schedule_custom: string;
  description: string;
  enabled: boolean;
  // AWS
  aws_auth_method: AWSAuthMethod;
  aws_region: string;
  aws_access_key_id: string;
  aws_secret_access_key: string;
  aws_role_arn: string;
  aws_web_identity_token_file: string;
  aws_role_session_name: string;
  aws_assume_role_arn: string;
  aws_external_id: string;
  aws_services: string[];
  // GCP
  gcp_project_id: string;
  gcp_auth_mode: "service_account_json" | "adc";
  gcp_credentials_json: string;
  // Azure
  azure_subscription_id: string;
  azure_auth_mode: "service_principal" | "default_credential";
  azure_tenant_id: string;
  azure_client_id: string;
  azure_client_secret: string;
  // Kubernetes
  k8s_mode: "kubeconfig" | "in_cluster";
  k8s_kubeconfig_path: string;
  k8s_context: string;
  k8s_namespace: string;
  // Vultr
  vultr_api_key: string;
}

const INITIAL_CLOUD_FORM: CloudFormState = {
  name: "",
  provider: "aws",
  organization_id: "",
  schedule_preset: "3600",
  schedule_custom: "3600",
  description: "",
  enabled: true,
  aws_auth_method: "static_credentials",
  aws_region: "us-east-1",
  aws_access_key_id: "",
  aws_secret_access_key: "",
  aws_role_arn: "",
  aws_web_identity_token_file: "",
  aws_role_session_name: "",
  aws_assume_role_arn: "",
  aws_external_id: "",
  aws_services: ["ec2", "rds", "s3"],
  gcp_project_id: "",
  gcp_auth_mode: "service_account_json",
  gcp_credentials_json: "",
  azure_subscription_id: "",
  azure_auth_mode: "service_principal",
  azure_tenant_id: "",
  azure_client_id: "",
  azure_client_secret: "",
  k8s_mode: "kubeconfig",
  k8s_kubeconfig_path: "",
  k8s_context: "",
  k8s_namespace: "default",
  vultr_api_key: "",
};

function inferAWSAuthMethod(cfg: Record<string, unknown>): AWSAuthMethod {
  const explicit = String(cfg.auth_method || "").toLowerCase();
  if (
    explicit === "static_credentials" ||
    explicit === "iam_role" ||
    explicit === "web_identity" ||
    explicit === "assume_role"
  ) {
    return explicit;
  }
  if (cfg.assume_role_arn) return "assume_role";
  if (cfg.role_arn || cfg.web_identity_token_file) return "web_identity";
  if (cfg.access_key_id || cfg.secret_access_key) return "static_credentials";
  return "iam_role";
}

function buildConfigFromForm(
  form: CloudFormState,
  isEdit: boolean,
): Record<string, unknown> {
  const cfg: Record<string, unknown> = {};
  if (form.provider === "aws") {
    cfg.auth_method = form.aws_auth_method;
    cfg.region = form.aws_region.trim() || "us-east-1";
    cfg.services = form.aws_services;
    if (
      form.aws_auth_method === "static_credentials" ||
      form.aws_auth_method === "assume_role"
    ) {
      if (form.aws_access_key_id.trim()) {
        cfg.access_key_id = form.aws_access_key_id.trim();
      }
      if (form.aws_secret_access_key.trim()) {
        cfg.secret_access_key = form.aws_secret_access_key.trim();
      }
    }
    if (form.aws_auth_method === "web_identity") {
      if (form.aws_role_arn.trim()) cfg.role_arn = form.aws_role_arn.trim();
      if (form.aws_web_identity_token_file.trim()) {
        cfg.web_identity_token_file = form.aws_web_identity_token_file.trim();
      }
      if (form.aws_role_session_name.trim()) {
        cfg.role_session_name = form.aws_role_session_name.trim();
      }
    }
    if (form.aws_auth_method === "assume_role") {
      if (form.aws_assume_role_arn.trim()) {
        cfg.assume_role_arn = form.aws_assume_role_arn.trim();
      }
      if (form.aws_external_id.trim()) {
        cfg.external_id = form.aws_external_id.trim();
      }
    }
  } else if (form.provider === "gcp") {
    cfg.project_id = form.gcp_project_id.trim();
    cfg.auth_mode = form.gcp_auth_mode;
    if (
      form.gcp_auth_mode === "service_account_json" &&
      form.gcp_credentials_json.trim()
    ) {
      cfg.credentials_json = form.gcp_credentials_json.trim();
    }
  } else if (form.provider === "azure") {
    cfg.subscription_id = form.azure_subscription_id.trim();
    cfg.auth_mode = form.azure_auth_mode;
    if (form.azure_auth_mode === "service_principal") {
      if (form.azure_tenant_id.trim()) {
        cfg.tenant_id = form.azure_tenant_id.trim();
      }
      if (form.azure_client_id.trim()) {
        cfg.client_id = form.azure_client_id.trim();
      }
      if (form.azure_client_secret.trim()) {
        cfg.client_secret = form.azure_client_secret.trim();
      }
    }
  } else if (form.provider === "kubernetes") {
    cfg.in_cluster = form.k8s_mode === "in_cluster";
    cfg.namespace = form.k8s_namespace.trim() || "default";
    if (form.k8s_mode === "kubeconfig") {
      if (form.k8s_kubeconfig_path.trim()) {
        cfg.kubeconfig_path = form.k8s_kubeconfig_path.trim();
      }
      if (form.k8s_context.trim()) {
        cfg.context = form.k8s_context.trim();
      }
    }
  } else if (form.provider === "vultr") {
    if (form.vultr_api_key.trim()) {
      cfg.api_key = form.vultr_api_key.trim();
    } else if (!isEdit) {
      cfg.api_key = "";
    }
  }
  return cfg;
}

function hydrateFormFromJob(
  job: DiscoveryJobItem,
  fallbackOrgId: string,
): CloudFormState {
  const cfg = (job.config_json || {}) as Record<string, unknown>;
  const schedule = resolveScheduleState(job.schedule_interval);
  const provider = CLOUD_PROVIDER_SET.has(job.provider)
    ? (job.provider as CloudDiscoveryProvider)
    : "aws";
  const orgId =
    job.organization_id ?? cfg._organization_id ?? fallbackOrgId ?? "";

  return {
    ...INITIAL_CLOUD_FORM,
    name: job.name,
    provider,
    organization_id: orgId !== "" ? String(orgId) : "",
    schedule_preset: schedule.preset,
    schedule_custom: schedule.customSeconds,
    description: String(job.description ?? cfg._description ?? ""),
    enabled: job.enabled,
    aws_auth_method: inferAWSAuthMethod(cfg),
    aws_region: String(cfg.region || "us-east-1"),
    aws_access_key_id: "",
    aws_secret_access_key: "",
    aws_role_arn: String(cfg.role_arn || ""),
    aws_web_identity_token_file: String(cfg.web_identity_token_file || ""),
    aws_role_session_name: String(cfg.role_session_name || ""),
    aws_assume_role_arn: String(cfg.assume_role_arn || ""),
    aws_external_id: String(cfg.external_id || ""),
    aws_services: Array.isArray(cfg.services)
      ? cfg.services.map((s) => String(s))
      : ["ec2", "rds", "s3"],
    gcp_project_id: String(cfg.project_id || ""),
    gcp_auth_mode:
      String(cfg.auth_mode || "") === "adc"
        ? "adc"
        : cfg.credentials_json || !cfg.auth_mode
          ? "service_account_json"
          : "adc",
    gcp_credentials_json: "",
    azure_subscription_id: String(cfg.subscription_id || ""),
    azure_auth_mode:
      String(cfg.auth_mode || "") === "default_credential"
        ? "default_credential"
        : "service_principal",
    azure_tenant_id: String(cfg.tenant_id || ""),
    azure_client_id: String(cfg.client_id || ""),
    azure_client_secret: "",
    k8s_mode: cfg.in_cluster ? "in_cluster" : "kubeconfig",
    k8s_kubeconfig_path: String(cfg.kubeconfig_path || ""),
    k8s_context: String(cfg.context || ""),
    k8s_namespace: String(cfg.namespace || "default"),
    vultr_api_key: "",
  };
}

function summarizeCloudJob(job: DiscoveryJobItem): string[] {
  const cfg = (job.config_json || {}) as Record<string, unknown>;
  const parts: string[] = [];
  if (job.provider === "aws") {
    if (cfg.region) parts.push(`Region: ${String(cfg.region)}`);
    parts.push(`Auth: ${inferAWSAuthMethod(cfg)}`);
    if (Array.isArray(cfg.services) && cfg.services.length > 0) {
      parts.push(`Services: ${cfg.services.join(", ")}`);
    }
  } else if (job.provider === "gcp") {
    if (cfg.project_id) parts.push(`Project: ${String(cfg.project_id)}`);
    parts.push(cfg.auth_mode === "adc" ? "Auth: ADC" : "Auth: Service Account");
  } else if (job.provider === "azure") {
    if (cfg.subscription_id) {
      parts.push(`Subscription: ${String(cfg.subscription_id)}`);
    }
    parts.push(
      cfg.auth_mode === "default_credential"
        ? "Auth: Default Credential"
        : "Auth: Service Principal",
    );
  } else if (job.provider === "kubernetes") {
    parts.push(cfg.in_cluster ? "Mode: In-Cluster" : "Mode: Kubeconfig");
    parts.push(`Namespace: ${String(cfg.namespace || "default")}`);
    if (cfg.context) parts.push(`Context: ${String(cfg.context)}`);
  } else if (job.provider === "vultr") {
    parts.push("Vultr API v2");
  }
  return parts;
}

export interface CloudProfilesPanelProps {
  openCreateRequest?: {
    nonce: number;
    provider?: CloudDiscoveryProvider;
  };
}

export default function CloudProfilesPanel({
  openCreateRequest,
}: CloudProfilesPanelProps) {
  const queryClient = useQueryClient();
  const [providerFilter, setProviderFilter] = useState<string>("all");
  const [showModal, setShowModal] = useState(false);
  const [editingJob, setEditingJob] = useState<DiscoveryJobItem | null>(null);
  const [formState, setFormState] =
    useState<CloudFormState>(INITIAL_CLOUD_FORM);
  const [rawJsonMode, setRawJsonMode] = useState(false);
  const [rawJsonText, setRawJsonText] = useState("{}");
  const [formError, setFormError] = useState<string | null>(null);
  const [historyJob, setHistoryJob] = useState<DiscoveryJobItem | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testResults, setTestResults] = useState<
    Record<number, DiscoveryJobTestResult>
  >({});
  const gcpFileInputRef = useRef<HTMLInputElement | null>(null);

  const { data: jobsData, isLoading } = useQuery({
    queryKey: ["discoveryJobs"],
    queryFn: () => api.getDiscoveryJobs(),
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

  const cloudJobs = useMemo(
    () =>
      (jobsData?.jobs ?? []).filter((job) =>
        CLOUD_PROVIDER_SET.has(job.provider),
      ),
    [jobsData],
  );

  const filteredJobs = useMemo(() => {
    if (providerFilter === "all") return cloudJobs;
    return cloudJobs.filter((job) => job.provider === providerFilter);
  }, [cloudJobs, providerFilter]);

  const handleOpenCreate = useCallback(
    (provider: CloudDiscoveryProvider = "aws") => {
      setEditingJob(null);
      setFormState({
        ...INITIAL_CLOUD_FORM,
        provider,
        organization_id: defaultOrgId,
      });
      setRawJsonMode(false);
      setRawJsonText("{}");
      setFormError(null);
      setShowModal(true);
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
      handleOpenCreate(openCreateRequest.provider ?? "aws");
    }
  }, [openCreateRequest, handleOpenCreate]);

  const handleOpenEdit = (job: DiscoveryJobItem) => {
    const hydrated = hydrateFormFromJob(job, defaultOrgId);
    setEditingJob(job);
    setFormState(hydrated);
    const cleanedCfg = {
      ...((job.config_json || {}) as Record<string, unknown>),
    };
    delete cleanedCfg._organization_id;
    delete cleanedCfg._description;
    setRawJsonText(JSON.stringify(cleanedCfg, null, 2));
    setRawJsonMode(false);
    setFormError(null);
    setShowModal(true);
  };

  const handleToggleRawJson = () => {
    setFormError(null);
    if (!rawJsonMode) {
      const built = buildConfigFromForm(formState, editingJob !== null);
      setRawJsonText(JSON.stringify(built, null, 2));
      setRawJsonMode(true);
    } else {
      try {
        const parsed = JSON.parse(rawJsonText || "{}") as Record<
          string,
          unknown
        >;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setFormError("Raw configuration must be a JSON object.");
          return;
        }
        setRawJsonMode(false);
      } catch {
        setFormError("Invalid JSON configuration.");
      }
    }
  };

  const createMutation = useMutation({
    mutationFn: (payload: {
      name: string;
      provider_type: string;
      organization_id: number;
      config: Record<string, unknown>;
      schedule_interval: number;
      description?: string;
    }) => api.createDiscoveryJob(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryJobs"] });
      toast.success("Cloud integration profile created");
      setShowModal(false);
    },
    onError: (err) => {
      setFormError(getApiErrorMessage(err, "Failed to create cloud profile"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: {
        name?: string;
        config?: Record<string, unknown>;
        schedule_interval?: number;
        description?: string;
        enabled?: boolean;
      };
    }) => api.updateDiscoveryJob(id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryJobs"] });
      toast.success("Cloud integration profile updated");
      setShowModal(false);
      setEditingJob(null);
    },
    onError: (err) => {
      setFormError(getApiErrorMessage(err, "Failed to update cloud profile"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryJobs"] });
      toast.success("Cloud integration profile deleted");
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Failed to delete cloud profile"));
    },
  });

  const runMutation = useMutation({
    mutationFn: (id: number) => api.runDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryHistory"] });
      toast.success("Discovery run queued");
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Failed to run discovery profile"));
    },
  });

  const handleTestConnection = async (job: DiscoveryJobItem) => {
    setTestingId(job.id);
    try {
      const result = await api.testDiscoveryJob(job.id);
      setTestResults((prev) => ({ ...prev, [job.id]: result }));
      if (result.success) {
        toast.success(`Connection verified for "${job.name}"`);
      } else {
        toast.error(result.error || `Connection test failed for "${job.name}"`);
      }
    } catch (err) {
      const message = getApiErrorMessage(err, "Connection test failed");
      setTestResults((prev) => ({
        ...prev,
        [job.id]: {
          job_id: job.id,
          success: false,
          tested_at: new Date().toISOString(),
          error: message,
        },
      }));
      toast.error(message);
    } finally {
      setTestingId(null);
    }
  };

  const handleGcpFileUpload = async (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const text = await readUploadedTextFile(file);
      setFormState((prev) => ({ ...prev, gcp_credentials_json: text.trim() }));
      setFormError(null);
    } catch {
      setFormError("Unable to read the selected JSON credentials file.");
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!formState.name.trim()) {
      setFormError("Profile name is required.");
      return;
    }

    const orgIdNum = Number(formState.organization_id);
    if (!editingJob && (!orgIdNum || Number.isNaN(orgIdNum))) {
      setFormError("Organization is required.");
      return;
    }

    let configPayload: Record<string, unknown>;
    if (rawJsonMode) {
      try {
        const parsed = JSON.parse(rawJsonText || "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setFormError("Raw configuration must be a JSON object.");
          return;
        }
        configPayload = parsed as Record<string, unknown>;
      } catch {
        setFormError("Invalid JSON configuration.");
        return;
      }
    } else {
      if (formState.provider === "gcp" && !formState.gcp_project_id.trim()) {
        setFormError("GCP Project ID is required.");
        return;
      }
      if (
        formState.provider === "azure" &&
        !formState.azure_subscription_id.trim()
      ) {
        setFormError("Azure Subscription ID is required.");
        return;
      }
      if (
        formState.provider === "vultr" &&
        !editingJob &&
        !formState.vultr_api_key.trim()
      ) {
        setFormError("Vultr API key is required.");
        return;
      }
      configPayload = buildConfigFromForm(formState, editingJob !== null);
    }

    const scheduleInterval = resolveScheduleIntervalNumber(
      formState.schedule_preset,
      formState.schedule_custom,
    );

    if (editingJob) {
      updateMutation.mutate({
        id: editingJob.id,
        payload: {
          name: formState.name.trim(),
          config: configPayload,
          schedule_interval: scheduleInterval,
          description: formState.description.trim(),
          enabled: formState.enabled,
        },
      });
    } else {
      createMutation.mutate({
        name: formState.name.trim(),
        provider_type: formState.provider,
        organization_id: orgIdNum,
        config: configPayload,
        schedule_interval: scheduleInterval,
        description: formState.description.trim() || undefined,
      });
    }
  };

  const hasSavedSecret = useMemo(
    () =>
      isSecretConfigured(editingJob?.config_json, [
        "access_key_id",
        "secret_access_key",
        "credentials_json",
        "client_secret",
        "kubeconfig",
        "api_key",
        "token",
      ]),
    [editingJob],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white">
            Cloud & Infrastructure Profiles
          </h2>
          <p className="mt-0.5 text-sm text-slate-400">
            Configure typed authentication profiles for AWS, GCP, Azure,
            Kubernetes, and Vultr discovery
          </p>
        </div>
        <Button
          onClick={() => handleOpenCreate("aws")}
          data-testid="add-cloud-profile-btn"
        >
          <Plus className="w-4 h-4 mr-2" />
          New Cloud Profile
        </Button>
      </div>

      {/* Provider Filter Pills */}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setProviderFilter("all")}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
            providerFilter === "all"
              ? "bg-yellow-500/15 border-yellow-500/60 text-yellow-300"
              : "bg-slate-900 border-slate-700 text-slate-300 hover:border-slate-600"
          }`}
        >
          All ({cloudJobs.length})
        </button>
        {CLOUD_PROVIDERS.map((p) => {
          const count = cloudJobs.filter((j) => j.provider === p.value).length;
          return (
            <button
              key={p.value}
              type="button"
              onClick={() => setProviderFilter(p.value)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                providerFilter === p.value
                  ? "bg-yellow-500/15 border-yellow-500/60 text-yellow-300"
                  : "bg-slate-900 border-slate-700 text-slate-300 hover:border-slate-600"
              }`}
            >
              {p.label} ({count})
            </button>
          );
        })}
      </div>

      {/* Profiles List */}
      {isLoading ? (
        <Card>
          <CardContent className="p-8 text-center text-slate-400">
            Loading cloud integration profiles...
          </CardContent>
        </Card>
      ) : filteredJobs.length === 0 ? (
        <Card>
          <CardContent className="p-10 text-center space-y-4">
            <Cloud className="w-10 h-10 text-yellow-500 mx-auto" />
            <div>
              <h3 className="text-lg font-semibold text-white">
                No cloud integration profiles yet
              </h3>
              <p className="text-sm text-slate-400 mt-1 max-w-xl mx-auto">
                Add typed credential profiles for AWS, GCP, Azure, Kubernetes,
                or Vultr to discover infrastructure resources automatically.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              {CLOUD_PROVIDERS.map((p) => (
                <Button
                  key={p.value}
                  size="sm"
                  variant="secondary"
                  onClick={() => handleOpenCreate(p.value)}
                >
                  <Plus className="w-3.5 h-3.5 mr-1.5" />
                  Connect {p.label}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredJobs.map((job) => {
            const cfg = (job.config_json || {}) as Record<string, unknown>;
            const orgId = job.organization_id ?? cfg._organization_id;
            const orgName =
              orgId != null ? orgMap.get(Number(orgId)) : undefined;
            const summaryItems = summarizeCloudJob(job);
            const hasCreds = isSecretConfigured(cfg, [
              "access_key_id",
              "secret_access_key",
              "credentials_json",
              "client_secret",
              "kubeconfig",
              "api_key",
              "token",
            ]);
            const testResult = testResults[job.id];
            const description = job.description ?? (cfg._description as string);

            return (
              <Card key={job.id}>
                <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-semibold text-white">
                        {job.name}
                      </h3>
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-slate-800 text-yellow-400 border border-slate-700 uppercase">
                        {job.provider}
                      </span>
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                          job.enabled
                            ? "bg-emerald-950/60 text-emerald-300 border-emerald-700/60"
                            : "bg-slate-800 text-slate-400 border-slate-700"
                        }`}
                      >
                        {job.enabled ? "Enabled" : "Paused"}
                      </span>
                      {hasCreds && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                          <ShieldCheck className="w-3 h-3" />
                          Credentials configured
                        </span>
                      )}
                    </div>

                    <div className="text-sm text-slate-400 flex flex-wrap items-center gap-x-4 gap-y-1">
                      {summaryItems.map((item) => (
                        <span key={item}>{item}</span>
                      ))}
                      {orgId != null && (
                        <span>
                          Org:{" "}
                          <span className="text-slate-200">
                            {orgName ?? `#${String(orgId)}`}
                          </span>
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1">
                        <Clock className="w-3.5 h-3.5" />
                        {formatScheduleInterval(job.schedule_interval)}
                      </span>
                      {job.last_run_at && (
                        <span>
                          Last run {new Date(job.last_run_at).toLocaleString()}
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
                      onClick={() => handleTestConnection(job)}
                      isLoading={testingId === job.id}
                    >
                      <PlugZap className="w-3.5 h-3.5 mr-1.5" />
                      Test Connection
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => runMutation.mutate(job.id)}
                    >
                      <Play className="w-3.5 h-3.5 mr-1.5" />
                      Run Now
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setHistoryJob(job)}
                    >
                      <History className="w-3.5 h-3.5 mr-1.5" />
                      History
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleOpenEdit(job)}
                      aria-label={`Edit ${job.name}`}
                    >
                      <Settings className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        updateMutation.mutate({
                          id: job.id,
                          payload: { enabled: !job.enabled },
                        })
                      }
                    >
                      {job.enabled ? "Pause" : "Enable"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        confirmDelete(job.name, () =>
                          deleteMutation.mutate(job.id),
                        )
                      }
                      aria-label={`Delete ${job.name}`}
                    >
                      <Trash2 className="w-4 h-4 text-red-400" />
                    </Button>
                  </div>
                </CardHeader>

                {testResult && (
                  <CardContent className="pt-0 pb-4">
                    <div
                      className={`rounded-lg border px-3 py-2 text-xs flex items-center gap-2 ${
                        testResult.success
                          ? "border-emerald-700/60 bg-emerald-950/30 text-emerald-200"
                          : "border-red-700/60 bg-red-950/30 text-red-200"
                      }`}
                    >
                      {testResult.success ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                      ) : (
                        <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                      )}
                      <span>
                        {testResult.success
                          ? `Connection verified${
                              testResult.auth_method
                                ? ` via ${testResult.auth_method}`
                                : ""
                            }${
                              testResult.identity?.arn
                                ? ` (${testResult.identity.arn})`
                                : testResult.identity?.account
                                  ? ` (Account ${testResult.identity.account})`
                                  : ""
                            }`
                          : testResult.error || "Connection test failed"}
                      </span>
                    </div>
                  </CardContent>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* Typed Create / Edit Cloud Profile Modal */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cloud-modal-title"
        >
          <div className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <h3
                id="cloud-modal-title"
                className="text-xl font-semibold text-white"
              >
                {editingJob
                  ? `Edit ${editingJob.name}`
                  : "New Cloud Integration Profile"}
              </h3>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={handleToggleRawJson}
                >
                  <Code2 className="w-3.5 h-3.5 mr-1.5" />
                  {rawJsonMode ? "Typed Form" : "Edit as Raw JSON"}
                </Button>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  aria-label="Close cloud modal"
                  className="text-slate-400 hover:text-white"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
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
                    htmlFor="cloud-name"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Profile Name *
                  </label>
                  <input
                    id="cloud-name"
                    type="text"
                    required
                    value={formState.name}
                    onChange={(e) =>
                      setFormState((p) => ({ ...p, name: e.target.value }))
                    }
                    placeholder="e.g., Production AWS Account"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="cloud-provider"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Cloud Provider *
                  </label>
                  <select
                    id="cloud-provider"
                    value={formState.provider}
                    disabled={Boolean(editingJob)}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        provider: e.target.value as CloudDiscoveryProvider,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none disabled:opacity-60"
                  >
                    {CLOUD_PROVIDERS.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="cloud-org"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Organization *
                  </label>
                  <select
                    id="cloud-org"
                    value={formState.organization_id}
                    disabled={Boolean(editingJob)}
                    onChange={(e) =>
                      setFormState((p) => ({
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
                    htmlFor="cloud-schedule"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Schedule Interval
                  </label>
                  <select
                    id="cloud-schedule"
                    value={formState.schedule_preset}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        schedule_preset: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  >
                    {SCHEDULE_PRESETS.map((preset) => (
                      <option key={preset.value} value={preset.value}>
                        {preset.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {formState.schedule_preset === "custom" && (
                <div>
                  <label
                    htmlFor="cloud-schedule-custom"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Custom Interval (seconds)
                  </label>
                  <input
                    id="cloud-schedule-custom"
                    type="number"
                    min={0}
                    value={formState.schedule_custom}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        schedule_custom: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              )}

              <div>
                <label
                  htmlFor="cloud-description"
                  className="block text-sm font-medium text-slate-300 mb-1.5"
                >
                  Description
                </label>
                <input
                  id="cloud-description"
                  type="text"
                  value={formState.description}
                  onChange={(e) =>
                    setFormState((p) => ({
                      ...p,
                      description: e.target.value,
                    }))
                  }
                  placeholder="Optional notes about scope or account"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                />
              </div>

              {rawJsonMode ? (
                <div>
                  <label
                    htmlFor="cloud-raw-json"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Raw Provider Configuration (JSON)
                  </label>
                  <textarea
                    id="cloud-raw-json"
                    rows={8}
                    value={rawJsonText}
                    onChange={(e) => setRawJsonText(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              ) : (
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                  <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-white">
                      {
                        CLOUD_PROVIDERS.find(
                          (p) => p.value === formState.provider,
                        )?.label
                      }{" "}
                      Credentials & Settings
                    </h4>
                    {editingJob && hasSavedSecret && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                        <ShieldCheck className="w-3 h-3" />
                        Credentials configured
                      </span>
                    )}
                  </div>

                  {/* AWS Fields */}
                  {formState.provider === "aws" && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="aws-auth-method"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Authentication Method
                          </label>
                          <select
                            id="aws-auth-method"
                            value={formState.aws_auth_method}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                aws_auth_method: e.target
                                  .value as AWSAuthMethod,
                              }))
                            }
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                          >
                            <option value="static_credentials">
                              Static Access Keys
                            </option>
                            <option value="iam_role">
                              IAM Role / Environment
                            </option>
                            <option value="web_identity">
                              Web Identity / OIDC (IRSA)
                            </option>
                            <option value="assume_role">
                              Cross-Account Assume Role
                            </option>
                          </select>
                        </div>

                        <div>
                          <label
                            htmlFor="aws-region"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            AWS Region *
                          </label>
                          <input
                            id="aws-region"
                            type="text"
                            value={formState.aws_region}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                aws_region: e.target.value,
                              }))
                            }
                            placeholder="us-east-1"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                          />
                        </div>
                      </div>

                      {(formState.aws_auth_method === "static_credentials" ||
                        formState.aws_auth_method === "assume_role") && (
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                          <div>
                            <label
                              htmlFor="aws-access-key-id"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Access Key ID
                            </label>
                            <input
                              id="aws-access-key-id"
                              type="text"
                              value={formState.aws_access_key_id}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  aws_access_key_id: e.target.value,
                                }))
                              }
                              placeholder={
                                editingJob && hasSavedSecret
                                  ? "Leave blank to keep existing secret"
                                  : "AKIA..."
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>

                          <div>
                            <label
                              htmlFor="aws-secret-access-key"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Secret Access Key
                            </label>
                            <input
                              id="aws-secret-access-key"
                              type="password"
                              value={formState.aws_secret_access_key}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  aws_secret_access_key: e.target.value,
                                }))
                              }
                              placeholder={
                                editingJob && hasSavedSecret
                                  ? "Leave blank to keep existing secret"
                                  : "Secret key"
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>
                        </div>
                      )}

                      {formState.aws_auth_method === "web_identity" && (
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                          <div>
                            <label
                              htmlFor="aws-role-arn"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Role ARN
                            </label>
                            <input
                              id="aws-role-arn"
                              type="text"
                              value={formState.aws_role_arn}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  aws_role_arn: e.target.value,
                                }))
                              }
                              placeholder="arn:aws:iam::123456789012:role/ElderDiscovery"
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>

                          <div>
                            <label
                              htmlFor="aws-web-identity-token-file"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Web Identity Token File
                            </label>
                            <input
                              id="aws-web-identity-token-file"
                              type="text"
                              value={formState.aws_web_identity_token_file}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  aws_web_identity_token_file: e.target.value,
                                }))
                              }
                              placeholder="/var/run/secrets/eks.amazonaws.com/serviceaccount/token"
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>
                        </div>
                      )}

                      {formState.aws_auth_method === "assume_role" && (
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                          <div>
                            <label
                              htmlFor="aws-assume-role-arn"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Assume Role ARN
                            </label>
                            <input
                              id="aws-assume-role-arn"
                              type="text"
                              value={formState.aws_assume_role_arn}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  aws_assume_role_arn: e.target.value,
                                }))
                              }
                              placeholder="arn:aws:iam::123456789012:role/TargetRole"
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>

                          <div>
                            <label
                              htmlFor="aws-external-id"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              External ID (optional)
                            </label>
                            <input
                              id="aws-external-id"
                              type="text"
                              value={formState.aws_external_id}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  aws_external_id: e.target.value,
                                }))
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                            />
                          </div>
                        </div>
                      )}

                      <div>
                        <p className="text-sm font-medium text-slate-300 mb-2">
                          Discovered AWS Services
                        </p>
                        <div className="flex flex-wrap gap-3">
                          {AWS_SERVICES.map((svc) => {
                            const checked =
                              formState.aws_services.includes(svc);
                            return (
                              <label
                                key={svc}
                                htmlFor={`aws-svc-${svc}`}
                                className="flex items-center gap-2 text-xs text-slate-200 bg-slate-900 border border-slate-700 rounded-md px-3 py-1.5 cursor-pointer"
                              >
                                <input
                                  id={`aws-svc-${svc}`}
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() =>
                                    setFormState((p) => ({
                                      ...p,
                                      aws_services: checked
                                        ? p.aws_services.filter(
                                            (s) => s !== svc,
                                          )
                                        : [...p.aws_services, svc],
                                    }))
                                  }
                                  className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                                />
                                <span className="uppercase">{svc}</span>
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* GCP Fields */}
                  {formState.provider === "gcp" && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="gcp-project-id"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            GCP Project ID *
                          </label>
                          <input
                            id="gcp-project-id"
                            type="text"
                            value={formState.gcp_project_id}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                gcp_project_id: e.target.value,
                              }))
                            }
                            placeholder="my-gcp-project-123"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor="gcp-auth-mode"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Authentication Mode
                          </label>
                          <select
                            id="gcp-auth-mode"
                            value={formState.gcp_auth_mode}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                gcp_auth_mode: e.target.value as
                                  | "service_account_json"
                                  | "adc",
                              }))
                            }
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                          >
                            <option value="service_account_json">
                              Service Account JSON
                            </option>
                            <option value="adc">
                              Application Default Credentials (ADC)
                            </option>
                          </select>
                        </div>
                      </div>

                      {formState.gcp_auth_mode === "service_account_json" && (
                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <label
                              htmlFor="gcp-credentials-json"
                              className="block text-sm font-medium text-slate-300"
                            >
                              Service Account JSON
                            </label>
                            <div>
                              <input
                                ref={gcpFileInputRef}
                                id="gcp-json-upload"
                                type="file"
                                accept=".json,application/json"
                                aria-label="Upload GCP service account JSON"
                                className="hidden"
                                onChange={handleGcpFileUpload}
                              />
                              <Button
                                type="button"
                                size="sm"
                                variant="secondary"
                                onClick={() => gcpFileInputRef.current?.click()}
                              >
                                <Upload className="w-3.5 h-3.5 mr-1.5" />
                                Upload .json file
                              </Button>
                            </div>
                          </div>
                          <textarea
                            id="gcp-credentials-json"
                            rows={4}
                            value={formState.gcp_credentials_json}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                gcp_credentials_json: e.target.value,
                              }))
                            }
                            placeholder={
                              editingJob && hasSavedSecret
                                ? "Leave blank to keep existing secret"
                                : '{"type": "service_account", "project_id": "..."}'
                            }
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>
                      )}
                    </div>
                  )}

                  {/* Azure Fields */}
                  {formState.provider === "azure" && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="azure-subscription-id"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Subscription ID *
                          </label>
                          <input
                            id="azure-subscription-id"
                            type="text"
                            value={formState.azure_subscription_id}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                azure_subscription_id: e.target.value,
                              }))
                            }
                            placeholder="00000000-0000-0000-0000-000000000000"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor="azure-auth-mode"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Authentication Mode
                          </label>
                          <select
                            id="azure-auth-mode"
                            value={formState.azure_auth_mode}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                azure_auth_mode: e.target.value as
                                  | "service_principal"
                                  | "default_credential",
                              }))
                            }
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                          >
                            <option value="service_principal">
                              Service Principal
                            </option>
                            <option value="default_credential">
                              Default Azure Credential / Managed Identity
                            </option>
                          </select>
                        </div>
                      </div>

                      {formState.azure_auth_mode === "service_principal" && (
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                          <div>
                            <label
                              htmlFor="azure-tenant-id"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Tenant ID
                            </label>
                            <input
                              id="azure-tenant-id"
                              type="text"
                              value={formState.azure_tenant_id}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  azure_tenant_id: e.target.value,
                                }))
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                            />
                          </div>

                          <div>
                            <label
                              htmlFor="azure-client-id"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Client ID
                            </label>
                            <input
                              id="azure-client-id"
                              type="text"
                              value={formState.azure_client_id}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  azure_client_id: e.target.value,
                                }))
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                            />
                          </div>

                          <div>
                            <label
                              htmlFor="azure-client-secret"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Client Secret
                            </label>
                            <input
                              id="azure-client-secret"
                              type="password"
                              value={formState.azure_client_secret}
                              onChange={(e) =>
                                setFormState((p) => ({
                                  ...p,
                                  azure_client_secret: e.target.value,
                                }))
                              }
                              placeholder={
                                editingJob && hasSavedSecret
                                  ? "Leave blank to keep existing secret"
                                  : "Client secret"
                              }
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Kubernetes Fields */}
                  {formState.provider === "kubernetes" && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor="k8s-mode"
                            className="block text-sm font-medium text-yellow-500 mb-1.5"
                          >
                            Connection Mode
                          </label>
                          <select
                            id="k8s-mode"
                            value={formState.k8s_mode}
                            onChange={(e) =>
                              setFormState((p) => ({
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
                            htmlFor="k8s-namespace"
                            className="block text-sm font-medium text-slate-300 mb-1.5"
                          >
                            Namespace
                          </label>
                          <input
                            id="k8s-namespace"
                            type="text"
                            value={formState.k8s_namespace}
                            onChange={(e) =>
                              setFormState((p) => ({
                                ...p,
                                k8s_namespace: e.target.value,
                              }))
                            }
                            placeholder="default"
                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                          />
                        </div>
                      </div>

                      {formState.k8s_mode === "kubeconfig" && (
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                          <div>
                            <label
                              htmlFor="k8s-kubeconfig-path"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Kubeconfig Path
                            </label>
                            <input
                              id="k8s-kubeconfig-path"
                              type="text"
                              value={formState.k8s_kubeconfig_path}
                              onChange={(e) =>
                                setFormState((p) => ({
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
                              htmlFor="k8s-context"
                              className="block text-sm font-medium text-slate-300 mb-1.5"
                            >
                              Context (optional)
                            </label>
                            <input
                              id="k8s-context"
                              type="text"
                              value={formState.k8s_context}
                              onChange={(e) =>
                                setFormState((p) => ({
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

                  {/* Vultr Fields */}
                  {formState.provider === "vultr" && (
                    <div>
                      <label
                        htmlFor="vultr-api-key"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        Vultr API Key {editingJob ? "" : "*"}
                      </label>
                      <input
                        id="vultr-api-key"
                        type="password"
                        value={formState.vultr_api_key}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            vultr_api_key: e.target.value,
                          }))
                        }
                        placeholder={
                          editingJob && hasSavedSecret
                            ? "Leave blank to keep existing secret"
                            : "Enter Vultr Personal Access Token"
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  )}
                </div>
              )}

              {editingJob && (
                <label
                  htmlFor="cloud-enabled"
                  className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                >
                  <input
                    id="cloud-enabled"
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
                  Profile Enabled
                </label>
              )}

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
                  {editingJob ? "Save Changes" : "Save Profile"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      <DiscoveryHistoryModal
        job={historyJob}
        onClose={() => setHistoryJob(null)}
      />
    </div>
  );
}
