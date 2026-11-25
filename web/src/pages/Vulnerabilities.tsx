import { useState, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Search,
  Calendar,
  Plus,
  Upload,
  X,
  Pencil,
  ExternalLink,
  CircleDot,
} from "lucide-react";
import toast from "react-hot-toast";
import { queryKeys } from "@/lib/queryKeys";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import SearchableSelect from "@/components/SearchableSelect";
import CsvImportModal from "@/components/CsvImportModal";
import VillageIdBadge from "@/components/VillageIdBadge";

interface AffectedEntity {
  parent_type: string;
  parent_name: string;
  source_file?: string;
}

interface RemediationIssueSummary {
  id: number;
  title: string;
  status: string;
  priority: string;
  village_id?: string | null;
}

interface Vulnerability {
  id: number;
  cve_id: string;
  title?: string | null;
  severity: string;
  cvss_score?: number | null;
  adjusted_risk_score?: string | null;
  owner_organization_id?: number | null;
  owner_organization_name?: string | null;
  owner_entity_id?: number | null;
  owner_entity_name?: string | null;
  remediation_issue_id?: number | null;
  remediation_issue?: RemediationIssueSummary | null;
  component?: string;
  status?: string;
  published_date?: string | null;
  published_at?: string | null;
  description?: string | null;
  affected_versions?: string[];
  affected_entities?: AffectedEntity[];
  village_id?: string | null;
}

interface VulnerabilitiesResponse {
  items: Vulnerability[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

interface Service {
  id: number;
  name: string;
}

interface Software {
  id: number;
  name: string;
}

interface OrganizationOption {
  id: number;
  name: string;
  display_name?: string | null;
}

interface EntityOption {
  id: number;
  name: string;
  type?: string | null;
}

interface PaginatedResponse<T> {
  items: T[];
}

const SEVERITY_LEVELS = [
  { value: "all", label: "All", color: "bg-slate-500/20 text-slate-400" },
  { value: "critical", label: "Critical", color: "bg-red-500/20 text-red-400" },
  { value: "high", label: "High", color: "bg-orange-500/20 text-orange-400" },
  {
    value: "medium",
    label: "Medium",
    color: "bg-yellow-500/20 text-yellow-400",
  },
  { value: "low", label: "Low", color: "bg-green-500/20 text-green-400" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "All Statuses" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In Progress" },
  { value: "resolved", label: "Resolved" },
  { value: "accepted_risk", label: "Accepted Risk" },
];

function formatIssueStatus(status?: string | null): string {
  const key = (status || "open").toLowerCase().replace(/_/g, " ");
  return key.charAt(0).toUpperCase() + key.slice(1);
}

function getIssueStatusBadgeColor(status?: string | null): string {
  const normalized = (status || "open").toLowerCase();
  if (normalized === "closed" || normalized === "resolved") {
    return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/25";
  }
  if (normalized === "in_progress") {
    return "bg-blue-500/15 text-blue-300 border-blue-500/30 hover:bg-blue-500/25";
  }
  return "bg-amber-500/15 text-amber-300 border-amber-500/30 hover:bg-amber-500/25";
}

function formatCvssScore(score?: number | null): string {
  if (score === null || score === undefined || Number.isNaN(Number(score))) {
    return "—";
  }
  return Number(score).toFixed(1);
}

export default function Vulnerabilities() {
  const [search, setSearch] = useState("");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [assignModalOpen, setAssignModalOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [createIssueModalOpen, setCreateIssueModalOpen] = useState(false);
  const [csvImportOpen, setCsvImportOpen] = useState(false);

  // Create form state
  const [newCveId, setNewCveId] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newSeverity, setNewSeverity] = useState("high");
  const [newCvss, setNewCvss] = useState("");
  const [newAdjustedRisk, setNewAdjustedRisk] = useState("");
  const [newOwnerOrgId, setNewOwnerOrgId] = useState<string | number>("");
  const [newOwnerEntityId, setNewOwnerEntityId] = useState<string | number>("");
  const [newDescription, setNewDescription] = useState("");
  const [newPublishedAt, setNewPublishedAt] = useState("");

  // Edit / detail form state
  const [editingVulnerability, setEditingVulnerability] =
    useState<Vulnerability | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editSeverity, setEditSeverity] = useState("high");
  const [editCvss, setEditCvss] = useState("");
  const [editAdjustedRisk, setEditAdjustedRisk] = useState("");
  const [editOwnerOrgId, setEditOwnerOrgId] = useState<string | number>("");
  const [editOwnerEntityId, setEditOwnerEntityId] = useState<string | number>(
    "",
  );
  const [editDescription, setEditDescription] = useState("");

  // Create issue modal state (when OU is not yet assigned on a row)
  const [issueTargetVuln, setIssueTargetVuln] = useState<Vulnerability | null>(
    null,
  );
  const [issueOrgId, setIssueOrgId] = useState<string | number>("");

  // Assign modal state
  const [selectedVulnerability, setSelectedVulnerability] =
    useState<Vulnerability | null>(null);
  const [assignParentType, setAssignParentType] = useState<string>("service");
  const [assignParentId, setAssignParentId] = useState<string | number>("");
  const [searchParams] = useSearchParams();
  const highlightId = searchParams.get("highlight");
  const queryClient = useQueryClient();

  // Fetch services, software, organizations (OUs), and entities (resources)
  const { data: servicesData } = useQuery({
    queryKey: queryKeys.services.all,
    queryFn: () =>
      api.getServices({ per_page: 200 }) as Promise<PaginatedResponse<Service>>,
  });

  const { data: softwareData } = useQuery({
    queryKey: queryKeys.software.all,
    queryFn: () =>
      api.getSoftware({ per_page: 200 }) as Promise<
        PaginatedResponse<Software>
      >,
  });

  const { data: organizationsData } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () =>
      api.getOrganizations({ per_page: 200 }) as Promise<
        PaginatedResponse<OrganizationOption>
      >,
  });

  const { data: entitiesData } = useQuery({
    queryKey: queryKeys.entities.all,
    queryFn: () =>
      api.getEntities({ per_page: 200 }) as Promise<
        PaginatedResponse<EntityOption>
      >,
  });

  const orgOptions = (organizationsData?.items || []).map(
    (org: OrganizationOption) => ({
      value: org.id,
      label: org.display_name || org.name,
    }),
  );

  const entityOptions = (entitiesData?.items || []).map(
    (ent: EntityOption) => ({
      value: ent.id,
      label: ent.type ? `${ent.name} (${ent.type})` : ent.name,
    }),
  );

  const openEditModal = (vuln: Vulnerability) => {
    setEditingVulnerability(vuln);
    setEditTitle(vuln.title || vuln.component || "");
    setEditSeverity((vuln.severity || "high").toLowerCase());
    setEditCvss(
      vuln.cvss_score !== null && vuln.cvss_score !== undefined
        ? String(vuln.cvss_score)
        : "",
    );
    setEditAdjustedRisk(vuln.adjusted_risk_score || "");
    setEditOwnerOrgId(vuln.owner_organization_id ?? "");
    setEditOwnerEntityId(vuln.owner_entity_id ?? "");
    setEditDescription(vuln.description || "");
    setEditModalOpen(true);
  };

  const assignMutation = useMutation({
    mutationFn: (data: {
      vulnId: number;
      parent_type: "service" | "software";
      parent_id: number;
      notes?: string;
    }) =>
      api.assignVulnerability(data.vulnId, {
        parent_type: data.parent_type,
        parent_id: data.parent_id,
        notes: data.notes,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.vulnerabilities.all,
        refetchType: "all",
      });
      setAssignModalOpen(false);
      setSelectedVulnerability(null);
      setAssignParentId("");
    },
  });

  const createVulnMutation = useMutation({
    mutationFn: (payload: {
      cve_id: string;
      title: string;
      severity: string;
      cvss_score?: number | null;
      adjusted_risk_score?: string | null;
      owner_organization_id?: number | null;
      owner_entity_id?: number | null;
      description?: string;
      published_at?: string;
    }) => api.createVulnerability(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.vulnerabilities.all,
        refetchType: "all",
      });
      toast.success("Vulnerability created");
      setCreateModalOpen(false);
      setNewCveId("");
      setNewTitle("");
      setNewSeverity("high");
      setNewCvss("");
      setNewAdjustedRisk("");
      setNewOwnerOrgId("");
      setNewOwnerEntityId("");
      setNewDescription("");
      setNewPublishedAt("");
    },
    onError: (err: {
      response?: { data?: { message?: string; error?: string } };
    }) => {
      toast.error(
        err?.response?.data?.message ||
          err?.response?.data?.error ||
          "Failed to create vulnerability",
      );
    },
  });

  const updateVulnMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: {
        title?: string;
        severity?: string;
        description?: string | null;
        cvss_score?: number | null;
        adjusted_risk_score?: string | null;
        owner_organization_id?: number | null;
        owner_entity_id?: number | null;
      };
    }) => api.updateVulnerability(id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.vulnerabilities.all,
        refetchType: "all",
      });
      toast.success("Vulnerability updated");
      setEditModalOpen(false);
      setEditingVulnerability(null);
    },
    onError: (err: {
      response?: { data?: { message?: string; error?: string } };
    }) => {
      toast.error(
        err?.response?.data?.message ||
          err?.response?.data?.error ||
          "Failed to update vulnerability",
      );
    },
  });

  const createIssueMutation = useMutation({
    mutationFn: ({
      vulnId,
      organization_id,
    }: {
      vulnId: number;
      organization_id?: number;
    }) =>
      api.createVulnerabilityIssue(
        vulnId,
        organization_id ? { organization_id } : undefined,
      ),
    onSuccess: async (resp: {
      issue?: RemediationIssueSummary;
      vulnerability?: Vulnerability;
    }) => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.vulnerabilities.all,
        refetchType: "all",
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.issues.all,
        refetchType: "all",
      });
      toast.success(
        resp?.issue?.id
          ? `Remediation issue #${resp.issue.id} created`
          : "Remediation issue created",
      );
      setCreateIssueModalOpen(false);
      setIssueTargetVuln(null);
      setIssueOrgId("");
      if (
        editingVulnerability &&
        resp?.vulnerability &&
        editingVulnerability.id === resp.vulnerability.id
      ) {
        setEditingVulnerability(resp.vulnerability);
        if (resp.vulnerability.owner_organization_id) {
          setEditOwnerOrgId(resp.vulnerability.owner_organization_id);
        }
      }
    },
    onError: (err: {
      response?: { data?: { message?: string; error?: string } };
    }) => {
      toast.error(
        err?.response?.data?.message ||
          err?.response?.data?.error ||
          "Failed to create remediation issue",
      );
    },
  });

  const handleRowCreateIssue = (vuln: Vulnerability) => {
    if (vuln.owner_organization_id) {
      createIssueMutation.mutate({ vulnId: vuln.id });
    } else {
      setIssueTargetVuln(vuln);
      setIssueOrgId("");
      setCreateIssueModalOpen(true);
    }
  };

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.vulnerabilities.list({
      search,
      severity: severityFilter,
      status: statusFilter,
    }),
    queryFn: () =>
      api.getVulnerabilities({
        search: search || undefined,
        severity: severityFilter !== "all" ? severityFilter : undefined,
        status: statusFilter !== "all" ? statusFilter : undefined,
      }) as Promise<VulnerabilitiesResponse>,
  });

  const items = data?.items || [];
  useEffect(() => {
    const list = data?.items || [];
    if (!highlightId || !list.length) return;
    const match = list.find(
      (v) => String(v.id) === highlightId || v.village_id === highlightId,
    );
    if (!match) return;
    const el = document.getElementById(`vulnerability-${match.id}`);
    el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlightId, data?.items]);

  const stats = {
    total: data?.total ?? items.length,
    critical: items.filter((v) => v.severity?.toLowerCase() === "critical")
      .length,
    high: items.filter((v) => v.severity?.toLowerCase() === "high").length,
    medium: items.filter((v) => v.severity?.toLowerCase() === "medium").length,
    low: items.filter((v) => v.severity?.toLowerCase() === "low").length,
  };

  const getSeverityColor = (severity: string): string => {
    const colorMap: { [key: string]: string } = {
      critical: "bg-red-500/10 text-red-400 border border-red-500/20",
      high: "bg-orange-500/10 text-orange-400 border border-orange-500/20",
      medium: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
      low: "bg-green-500/10 text-green-400 border border-green-500/20",
    };
    return colorMap[severity.toLowerCase()] || "bg-slate-500/10 text-slate-400";
  };

  const getStatusColor = (status: string): string => {
    const colorMap: { [key: string]: string } = {
      open: "bg-red-500/20 text-red-400",
      in_progress: "bg-blue-500/20 text-blue-400",
      resolved: "bg-green-500/20 text-green-400",
      accepted_risk: "bg-slate-500/20 text-slate-400",
    };
    return colorMap[status] || "bg-slate-500/20 text-slate-400";
  };

  const formatDate = (date?: string | null): string => {
    if (!date) return "—";
    const parsed = new Date(date);
    if (Number.isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  };

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4 mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Vulnerabilities</h1>
          <p className="mt-2 text-slate-400">
            Track and remediate CVEs and security vulnerabilities
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="secondary" onClick={() => setCsvImportOpen(true)}>
            <Upload className="w-4 h-4 mr-2" />
            Import CSV
          </Button>
          <Button onClick={() => setCreateModalOpen(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Add Vulnerability
          </Button>
        </div>
      </div>

      {/* Dashboard Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-slate-400 mb-1">Total</div>
            <div className="text-2xl font-bold text-white">{stats.total}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-red-400 mb-1">Critical</div>
            <div className="text-2xl font-bold text-red-400">
              {stats.critical}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-orange-400 mb-1">High</div>
            <div className="text-2xl font-bold text-orange-400">
              {stats.high}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-yellow-400 mb-1">Medium</div>
            <div className="text-2xl font-bold text-yellow-400">
              {stats.medium}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-green-400 mb-1">Low</div>
            <div className="text-2xl font-bold text-green-400">{stats.low}</div>
          </CardContent>
        </Card>
      </div>

      {/* Severity Filter Tabs */}
      <div className="flex flex-wrap gap-2 mb-6">
        {SEVERITY_LEVELS.map((level) => (
          <button
            key={level.value}
            onClick={() => setSeverityFilter(level.value)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              severityFilter === level.value
                ? `${level.color} ring-2 ring-offset-2 ring-offset-slate-900`
                : "bg-slate-800 text-slate-400 hover:bg-slate-700"
            }`}
          >
            {level.label}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-4 mb-6 lg:flex-row lg:items-end">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search CVE ID, component..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-200 hover:border-slate-600 transition-colors"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {/* Vulnerabilities Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <AlertTriangle className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No vulnerabilities found</p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-700 bg-slate-800/50">
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      CVE ID
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Severity
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      CVSS
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Adjusted Risk
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Component / Title
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Owner (OU / Resource)
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Service / Software
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Remediation Issue
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Status
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Published
                    </th>
                    <th className="px-4 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data?.items?.map((vuln: Vulnerability, idx: number) => {
                    const isHighlighted = Boolean(
                      highlightId &&
                        (String(vuln.id) === highlightId ||
                          vuln.village_id === highlightId),
                    );
                    const sevLabel = vuln.severity
                      ? vuln.severity.charAt(0).toUpperCase() +
                        vuln.severity.slice(1).toLowerCase()
                      : "Unknown";
                    return (
                      <tr
                        key={vuln.id}
                        id={`vulnerability-${vuln.id}`}
                        data-highlighted={isHighlighted ? "true" : undefined}
                        className={`border-b border-slate-700 hover:bg-slate-800/50 transition-colors ${
                          isHighlighted
                            ? "bg-primary-500/15 ring-2 ring-inset ring-primary-500"
                            : idx % 2 === 0
                              ? "bg-slate-900/30"
                              : ""
                        }`}
                      >
                        <td className="px-4 py-4">
                          <div>
                            <a
                              href={`https://cve.mitre.org/cgi-bin/cvename.cgi?name=${vuln.cve_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              <span className="text-primary-400 hover:text-primary-300 font-mono text-sm">
                                {vuln.cve_id}
                              </span>
                            </a>
                            {vuln.village_id && (
                              <div className="mt-1">
                                <VillageIdBadge villageId={vuln.village_id} />
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          <span
                            className={`px-3 py-1 rounded-full text-xs font-semibold ${getSeverityColor(vuln.severity || "unknown")}`}
                          >
                            {sevLabel}
                          </span>
                        </td>
                        <td className="px-4 py-4">
                          <span className="font-mono text-sm text-slate-200">
                            {formatCvssScore(vuln.cvss_score)}
                          </span>
                        </td>
                        <td className="px-4 py-4">
                          {vuln.adjusted_risk_score ? (
                            <span className="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                              {vuln.adjusted_risk_score}
                            </span>
                          ) : (
                            <span className="text-slate-500 text-xs">—</span>
                          )}
                        </td>
                        <td className="px-4 py-4">
                          <div>
                            <div className="text-white text-sm font-medium">
                              {vuln.component || vuln.title || "—"}
                            </div>
                            {vuln.description && (
                              <div className="text-slate-400 text-xs mt-1 line-clamp-2">
                                {vuln.description}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          {vuln.owner_organization_name ||
                          vuln.owner_entity_name ? (
                            <div className="flex flex-col gap-1">
                              {vuln.owner_organization_name && (
                                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 w-fit">
                                  OU: {vuln.owner_organization_name}
                                </span>
                              )}
                              {vuln.owner_entity_name && (
                                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 w-fit">
                                  Resource: {vuln.owner_entity_name}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-slate-500 text-xs">—</span>
                          )}
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex flex-wrap gap-1">
                            {vuln.affected_entities &&
                            vuln.affected_entities.length > 0 ? (
                              vuln.affected_entities.map(
                                (entity: AffectedEntity, i: number) => (
                                  <span
                                    key={i}
                                    className={`px-2 py-0.5 rounded text-xs font-medium ${
                                      entity.parent_type === "service"
                                        ? "bg-blue-500/10 text-blue-400 border border-blue-500/20"
                                        : "bg-purple-500/10 text-purple-400 border border-purple-500/20"
                                    }`}
                                  >
                                    {entity.parent_name}
                                  </span>
                                ),
                              )
                            ) : (
                              <span className="text-slate-500 text-xs">—</span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          {vuln.remediation_issue ? (
                            <Link
                              to={`/issues/${vuln.remediation_issue.id}`}
                              title={vuln.remediation_issue.title}
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${getIssueStatusBadgeColor(vuln.remediation_issue.status)}`}
                            >
                              <CircleDot className="w-3 h-3" />#
                              {vuln.remediation_issue.id} ·{" "}
                              {formatIssueStatus(vuln.remediation_issue.status)}
                              <ExternalLink className="w-3 h-3 opacity-70" />
                            </Link>
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleRowCreateIssue(vuln)}
                              disabled={createIssueMutation.isPending}
                              className="px-2.5 py-1 text-xs font-medium rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 hover:bg-amber-500/25 disabled:opacity-50 transition-colors inline-flex items-center gap-1"
                            >
                              <Plus className="w-3 h-3" />
                              Create Issue
                            </button>
                          )}
                        </td>
                        <td className="px-4 py-4">
                          {(() => {
                            const statusKey = vuln.status || "open";
                            const statusLabel = statusKey.replace(/_/g, " ");
                            return (
                              <span
                                className={`px-3 py-1 rounded-full text-xs font-semibold ${getStatusColor(statusKey)}`}
                              >
                                {statusLabel.charAt(0).toUpperCase() +
                                  statusLabel.slice(1)}
                              </span>
                            );
                          })()}
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex items-center gap-2 text-slate-400 text-sm">
                            <Calendar className="w-4 h-4" />
                            {formatDate(vuln.published_date || vuln.published_at)}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => openEditModal(vuln)}
                              className="px-2.5 py-1 text-xs font-medium rounded bg-slate-700 text-slate-200 hover:bg-slate-600 transition-colors flex items-center gap-1"
                            >
                              <Pencil className="w-3 h-3" />
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedVulnerability(vuln);
                                setAssignParentId("");
                                setAssignModalOpen(true);
                              }}
                              className="px-2.5 py-1 text-xs font-medium rounded bg-primary-600/20 text-primary-400 hover:bg-primary-600/30 transition-colors flex items-center gap-1"
                            >
                              <Plus className="w-3 h-3" />
                              Assign
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Assign Vulnerability Modal */}
      {assignModalOpen && selectedVulnerability && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 w-full max-w-md">
            <div className="flex items-center justify-between gap-2 mb-4 flex-wrap">
              <h3 className="text-lg font-semibold text-white">
                Assign {selectedVulnerability.cve_id}
              </h3>
              {selectedVulnerability.village_id && (
                <VillageIdBadge villageId={selectedVulnerability.village_id} />
              )}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const formData = new FormData(e.currentTarget);
                const parentType = formData.get("parent_type") as
                  | "service"
                  | "software";
                const parentId = Number(assignParentId);
                const notes = formData.get("notes") as string;
                if (parentId) {
                  assignMutation.mutate({
                    vulnId: selectedVulnerability.id,
                    parent_type: parentType,
                    parent_id: parentId,
                    notes: notes || undefined,
                  });
                }
              }}
            >
              <div className="space-y-4">
                <div>
                  <label
                    htmlFor="assign-parent-type"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Type
                  </label>
                  <select
                    id="assign-parent-type"
                    name="parent_type"
                    value={assignParentType}
                    onChange={(e) => {
                      setAssignParentType(e.target.value);
                      setAssignParentId("");
                    }}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-200"
                  >
                    <option value="service">Service</option>
                    <option value="software">Software</option>
                  </select>
                </div>
                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1">
                    {assignParentType === "service" ? "Service" : "Software"}
                  </span>
                  <SearchableSelect
                    options={
                      assignParentType === "service"
                        ? (servicesData?.items || []).map((s: Service) => ({
                            value: s.id,
                            label: s.name,
                          }))
                        : (softwareData?.items || []).map((s: Software) => ({
                            value: s.id,
                            label: s.name,
                          }))
                    }
                    value={assignParentId}
                    onChange={(val) => setAssignParentId(val)}
                    placeholder={`Search ${assignParentType === "service" ? "services" : "software"}...`}
                    ariaLabel={
                      assignParentType === "service" ? "Service" : "Software"
                    }
                  />
                </div>
                <div>
                  <label
                    htmlFor="assign-notes"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Notes (optional)
                  </label>
                  <textarea
                    id="assign-notes"
                    name="notes"
                    rows={3}
                    maxLength={5000}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-200 resize-none"
                    placeholder="Why is this vulnerability being assigned?"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-3 mt-6">
                <button
                  type="button"
                  onClick={() => {
                    setAssignModalOpen(false);
                    setSelectedVulnerability(null);
                    setAssignParentId("");
                  }}
                  className="px-4 py-2 text-sm font-medium text-slate-400 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={assignMutation.isPending}
                  className="px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 disabled:opacity-50 transition-colors"
                >
                  {assignMutation.isPending ? "Assigning..." : "Assign"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Manual Create Vulnerability Modal */}
      {createModalOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <CardHeader className="flex flex-row items-center justify-between">
              <h2 className="text-xl font-semibold text-white">
                Add Vulnerability
              </h2>
              <button
                type="button"
                onClick={() => setCreateModalOpen(false)}
                className="p-1 text-slate-400 hover:text-white rounded"
                aria-label="Close modal"
              >
                <X className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!newCveId.trim() || !newTitle.trim()) return;
                  let parsedCvss: number | null = null;
                  if (newCvss.trim()) {
                    const num = Number(newCvss);
                    if (Number.isNaN(num) || num < 0 || num > 10) {
                      toast.error("CVSS score must be between 0.0 and 10.0");
                      return;
                    }
                    parsedCvss = num;
                  }
                  createVulnMutation.mutate({
                    cve_id: newCveId.trim().toUpperCase(),
                    title: newTitle.trim(),
                    severity: newSeverity,
                    cvss_score: parsedCvss,
                    adjusted_risk_score: newAdjustedRisk.trim() || null,
                    owner_organization_id: newOwnerOrgId
                      ? Number(newOwnerOrgId)
                      : null,
                    owner_entity_id: newOwnerEntityId
                      ? Number(newOwnerEntityId)
                      : null,
                    description: newDescription.trim() || undefined,
                    published_at: newPublishedAt || undefined,
                  });
                }}
                className="space-y-4"
              >
                <Input
                  label="CVE / Advisory ID"
                  required
                  value={newCveId}
                  onChange={(e) => setNewCveId(e.target.value)}
                  placeholder="CVE-2026-1234"
                />
                <Input
                  label="Title"
                  required
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="Short vulnerability summary"
                />
                <div className="grid grid-cols-2 gap-4">
                  <Select
                    label="Severity"
                    required
                    value={newSeverity}
                    onChange={(e) => setNewSeverity(e.target.value)}
                  >
                    <option value="critical">Critical</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                    <option value="info">Info</option>
                  </Select>
                  <Input
                    label="CVSS Score (0.0 - 10.0)"
                    type="number"
                    step="0.1"
                    min="0"
                    max="10"
                    value={newCvss}
                    onChange={(e) => setNewCvss(e.target.value)}
                    placeholder="7.5"
                  />
                </div>
                <Input
                  label="Adjusted Risk Score (Optional)"
                  maxLength={64}
                  value={newAdjustedRisk}
                  onChange={(e) => setNewAdjustedRisk(e.target.value)}
                  placeholder="e.g. 4.2, high, low, accepted"
                />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-1.5">
                      Owning OU (Organization)
                    </span>
                    <SearchableSelect
                      options={orgOptions}
                      value={newOwnerOrgId}
                      onChange={(val) => setNewOwnerOrgId(val)}
                      allowClear
                      placeholder="Search organizations (optional)..."
                      ariaLabel="Owning OU"
                    />
                  </div>
                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-1.5">
                      Owning Resource (Entity)
                    </span>
                    <SearchableSelect
                      options={entityOptions}
                      value={newOwnerEntityId}
                      onChange={(val) => setNewOwnerEntityId(val)}
                      allowClear
                      placeholder="Search resources (optional)..."
                      ariaLabel="Owning Resource"
                    />
                  </div>
                </div>
                <Input
                  label="Published Date (Optional)"
                  type="date"
                  value={newPublishedAt}
                  onChange={(e) => setNewPublishedAt(e.target.value)}
                />
                <div>
                  <label
                    htmlFor="vuln-description"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Description (Optional)
                  </label>
                  <textarea
                    id="vuln-description"
                    value={newDescription}
                    onChange={(e) => setNewDescription(e.target.value)}
                    rows={3}
                    placeholder="Vulnerability details, impact, and remediation notes..."
                    className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                </div>
                <div className="flex justify-end gap-3 pt-2">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setCreateModalOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={createVulnMutation.isPending}>
                    {createVulnMutation.isPending
                      ? "Creating..."
                      : "Create Vulnerability"}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Edit / Detail Vulnerability Modal */}
      {editModalOpen && editingVulnerability && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-xl font-semibold text-white">
                  Edit {editingVulnerability.cve_id}
                </h2>
                {editingVulnerability.village_id && (
                  <VillageIdBadge villageId={editingVulnerability.village_id} />
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setEditModalOpen(false);
                  setEditingVulnerability(null);
                }}
                className="p-1 text-slate-400 hover:text-white rounded"
                aria-label="Close edit modal"
              >
                <X className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!editTitle.trim()) return;
                  let parsedCvss: number | null = null;
                  if (editCvss.trim()) {
                    const num = Number(editCvss);
                    if (Number.isNaN(num) || num < 0 || num > 10) {
                      toast.error("CVSS score must be between 0.0 and 10.0");
                      return;
                    }
                    parsedCvss = num;
                  }
                  updateVulnMutation.mutate({
                    id: editingVulnerability.id,
                    payload: {
                      title: editTitle.trim(),
                      severity: editSeverity,
                      cvss_score: parsedCvss,
                      adjusted_risk_score: editAdjustedRisk.trim() || null,
                      owner_organization_id: editOwnerOrgId
                        ? Number(editOwnerOrgId)
                        : null,
                      owner_entity_id: editOwnerEntityId
                        ? Number(editOwnerEntityId)
                        : null,
                      description: editDescription.trim() || null,
                    },
                  });
                }}
                className="space-y-4"
              >
                <Input
                  label="Title"
                  required
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  placeholder="Short vulnerability summary"
                />
                <div className="grid grid-cols-2 gap-4">
                  <Select
                    label="Severity"
                    required
                    value={editSeverity}
                    onChange={(e) => setEditSeverity(e.target.value)}
                  >
                    <option value="critical">Critical</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                    <option value="info">Info</option>
                  </Select>
                  <Input
                    label="CVSS Score (0.0 - 10.0)"
                    type="number"
                    step="0.1"
                    min="0"
                    max="10"
                    value={editCvss}
                    onChange={(e) => setEditCvss(e.target.value)}
                    placeholder="7.5"
                  />
                </div>
                <Input
                  label="Adjusted Risk Score (Optional)"
                  maxLength={64}
                  value={editAdjustedRisk}
                  onChange={(e) => setEditAdjustedRisk(e.target.value)}
                  placeholder="e.g. 4.2, high, low, accepted"
                />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-1.5">
                      Owning OU (Organization)
                    </span>
                    <SearchableSelect
                      options={orgOptions}
                      value={editOwnerOrgId}
                      onChange={(val) => setEditOwnerOrgId(val)}
                      allowClear
                      placeholder="Search organizations (optional)..."
                      ariaLabel="Edit Owning OU"
                    />
                  </div>
                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-1.5">
                      Owning Resource (Entity)
                    </span>
                    <SearchableSelect
                      options={entityOptions}
                      value={editOwnerEntityId}
                      onChange={(val) => setEditOwnerEntityId(val)}
                      allowClear
                      placeholder="Search resources (optional)..."
                      ariaLabel="Edit Owning Resource"
                    />
                  </div>
                </div>
                <div>
                  <label
                    htmlFor="edit-vuln-description"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Description (Optional)
                  </label>
                  <textarea
                    id="edit-vuln-description"
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    rows={3}
                    placeholder="Vulnerability details, impact, and remediation notes..."
                    className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                </div>

                {/* Remediation Issue Section */}
                <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                      Remediation Issue
                    </span>
                    {editingVulnerability.remediation_issue ? (
                      <Link
                        to={`/issues/${editingVulnerability.remediation_issue.id}`}
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${getIssueStatusBadgeColor(editingVulnerability.remediation_issue.status)}`}
                      >
                        <CircleDot className="w-3 h-3" />#
                        {editingVulnerability.remediation_issue.id} ·{" "}
                        {formatIssueStatus(
                          editingVulnerability.remediation_issue.status,
                        )}
                        <ExternalLink className="w-3 h-3 opacity-70" />
                      </Link>
                    ) : (
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        disabled={
                          createIssueMutation.isPending || !editOwnerOrgId
                        }
                        onClick={() => {
                          if (!editOwnerOrgId) {
                            toast.error(
                              "Select an Owning OU before creating a remediation issue",
                            );
                            return;
                          }
                          createIssueMutation.mutate({
                            vulnId: editingVulnerability.id,
                            organization_id: Number(editOwnerOrgId),
                          });
                        }}
                      >
                        <Plus className="w-3.5 h-3.5 mr-1" />
                        {createIssueMutation.isPending
                          ? "Creating Issue..."
                          : "Create Issue"}
                      </Button>
                    )}
                  </div>
                  {!editingVulnerability.remediation_issue &&
                    !editOwnerOrgId && (
                      <p className="mt-1.5 text-xs text-slate-500">
                        Assign an Owning OU above to create a remediation issue
                        for that team.
                      </p>
                    )}
                </div>

                <div className="flex justify-end gap-3 pt-2">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      setEditModalOpen(false);
                      setEditingVulnerability(null);
                    }}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={updateVulnMutation.isPending}>
                    {updateVulnMutation.isPending
                      ? "Saving..."
                      : "Save Changes"}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Create Remediation Issue Modal (when row has no Owning OU yet) */}
      {createIssueModalOpen && issueTargetVuln && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
            <CardHeader className="flex flex-row items-center justify-between">
              <h2 className="text-lg font-semibold text-white">
                Create Remediation Issue for {issueTargetVuln.cve_id}
              </h2>
              <button
                type="button"
                onClick={() => {
                  setCreateIssueModalOpen(false);
                  setIssueTargetVuln(null);
                  setIssueOrgId("");
                }}
                className="p-1 text-slate-400 hover:text-white rounded"
                aria-label="Close create issue modal"
              >
                <X className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!issueOrgId) {
                    toast.error("Please select an Owning OU");
                    return;
                  }
                  createIssueMutation.mutate({
                    vulnId: issueTargetVuln.id,
                    organization_id: Number(issueOrgId),
                  });
                }}
                className="space-y-4"
              >
                <p className="text-sm text-slate-400">
                  Select the Owning OU to assign this remediation issue to. It
                  will also be saved as the vulnerability&apos;s Owning OU.
                </p>
                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1.5">
                    Owning OU (Organization)
                  </span>
                  <SearchableSelect
                    options={orgOptions}
                    value={issueOrgId}
                    onChange={(val) => setIssueOrgId(val)}
                    allowClear
                    placeholder="Search organizations..."
                    ariaLabel="Issue Owning OU"
                  />
                </div>
                <div className="flex justify-end gap-3 pt-2">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      setCreateIssueModalOpen(false);
                      setIssueTargetVuln(null);
                      setIssueOrgId("");
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    disabled={createIssueMutation.isPending || !issueOrgId}
                  >
                    {createIssueMutation.isPending
                      ? "Creating..."
                      : "Create Issue"}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}

      {/* CSV Import Modal */}
      <CsvImportModal
        resource="vulnerabilities"
        isOpen={csvImportOpen}
        onClose={() => setCsvImportOpen(false)}
        onSuccess={() => {
          queryClient.invalidateQueries({
            queryKey: queryKeys.vulnerabilities.all,
            refetchType: "all",
          });
        }}
      />
    </div>
  );
}
