import { useState, type ReactNode } from "react";
import { Link, useInRouterContext } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Webhook,
  TestTube,
  Trash2,
  CheckCircle,
  XCircle,
  Edit,
  Copy,
  KeyRound,
  Activity,
  Power,
  ShieldCheck,
  ArrowDownLeft,
  ArrowUpRight,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import SearchableSelect from "@/components/SearchableSelect";
import AssigneePicker, { AssigneeValue } from "@/components/AssigneePicker";
import { ISSUE_TYPES, issueTypeLabel } from "@/lib/constants/issueTypes";
import { Organization } from "@/types";

interface WebhookConfig {
  id: number;
  name: string;
  url: string;
  organization_id: number;
  events: string[];
  is_active: boolean;
  filter_issue_type?: string;
  filter_assignee_type?: "identity" | "org_unit";
  filter_assignee_id?: number;
}

interface WebhooksResponse {
  webhooks: WebhookConfig[];
}

interface InboundWebhookConfig {
  id: number;
  name: string;
  description?: string | null;
  slug: string;
  receive_path: string;
  auth_type: "bearer" | "basic";
  bearer_token_preview?: string | null;
  basic_username?: string | null;
  is_active: boolean;
  organization_id?: number | null;
  stream_id?: number | null;
  scopes?: string[];
  last_received_at?: string | null;
  receive_count: number;
  created_at?: string | null;
}

interface InboundScopeOption {
  scope: string;
  label: string;
  category: string;
  description: string;
}

const FALLBACK_INBOUND_SCOPES: InboundScopeOption[] = [
  {
    scope: "events:log",
    label: "Log Events",
    category: "General",
    description:
      "Receive and record incoming webhook payloads in the audit event log.",
  },
  {
    scope: "playbooks:trigger",
    label: "Trigger Playbooks",
    category: "Automation",
    description:
      "Start linked or requested Stream playbook executions with the incoming payload.",
  },
  {
    scope: "issues:create",
    label: "Create Issues",
    category: "Issues",
    description: "Create new issues or incidents scoped to this tenant.",
  },
  {
    scope: "issues:update",
    label: "Update Issues",
    category: "Issues",
    description:
      "Update existing issues (status, priority, description, metadata) in this tenant.",
  },
  {
    scope: "vulnerabilities:create",
    label: "Create Vulnerabilities",
    category: "Security",
    description: "Create new CVE/vulnerability records scoped to this tenant.",
  },
  {
    scope: "vulnerabilities:update",
    label: "Update Vulnerabilities",
    category: "Security",
    description:
      "Update existing vulnerabilities (by id or cve_id) in this tenant.",
  },
  {
    scope: "entities:create",
    label: "Create Entities",
    category: "Infrastructure",
    description:
      "Create new infrastructure entities in an organization of this tenant.",
  },
  {
    scope: "entities:update",
    label: "Update Entities",
    category: "Infrastructure",
    description: "Update existing infrastructure entities in this tenant.",
  },
  {
    scope: "data_stores:create",
    label: "Create Data Inventory",
    category: "Infrastructure",
    description:
      "Create new data inventory records in an organization of this tenant.",
  },
  {
    scope: "data_stores:update",
    label: "Update Data Inventory",
    category: "Infrastructure",
    description: "Update existing data inventory records in this tenant.",
  },
  {
    scope: "services:create",
    label: "Create Services",
    category: "Services & Software",
    description:
      "Create new tracked services in an organization of this tenant.",
  },
  {
    scope: "services:update",
    label: "Update Services",
    category: "Services & Software",
    description: "Update existing tracked services in this tenant.",
  },
  {
    scope: "software:create",
    label: "Create Software",
    category: "Services & Software",
    description:
      "Create new software inventory entries in an organization of this tenant.",
  },
  {
    scope: "software:update",
    label: "Update Software",
    category: "Services & Software",
    description: "Update existing software inventory entries in this tenant.",
  },
  {
    scope: "ai_resources:create",
    label: "Create AI Resources",
    category: "AI",
    description:
      "Create new AI resource records in an organization of this tenant.",
  },
  {
    scope: "ai_resources:update",
    label: "Update AI Resources",
    category: "AI",
    description: "Update existing AI resource records in this tenant.",
  },
];

interface InboundWebhooksResponse {
  inbound_webhooks: InboundWebhookConfig[];
  count: number;
}

interface InboundCredentials {
  auth_type: "bearer" | "basic";
  bearer_token?: string;
  username?: string;
  password?: string;
}

interface InboundWebhookEvent {
  id: number;
  inbound_webhook_id: number;
  source_ip?: string | null;
  headers?: Record<string, string> | null;
  payload?: unknown;
  action?: string | null;
  action_result?: Record<string, unknown> | null;
  status: string;
  received_at: string;
}

interface InboundEventsResponse {
  events: InboundWebhookEvent[];
  count: number;
}

interface OrganizationResponse {
  items: Organization[];
}

const EVENT_TYPES = [
  { value: "entity.created", label: "Entity Created" },
  { value: "entity.updated", label: "Entity Updated" },
  { value: "entity.deleted", label: "Entity Deleted" },
  { value: "organization.created", label: "Organization Created" },
  { value: "organization.updated", label: "Organization Updated" },
  { value: "issue.created", label: "Issue Created" },
  { value: "issue.updated", label: "Issue Updated" },
  { value: "issue.closed", label: "Issue Closed" },
  { value: "issue.assigned", label: "Issue Assigned" },
];

export default function Webhooks() {
  const initialQuery = (() => {
    if (typeof window === "undefined") {
      return {
        tab: "inbound" as const,
        openCreate: false,
        streamId: undefined as number | undefined,
        streamName: undefined as string | undefined,
      };
    }
    const params = new URLSearchParams(window.location.search);
    const rawTab = params.get("tab");
    const parsedStreamId = Number(params.get("stream_id"));
    return {
      tab: (rawTab === "outbound" ? "outbound" : "inbound") as
        | "inbound"
        | "outbound",
      openCreate: params.get("create") === "1" && rawTab !== "outbound",
      streamId:
        Number.isFinite(parsedStreamId) && parsedStreamId > 0
          ? parsedStreamId
          : undefined,
      streamName: params.get("stream_name") || undefined,
    };
  })();

  const [activeTab, setActiveTab] = useState<"inbound" | "outbound">(
    initialQuery.tab,
  );
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showCreateInboundModal, setShowCreateInboundModal] = useState(
    initialQuery.openCreate,
  );
  const [prefillStream] = useState<{
    streamId?: number;
    streamName?: string;
  }>({
    streamId: initialQuery.streamId,
    streamName: initialQuery.streamName,
  });
  const [editingWebhook, setEditingWebhook] = useState<WebhookConfig | null>(
    null,
  );
  const [editingInboundWebhook, setEditingInboundWebhook] =
    useState<InboundWebhookConfig | null>(null);
  const [revealedCredentials, setRevealedCredentials] = useState<{
    webhook: InboundWebhookConfig;
    credentials: InboundCredentials;
  } | null>(null);
  const [eventsWebhook, setEventsWebhook] =
    useState<InboundWebhookConfig | null>(null);
  const queryClient = useQueryClient();

  const { data: profile } = useQuery({
    queryKey: ["portal-profile"],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000,
  });
  const isAdmin =
    profile?.global_role === "admin" || profile?.tenant_role === "admin";

  const { data, isLoading } = useQuery({
    queryKey: ["webhooks"],
    queryFn: () => api.getWebhooks() as Promise<WebhooksResponse>,
    enabled: isAdmin,
  });

  const { data: inboundData, isLoading: inboundLoading } = useQuery({
    queryKey: ["inbound-webhooks"],
    queryFn: () =>
      typeof api.getInboundWebhooks === "function"
        ? (api.getInboundWebhooks() as Promise<InboundWebhooksResponse>)
        : Promise.resolve({ inbound_webhooks: [], count: 0 }),
    enabled: isAdmin,
  });

  const testMutation = useMutation({
    mutationFn: (id: number) => api.testWebhook(id),
    onSuccess: () => toast.success("Test webhook sent"),
    onError: () => toast.error("Failed to send test webhook"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteWebhook(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["webhooks"],
        refetchType: "all",
      });
      toast.success("Webhook deleted");
    },
  });

  const toggleInboundMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      api.updateInboundWebhook(id, { is_active }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["inbound-webhooks"],
        refetchType: "all",
      });
      toast.success("Inbound webhook updated");
    },
    onError: () => toast.error("Failed to update inbound webhook"),
  });

  const rotateInboundMutation = useMutation({
    mutationFn: (id: number) => api.rotateInboundWebhookCredentials(id),
    onSuccess: async (res: {
      webhook: InboundWebhookConfig;
      credentials: InboundCredentials;
    }) => {
      await queryClient.invalidateQueries({
        queryKey: ["inbound-webhooks"],
        refetchType: "all",
      });
      setRevealedCredentials(res);
      toast.success("Credentials rotated");
    },
    onError: () => toast.error("Failed to rotate credentials"),
  });

  const deleteInboundMutation = useMutation({
    mutationFn: (id: number) => api.deleteInboundWebhook(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["inbound-webhooks"],
        refetchType: "all",
      });
      toast.success("Inbound webhook deleted");
    },
    onError: () => toast.error("Failed to delete inbound webhook"),
  });

  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied`);
    } catch {
      toast.error(`Failed to copy ${label}`);
    }
  };

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Admin access required</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-white">Webhooks</h1>
          <p className="mt-2 text-slate-400">
            Receive authenticated inbound events or configure basic outbound
            notifications
          </p>
        </div>
        {activeTab === "inbound" ? (
          <Button onClick={() => setShowCreateInboundModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Create Inbound Webhook
          </Button>
        ) : (
          <Button onClick={() => setShowCreateModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Create Outbound Webhook
          </Button>
        )}
      </div>

      <div className="flex items-center gap-2 border-b border-slate-700 mb-6">
        <button
          type="button"
          onClick={() => setActiveTab("inbound")}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === "inbound"
              ? "border-primary-500 text-primary-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <ArrowDownLeft className="w-4 h-4" />
          Inbound (Receiving)
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("outbound")}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === "outbound"
              ? "border-primary-500 text-primary-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <ArrowUpRight className="w-4 h-4" />
          Outbound (Basic Events)
        </button>
      </div>

      {activeTab === "inbound" ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-slate-800/60 border border-slate-700 text-sm text-slate-300">
            <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>
              All inbound webhooks require authentication (Bearer Token or Basic
              Auth). Credentials are generated automatically unless specified
              and are only shown once.
            </span>
          </div>

          {inboundLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : !inboundData?.inbound_webhooks?.length ? (
            <Card>
              <CardContent className="text-center py-12">
                <ArrowDownLeft className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No inbound webhooks configured</p>
                <Button
                  className="mt-4"
                  onClick={() => setShowCreateInboundModal(true)}
                >
                  Create your first inbound webhook
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {inboundData.inbound_webhooks.map((hook) => {
                const fullUrl = `${window.location.origin}/api/v1/webhooks/receive/${hook.slug}`;
                return (
                  <Card key={hook.id}>
                    <CardContent>
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-3 mb-1.5 flex-wrap">
                            {hook.is_active ? (
                              <CheckCircle className="w-5 h-5 text-green-400 shrink-0" />
                            ) : (
                              <XCircle className="w-5 h-5 text-slate-500 shrink-0" />
                            )}
                            <h3 className="text-lg font-semibold text-white">
                              {hook.name}
                            </h3>
                            <span className="px-2 py-0.5 text-xs font-medium rounded bg-primary-500/20 text-primary-300">
                              {hook.auth_type === "basic" ? "Basic" : "Bearer"}
                            </span>
                            {hook.stream_id && (
                              <SafeLink
                                to={`/streams/${hook.stream_id}`}
                                className="px-2 py-0.5 text-xs rounded bg-slate-700 hover:bg-slate-600 text-amber-300 transition-colors"
                              >
                                Playbook #{hook.stream_id}
                              </SafeLink>
                            )}
                          </div>
                          {hook.description && (
                            <p className="text-sm text-slate-400 mb-2">
                              {hook.description}
                            </p>
                          )}
                          <div className="flex flex-wrap gap-1.5 mb-2">
                            {(hook.scopes?.length
                              ? hook.scopes
                              : ["events:log"]
                            ).map((sc) => (
                              <span
                                key={sc}
                                className={`px-2 py-0.5 text-xs font-mono rounded ${
                                  sc === "events:log"
                                    ? "bg-slate-800 text-slate-300 border border-slate-700"
                                    : sc === "playbooks:trigger"
                                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                                      : "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                                }`}
                              >
                                {sc}
                              </span>
                            ))}
                          </div>
                          <div className="flex items-center gap-2 mb-2">
                            <code className="text-xs text-slate-300 bg-slate-900/80 px-2.5 py-1 rounded font-mono truncate max-w-xl">
                              {fullUrl}
                            </code>
                            <Button
                              size="sm"
                              variant="ghost"
                              title="Copy Receive URL"
                              onClick={() => copyText(fullUrl, "Receive URL")}
                            >
                              <Copy className="w-3.5 h-3.5" />
                            </Button>
                          </div>
                          <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400">
                            <span>
                              Credential:{" "}
                              <span className="font-mono text-slate-300">
                                {hook.auth_type === "basic"
                                  ? hook.basic_username || "—"
                                  : hook.bearer_token_preview || "—"}
                              </span>
                            </span>
                            <span>Received: {hook.receive_count}</span>
                            <span>
                              Last received:{" "}
                              {hook.last_received_at
                                ? new Date(
                                    hook.last_received_at,
                                  ).toLocaleString()
                                : "Never"}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <Button
                            size="sm"
                            variant="ghost"
                            title="Edit Inbound Webhook"
                            onClick={() => setEditingInboundWebhook(hook)}
                          >
                            <Edit className="w-4 h-4" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            title="Rotate Credentials"
                            onClick={() =>
                              rotateInboundMutation.mutate(hook.id)
                            }
                          >
                            <KeyRound className="w-4 h-4" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            title="View Events"
                            onClick={() => setEventsWebhook(hook)}
                          >
                            <Activity className="w-4 h-4" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            title={hook.is_active ? "Disable" : "Enable"}
                            onClick={() =>
                              toggleInboundMutation.mutate({
                                id: hook.id,
                                is_active: !hook.is_active,
                              })
                            }
                          >
                            <Power className="w-4 h-4" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            title="Delete Inbound Webhook"
                            onClick={() =>
                              deleteInboundMutation.mutate(hook.id)
                            }
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="px-4 py-3 rounded-lg bg-slate-800/60 border border-slate-700 text-sm text-slate-300">
            Need multi-step outbound workflows, retries, or transformations?
            Build them in{" "}
            <SafeLink
              to="/streams"
              className="text-primary-400 hover:text-primary-300 underline"
            >
              Playbooks
            </SafeLink>
            .
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : data?.webhooks?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Webhook className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No webhooks configured</p>
                <Button
                  className="mt-4"
                  onClick={() => setShowCreateModal(true)}
                >
                  Create your first webhook
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {data?.webhooks?.map((webhook: WebhookConfig) => (
                <Card key={webhook.id}>
                  <CardContent>
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-2">
                          {webhook.is_active ? (
                            <CheckCircle className="w-5 h-5 text-green-400" />
                          ) : (
                            <XCircle className="w-5 h-5 text-slate-500" />
                          )}
                          <h3 className="text-lg font-semibold text-white">
                            {webhook.name}
                          </h3>
                        </div>
                        <p className="text-sm text-slate-400 mb-3 font-mono">
                          {webhook.url}
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {webhook.events?.map((event: string) => (
                            <span
                              key={event}
                              className="px-2 py-1 text-xs bg-primary-500/20 text-primary-400 rounded"
                            >
                              {event}
                            </span>
                          ))}
                          {webhook.filter_issue_type && (
                            <span className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 rounded">
                              type={issueTypeLabel(webhook.filter_issue_type)}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          title="Edit webhook"
                          onClick={() => setEditingWebhook(webhook)}
                        >
                          <Edit className="w-4 h-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => testMutation.mutate(webhook.id)}
                        >
                          <TestTube className="w-4 h-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => deleteMutation.mutate(webhook.id)}
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {showCreateInboundModal && (
        <CreateInboundWebhookModal
          initialStreamId={prefillStream.streamId}
          initialStreamName={prefillStream.streamName}
          onClose={() => setShowCreateInboundModal(false)}
          onSuccess={async (res) => {
            await queryClient.invalidateQueries({
              queryKey: ["inbound-webhooks"],
              refetchType: "all",
            });
            setShowCreateInboundModal(false);
            setRevealedCredentials(res);
          }}
        />
      )}

      {editingInboundWebhook && (
        <EditInboundWebhookModal
          webhook={editingInboundWebhook}
          onClose={() => setEditingInboundWebhook(null)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ["inbound-webhooks"],
              refetchType: "all",
            });
            setEditingInboundWebhook(null);
          }}
        />
      )}

      {revealedCredentials && (
        <RevealInboundCredentialsModal
          webhook={revealedCredentials.webhook}
          credentials={revealedCredentials.credentials}
          onClose={() => setRevealedCredentials(null)}
        />
      )}

      {eventsWebhook && (
        <InboundEventsModal
          webhook={eventsWebhook}
          onClose={() => setEventsWebhook(null)}
        />
      )}

      {showCreateModal && (
        <CreateWebhookModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ["webhooks"],
              refetchType: "all",
            });
            setShowCreateModal(false);
          }}
        />
      )}

      {editingWebhook && (
        <EditWebhookModal
          webhook={editingWebhook}
          onClose={() => setEditingWebhook(null)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ["webhooks"],
              refetchType: "all",
            });
            setEditingWebhook(null);
          }}
        />
      )}
    </div>
  );
}

interface CreateWebhookModalProps {
  onClose: () => void;
  onSuccess: () => Promise<void>;
}

function AssignmentFilters({
  filterIssueType,
  onFilterIssueTypeChange,
  filterAssignee,
  onFilterAssigneeChange,
}: {
  filterIssueType: string;
  onFilterIssueTypeChange: (v: string) => void;
  filterAssignee: AssigneeValue | null;
  onFilterAssigneeChange: (v: AssigneeValue | null) => void;
}) {
  return (
    <div className="pt-4 border-t border-slate-700">
      <p className="text-sm font-medium text-slate-300 mb-3">
        Assignment Filters (issue.assigned events only)
      </p>
      <div className="space-y-3">
        <Select
          label="Issue Type"
          value={filterIssueType}
          onChange={(e) => onFilterIssueTypeChange(e.target.value)}
        >
          <option value="">Any issue type</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </Select>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1.5">
            Assignee
          </label>
          <AssigneePicker
            value={filterAssignee}
            onChange={onFilterAssigneeChange}
          />
        </div>
      </div>
    </div>
  );
}

function CreateWebhookModal({ onClose, onSuccess }: CreateWebhookModalProps) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [orgId, setOrgId] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [secret, setSecret] = useState("");
  const [filterIssueType, setFilterIssueType] = useState("");
  const [filterAssignee, setFilterAssignee] = useState<AssigneeValue | null>(
    null,
  );

  const { data: orgs } = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api.getOrganizations() as Promise<OrganizationResponse>,
  });

  const { data: existingWebhooks } = useQuery({
    queryKey: ["webhooks"],
    queryFn: () => api.getWebhooks() as Promise<WebhooksResponse>,
  });

  const urlOptions = Array.from(
    new Set(
      (existingWebhooks?.webhooks || [])
        .map((w) => w.url)
        .filter((u): u is string => Boolean(u)),
    ),
  ).map((u) => ({ value: u, label: u }));

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.createWebhook(data as Parameters<typeof api.createWebhook>[0]),
    onSuccess: (result) => {
      console.log("[Webhooks] Create", {
        name: result?.name,
        events: result?.events,
        filter_issue_type: result?.filter_issue_type,
      });
      toast.success("Webhook created");
      onSuccess();
    },
    onError: () => toast.error("Failed to create webhook"),
  });

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event],
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const webhookData: Parameters<typeof api.createWebhook>[0] = {
      name,
      url,
      organization_id: orgId ? parseInt(orgId) : undefined,
      events: selectedEvents,
      secret: secret || undefined,
      is_active: true,
      filter_issue_type: filterIssueType || undefined,
      filter_assignee_type: filterAssignee?.assignee_type,
      filter_assignee_id: filterAssignee?.assignee_id,
    };
    createMutation.mutate(webhookData);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Create Webhook</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Production Webhook"
            />
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1.5">
                URL <span className="text-red-400">*</span>
              </span>
              <SearchableSelect
                options={urlOptions}
                value={url}
                onChange={(val) => setUrl(String(val))}
                placeholder="https://example.com/webhook"
                allowCustom
                ariaLabel="URL"
              />
            </div>
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1.5">
                Organization
              </span>
              <SearchableSelect
                options={(orgs?.items || []).map((o: Organization) => ({
                  value: String(o.id),
                  label: o.name,
                }))}
                value={orgId}
                onChange={(val) => setOrgId(String(val))}
                placeholder="Select organization"
                allowClear
                ariaLabel="Organization"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Events
              </label>
              <div className="space-y-2">
                {EVENT_TYPES.map((event) => (
                  <label
                    key={event.value}
                    className="flex items-center gap-2 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                      data-testid={`event-checkbox-${event.value}`}
                    />
                    <span className="text-sm text-slate-300">
                      {event.label}
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <Input
              label="Secret (optional)"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="Webhook signing secret"
            />
            <AssignmentFilters
              filterIssueType={filterIssueType}
              onFilterIssueTypeChange={setFilterIssueType}
              filterAssignee={filterAssignee}
              onFilterAssigneeChange={setFilterAssignee}
            />
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" isLoading={createMutation.isPending}>
                Create
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

interface EditWebhookModalProps {
  webhook: WebhookConfig;
  onClose: () => void;
  onSuccess: () => Promise<void>;
}

function EditWebhookModal({
  webhook,
  onClose,
  onSuccess,
}: EditWebhookModalProps) {
  const [name, setName] = useState(webhook.name);
  const [url, setUrl] = useState(webhook.url);
  const [selectedEvents, setSelectedEvents] = useState<string[]>(
    webhook.events || [],
  );
  const [isActive, setIsActive] = useState(webhook.is_active);
  const [filterIssueType, setFilterIssueType] = useState(
    webhook.filter_issue_type || "",
  );
  const [filterAssignee, setFilterAssignee] = useState<AssigneeValue | null>(
    webhook.filter_assignee_id && webhook.filter_assignee_type
      ? {
          assignee_type: webhook.filter_assignee_type,
          assignee_id: webhook.filter_assignee_id,
        }
      : null,
  );

  const updateMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.updateWebhook>[1]) =>
      api.updateWebhook(webhook.id, data),
    onSuccess: async (result) => {
      console.log("[Webhooks] Update", {
        name: result?.name,
        is_active: result?.is_active,
        filter_issue_type: result?.filter_issue_type,
      });
      toast.success("Webhook updated");
      await onSuccess();
    },
    onError: () => toast.error("Failed to update webhook"),
  });

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event],
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    updateMutation.mutate({
      name,
      url,
      events: selectedEvents,
      is_active: isActive,
      filter_issue_type: filterIssueType || undefined,
      filter_assignee_type: filterAssignee?.assignee_type,
      filter_assignee_id: filterAssignee?.assignee_id,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Edit Webhook</h2>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={handleSubmit}
            className="space-y-4"
            data-testid="edit-webhook-form"
          >
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              label="URL"
              type="url"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                  className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                  data-testid="is-active-toggle"
                />
                <span className="text-sm font-medium text-slate-300">
                  Active
                </span>
              </label>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Events
              </label>
              <div className="space-y-2">
                {EVENT_TYPES.map((event) => (
                  <label
                    key={event.value}
                    className="flex items-center gap-2 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                      data-testid={`event-checkbox-${event.value}`}
                    />
                    <span className="text-sm text-slate-300">
                      {event.label}
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <AssignmentFilters
              filterIssueType={filterIssueType}
              onFilterIssueTypeChange={setFilterIssueType}
              filterAssignee={filterAssignee}
              onFilterAssigneeChange={setFilterAssignee}
            />
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                isLoading={updateMutation.isPending}
                data-testid="save-webhook-button"
              >
                Save Changes
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

interface StreamOption {
  id: number;
  name: string;
}

const SCOPE_CATEGORY_ORDER = [
  "General",
  "Automation",
  "Issues",
  "Security",
  "Infrastructure",
  "Services & Software",
  "AI",
];

function SafeLink({
  to,
  className,
  children,
}: {
  to: string;
  className?: string;
  children: ReactNode;
}) {
  const inRouter = useInRouterContext();
  if (inRouter) {
    return (
      <Link to={to} className={className}>
        {children}
      </Link>
    );
  }
  return (
    <a href={to} className={className}>
      {children}
    </a>
  );
}

function scopeBadgeClass(scope: string): string {
  if (scope === "events:log") {
    return "bg-slate-800 text-slate-300 border border-slate-700";
  }
  if (scope === "playbooks:trigger") {
    return "bg-amber-500/20 text-amber-300 border border-amber-500/30";
  }
  return "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30";
}

function useInboundReferenceData() {
  const { data: scopesData } = useQuery({
    queryKey: ["inbound-webhook-scopes"],
    queryFn: () =>
      typeof api.getInboundWebhookScopes === "function"
        ? (api.getInboundWebhookScopes() as Promise<{
            scopes?: InboundScopeOption[];
          }>)
        : Promise.resolve({ scopes: FALLBACK_INBOUND_SCOPES }),
    staleTime: 60000,
  });

  const { data: streamsData } = useQuery({
    queryKey: ["streams", "inbound-select"],
    queryFn: () => {
      const apiWithOptional = api as unknown as {
        listStreams?: (p?: {
          page?: number;
          per_page?: number;
        }) => Promise<{ data?: StreamOption[]; items?: StreamOption[] }>;
        getStreams?: () => Promise<{
          data?: StreamOption[];
          items?: StreamOption[];
        }>;
      };
      if (typeof apiWithOptional.listStreams === "function") {
        return apiWithOptional.listStreams({ page: 1, per_page: 100 });
      }
      if (typeof apiWithOptional.getStreams === "function") {
        return apiWithOptional.getStreams();
      }
      return Promise.resolve({ data: [] });
    },
    staleTime: 30000,
  });

  const { data: orgsData } = useQuery({
    queryKey: ["organizations"],
    queryFn: () =>
      typeof api.getOrganizations === "function"
        ? (api.getOrganizations() as Promise<OrganizationResponse>)
        : Promise.resolve({ items: [] }),
    staleTime: 30000,
  });

  const scopeOptions = scopesData?.scopes?.length
    ? scopesData.scopes
    : FALLBACK_INBOUND_SCOPES;
  const streams = streamsData?.data || streamsData?.items || [];
  const organizations = orgsData?.items || [];

  return { scopeOptions, streams, organizations };
}

function InboundScopeSelector({
  options,
  selectedScopes,
  onChange,
}: {
  options: InboundScopeOption[];
  selectedScopes: string[];
  onChange: (next: string[]) => void;
}) {
  const grouped = new Map<string, InboundScopeOption[]>();
  for (const cat of SCOPE_CATEGORY_ORDER) {
    grouped.set(cat, []);
  }
  for (const opt of options) {
    const existing = grouped.get(opt.category);
    if (existing) {
      existing.push(opt);
    } else {
      grouped.set(opt.category, [opt]);
    }
  }

  const toggleScope = (scope: string) => {
    if (selectedScopes.includes(scope)) {
      onChange(selectedScopes.filter((s) => s !== scope));
    } else {
      onChange([...selectedScopes, scope]);
    }
  };

  const applyPreset = (scopes: string[]) => {
    const valid = new Set(options.map((o) => o.scope));
    const filtered = scopes.filter((s) => valid.has(s));
    onChange(filtered.length ? filtered : ["events:log"]);
  };

  const allResourceScopes = options
    .filter((o) => o.scope !== "playbooks:trigger")
    .map((o) => o.scope);

  return (
    <div className="space-y-3 pt-2 border-t border-slate-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-yellow-500">
          Permission Scopes
        </span>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => applyPreset(["events:log"])}
            className="px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-colors"
          >
            Log Only
          </button>
          <button
            type="button"
            onClick={() => applyPreset(["events:log", "playbooks:trigger"])}
            className="px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-amber-300 border border-amber-500/30 transition-colors"
          >
            Playbook Trigger
          </button>
          <button
            type="button"
            onClick={() =>
              applyPreset(["events:log", "issues:create", "issues:update"])
            }
            className="px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-emerald-300 border border-emerald-500/30 transition-colors"
          >
            Issues R/W
          </button>
          <button
            type="button"
            onClick={() => applyPreset(allResourceScopes)}
            className="px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-primary-300 border border-primary-500/30 transition-colors"
          >
            All Resource Scopes
          </button>
        </div>
      </div>

      {selectedScopes.length === 0 && (
        <p className="text-xs text-amber-400">
          Select at least one permission scope.
        </p>
      )}

      <div className="space-y-3 max-h-64 overflow-y-auto pr-1">
        {Array.from(grouped.entries()).map(([category, items]) => {
          if (!items.length) return null;
          return (
            <div
              key={category}
              className="rounded-lg bg-slate-900/60 border border-slate-800 p-2.5 space-y-2"
            >
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                {category}
              </div>
              <div className="grid grid-cols-1 gap-1.5">
                {items.map((opt) => (
                  <label
                    key={opt.scope}
                    className="flex items-start gap-2.5 cursor-pointer rounded p-1.5 hover:bg-slate-800/70 transition-colors"
                  >
                    <input
                      type="checkbox"
                      checked={selectedScopes.includes(opt.scope)}
                      onChange={() => toggleScope(opt.scope)}
                      className="mt-0.5 w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500 shrink-0"
                      data-testid={`inbound-scope-${opt.scope}`}
                    />
                    <span className="min-w-0 flex-1 block">
                      <span className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-slate-200">
                          {opt.label}
                        </span>
                        <code className="text-[11px] font-mono text-slate-400 bg-slate-950/70 px-1.5 py-0.5 rounded">
                          {opt.scope}
                        </code>
                      </span>
                      <span className="block text-xs text-slate-400 mt-0.5">
                        {opt.description}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CreateInboundWebhookModal({
  initialStreamId,
  initialStreamName,
  onClose,
  onSuccess,
}: {
  initialStreamId?: number;
  initialStreamName?: string;
  onClose: () => void;
  onSuccess: (res: {
    webhook: InboundWebhookConfig;
    credentials: InboundCredentials;
  }) => Promise<void>;
}) {
  const [name, setName] = useState(
    initialStreamName ? `${initialStreamName} Webhook` : "",
  );
  const [description, setDescription] = useState("");
  const [authType, setAuthType] = useState<"bearer" | "basic">("bearer");
  const [bearerToken, setBearerToken] = useState("");
  const [basicUsername, setBasicUsername] = useState("");
  const [basicPassword, setBasicPassword] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [streamId, setStreamId] = useState(
    initialStreamId ? String(initialStreamId) : "",
  );
  const [scopes, setScopes] = useState<string[]>(
    initialStreamId ? ["events:log", "playbooks:trigger"] : ["events:log"],
  );

  const { scopeOptions, streams, organizations } = useInboundReferenceData();

  const createMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.createInboundWebhook>[0]) =>
      api.createInboundWebhook(data),
    onSuccess: async (res) => {
      toast.success("Inbound webhook created");
      await onSuccess(res);
    },
    onError: () => toast.error("Failed to create inbound webhook"),
  });

  const handleStreamChange = (nextVal: string) => {
    setStreamId(nextVal);
    if (nextVal) {
      setScopes((prev) =>
        prev.includes("playbooks:trigger")
          ? prev
          : [...prev, "playbooks:trigger"],
      );
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!scopes.length) {
      toast.error("Select at least one permission scope");
      return;
    }
    const payload: Parameters<typeof api.createInboundWebhook>[0] = {
      name,
      description: description || undefined,
      auth_type: authType,
      organization_id: organizationId
        ? parseInt(organizationId, 10)
        : undefined,
      stream_id: streamId ? parseInt(streamId, 10) : undefined,
      scopes,
    };
    if (authType === "bearer" && bearerToken.trim()) {
      payload.bearer_token = bearerToken.trim();
    } else if (authType === "basic") {
      if (basicUsername.trim()) payload.basic_username = basicUsername.trim();
      if (basicPassword.trim()) payload.basic_password = basicPassword.trim();
    }
    createMutation.mutate(payload);
  };

  const hasInitialStreamInList =
    initialStreamId !== undefined &&
    streams.some((s) => s.id === initialStreamId);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">
              Create Inbound Webhook
            </h2>
            <Button size="sm" variant="ghost" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={handleSubmit}
            className="space-y-4"
            data-testid="create-inbound-webhook-form"
          >
            {initialStreamId && (
              <div
                className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-sm text-amber-200"
                data-testid="playbook-softlink-banner"
              >
                Soft-linked to{" "}
                <span className="font-semibold">
                  {initialStreamName || `Playbook #${initialStreamId}`}
                </span>{" "}
                with{" "}
                <code className="font-mono text-amber-300">
                  playbooks:trigger
                </code>{" "}
                pre-selected.
              </div>
            )}

            <Input
              id="inbound-name"
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Alertmanager Receiver"
            />
            <Input
              id="inbound-description"
              label="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Receives production alerts"
            />
            <Select
              id="inbound-auth-type"
              label="Authentication Type (Required)"
              value={authType}
              onChange={(e) =>
                setAuthType(e.target.value as "bearer" | "basic")
              }
            >
              <option value="bearer">Bearer Token (Autogenerated)</option>
              <option value="basic">
                Basic Auth (User/Pass - Autogenerated)
              </option>
            </Select>

            {authType === "bearer" ? (
              <Input
                id="inbound-bearer-token"
                label="Custom Bearer Token (optional — leave blank to autogenerate)"
                value={bearerToken}
                onChange={(e) => setBearerToken(e.target.value)}
                placeholder="Leave blank to autogenerate secure token"
              />
            ) : (
              <div className="space-y-3">
                <Input
                  id="inbound-basic-username"
                  label="Custom Username (optional — leave blank to autogenerate)"
                  value={basicUsername}
                  onChange={(e) => setBasicUsername(e.target.value)}
                  placeholder="Leave blank to autogenerate"
                />
                <Input
                  id="inbound-basic-password"
                  label="Custom Password (optional — leave blank to autogenerate)"
                  type="password"
                  value={basicPassword}
                  onChange={(e) => setBasicPassword(e.target.value)}
                  placeholder="Leave blank to autogenerate"
                />
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Select
                id="inbound-stream-id"
                data-testid="inbound-stream-select"
                label="Linked Playbook (optional)"
                value={streamId}
                onChange={(e) => handleStreamChange(e.target.value)}
              >
                <option value="">None (use explicit action payloads)</option>
                {initialStreamId && !hasInitialStreamInList && (
                  <option value={String(initialStreamId)}>
                    {initialStreamName || `Playbook #${initialStreamId}`}
                  </option>
                )}
                {streams.map((stream) => (
                  <option key={stream.id} value={String(stream.id)}>
                    {stream.name} (#{stream.id})
                  </option>
                ))}
              </Select>

              <Select
                id="inbound-organization-id"
                data-testid="inbound-organization-select"
                label="Default Organization (optional)"
                value={organizationId}
                onChange={(e) => setOrganizationId(e.target.value)}
              >
                <option value="">
                  None (supply organization_id in payload)
                </option>
                {organizations.map((org) => (
                  <option key={org.id} value={String(org.id)}>
                    {org.name}
                  </option>
                ))}
              </Select>
            </div>

            <InboundScopeSelector
              options={scopeOptions}
              selectedScopes={scopes}
              onChange={setScopes}
            />

            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                isLoading={createMutation.isPending}
                disabled={scopes.length === 0}
              >
                Create Inbound Webhook
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function EditInboundWebhookModal({
  webhook,
  onClose,
  onSuccess,
}: {
  webhook: InboundWebhookConfig;
  onClose: () => void;
  onSuccess: () => Promise<void>;
}) {
  const [name, setName] = useState(webhook.name);
  const [description, setDescription] = useState(webhook.description || "");
  const [isActive, setIsActive] = useState(webhook.is_active);
  const [organizationId, setOrganizationId] = useState(
    webhook.organization_id ? String(webhook.organization_id) : "",
  );
  const [streamId, setStreamId] = useState(
    webhook.stream_id ? String(webhook.stream_id) : "",
  );
  const [scopes, setScopes] = useState<string[]>(
    webhook.scopes?.length ? webhook.scopes : ["events:log"],
  );

  const { scopeOptions, streams, organizations } = useInboundReferenceData();

  const updateMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.updateInboundWebhook>[1]) =>
      api.updateInboundWebhook(webhook.id, data),
    onSuccess: async () => {
      toast.success("Inbound webhook updated");
      await onSuccess();
    },
    onError: () => toast.error("Failed to update inbound webhook"),
  });

  const handleStreamChange = (nextVal: string) => {
    setStreamId(nextVal);
    if (nextVal) {
      setScopes((prev) =>
        prev.includes("playbooks:trigger")
          ? prev
          : [...prev, "playbooks:trigger"],
      );
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!scopes.length) {
      toast.error("Select at least one permission scope");
      return;
    }
    updateMutation.mutate({
      name,
      description: description || undefined,
      is_active: isActive,
      organization_id: organizationId ? parseInt(organizationId, 10) : null,
      stream_id: streamId ? parseInt(streamId, 10) : null,
      scopes,
    });
  };

  const hasCurrentStreamInList =
    Boolean(webhook.stream_id) &&
    streams.some((s) => s.id === webhook.stream_id);
  const hasCurrentOrgInList =
    Boolean(webhook.organization_id) &&
    organizations.some((o) => o.id === webhook.organization_id);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">
              Edit Inbound Webhook
            </h2>
            <Button size="sm" variant="ghost" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={handleSubmit}
            className="space-y-4"
            data-testid="edit-inbound-webhook-form"
          >
            <Input
              id="edit-inbound-name"
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              id="edit-inbound-description"
              label="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Receives production alerts"
            />
            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                  className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                  data-testid="edit-inbound-is-active"
                />
                <span className="text-sm font-medium text-slate-300">
                  Active
                </span>
              </label>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Select
                id="edit-inbound-stream-id"
                data-testid="edit-inbound-stream-select"
                label="Linked Playbook (optional)"
                value={streamId}
                onChange={(e) => handleStreamChange(e.target.value)}
              >
                <option value="">None</option>
                {webhook.stream_id && !hasCurrentStreamInList && (
                  <option value={String(webhook.stream_id)}>
                    Playbook #{webhook.stream_id}
                  </option>
                )}
                {streams.map((stream) => (
                  <option key={stream.id} value={String(stream.id)}>
                    {stream.name} (#{stream.id})
                  </option>
                ))}
              </Select>

              <Select
                id="edit-inbound-organization-id"
                data-testid="edit-inbound-organization-select"
                label="Default Organization (optional)"
                value={organizationId}
                onChange={(e) => setOrganizationId(e.target.value)}
              >
                <option value="">None</option>
                {webhook.organization_id && !hasCurrentOrgInList && (
                  <option value={String(webhook.organization_id)}>
                    Organization #{webhook.organization_id}
                  </option>
                )}
                {organizations.map((org) => (
                  <option key={org.id} value={String(org.id)}>
                    {org.name}
                  </option>
                ))}
              </Select>
            </div>

            <InboundScopeSelector
              options={scopeOptions}
              selectedScopes={scopes}
              onChange={setScopes}
            />

            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                isLoading={updateMutation.isPending}
                disabled={scopes.length === 0}
                data-testid="save-inbound-webhook-button"
              >
                Save Changes
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function buildCurlSamplePayload(webhook: InboundWebhookConfig): string {
  const granted = webhook.scopes?.length ? webhook.scopes : ["events:log"];
  if (granted.includes("playbooks:trigger") && webhook.stream_id) {
    return JSON.stringify({
      action: "playbooks:trigger",
      stream_id: webhook.stream_id,
      input: { event: "trigger" },
    });
  }
  if (granted.includes("playbooks:trigger")) {
    return JSON.stringify({
      action: "playbooks:trigger",
      playbook_id: 1,
      input: { event: "trigger" },
    });
  }
  if (granted.includes("issues:create")) {
    return JSON.stringify({
      action: "issues:create",
      data: { title: "Alert from external system", priority: "high" },
    });
  }
  return JSON.stringify({ event: "test" });
}

function RevealInboundCredentialsModal({
  webhook,
  credentials,
  onClose,
}: {
  webhook: InboundWebhookConfig;
  credentials: InboundCredentials;
  onClose: () => void;
}) {
  const receiveUrl = `${window.location.origin}/api/v1/webhooks/receive/${webhook.slug}`;
  const basicCreds = [
    credentials.username || "",
    credentials.password || "",
  ].join(":");
  const samplePayload = buildCurlSamplePayload(webhook);
  const curlExample =
    credentials.auth_type === "bearer"
      ? `curl -X POST "${receiveUrl}" \\\n  -H "Authorization: Bearer ${credentials.bearer_token}" \\\n  -H "Content-Type: application/json" \\\n  -d '${samplePayload}'`
      : `curl -X POST "${receiveUrl}" \\\n  -u "${basicCreds}" \\\n  -H "Content-Type: application/json" \\\n  -d '${samplePayload}'`;
  const grantedScopes = webhook.scopes?.length
    ? webhook.scopes
    : ["events:log"];

  const copyValue = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied`);
    } catch {
      toast.error(`Failed to copy ${label}`);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">
              Inbound Webhook Credentials — {webhook.name}
            </h2>
            <Button size="sm" variant="ghost" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-sm text-amber-300">
            Save these credentials now. They are hashed before storage and
            cannot be viewed again (you can rotate them at any time).
          </div>

          <div>
            <span className="block text-xs font-medium text-slate-400 mb-1.5">
              Granted Scopes
            </span>
            <div className="flex flex-wrap gap-1.5">
              {grantedScopes.map((sc) => (
                <span
                  key={sc}
                  className={`px-2 py-0.5 text-xs font-mono rounded ${scopeBadgeClass(sc)}`}
                >
                  {sc}
                </span>
              ))}
            </div>
          </div>

          <div>
            <span className="block text-xs font-medium text-slate-400 mb-1">
              Receive URL
            </span>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs bg-slate-900 px-3 py-2 rounded font-mono text-slate-200 break-all">
                {receiveUrl}
              </code>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => copyValue(receiveUrl, "Receive URL")}
              >
                <Copy className="w-3.5 h-3.5 mr-1" />
                Copy
              </Button>
            </div>
          </div>

          {credentials.auth_type === "bearer" ? (
            <div>
              <span className="block text-xs font-medium text-slate-400 mb-1">
                Bearer Token
              </span>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-slate-900 px-3 py-2 rounded font-mono text-emerald-300 break-all">
                  {credentials.bearer_token}
                </code>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    copyValue(credentials.bearer_token || "", "Bearer Token")
                  }
                >
                  <Copy className="w-3.5 h-3.5 mr-1" />
                  Copy
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <span className="block text-xs font-medium text-slate-400 mb-1">
                  Basic Auth Username
                </span>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs bg-slate-900 px-3 py-2 rounded font-mono text-emerald-300 break-all">
                    {credentials.username}
                  </code>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      copyValue(credentials.username || "", "Username")
                    }
                  >
                    <Copy className="w-3.5 h-3.5 mr-1" />
                    Copy
                  </Button>
                </div>
              </div>
              <div>
                <span className="block text-xs font-medium text-slate-400 mb-1">
                  Basic Auth Password
                </span>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs bg-slate-900 px-3 py-2 rounded font-mono text-emerald-300 break-all">
                    {credentials.password}
                  </code>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      copyValue(credentials.password || "", "Password")
                    }
                  >
                    <Copy className="w-3.5 h-3.5 mr-1" />
                    Copy
                  </Button>
                </div>
              </div>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-slate-400">
                cURL Example
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => copyValue(curlExample, "cURL example")}
              >
                <Copy className="w-3.5 h-3.5 mr-1" />
                Copy cURL
              </Button>
            </div>
            <pre className="text-xs bg-slate-950 p-3 rounded font-mono text-slate-300 overflow-x-auto">
              {curlExample}
            </pre>
          </div>

          <div className="flex justify-end pt-2">
            <Button onClick={onClose}>Done</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function eventStatusClass(status: string): string {
  if (status === "forbidden" || status === "failed" || status === "invalid") {
    return "text-red-400";
  }
  return "text-emerald-400";
}

function InboundEventsModal({
  webhook,
  onClose,
}: {
  webhook: InboundWebhookConfig;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["inbound-webhook-events", webhook.id],
    queryFn: () =>
      api.getInboundWebhookEvents(webhook.id) as Promise<InboundEventsResponse>,
  });

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-3xl max-h-[85vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">
              Recent Events — {webhook.name}
            </h2>
            <Button size="sm" variant="ghost" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : !data?.events?.length ? (
            <p className="text-slate-400 text-center py-8">
              No events received yet.
            </p>
          ) : (
            <div className="space-y-3">
              {data.events.map((ev) => (
                <div
                  key={ev.id}
                  className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 space-y-2"
                >
                  <div className="flex items-center justify-between gap-2 flex-wrap text-xs text-slate-400">
                    <span className="flex items-center gap-2 flex-wrap">
                      <span>Event #{ev.id}</span>
                      <span>•</span>
                      <span className={eventStatusClass(ev.status)}>
                        {ev.status}
                      </span>
                      {ev.action && (
                        <span className="px-1.5 py-0.5 rounded bg-slate-800 text-primary-300 border border-slate-700 font-mono">
                          {ev.action}
                        </span>
                      )}
                      {ev.source_ip ? `• ${ev.source_ip}` : ""}
                    </span>
                    <span>{new Date(ev.received_at).toLocaleString()}</span>
                  </div>
                  <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-2.5 rounded overflow-x-auto max-h-48">
                    {JSON.stringify(ev.payload, null, 2)}
                  </pre>
                  {ev.action_result !== null &&
                    ev.action_result !== undefined && (
                      <div>
                        <span className="block text-[11px] font-medium text-slate-400 mb-1">
                          Action Result
                        </span>
                        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-2.5 rounded overflow-x-auto max-h-40">
                          {JSON.stringify(ev.action_result, null, 2)}
                        </pre>
                      </div>
                    )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
