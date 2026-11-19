import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  Edit,
  Trash2,
  Bot,
  Server,
  Network,
  DollarSign,
  ExternalLink,
  CheckCircle2,
  Clock,
  ShieldAlert,
  Ban,
  Eye,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { invalidateCache } from "@/lib/invalidateCache";
import { confirmDelete } from "@/lib/confirmActions";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import type {
  AIResource,
  AIResourceType,
  AIResourceStatus,
  AIAgreementLevel,
} from "@/types";

interface Organization {
  id: number;
  name: string;
}

export interface AIResourcesProps {
  defaultType?: AIResourceType;
}

const RESOURCE_TABS: { value: "" | AIResourceType; label: string }[] = [
  { value: "", label: "All Resources" },
  { value: "llm", label: "AI LLMs" },
  { value: "ai_gateway", label: "AI Gateways" },
  { value: "mcp_server", label: "MCP Servers" },
];

const RESOURCE_TYPES: { value: AIResourceType; label: string }[] = [
  { value: "llm", label: "AI LLM" },
  { value: "ai_gateway", label: "AI Gateway" },
  { value: "mcp_server", label: "MCP Server" },
];

const STATUS_OPTIONS: { value: AIResourceStatus; label: string }[] = [
  { value: "pending_review", label: "Pending Review" },
  { value: "allowed", label: "Allowed" },
  { value: "restricted", label: "Restricted" },
  { value: "not_allowed", label: "Not Allowed" },
];

const AGREEMENT_OPTIONS: { value: AIAgreementLevel; label: string }[] = [
  { value: "user", label: "User" },
  { value: "team", label: "Team" },
  { value: "enterprise", label: "Enterprise" },
];

const STATUS_BADGE: Record<
  AIResourceStatus,
  { label: string; className: string; icon: typeof CheckCircle2 }
> = {
  allowed: {
    label: "Allowed",
    className:
      "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
    icon: CheckCircle2,
  },
  pending_review: {
    label: "Pending Review",
    className:
      "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
    icon: Clock,
  },
  restricted: {
    label: "Restricted",
    className:
      "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
    icon: ShieldAlert,
  },
  not_allowed: {
    label: "Not Allowed",
    className: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
    icon: Ban,
  },
};

const AGREEMENT_BADGE: Record<AIAgreementLevel, string> = {
  user: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  team: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  enterprise:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
};

const TYPE_LABEL: Record<AIResourceType, string> = {
  llm: "AI LLM",
  ai_gateway: "AI Gateway",
  mcp_server: "MCP Server",
};

const formatCurrency = (amount: number | null | undefined): string => {
  if (amount === null || amount === undefined) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(amount);
};

interface AIResourceFormState {
  name: string;
  description: string;
  organization_id: string;
  resource_type: AIResourceType;
  provider: string;
  model_name: string;
  endpoint_url: string;
  status: AIResourceStatus;
  agreement_level: "" | AIAgreementLevel;
  plan_info: string;
  budget_monthly: string;
  cost_per_10m_tokens: string;
  seats: string;
  notes: string;
  tags: string;
  metadataText: string;
  is_active: boolean;
}

const emptyFormState = (
  defaultType?: AIResourceType,
  defaultOrgId?: string,
): AIResourceFormState => ({
  name: "",
  description: "",
  organization_id: defaultOrgId || "",
  resource_type: defaultType || "llm",
  provider: "",
  model_name: "",
  endpoint_url: "",
  status: "pending_review",
  agreement_level: "",
  plan_info: "",
  budget_monthly: "",
  cost_per_10m_tokens: "",
  seats: "",
  notes: "",
  tags: "",
  metadataText: "",
  is_active: true,
});

const resourceToFormState = (r: AIResource): AIResourceFormState => ({
  name: r.name,
  description: r.description || "",
  organization_id: String(r.organization_id),
  resource_type: r.resource_type,
  provider: r.provider || "",
  model_name: r.model_name || "",
  endpoint_url: r.endpoint_url || "",
  status: r.status,
  agreement_level: r.agreement_level || "",
  plan_info: r.plan_info || "",
  budget_monthly:
    r.budget_monthly !== null && r.budget_monthly !== undefined
      ? String(r.budget_monthly)
      : "",
  cost_per_10m_tokens:
    r.cost_per_10m_tokens !== null && r.cost_per_10m_tokens !== undefined
      ? String(r.cost_per_10m_tokens)
      : "",
  seats: r.seats !== null && r.seats !== undefined ? String(r.seats) : "",
  notes: r.notes || "",
  tags: r.tags?.join(", ") || "",
  metadataText: r.metadata ? JSON.stringify(r.metadata, null, 2) : "",
  is_active: r.is_active,
});

function renderMetadataBadges(metadata?: Record<string, unknown> | null) {
  if (!metadata) return null;
  const badges: string[] = [];
  if (metadata.baa === true) badges.push("BAA");
  if (metadata.fedramp)
    badges.push(`FEDRAMP: ${String(metadata.fedramp).toUpperCase()}`);
  if (metadata.hipaa === true) badges.push("HIPAA");
  if (metadata.soc2 === true) badges.push("SOC2");
  if (badges.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {badges.map((badge) => (
        <span
          key={badge}
          className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300"
        >
          {badge}
        </span>
      ))}
    </div>
  );
}

export default function AIResources({ defaultType }: AIResourcesProps) {
  const [activeType, setActiveType] = useState<"" | AIResourceType>(
    defaultType || "",
  );
  const [search, setSearch] = useState("");
  const [organizationFilter, setOrganizationFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [agreementFilter, setAgreementFilter] = useState("");

  const [showFormModal, setShowFormModal] = useState(false);
  const [editingResource, setEditingResource] = useState<AIResource | null>(
    null,
  );
  const [viewingResource, setViewingResource] = useState<AIResource | null>(
    null,
  );
  const [formState, setFormState] = useState<AIResourceFormState>(() =>
    emptyFormState(defaultType),
  );
  const [metadataError, setMetadataError] = useState<string | null>(null);

  const queryClient = useQueryClient();

  useEffect(() => {
    setActiveType(defaultType || "");
  }, [defaultType]);

  const { data: organizationsData } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  });

  const organizations: Organization[] = useMemo(
    () => organizationsData?.items || [],
    [organizationsData],
  );

  const orgNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const org of organizations) {
      map.set(org.id, org.name);
    }
    return map;
  }, [organizations]);

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.aiResources.list({
      search,
      organization_id: organizationFilter,
      resource_type: activeType,
      status: statusFilter,
      agreement_level: agreementFilter,
    }),
    queryFn: () =>
      api.getAIResources({
        per_page: 100,
        search: search || undefined,
        organization_id: organizationFilter
          ? parseInt(organizationFilter, 10)
          : undefined,
        resource_type: activeType || undefined,
        status: statusFilter || undefined,
        agreement_level: agreementFilter || undefined,
      }),
  });

  const resources: AIResource[] = useMemo(() => data?.items || [], [data]);

  const stats = useMemo(() => {
    const total = data?.total ?? resources.length;
    const allowed = resources.filter((r) => r.status === "allowed").length;
    const pending = resources.filter(
      (r) => r.status === "pending_review",
    ).length;
    const monthlyBudget = resources.reduce(
      (sum, r) => sum + (r.budget_monthly || 0),
      0,
    );
    return { total, allowed, pending, monthlyBudget };
  }, [data, resources]);

  const createMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.createAIResource>[0]) =>
      api.createAIResource(payload),
    onSuccess: async () => {
      await invalidateCache.aiResources(queryClient);
      toast.success("AI resource created");
      setShowFormModal(false);
    },
    onError: () => {
      toast.error("Failed to create AI resource");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: Parameters<typeof api.updateAIResource>[1];
    }) => api.updateAIResource(id, payload),
    onSuccess: async () => {
      await invalidateCache.aiResources(queryClient);
      toast.success("AI resource updated");
      setShowFormModal(false);
      setEditingResource(null);
    },
    onError: () => {
      toast.error("Failed to update AI resource");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteAIResource(id),
    onSuccess: async () => {
      await invalidateCache.aiResources(queryClient);
      toast.success("AI resource deleted");
    },
    onError: () => {
      toast.error("Failed to delete AI resource");
    },
  });

  const openCreateModal = () => {
    setEditingResource(null);
    setMetadataError(null);
    setFormState(
      emptyFormState(
        activeType || defaultType || "llm",
        organizations[0] ? String(organizations[0].id) : "",
      ),
    );
    setShowFormModal(true);
  };

  const openEditModal = (resource: AIResource) => {
    setEditingResource(resource);
    setMetadataError(null);
    setFormState(resourceToFormState(resource));
    setShowFormModal(true);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setMetadataError(null);

    if (!formState.name.trim()) {
      toast.error("Name is required");
      return;
    }
    const orgId = parseInt(formState.organization_id, 10);
    if (!orgId || Number.isNaN(orgId)) {
      toast.error("Organization is required");
      return;
    }

    let parsedMetadata: Record<string, unknown> | null = null;
    if (formState.metadataText.trim()) {
      try {
        const parsed = JSON.parse(formState.metadataText);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          setMetadataError(
            'Metadata must be a JSON object (e.g. {"baa": true})',
          );
          return;
        }
        parsedMetadata = parsed as Record<string, unknown>;
      } catch {
        setMetadataError("Invalid JSON in metadata");
        return;
      }
    }

    const tags = formState.tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    const payload = {
      name: formState.name.trim(),
      description: formState.description.trim() || null,
      organization_id: orgId,
      resource_type: formState.resource_type,
      provider: formState.provider.trim() || null,
      model_name: formState.model_name.trim() || null,
      endpoint_url: formState.endpoint_url.trim() || null,
      status: formState.status,
      agreement_level: formState.agreement_level || null,
      plan_info: formState.plan_info.trim() || null,
      budget_monthly: formState.budget_monthly
        ? parseFloat(formState.budget_monthly)
        : null,
      cost_per_10m_tokens: formState.cost_per_10m_tokens
        ? parseFloat(formState.cost_per_10m_tokens)
        : null,
      seats: formState.seats ? parseInt(formState.seats, 10) : null,
      notes: formState.notes.trim() || null,
      tags: tags.length > 0 ? tags : null,
      metadata: parsedMetadata,
      is_active: formState.is_active,
    };

    if (editingResource) {
      updateMutation.mutate({ id: editingResource.id, payload });
    } else {
      createMutation.mutate(payload);
    }
  };

  const renderTypeIcon = (type: AIResourceType) => {
    if (type === "llm") return <Bot className="w-4 h-4 text-indigo-500" />;
    if (type === "ai_gateway")
      return <Network className="w-4 h-4 text-cyan-500" />;
    return <Server className="w-4 h-4 text-emerald-500" />;
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            AI Resources
          </h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Track and govern approved LLMs, AI gateways, and MCP servers
          </p>
        </div>
        <Button onClick={openCreateModal}>
          <Plus className="w-4 h-4 mr-2" />
          Add AI Resource
        </Button>
      </div>

      {/* Type Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-slate-200 dark:border-slate-700 pb-3">
        {RESOURCE_TABS.map((tab) => {
          const isActive = activeType === tab.value;
          return (
            <button
              key={tab.value || "all"}
              type="button"
              onClick={() => setActiveType(tab.value)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? "bg-blue-600 text-white"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              }`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase">
                Total Resources
              </p>
              <p className="text-2xl font-bold text-slate-900 dark:text-white mt-1">
                {stats.total}
              </p>
            </div>
            <Bot className="w-8 h-8 text-blue-500 opacity-80" />
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase">
                Allowed
              </p>
              <p className="text-2xl font-bold text-green-600 dark:text-green-400 mt-1">
                {stats.allowed}
              </p>
            </div>
            <CheckCircle2 className="w-8 h-8 text-green-500 opacity-80" />
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase">
                Pending Review
              </p>
              <p className="text-2xl font-bold text-amber-600 dark:text-amber-400 mt-1">
                {stats.pending}
              </p>
            </div>
            <Clock className="w-8 h-8 text-amber-500 opacity-80" />
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase">
                Monthly Budget
              </p>
              <p className="text-2xl font-bold text-slate-900 dark:text-white mt-1">
                {formatCurrency(stats.monthlyBudget)}
              </p>
            </div>
            <DollarSign className="w-8 h-8 text-emerald-500 opacity-80" />
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, provider, model..."
                className="pl-9"
              />
            </div>

            <Select
              value={organizationFilter}
              onChange={(e) => setOrganizationFilter(e.target.value)}
            >
              <option value="">All Organizations</option>
              {organizations.map((org) => (
                <option key={org.id} value={String(org.id)}>
                  {org.name}
                </option>
              ))}
            </Select>

            <Select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">All Statuses</option>
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>

            <Select
              value={agreementFilter}
              onChange={(e) => setAgreementFilter(e.target.value)}
            >
              <option value="">All Agreement Levels</option>
              {AGREEMENT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Resources Table */}
      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Inventory ({resources.length})
          </h2>
        </CardHeader>
        <CardContent className="p-0 overflow-x-auto">
          {isLoading ? (
            <div className="p-8 text-center text-slate-500">
              Loading AI resources...
            </div>
          ) : resources.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              No AI resources found. Add one to get started.
            </div>
          ) : (
            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 text-xs uppercase text-slate-500">
                  <th className="py-3 px-4">Name</th>
                  <th className="py-3 px-4">Type</th>
                  <th className="py-3 px-4">Provider / Model</th>
                  <th className="py-3 px-4">Endpoint</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Agreement</th>
                  <th className="py-3 px-4">Plan</th>
                  <th className="py-3 px-4">Budget / mo</th>
                  <th className="py-3 px-4">Cost / 10M</th>
                  <th className="py-3 px-4">Seats</th>
                  <th className="py-3 px-4">Compliance</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                {resources.map((resource) => {
                  const statusMeta = STATUS_BADGE[resource.status];
                  const StatusIcon = statusMeta.icon;
                  return (
                    <tr
                      key={resource.id}
                      className="hover:bg-slate-50 dark:hover:bg-slate-800/40"
                    >
                      <td className="py-3 px-4 font-medium text-slate-900 dark:text-white">
                        <div>{resource.name}</div>
                        <div className="text-xs text-slate-500">
                          {orgNameById.get(resource.organization_id) ||
                            `Org #${resource.organization_id}`}
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        <span className="inline-flex items-center gap-1.5 text-slate-700 dark:text-slate-300">
                          {renderTypeIcon(resource.resource_type)}
                          {TYPE_LABEL[resource.resource_type]}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                        <div>{resource.provider || "-"}</div>
                        {resource.model_name && (
                          <div className="text-xs text-slate-500 font-mono">
                            {resource.model_name}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-4 max-w-[200px] truncate">
                        {resource.endpoint_url ? (
                          <a
                            href={resource.endpoint_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400"
                          >
                            <span className="truncate">
                              {resource.endpoint_url}
                            </span>
                            <ExternalLink className="w-3 h-3 shrink-0" />
                          </a>
                        ) : (
                          <span className="text-slate-400">-</span>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusMeta.className}`}
                        >
                          <StatusIcon className="w-3 h-3" />
                          {statusMeta.label}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        {resource.agreement_level ? (
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${AGREEMENT_BADGE[resource.agreement_level]}`}
                          >
                            {resource.agreement_level}
                          </span>
                        ) : (
                          <span className="text-slate-400">-</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                        {resource.plan_info || "-"}
                      </td>
                      <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                        {formatCurrency(resource.budget_monthly)}
                      </td>
                      <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                        {formatCurrency(resource.cost_per_10m_tokens)}
                      </td>
                      <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                        {resource.seats ?? "-"}
                      </td>
                      <td className="py-3 px-4">
                        {renderMetadataBadges(resource.metadata) || (
                          <span className="text-slate-400">-</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-right whitespace-nowrap">
                        <div className="inline-flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => setViewingResource(resource)}
                            className="p-1.5 text-slate-500 hover:text-blue-600 rounded"
                            title="View Details"
                          >
                            <Eye className="w-4 h-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => openEditModal(resource)}
                            className="p-1.5 text-slate-500 hover:text-amber-600 rounded"
                            title="Edit"
                          >
                            <Edit className="w-4 h-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              confirmDelete(resource.name, () =>
                                deleteMutation.mutate(resource.id),
                              )
                            }
                            className="p-1.5 text-slate-500 hover:text-red-600 rounded"
                            title="Delete"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      {/* Create / Edit Modal */}
      {showFormModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 overflow-y-auto">
          <div className="bg-white dark:bg-slate-900 rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
              <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
                {editingResource ? "Edit AI Resource" : "Add AI Resource"}
              </h3>
              <button
                type="button"
                onClick={() => setShowFormModal(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">
                    Name *
                  </label>
                  <Input
                    required
                    value={formState.name}
                    onChange={(e) =>
                      setFormState({ ...formState, name: e.target.value })
                    }
                    placeholder="e.g. Claude 3.7 Sonnet"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Organization *
                  </label>
                  <Select
                    required
                    value={formState.organization_id}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        organization_id: e.target.value,
                      })
                    }
                  >
                    <option value="">Select Organization</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={String(org.id)}>
                        {org.name}
                      </option>
                    ))}
                  </Select>
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Resource Type *
                  </label>
                  <Select
                    value={formState.resource_type}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        resource_type: e.target.value as AIResourceType,
                      })
                    }
                  >
                    {RESOURCE_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Status *
                  </label>
                  <Select
                    value={formState.status}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        status: e.target.value as AIResourceStatus,
                      })
                    }
                  >
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Provider
                  </label>
                  <Input
                    value={formState.provider}
                    onChange={(e) =>
                      setFormState({ ...formState, provider: e.target.value })
                    }
                    placeholder="e.g. Anthropic, OpenAI, LiteLLM"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Model Name
                  </label>
                  <Input
                    value={formState.model_name}
                    onChange={(e) =>
                      setFormState({ ...formState, model_name: e.target.value })
                    }
                    placeholder="e.g. claude-3-7-sonnet"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-sm font-medium mb-1">
                    Endpoint URL
                  </label>
                  <Input
                    value={formState.endpoint_url}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        endpoint_url: e.target.value,
                      })
                    }
                    placeholder="https://api.anthropic.com"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Agreement Level
                  </label>
                  <Select
                    value={formState.agreement_level}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        agreement_level: e.target.value as
                          | ""
                          | AIAgreementLevel,
                      })
                    }
                  >
                    <option value="">None</option>
                    {AGREEMENT_OPTIONS.map((a) => (
                      <option key={a.value} value={a.value}>
                        {a.label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Plan Info
                  </label>
                  <Input
                    value={formState.plan_info}
                    onChange={(e) =>
                      setFormState({ ...formState, plan_info: e.target.value })
                    }
                    placeholder="e.g. Enterprise API"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Monthly Budget (USD)
                  </label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={formState.budget_monthly}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        budget_monthly: e.target.value,
                      })
                    }
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Cost / 10M Tokens (USD)
                  </label>
                  <Input
                    type="number"
                    step="0.0001"
                    min="0"
                    value={formState.cost_per_10m_tokens}
                    onChange={(e) =>
                      setFormState({
                        ...formState,
                        cost_per_10m_tokens: e.target.value,
                      })
                    }
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Seats
                  </label>
                  <Input
                    type="number"
                    min="0"
                    value={formState.seats}
                    onChange={(e) =>
                      setFormState({ ...formState, seats: e.target.value })
                    }
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium mb-1">
                    Tags (comma-separated)
                  </label>
                  <Input
                    value={formState.tags}
                    onChange={(e) =>
                      setFormState({ ...formState, tags: e.target.value })
                    }
                    placeholder="prod, coding, internal"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium mb-1">
                  Description
                </label>
                <textarea
                  rows={2}
                  value={formState.description}
                  onChange={(e) =>
                    setFormState({ ...formState, description: e.target.value })
                  }
                  className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1">Notes</label>
                <textarea
                  rows={2}
                  value={formState.notes}
                  onChange={(e) =>
                    setFormState({ ...formState, notes: e.target.value })
                  }
                  className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1">
                  Metadata (JSON)
                </label>
                <textarea
                  rows={3}
                  value={formState.metadataText}
                  onChange={(e) => {
                    setFormState({
                      ...formState,
                      metadataText: e.target.value,
                    });
                    setMetadataError(null);
                  }}
                  placeholder='{"baa": true, "fedramp": "moderate"}'
                  className="w-full font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-xs"
                />
                {metadataError && (
                  <p className="text-xs text-red-600 mt-1">{metadataError}</p>
                )}
              </div>

              <label className="inline-flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={formState.is_active}
                  onChange={(e) =>
                    setFormState({ ...formState, is_active: e.target.checked })
                  }
                />
                Active
              </label>

              <div className="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-700">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setShowFormModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={
                    createMutation.isPending || updateMutation.isPending
                  }
                >
                  {editingResource ? "Save Changes" : "Create Resource"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Detail View Modal */}
      {viewingResource && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 overflow-y-auto">
          <div className="bg-white dark:bg-slate-900 rounded-xl shadow-xl w-full max-w-xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
              <div className="flex items-center gap-2">
                {renderTypeIcon(viewingResource.resource_type)}
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
                  {viewingResource.name}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setViewingResource(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6 space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className="text-slate-500 block text-xs">Type</span>
                  <span className="font-medium">
                    {TYPE_LABEL[viewingResource.resource_type]}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">Status</span>
                  <span className="font-medium">
                    {STATUS_BADGE[viewingResource.status].label}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">
                    Organization
                  </span>
                  <span className="font-medium">
                    {orgNameById.get(viewingResource.organization_id) ||
                      `Org #${viewingResource.organization_id}`}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">
                    Agreement Level
                  </span>
                  <span className="font-medium capitalize">
                    {viewingResource.agreement_level || "-"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">Provider</span>
                  <span className="font-medium">
                    {viewingResource.provider || "-"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">
                    Model Name
                  </span>
                  <span className="font-mono">
                    {viewingResource.model_name || "-"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">
                    Plan Info
                  </span>
                  <span>{viewingResource.plan_info || "-"}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">Seats</span>
                  <span>{viewingResource.seats ?? "-"}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">
                    Monthly Budget
                  </span>
                  <span>{formatCurrency(viewingResource.budget_monthly)}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs">
                    Cost / 10M Tokens
                  </span>
                  <span>
                    {formatCurrency(viewingResource.cost_per_10m_tokens)}
                  </span>
                </div>
              </div>

              {viewingResource.endpoint_url && (
                <div>
                  <span className="text-slate-500 block text-xs">
                    Endpoint URL
                  </span>
                  <a
                    href={viewingResource.endpoint_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline break-all"
                  >
                    {viewingResource.endpoint_url}
                  </a>
                </div>
              )}

              {viewingResource.description && (
                <div>
                  <span className="text-slate-500 block text-xs">
                    Description
                  </span>
                  <p className="text-slate-700 dark:text-slate-300">
                    {viewingResource.description}
                  </p>
                </div>
              )}

              {viewingResource.notes && (
                <div>
                  <span className="text-slate-500 block text-xs">Notes</span>
                  <p className="text-slate-700 dark:text-slate-300">
                    {viewingResource.notes}
                  </p>
                </div>
              )}

              <div>
                <span className="text-slate-500 block text-xs mb-1">
                  Metadata
                </span>
                <pre className="bg-slate-100 dark:bg-slate-800 rounded-lg p-3 text-xs font-mono overflow-x-auto">
                  {viewingResource.metadata
                    ? JSON.stringify(viewingResource.metadata, null, 2)
                    : "{}"}
                </pre>
              </div>

              <div className="flex justify-end pt-2 border-t border-slate-200 dark:border-slate-700">
                <Button
                  variant="secondary"
                  onClick={() => setViewingResource(null)}
                >
                  Close
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
