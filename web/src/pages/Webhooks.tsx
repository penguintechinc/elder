import { useState } from "react";
import { Link } from "react-router-dom";
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
  stream_id?: number | null;
  last_received_at?: string | null;
  receive_count: number;
  created_at?: string | null;
}

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
  const [activeTab, setActiveTab] = useState<"inbound" | "outbound">("inbound");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showCreateInboundModal, setShowCreateInboundModal] = useState(false);
  const [editingWebhook, setEditingWebhook] = useState<WebhookConfig | null>(
    null,
  );
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
    queryFn: () => api.getInboundWebhooks() as Promise<InboundWebhooksResponse>,
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
                              <span className="px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300">
                                Playbook #{hook.stream_id}
                              </span>
                            )}
                          </div>
                          {hook.description && (
                            <p className="text-sm text-slate-400 mb-2">
                              {hook.description}
                            </p>
                          )}
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
            <Link
              to="/streams"
              className="text-primary-400 hover:text-primary-300 underline"
            >
              Playbooks
            </Link>
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

function CreateInboundWebhookModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: (res: {
    webhook: InboundWebhookConfig;
    credentials: InboundCredentials;
  }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [authType, setAuthType] = useState<"bearer" | "basic">("bearer");
  const [bearerToken, setBearerToken] = useState("");
  const [basicUsername, setBasicUsername] = useState("");
  const [basicPassword, setBasicPassword] = useState("");
  const [streamId, setStreamId] = useState("");

  const createMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.createInboundWebhook>[0]) =>
      api.createInboundWebhook(data),
    onSuccess: async (res) => {
      toast.success("Inbound webhook created");
      await onSuccess(res);
    },
    onError: () => toast.error("Failed to create inbound webhook"),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload: Parameters<typeof api.createInboundWebhook>[0] = {
      name,
      description: description || undefined,
      auth_type: authType,
      stream_id: streamId ? parseInt(streamId, 10) : undefined,
    };
    if (authType === "bearer" && bearerToken.trim()) {
      payload.bearer_token = bearerToken.trim();
    } else if (authType === "basic") {
      if (basicUsername.trim()) payload.basic_username = basicUsername.trim();
      if (basicPassword.trim()) payload.basic_password = basicPassword.trim();
    }
    createMutation.mutate(payload);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">
            Create Inbound Webhook
          </h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
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

            <Input
              id="inbound-stream-id"
              label="Playbook / Stream ID (optional)"
              type="number"
              value={streamId}
              onChange={(e) => setStreamId(e.target.value)}
              placeholder="e.g. 12"
            />

            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" isLoading={createMutation.isPending}>
                Create Inbound Webhook
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
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
  const curlExample =
    credentials.auth_type === "bearer"
      ? `curl -X POST "${receiveUrl}" \\\n  -H "Authorization: Bearer ${credentials.bearer_token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"event": "test"}'`
      : `curl -X POST "${receiveUrl}" \\\n  -u "${basicCreds}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"event": "test"}'`;

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
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>
                      Event #{ev.id} •{" "}
                      <span className="text-emerald-400">{ev.status}</span>
                      {ev.source_ip ? ` • ${ev.source_ip}` : ""}
                    </span>
                    <span>{new Date(ev.received_at).toLocaleString()}</span>
                  </div>
                  <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-2.5 rounded overflow-x-auto max-h-48">
                    {JSON.stringify(ev.payload, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
