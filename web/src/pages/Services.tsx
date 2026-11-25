import { useState, useMemo, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  Edit,
  Trash2,
  Server,
  Globe,
  Lock,
  ExternalLink,
  Clock,
  Play,
  Route,
  Check,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { invalidateCache } from "@/lib/invalidateCache";
import { getStatusColor } from "@/lib/colorHelpers";
import { confirmDelete } from "@/lib/confirmActions";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import ModalFormBuilder from "@/components/ModalFormBuilder";
import SearchableSelect from "@/components/SearchableSelect";
import { FormField } from "@/types/form";
import OnCallBadge from "@/components/OnCallBadge";
import VillageIdBadge from "@/components/VillageIdBadge";

interface Organization {
  id: number;
  name: string;
}

interface Service {
  id: number;
  name: string;
  description?: string;
  organization_id: number;
  language?: string;
  deployment_method?: string;
  status?: string;
  port?: number;
  is_public?: boolean;
  domains?: string[];
  paths?: string[];
  village_id?: string | null;
}

interface SBOMSchedule {
  id: number;
  schedule_cron: string;
  is_active: boolean;
  next_run_at?: string;
  last_run_at?: string;
}

interface SBOMScan {
  id: number;
  status: string;
  components_found?: number;
  created_at: string;
}

const SERVICE_STATUSES = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "maintenance", label: "Maintenance" },
  { value: "deprecated", label: "Deprecated" },
];

const LANGUAGES = [
  { value: "python", label: "Python" },
  { value: "go", label: "Go" },
  { value: "javascript", label: "JavaScript" },
  { value: "typescript", label: "TypeScript" },
  { value: "java", label: "Java" },
  { value: "rust", label: "Rust" },
  { value: "ruby", label: "Ruby" },
  { value: "php", label: "PHP" },
  { value: "csharp", label: "C#" },
  { value: "other", label: "Other" },
];

const DEPLOYMENT_METHODS = [
  { value: "kubernetes", label: "Kubernetes" },
  { value: "docker", label: "Docker" },
  { value: "docker-compose", label: "Docker Compose" },
  { value: "serverless", label: "Serverless" },
  { value: "vm", label: "Virtual Machine" },
  { value: "bare-metal", label: "Bare Metal" },
  { value: "paas", label: "PaaS" },
  { value: "other", label: "Other" },
];

const ENDPOINT_METHODS = [
  "ALL",
  "GET",
  "POST",
  "PUT",
  "DELETE",
  "PATCH",
  "OPTIONS",
  "HEAD",
] as const;

const COMMON_ENDPOINT_PATHS = [
  "/",
  "/.well-known/openid-configuration",
  "/api/v1",
  "/api/v1/health",
  "/api/v1/status",
  "/docs",
  "/graphql",
  "/health",
  "/healthz",
  "/metrics",
  "/openapi.json",
  "/ready",
  "/readyz",
  "/swagger",
];

type EndpointMethod = (typeof ENDPOINT_METHODS)[number];

interface EndpointRow {
  id: string;
  method: EndpointMethod;
  path: string;
}

function createEmptyEndpointRow(index: number): EndpointRow {
  return {
    id: `endpoint-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`,
    method: "ALL",
    path: "",
  };
}

function parsePathToEndpointRow(rawPath: string, index: number): EndpointRow {
  const trimmed = rawPath.trim();
  const parts = trimmed.split(/\s+/);
  if (
    parts.length >= 2 &&
    ENDPOINT_METHODS.includes(parts[0].toUpperCase() as EndpointMethod)
  ) {
    return {
      id: `endpoint-init-${index}`,
      method: parts[0].toUpperCase() as EndpointMethod,
      path: parts.slice(1).join(" "),
    };
  }
  return {
    id: `endpoint-init-${index}`,
    method: "ALL",
    path: trimmed,
  };
}

function formatEndpointRowsToPaths(rows: EndpointRow[]): string[] {
  const seen = new Set<string>();
  const paths: string[] = [];
  for (const row of rows) {
    const trimmedPath = row.path.trim();
    if (!trimmedPath) continue;
    const formatted =
      row.method === "ALL" ? trimmedPath : `${row.method} ${trimmedPath}`;
    if (!seen.has(formatted)) {
      seen.add(formatted);
      paths.push(formatted);
    }
  }
  return paths;
}

function countValidEndpoints(rows: EndpointRow[]): number {
  return formatEndpointRowsToPaths(rows).length;
}

function getEndpointMethodColor(method: EndpointMethod): string {
  switch (method) {
    case "GET":
      return "bg-blue-500/20 text-blue-400";
    case "POST":
      return "bg-green-500/20 text-green-400";
    case "PUT":
      return "bg-yellow-500/20 text-yellow-400";
    case "DELETE":
      return "bg-red-500/20 text-red-400";
    case "PATCH":
      return "bg-purple-500/20 text-purple-400";
    default:
      return "bg-slate-500/20 text-slate-300";
  }
}

export default function Services() {
  const [search, setSearch] = useState("");
  const [organizationFilter, setOrganizationFilter] = useState<string>("");
  const [languageFilter, setLanguageFilter] = useState<string>("");
  const [deploymentFilter, setDeploymentFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingService, setEditingService] = useState<Service | null>(null);
  const [viewingService, setViewingService] = useState<Service | null>(null);
  const [viewingServiceTab, setViewingServiceTab] = useState<
    "details" | "endpoints" | "schedules"
  >("details");
  const [searchParams] = useSearchParams();
  const highlightId = searchParams.get("highlight");
  const appliedHighlightRef = useRef<string | null>(null);
  const queryClient = useQueryClient();

  const { data: organizations } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  });

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.services.list({
      search,
      organization_id: organizationFilter,
      language: languageFilter,
      deployment_method: deploymentFilter,
      status: statusFilter,
    }),
    queryFn: () =>
      api.getServices({
        search,
        organization_id: organizationFilter
          ? parseInt(organizationFilter)
          : undefined,
        language: languageFilter || undefined,
        deployment_method: deploymentFilter || undefined,
        status: statusFilter || undefined,
      }),
  });

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.createService(data as Parameters<typeof api.createService>[0]),
    onSuccess: async () => {
      await invalidateCache.services(queryClient);
      toast.success("Service created successfully");
      setShowCreateModal(false);
    },
    onError: () => {
      toast.error("Failed to create service");
    },
  });

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.updateService(
        editingService!.id,
        data as Parameters<typeof api.updateService>[1],
      ),
    onSuccess: async () => {
      await invalidateCache.services(queryClient);
      toast.success("Service updated successfully");
      setEditingService(null);
    },
    onError: () => {
      toast.error("Failed to update service");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteService(id),
    onSuccess: async () => {
      await invalidateCache.services(queryClient);
      toast.success("Service deleted successfully");
    },
    onError: () => {
      toast.error("Failed to delete service");
    },
  });

  const handleDelete = (id: number, name: string) => {
    confirmDelete(`service "${name}"`, () => {
      deleteMutation.mutate(id);
    });
  };

  useEffect(() => {
    if (!highlightId || !data?.items?.length) return;
    const match = (data.items as Service[]).find(
      (s) => String(s.id) === highlightId || s.village_id === highlightId,
    );
    if (!match) return;
    if (appliedHighlightRef.current !== highlightId) {
      appliedHighlightRef.current = highlightId;
      setViewingService(match);
    }
    const el = document.getElementById(`service-${match.id}`);
    el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlightId, data?.items]);

  const getLanguageColor = (language: string) => {
    switch (language) {
      case "python":
        return "bg-blue-500/20 text-blue-400";
      case "go":
        return "bg-cyan-500/20 text-cyan-400";
      case "javascript":
      case "typescript":
        return "bg-yellow-500/20 text-yellow-400";
      case "java":
        return "bg-orange-500/20 text-orange-400";
      case "rust":
        return "bg-red-500/20 text-red-400";
      default:
        return "bg-purple-500/20 text-purple-400";
    }
  };

  // Build organization options for form
  const organizationOptions = useMemo(
    () =>
      organizations?.items?.map((org: Organization) => ({
        value: org.id.toString(),
        label: org.name,
      })) || [],
    [organizations?.items],
  );

  const { data: allServicesData } = useQuery({
    queryKey: queryKeys.services.list({ per_page: 1000 }),
    queryFn: () => api.getServices({ per_page: 1000 }),
    enabled: showCreateModal || !!editingService || !!viewingService,
  });

  // Collect unique existing endpoint paths across all services for lookup/autocomplete
  const endpointPathOptions = useMemo(() => {
    const pathSet = new Set<string>(COMMON_ENDPOINT_PATHS);
    const items: Service[] = allServicesData?.items || data?.items || [];
    items.forEach((svc) => {
      svc.paths?.forEach((rawPath, idx) => {
        const parsed = parsePathToEndpointRow(rawPath, idx);
        if (parsed.path) {
          pathSet.add(parsed.path);
        }
      });
    });
    return Array.from(pathSet)
      .sort((a, b) => a.localeCompare(b))
      .map((path) => ({ value: path, label: path }));
  }, [allServicesData?.items, data?.items]);

  const handleCreateSubmit = (
    serviceData: Parameters<typeof api.createService>[0],
  ) => {
    createMutation.mutate(serviceData);
  };

  const handleEditSubmit = (
    serviceData: Parameters<typeof api.createService>[0],
  ) => {
    const updateData: Parameters<typeof api.updateService>[1] = {
      name: serviceData.name,
      description: serviceData.description,
      language: serviceData.language,
      deployment_method: serviceData.deployment_method,
      is_public: serviceData.is_public,
      domains: serviceData.domains,
      paths: serviceData.paths,
      port: serviceData.port,
      status: serviceData.status,
    };
    updateMutation.mutate(updateData);
  };

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Services</h1>
          <p className="mt-2 text-slate-400">
            Track and manage microservices across your infrastructure
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Service
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        <div className="flex-1 min-w-[200px] max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search services..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={organizationFilter}
          onChange={(e) => setOrganizationFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Organizations</option>
          {organizations?.items?.map((org: Organization) => (
            <option key={org.id} value={org.id}>
              {org.name}
            </option>
          ))}
        </Select>
        <Select
          value={languageFilter}
          onChange={(e) => setLanguageFilter(e.target.value)}
          className="w-40"
        >
          <option value="">All Languages</option>
          {LANGUAGES.map((lang) => (
            <option key={lang.value} value={lang.value}>
              {lang.label}
            </option>
          ))}
        </Select>
        <Select
          value={deploymentFilter}
          onChange={(e) => setDeploymentFilter(e.target.value)}
          className="w-44"
        >
          <option value="">All Deployments</option>
          {DEPLOYMENT_METHODS.map((method) => (
            <option key={method.value} value={method.value}>
              {method.label}
            </option>
          ))}
        </Select>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-36"
        >
          <option value="">All Statuses</option>
          {SERVICE_STATUSES.map((status) => (
            <option key={status.value} value={status.value}>
              {status.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Services List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Server className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No services found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first service
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((service: Service) => {
            const isHighlighted = Boolean(
              highlightId &&
                (String(service.id) === highlightId ||
                  service.village_id === highlightId),
            );
            return (
              <Card
                key={service.id}
                id={`service-${service.id}`}
                data-highlighted={isHighlighted ? "true" : undefined}
                className={`cursor-pointer hover:border-primary-500/50 transition-colors ${
                  isHighlighted
                    ? "ring-2 ring-primary-500 border-primary-500 bg-primary-500/10"
                    : ""
                }`}
                onClick={() => {
                  setViewingServiceTab("details");
                  setViewingService(service);
                }}
              >
                <CardContent>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <Server className="w-5 h-5 text-primary-400 flex-shrink-0" />
                      <h3 className="text-lg font-semibold text-white truncate">
                        {service.name}
                      </h3>
                      {service.is_public ? (
                        <span title="Public">
                          <Globe className="w-4 h-4 text-green-400 flex-shrink-0" />
                        </span>
                      ) : (
                        <span title="Private">
                          <Lock className="w-4 h-4 text-slate-400 flex-shrink-0" />
                        </span>
                      )}
                    </div>
                    <div className="flex gap-1.5 flex-shrink-0">
                      <button
                        type="button"
                        title="Manage Endpoints"
                        aria-label={`Manage endpoints for ${service.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setViewingServiceTab("endpoints");
                          setViewingService(service);
                        }}
                        className="p-1.5 text-slate-400 hover:text-primary-400 hover:bg-primary-500/10 rounded transition-colors"
                      >
                        <Route className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        title="Edit Service"
                        aria-label={`Edit ${service.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingService(service);
                        }}
                        className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        title="Delete Service"
                        aria-label={`Delete ${service.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(service.id, service.name);
                        }}
                        className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {service.village_id && (
                    <div className="mb-2">
                      <VillageIdBadge villageId={service.village_id} />
                    </div>
                  )}

                  {service.description && (
                    <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                      {service.description}
                    </p>
                  )}

                  <div className="flex flex-wrap gap-2 mb-3">
                    <span
                      className={`text-xs px-2 py-1 rounded ${getStatusColor(service.status)}`}
                    >
                      {service.status}
                    </span>
                    <span
                      className={`text-xs px-2 py-1 rounded ${getLanguageColor(service.language)}`}
                    >
                      {service.language}
                    </span>
                    <span className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-300">
                      {service.deployment_method}
                    </span>
                    {service.paths && service.paths.length > 0 && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setViewingServiceTab("endpoints");
                          setViewingService(service);
                        }}
                        className="text-xs px-2 py-1 rounded bg-primary-500/15 text-primary-300 hover:bg-primary-500/25 transition-colors inline-flex items-center gap-1"
                      >
                        <Route className="w-3 h-3" />
                        {service.paths.length}{" "}
                        {service.paths.length === 1 ? "endpoint" : "endpoints"}
                      </button>
                    )}
                  </div>

                  {service.port && (
                    <div className="text-xs text-slate-500 mb-3">
                      Port: {service.port}
                    </div>
                  )}

                  <div className="mb-3">
                    <OnCallBadge
                      scopeType="service"
                      scopeId={service.id}
                      compact
                    />
                  </div>

                  {service.domains && service.domains.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {service.domains
                        .slice(0, 2)
                        .map((domain: string, idx: number) => (
                          <span
                            key={idx}
                            className="text-xs px-2 py-0.5 rounded bg-primary-500/20 text-primary-400"
                          >
                            {domain}
                          </span>
                        ))}
                      {service.domains.length > 2 && (
                        <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-400">
                          +{service.domains.length - 2} more
                        </span>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <ServiceFormModal
          onClose={() => setShowCreateModal(false)}
          title="Create Service"
          submitLabel="Create"
          organizationOptions={organizationOptions}
          endpointPathOptions={endpointPathOptions}
          onSubmit={handleCreateSubmit}
          isLoading={createMutation.isPending}
        />
      )}

      {/* Edit Modal */}
      {editingService && (
        <ServiceFormModal
          key={editingService.id}
          onClose={() => setEditingService(null)}
          title="Edit Service"
          submitLabel="Update"
          initialService={editingService}
          organizationOptions={organizationOptions}
          endpointPathOptions={endpointPathOptions}
          onSubmit={handleEditSubmit}
          isLoading={updateMutation.isPending}
        />
      )}

      {/* View Details Modal */}
      {viewingService && (
        <ServiceDetailsModal
          key={`${viewingService.id}-${viewingServiceTab}`}
          service={viewingService}
          initialTab={viewingServiceTab}
          endpointPathOptions={endpointPathOptions}
          onServiceUpdated={(updated) => setViewingService(updated)}
          onClose={() => setViewingService(null)}
          onEdit={() => {
            setEditingService(viewingService);
            setViewingService(null);
          }}
        />
      )}
    </div>
  );
}

interface ServiceFormModalProps {
  onClose: () => void;
  title: string;
  submitLabel: string;
  initialService?: Service | null;
  initialTab?: "details" | "endpoints";
  organizationOptions: Array<{ value: string; label: string }>;
  endpointPathOptions: Array<{ value: string; label: string }>;
  onSubmit: (data: Parameters<typeof api.createService>[0]) => void;
  isLoading: boolean;
}

function ServiceFormModal({
  onClose,
  title,
  submitLabel,
  initialService,
  initialTab = "details",
  organizationOptions,
  endpointPathOptions,
  onSubmit,
  isLoading,
}: ServiceFormModalProps) {
  const [activeTab, setActiveTab] = useState<"details" | "endpoints">(
    initialTab,
  );
  const [name, setName] = useState(initialService?.name || "");
  const [description, setDescription] = useState(
    initialService?.description || "",
  );
  const [organizationId, setOrganizationId] = useState(
    initialService?.organization_id?.toString() || "",
  );
  const [language, setLanguage] = useState(
    initialService?.language || "python",
  );
  const [deploymentMethod, setDeploymentMethod] = useState(
    initialService?.deployment_method || "kubernetes",
  );
  const [status, setStatus] = useState(initialService?.status || "active");
  const [port, setPort] = useState(initialService?.port?.toString() || "");
  const [isPublic, setIsPublic] = useState(initialService?.is_public || false);
  const [domainsText, setDomainsText] = useState(
    initialService?.domains?.join("\n") || "",
  );
  const [endpointRows, setEndpointRows] = useState<EndpointRow[]>(() => {
    if (initialService?.paths && initialService.paths.length > 0) {
      return initialService.paths.map((p, idx) =>
        parsePathToEndpointRow(p, idx),
      );
    }
    return [createEmptyEndpointRow(0)];
  });

  const validEndpointCount = useMemo(
    () => countValidEndpoints(endpointRows),
    [endpointRows],
  );

  const handleAddEndpointRow = () => {
    setEndpointRows((prev) => [...prev, createEmptyEndpointRow(prev.length)]);
  };

  const handleRemoveEndpointRow = (id: string) => {
    setEndpointRows((prev) => prev.filter((row) => row.id !== id));
  };

  const handleUpdateEndpointRow = (
    id: string,
    patch: Partial<Pick<EndpointRow, "method" | "path">>,
  ) => {
    setEndpointRows((prev) =>
      prev.map((row) => (row.id === id ? { ...row, ...patch } : row)),
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setActiveTab("details");
      toast.error("Service name is required");
      return;
    }
    if (!organizationId) {
      setActiveTab("details");
      toast.error("Organization is required");
      return;
    }

    const parsedDomains = domainsText
      .split("\n")
      .map((d) => d.trim())
      .filter(Boolean);

    const formattedPaths = formatEndpointRowsToPaths(endpointRows);

    onSubmit({
      name: name.trim(),
      organization_id: parseInt(organizationId, 10),
      description: description.trim() || undefined,
      language: language || undefined,
      deployment_method: deploymentMethod || undefined,
      is_public: isPublic,
      domains: parsedDomains.length > 0 ? parsedDomains : undefined,
      paths: formattedPaths.length > 0 ? formattedPaths : [],
      port: port ? parseInt(port, 10) : undefined,
      status: status || undefined,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">{title}</h2>
          <div
            role="tablist"
            aria-label="Service form sections"
            className="flex gap-4 mt-4 border-b border-slate-700"
          >
            <button
              type="button"
              id="service-form-tab-details"
              role="tab"
              aria-selected={activeTab === "details"}
              aria-controls="service-form-panel-details"
              onClick={() => setActiveTab("details")}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === "details"
                  ? "border-primary-500 text-primary-400"
                  : "border-transparent text-slate-400 hover:text-white"
              }`}
            >
              Details
            </button>
            <button
              type="button"
              id="service-form-tab-endpoints"
              role="tab"
              aria-selected={activeTab === "endpoints"}
              aria-controls="service-form-panel-endpoints"
              onClick={() => setActiveTab("endpoints")}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors inline-flex items-center gap-2 ${
                activeTab === "endpoints"
                  ? "border-primary-500 text-primary-400"
                  : "border-transparent text-slate-400 hover:text-white"
              }`}
            >
              <span>Endpoints</span>
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-slate-700 text-slate-200">
                {validEndpointCount}
              </span>
            </button>
          </div>
        </CardHeader>
        <CardContent className="overflow-y-auto flex-1">
          <form onSubmit={handleSubmit} className="space-y-4">
            {activeTab === "details" ? (
              <div
                id="service-form-panel-details"
                role="tabpanel"
                aria-labelledby="service-form-tab-details"
                className="space-y-4"
              >
                <Input
                  label="Name *"
                  required
                  placeholder="api-gateway"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />

                <div>
                  <label
                    htmlFor="service-description"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Description
                  </label>
                  <textarea
                    id="service-description"
                    rows={2}
                    placeholder="Service description (optional)"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>

                <div>
                  <span className="block text-sm font-medium text-yellow-500 mb-1.5">
                    Organization *
                  </span>
                  <SearchableSelect
                    options={organizationOptions}
                    value={organizationId}
                    onChange={(val) => setOrganizationId(String(val))}
                    placeholder={
                      organizationOptions.length
                        ? "Select organization"
                        : "No organizations found"
                    }
                    ariaLabel="Organization"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <Select
                    label="Language"
                    options={LANGUAGES}
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                  />
                  <Select
                    label="Deployment Method"
                    options={DEPLOYMENT_METHODS}
                    value={deploymentMethod}
                    onChange={(e) => setDeploymentMethod(e.target.value)}
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <Select
                    label="Status"
                    options={SERVICE_STATUSES}
                    value={status}
                    onChange={(e) => setStatus(e.target.value)}
                  />
                  <Input
                    label="Port"
                    type="number"
                    placeholder="8080"
                    value={port}
                    onChange={(e) => setPort(e.target.value)}
                  />
                </div>

                <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isPublic}
                    onChange={(e) => setIsPublic(e.target.checked)}
                    className="rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
                  />
                  Public service (accessible from internet)
                </label>

                <div>
                  <label
                    htmlFor="service-domains"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Domains (one per line)
                  </label>
                  <textarea
                    id="service-domains"
                    rows={2}
                    placeholder={"api.example.com\napi-v2.example.com"}
                    value={domainsText}
                    onChange={(e) => setDomainsText(e.target.value)}
                    className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>
              </div>
            ) : (
              <div
                id="service-form-panel-endpoints"
                role="tabpanel"
                aria-labelledby="service-form-tab-endpoints"
                className="space-y-4"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-medium text-white">
                      Service Endpoints / Paths
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      {initialService
                        ? "Add, edit, or remove endpoints for this service. Changes are saved when you click Update."
                        : "Stage endpoints now — they will be saved automatically when you create the service."}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={handleAddEndpointRow}
                  >
                    <Plus className="w-3.5 h-3.5 mr-1" />
                    Add Endpoint
                  </Button>
                </div>

                {endpointRows.length === 0 ? (
                  <div className="text-center py-6 bg-slate-800/30 rounded-lg border border-slate-700/60">
                    <Route className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                    <p className="text-xs text-slate-400">
                      No endpoints configured yet. Click &ldquo;Add
                      Endpoint&rdquo; to add one.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {endpointRows.map((row, idx) => (
                      <div key={row.id} className="flex items-center gap-2">
                        <select
                          aria-label={`Endpoint ${idx + 1} method`}
                          value={row.method}
                          onChange={(e) =>
                            handleUpdateEndpointRow(row.id, {
                              method: e.target.value as EndpointMethod,
                            })
                          }
                          className="w-24 shrink-0 px-2.5 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                        >
                          {ENDPOINT_METHODS.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                        </select>

                        <SearchableSelect
                          options={endpointPathOptions}
                          value={row.path}
                          onChange={(val) =>
                            handleUpdateEndpointRow(row.id, {
                              path: String(val),
                            })
                          }
                          placeholder="/api/v1/resource"
                          allowCustom
                          allowClear
                          ariaLabel={`Endpoint ${idx + 1} path`}
                          className="flex-1"
                        />

                        <button
                          type="button"
                          aria-label={`Remove endpoint ${idx + 1}`}
                          onClick={() => handleRemoveEndpointRow(row.id)}
                          className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors shrink-0"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                <p className="text-xs text-slate-500">
                  Pick an existing endpoint path or type a new one and press
                  Enter (or click &ldquo;Use ...&rdquo;).
                </p>
              </div>
            )}

            <div className="flex justify-end gap-3 pt-4 border-t border-slate-700">
              <Button
                type="button"
                variant="ghost"
                onClick={onClose}
                disabled={isLoading}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? "Saving..." : submitLabel}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

interface ServiceDetailsModalProps {
  service: Service;
  initialTab?: "details" | "endpoints" | "schedules";
  endpointPathOptions: Array<{ value: string; label: string }>;
  onServiceUpdated: (updated: Service) => void;
  onClose: () => void;
  onEdit: () => void;
}

function ServiceDetailsModal({
  service,
  initialTab = "details",
  endpointPathOptions,
  onServiceUpdated,
  onClose,
  onEdit,
}: ServiceDetailsModalProps) {
  const [activeTab, setActiveTab] = useState<
    "details" | "endpoints" | "schedules"
  >(initialTab);
  const endpointCount = service.paths?.length ?? 0;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 flex-wrap">
              <Server className="w-5 h-5 text-primary-400" />
              <h2 className="text-xl font-semibold text-white">
                {service.name}
              </h2>
              {service.is_public ? (
                <span title="Public">
                  <Globe className="w-4 h-4 text-green-400" />
                </span>
              ) : (
                <span title="Private">
                  <Lock className="w-4 h-4 text-slate-400" />
                </span>
              )}
              {service.village_id && (
                <VillageIdBadge villageId={service.village_id} />
              )}
            </div>
            <button
              type="button"
              aria-label={`Edit ${service.name}`}
              onClick={onEdit}
              className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
            >
              <Edit className="w-4 h-4" />
            </button>
          </div>
          <div
            role="tablist"
            aria-label="Service details sections"
            className="flex gap-4 mt-4 border-b border-slate-700"
          >
            <button
              type="button"
              id="service-details-tab-details"
              role="tab"
              aria-selected={activeTab === "details"}
              aria-controls="service-details-panel-details"
              onClick={() => setActiveTab("details")}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === "details"
                  ? "border-primary-500 text-primary-400"
                  : "border-transparent text-slate-400 hover:text-white"
              }`}
            >
              Details
            </button>
            <button
              type="button"
              id="service-details-tab-endpoints"
              role="tab"
              aria-selected={activeTab === "endpoints"}
              aria-controls="service-details-panel-endpoints"
              onClick={() => setActiveTab("endpoints")}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors inline-flex items-center gap-2 ${
                activeTab === "endpoints"
                  ? "border-primary-500 text-primary-400"
                  : "border-transparent text-slate-400 hover:text-white"
              }`}
            >
              <span>Endpoints</span>
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-slate-700 text-slate-200">
                {endpointCount}
              </span>
            </button>
            <button
              type="button"
              id="service-details-tab-schedules"
              role="tab"
              aria-selected={activeTab === "schedules"}
              aria-controls="service-details-panel-schedules"
              onClick={() => setActiveTab("schedules")}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === "schedules"
                  ? "border-primary-500 text-primary-400"
                  : "border-transparent text-slate-400 hover:text-white"
              }`}
            >
              SBOM Schedules
            </button>
          </div>
        </CardHeader>
        <CardContent className="overflow-y-auto flex-1">
          {activeTab === "details" ? (
            <div
              id="service-details-panel-details"
              role="tabpanel"
              aria-labelledby="service-details-tab-details"
            >
              <ServiceDetailsTab
                service={service}
                onOpenEndpoints={() => setActiveTab("endpoints")}
              />
            </div>
          ) : activeTab === "endpoints" ? (
            <div
              id="service-details-panel-endpoints"
              role="tabpanel"
              aria-labelledby="service-details-tab-endpoints"
            >
              <ServiceEndpointsTab
                service={service}
                endpointPathOptions={endpointPathOptions}
                onServiceUpdated={onServiceUpdated}
              />
            </div>
          ) : (
            <div
              id="service-details-panel-schedules"
              role="tabpanel"
              aria-labelledby="service-details-tab-schedules"
            >
              <ServiceSchedulesTab service={service} />
            </div>
          )}
        </CardContent>
        <div className="border-t border-slate-700 p-4 flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </Card>
    </div>
  );
}

function ServiceDetailsTab({
  service,
  onOpenEndpoints,
}: {
  service: Service;
  onOpenEndpoints: () => void;
}) {
  return (
    <div className="space-y-4">
      {service.description && (
        <p className="text-sm text-slate-400">{service.description}</p>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <span className="block text-xs text-slate-500 uppercase">Status</span>
          <div className="mt-1">
            <span
              className={`text-sm px-2 py-1 rounded ${getStatusColor(service.status)}`}
            >
              {service.status}
            </span>
          </div>
        </div>
        <div>
          <span className="block text-xs text-slate-500 uppercase">
            Language
          </span>
          <p className="text-sm text-white mt-1">{service.language}</p>
        </div>
        <div>
          <span className="block text-xs text-slate-500 uppercase">
            Deployment
          </span>
          <p className="text-sm text-white mt-1">{service.deployment_method}</p>
        </div>
        {service.port && (
          <div>
            <span className="block text-xs text-slate-500 uppercase">Port</span>
            <p className="text-sm text-white mt-1">{service.port}</p>
          </div>
        )}
      </div>

      {service.domains && service.domains.length > 0 && (
        <div>
          <span className="block text-xs text-slate-500 uppercase">
            Domains
          </span>
          <div className="flex flex-wrap gap-2 mt-2">
            {service.domains.map((domain: string, idx: number) => (
              <span
                key={idx}
                className="text-sm px-2 py-1 rounded bg-primary-500/20 text-primary-400 flex items-center gap-1"
              >
                {domain}
                <ExternalLink className="w-3 h-3" />
              </span>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="flex items-center justify-between">
          <span className="block text-xs text-slate-500 uppercase">
            Endpoints ({service.paths?.length ?? 0})
          </span>
          <button
            type="button"
            onClick={onOpenEndpoints}
            className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
          >
            Manage endpoints
          </button>
        </div>
        {service.paths && service.paths.length > 0 ? (
          <div className="flex flex-wrap gap-2 mt-2">
            {service.paths.map((rawPath: string, idx: number) => {
              const parsed = parsePathToEndpointRow(rawPath, idx);
              return (
                <span
                  key={idx}
                  className="text-xs px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-300 inline-flex items-center gap-1.5 font-mono"
                >
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-sans font-semibold ${getEndpointMethodColor(parsed.method)}`}
                  >
                    {parsed.method}
                  </span>
                  {parsed.path}
                </span>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-slate-500 mt-1">
            No endpoints configured yet.
          </p>
        )}
      </div>
    </div>
  );
}

interface ServiceEndpointsTabProps {
  service: Service;
  endpointPathOptions: Array<{ value: string; label: string }>;
  onServiceUpdated: (updated: Service) => void;
}

function ServiceEndpointsTab({
  service,
  endpointPathOptions,
  onServiceUpdated,
}: ServiceEndpointsTabProps) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [methodFilter, setMethodFilter] = useState("");
  const [newMethod, setNewMethod] = useState<EndpointMethod>("GET");
  const [newPath, setNewPath] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editMethod, setEditMethod] = useState<EndpointMethod>("GET");
  const [editPath, setEditPath] = useState("");

  const parsedEndpoints = useMemo(
    () =>
      (service.paths || []).map((rawPath, idx) => ({
        ...parsePathToEndpointRow(rawPath, idx),
        index: idx,
        raw: rawPath,
      })),
    [service.paths],
  );

  const filteredEndpoints = useMemo(
    () =>
      parsedEndpoints.filter((ep) => {
        if (
          search &&
          !ep.path.toLowerCase().includes(search.toLowerCase()) &&
          !ep.method.toLowerCase().includes(search.toLowerCase())
        ) {
          return false;
        }
        if (methodFilter && ep.method !== methodFilter) {
          return false;
        }
        return true;
      }),
    [parsedEndpoints, search, methodFilter],
  );

  const updatePathsMutation = useMutation({
    mutationFn: ({
      nextPaths,
    }: {
      nextPaths: string[];
      successMessage: string;
    }) => api.updateService(service.id, { paths: nextPaths }),
    onSuccess: async (_data, variables) => {
      await invalidateCache.services(queryClient);
      onServiceUpdated({ ...service, paths: variables.nextPaths });
      toast.success(variables.successMessage);
    },
    onError: () => {
      toast.error("Failed to update service endpoints");
    },
  });

  const handleAddEndpoint = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = newPath.trim();
    if (!trimmed) {
      toast.error("Endpoint path is required");
      return;
    }
    const nextRows: EndpointRow[] = [
      ...parsedEndpoints,
      { id: "new", method: newMethod, path: trimmed },
    ];
    const nextPaths = formatEndpointRowsToPaths(nextRows);
    updatePathsMutation.mutate(
      { nextPaths, successMessage: "Endpoint added" },
      {
        onSuccess: () => {
          setNewPath("");
        },
      },
    );
  };

  const handleStartEdit = (idx: number, method: EndpointMethod, path: string) => {
    setEditingIndex(idx);
    setEditMethod(method);
    setEditPath(path);
  };

  const handleCancelEdit = () => {
    setEditingIndex(null);
    setEditPath("");
  };

  const handleSaveEdit = (idx: number) => {
    const trimmed = editPath.trim();
    if (!trimmed) {
      toast.error("Endpoint path is required");
      return;
    }
    const nextRows = parsedEndpoints.map((ep) =>
      ep.index === idx ? { ...ep, method: editMethod, path: trimmed } : ep,
    );
    const nextPaths = formatEndpointRowsToPaths(nextRows);
    updatePathsMutation.mutate(
      { nextPaths, successMessage: "Endpoint updated" },
      {
        onSuccess: () => {
          setEditingIndex(null);
          setEditPath("");
        },
      },
    );
  };

  const handleDeleteEndpoint = (idx: number) => {
    const nextRows = parsedEndpoints.filter((ep) => ep.index !== idx);
    const nextPaths = formatEndpointRowsToPaths(nextRows);
    updatePathsMutation.mutate({
      nextPaths,
      successMessage: "Endpoint removed",
    });
  };

  return (
    <div className="space-y-4">
      {/* Inline Add Endpoint Form */}
      <form
        onSubmit={handleAddEndpoint}
        className="p-3 bg-slate-800/50 rounded-lg border border-slate-700 space-y-3"
      >
        <div className="text-xs font-medium text-slate-300 uppercase tracking-wider">
          Add Endpoint
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-28">
            <label
              htmlFor="new-endpoint-method"
              className="block text-xs text-slate-400 mb-1"
            >
              Method
            </label>
            <select
              id="new-endpoint-method"
              value={newMethod}
              onChange={(e) => setNewMethod(e.target.value as EndpointMethod)}
              className="w-full px-2.5 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              {ENDPOINT_METHODS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-w-[200px]">
            <label
              htmlFor="new-endpoint-path"
              className="block text-xs text-slate-400 mb-1"
            >
              Path
            </label>
            <input
              id="new-endpoint-path"
              type="text"
              list="service-endpoint-path-suggestions"
              placeholder="/api/v1/resource"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              className="block w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 font-mono"
            />
            <datalist id="service-endpoint-path-suggestions">
              {endpointPathOptions.map((opt) => (
                <option key={opt.value} value={opt.value} />
              ))}
            </datalist>
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={updatePathsMutation.isPending}
          >
            <Plus className="w-3.5 h-3.5 mr-1" />
            Add Endpoint
          </Button>
        </div>
      </form>

      {/* Search & Method Filter */}
      {parsedEndpoints.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <div className="relative flex-1 min-w-[180px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              type="text"
              aria-label="Search endpoints"
              placeholder="Filter endpoints..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="block w-full pl-9 pr-3 py-1.5 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
          </div>
          <select
            aria-label="Filter endpoints by method"
            value={methodFilter}
            onChange={(e) => setMethodFilter(e.target.value)}
            className="px-3 py-1.5 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          >
            <option value="">All Methods</option>
            {ENDPOINT_METHODS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Endpoints List */}
      {parsedEndpoints.length === 0 ? (
        <div className="text-center py-8 bg-slate-800/30 rounded-lg border border-slate-700/60">
          <Route className="w-8 h-8 text-slate-600 mx-auto mb-2" />
          <p className="text-sm text-slate-400">
            No endpoints configured for this service
          </p>
          <p className="text-xs text-slate-500 mt-1">
            Add an HTTP method and path above to register an endpoint.
          </p>
        </div>
      ) : filteredEndpoints.length === 0 ? (
        <div className="text-center py-6 bg-slate-800/30 rounded-lg">
          <p className="text-sm text-slate-400">
            No endpoints match the current filter
          </p>
        </div>
      ) : (
        <div className="border border-slate-700 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-slate-800/60 border-b border-slate-700">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-slate-400 uppercase">
                  Method
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium text-slate-400 uppercase">
                  Path
                </th>
                <th className="px-3 py-2 text-right text-xs font-medium text-slate-400 uppercase">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/80">
              {filteredEndpoints.map((ep) => (
                <tr key={`${ep.index}-${ep.raw}`} className="hover:bg-slate-800/30">
                  <td className="px-3 py-2 whitespace-nowrap">
                    {editingIndex === ep.index ? (
                      <select
                        aria-label={`Edit endpoint ${ep.index + 1} method`}
                        value={editMethod}
                        onChange={(e) =>
                          setEditMethod(e.target.value as EndpointMethod)
                        }
                        className="w-24 px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
                      >
                        {ENDPOINT_METHODS.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span
                        className={`text-xs px-2 py-0.5 rounded font-medium ${getEndpointMethodColor(ep.method)}`}
                      >
                        {ep.method}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {editingIndex === ep.index ? (
                      <input
                        type="text"
                        aria-label={`Edit endpoint ${ep.index + 1} path`}
                        value={editPath}
                        onChange={(e) => setEditPath(e.target.value)}
                        className="w-full px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-white font-mono focus:outline-none focus:ring-2 focus:ring-primary-500"
                      />
                    ) : (
                      <code className="text-sm text-slate-200 font-mono">
                        {ep.path}
                      </code>
                    )}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-right">
                    {editingIndex === ep.index ? (
                      <div className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          aria-label={`Save endpoint ${ep.index + 1}`}
                          disabled={updatePathsMutation.isPending}
                          onClick={() => handleSaveEdit(ep.index)}
                          className="p-1 text-green-400 hover:bg-green-500/10 rounded transition-colors disabled:opacity-50"
                        >
                          <Check className="w-4 h-4" />
                        </button>
                        <button
                          type="button"
                          aria-label={`Cancel editing endpoint ${ep.index + 1}`}
                          onClick={handleCancelEdit}
                          className="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    ) : (
                      <div className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          aria-label={`Edit endpoint ${ep.method} ${ep.path}`}
                          onClick={() =>
                            handleStartEdit(ep.index, ep.method, ep.path)
                          }
                          className="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                        >
                          <Edit className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          aria-label={`Delete endpoint ${ep.method} ${ep.path}`}
                          disabled={updatePathsMutation.isPending}
                          onClick={() => handleDeleteEndpoint(ep.index)}
                          className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors disabled:opacity-50"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ServiceSchedulesTab({ service }: { service: Service }) {
  const [showCreateSchedule, setShowCreateSchedule] = useState(false);
  const queryClient = useQueryClient();

  // Fetch schedules for this service
  const { data: schedules, isLoading } = useQuery({
    queryKey: ["sbom-schedules", "service", service.id],
    queryFn: () =>
      api.getSBOMSchedules({
        parent_type: "service",
        parent_id: service.id,
      }),
  });

  // Fetch recent scans for this service
  const { data: scans } = useQuery({
    queryKey: ["sbom-scans", "service", service.id],
    queryFn: () =>
      api.getSBOMScans({
        parent_type: "service",
        parent_id: service.id,
        per_page: 5,
      }),
  });

  const createScheduleMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.createSBOMSchedule({
        parent_type: "service",
        parent_id: service.id,
        schedule_cron: data.schedule_cron as string,
        is_active: (data.is_active as boolean) !== false,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["sbom-schedules", "service", service.id],
      });
      toast.success("Schedule created successfully");
      setShowCreateSchedule(false);
    },
    onError: () => toast.error("Failed to create schedule"),
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: (id: number) => api.deleteSBOMSchedule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["sbom-schedules", "service", service.id],
      });
      toast.success("Schedule deleted successfully");
    },
    onError: () => toast.error("Failed to delete schedule"),
  });

  // Trigger an immediate SBOM scan
  const runScanMutation = useMutation({
    mutationFn: () =>
      api.createSBOMScan({
        parent_type: "service",
        parent_id: service.id,
        scan_type: "git_clone",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["sbom-scans", "service", service.id],
      });
      toast.success("SBOM scan started");
    },
    onError: () => toast.error("Failed to start SBOM scan"),
  });

  // Schedule form fields using useMemo
  const scheduleFields: FormField[] = useMemo(
    () => [
      {
        name: "schedule_cron",
        label: "Cron Expression",
        type: "text",
        required: true,
        placeholder: "0 0 * * *",
        helpText:
          'E.g., "0 0 * * *" for daily at midnight, "0 */6 * * *" for every 6 hours',
      },
      {
        name: "is_active",
        label: "Enable schedule",
        type: "checkbox",
        defaultValue: true,
      },
    ],
    [],
  );

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <div className="w-6 h-6 border-2 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Run Now Section */}
      <div className="flex justify-between items-center">
        <div>
          <h3 className="text-lg font-semibold text-white">SBOM Scans</h3>
          <p className="text-sm text-slate-400">
            Scan repository to discover dependencies and vulnerabilities
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => runScanMutation.mutate()}
          disabled={runScanMutation.isPending}
        >
          <Play className="w-3 h-3 mr-1" />
          {runScanMutation.isPending ? "Starting..." : "Scan Now"}
        </Button>
      </div>

      {/* Recent Scans */}
      {scans?.items && scans.items.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-slate-400">Recent Scans</h4>
          {scans.items.map((scan: SBOMScan) => (
            <div
              key={scan.id}
              className="flex items-center justify-between p-2 bg-slate-800/30 rounded text-sm"
            >
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    scan.status === "completed"
                      ? "bg-green-500"
                      : scan.status === "running"
                        ? "bg-blue-500 animate-pulse"
                        : scan.status === "failed"
                          ? "bg-red-500"
                          : "bg-yellow-500"
                  }`}
                />
                <span className="text-slate-300 capitalize">{scan.status}</span>
              </div>
              <div className="flex items-center gap-4 text-slate-500">
                {scan.components_found !== undefined && (
                  <span>{scan.components_found} components</span>
                )}
                <span>{new Date(scan.created_at).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Schedules Section */}
      <div className="border-t border-slate-700 pt-4">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h4 className="text-sm font-medium text-white">Scheduled Scans</h4>
            <p className="text-xs text-slate-500">
              Automatically scan on a recurring schedule
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setShowCreateSchedule(true)}
          >
            <Plus className="w-3 h-3 mr-1" />
            Add Schedule
          </Button>
        </div>

        {!schedules?.items || schedules.items.length === 0 ? (
          <div className="text-center py-6 bg-slate-800/30 rounded">
            <Clock className="w-6 h-6 text-slate-600 mx-auto mb-2" />
            <p className="text-sm text-slate-400">No schedules configured</p>
          </div>
        ) : (
          <div className="space-y-2">
            {schedules.items.map((schedule: SBOMSchedule) => (
              <div
                key={schedule.id}
                className="flex items-center justify-between p-3 bg-slate-800/50 rounded border border-slate-700"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Clock className="w-4 h-4 text-primary-400 flex-shrink-0" />
                    <code className="text-sm font-mono text-white">
                      {schedule.schedule_cron}
                    </code>
                    {schedule.is_active ? (
                      <span className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400">
                        Active
                      </span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded bg-slate-500/20 text-slate-400">
                        Inactive
                      </span>
                    )}
                  </div>
                  {schedule.next_run_at && (
                    <p className="text-xs text-slate-500">
                      Next run:{" "}
                      {new Date(schedule.next_run_at).toLocaleString()}
                    </p>
                  )}
                  {schedule.last_run_at && (
                    <p className="text-xs text-slate-500">
                      Last run:{" "}
                      {new Date(schedule.last_run_at).toLocaleString()}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => deleteScheduleMutation.mutate(schedule.id)}
                  disabled={deleteScheduleMutation.isPending}
                  className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors disabled:opacity-50"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <ModalFormBuilder
        isOpen={showCreateSchedule}
        title="Create SBOM Schedule"
        fields={scheduleFields}
        onSubmit={(data) => createScheduleMutation.mutate(data)}
        onClose={() => setShowCreateSchedule(false)}
        submitLabel="Create"
      />
    </div>
  );
}
