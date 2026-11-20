import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Search, Calendar, Plus, Upload, X } from "lucide-react";
import toast from "react-hot-toast";
import { queryKeys } from "@/lib/queryKeys";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import SearchableSelect from "@/components/SearchableSelect";
import CsvImportModal from "@/components/CsvImportModal";

interface AffectedEntity {
  parent_type: string;
  parent_name: string;
  source_file?: string;
}

interface Vulnerability {
  id: number;
  cve_id: string;
  severity: string;
  component: string;
  status: string;
  published_date: string;
  description: string;
  affected_versions: string[];
  affected_entities: AffectedEntity[];
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

export default function Vulnerabilities() {
  const [search, setSearch] = useState("");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [assignModalOpen, setAssignModalOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [csvImportOpen, setCsvImportOpen] = useState(false);
  const [newCveId, setNewCveId] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newSeverity, setNewSeverity] = useState("high");
  const [newCvss, setNewCvss] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newPublishedAt, setNewPublishedAt] = useState("");
  const [selectedVulnerability, setSelectedVulnerability] =
    useState<Vulnerability | null>(null);
  const [assignParentType, setAssignParentType] = useState<string>("service");
  const [assignParentId, setAssignParentId] = useState<string | number>("");
  const queryClient = useQueryClient();

  // Fetch services and software for assignment dropdowns
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
      cvss_score?: number;
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
      setNewDescription("");
      setNewPublishedAt("");
    },
    onError: (err: { response?: { data?: { message?: string; error?: string } } }) => {
      toast.error(
        err?.response?.data?.message ||
          err?.response?.data?.error ||
          "Failed to create vulnerability",
      );
    },
  });

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
  const stats = {
    total: data?.total ?? items.length,
    critical: items.filter((v) => v.severity === "critical").length,
    high: items.filter((v) => v.severity === "high").length,
    medium: items.filter((v) => v.severity === "medium").length,
    low: items.filter((v) => v.severity === "low").length,
  };

  const getSeverityColor = (severity: string): string => {
    const colorMap: { [key: string]: string } = {
      critical: "bg-red-500/10 text-red-400 border border-red-500/20",
      high: "bg-orange-500/10 text-orange-400 border border-orange-500/20",
      medium: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
      low: "bg-green-500/10 text-green-400 border border-green-500/20",
    };
    return colorMap[severity] || "bg-slate-500/10 text-slate-400";
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

  const formatDate = (date: string): string => {
    return new Date(date).toLocaleDateString("en-US", {
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
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      CVE ID
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Severity
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Component
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Service / Software
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Source File
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Status
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Published
                    </th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data?.items?.map((vuln: Vulnerability, idx: number) => (
                    <tr
                      key={vuln.id}
                      className={`border-b border-slate-700 hover:bg-slate-800/50 transition-colors ${
                        idx % 2 === 0 ? "bg-slate-900/30" : ""
                      }`}
                    >
                      <td className="px-6 py-4">
                        <a
                          href={`https://cve.mitre.org/cgi-bin/cvename.cgi?name=${vuln.cve_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <span className="text-primary-400 hover:text-primary-300 font-mono text-sm">
                            {vuln.cve_id}
                          </span>
                        </a>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`px-3 py-1 rounded-full text-xs font-semibold ${getSeverityColor(vuln.severity)}`}
                        >
                          {vuln.severity.charAt(0).toUpperCase() +
                            vuln.severity.slice(1)}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <div>
                          <div className="text-white text-sm font-medium">
                            {vuln.component}
                          </div>
                          <div className="text-slate-400 text-xs mt-1">
                            {vuln.description}
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex flex-wrap gap-1">
                          {vuln.affected_entities?.map(
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
                          ) || (
                            <span className="text-slate-500 text-xs">—</span>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="text-slate-400 text-xs font-mono">
                          {vuln.affected_entities
                            ?.map((e: AffectedEntity) => e.source_file)
                            .filter(Boolean)
                            .join(", ") || "—"}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`px-3 py-1 rounded-full text-xs font-semibold ${getStatusColor(vuln.status)}`}
                        >
                          {vuln.status
                            .replace("_", " ")
                            .charAt(0)
                            .toUpperCase() +
                            vuln.status.replace("_", " ").slice(1)}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2 text-slate-400 text-sm">
                          <Calendar className="w-4 h-4" />
                          {formatDate(vuln.published_date)}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <button
                          onClick={() => {
                            setSelectedVulnerability(vuln);
                            setAssignParentId("");
                            setAssignModalOpen(true);
                          }}
                          className="px-3 py-1 text-xs font-medium rounded bg-primary-600/20 text-primary-400 hover:bg-primary-600/30 transition-colors flex items-center gap-1"
                        >
                          <Plus className="w-3 h-3" />
                          Assign
                        </button>
                      </td>
                    </tr>
                  ))}
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
            <h3 className="text-lg font-semibold text-white mb-4">
              Assign {selectedVulnerability.cve_id}
            </h3>
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
                  <label className="block text-sm font-medium text-slate-300 mb-1">
                    Type
                  </label>
                  <select
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
                  <label className="block text-sm font-medium text-slate-300 mb-1">
                    {assignParentType === "service" ? "Service" : "Software"}
                  </label>
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
                  <label className="block text-sm font-medium text-slate-300 mb-1">
                    Notes (optional)
                  </label>
                  <textarea
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
                  let parsedCvss: number | undefined;
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
                  label="Published Date (Optional)"
                  type="date"
                  value={newPublishedAt}
                  onChange={(e) => setNewPublishedAt(e.target.value)}
                />
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1.5">
                    Description (Optional)
                  </label>
                  <textarea
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
