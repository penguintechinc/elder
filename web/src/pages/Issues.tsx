import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Plus,
  Search,
  MessageSquare,
  Tag,
  User,
  AlertTriangle,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { invalidateCache } from "@/lib/invalidateCache";
import { getStatusColor, getPriorityColor } from "@/lib/colorHelpers";
import {
  ISSUE_TYPES,
  issueTypeLabel,
  SUPPORT_ISSUE_TYPE,
} from "@/lib/constants/issueTypes";
import {
  Issue,
  IssueStatus,
  IssuePriority,
  IssueType,
  Organization,
  Entity,
  IssueLabel,
} from "@/types";
import Button from "@/components/Button";
import Card, { CardContent, CardHeader } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import SearchableSelect from "@/components/SearchableSelect";
import AssigneePicker, { AssigneeValue } from "@/components/AssigneePicker";

/** Convert snake_case/UPPERCASE status or priority to Title Case display format */
function formatStatusLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export default function Issues() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<IssueStatus | "">("");
  const [priorityFilter, setPriorityFilter] = useState<IssuePriority | "">("");
  const [issueTypeFilter, setIssueTypeFilter] = useState<IssueType | "">("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const organizationId = searchParams.get("organization_id");
  const entityId = searchParams.get("entity_id");

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.issues.list({
      status: statusFilter,
      priority: priorityFilter,
      organizationId,
      entityId,
    }),
    queryFn: () =>
      api.getIssues({
        status: statusFilter || undefined,
        priority: priorityFilter || undefined,
        organization_id: organizationId ? parseInt(organizationId) : undefined,
        entity_id: entityId ? parseInt(entityId) : undefined,
      }),
  });

  // GET /issues has no server-side `search` or `issue_type` filter
  // (apps/api/modules/issues/routes/issues.py::list_issues only applies
  // status/priority/assignee_id/reporter_id) — filter client-side over the
  // fetched page so the search box and type filter actually narrow results.
  // Backend stores issue_type as UPPERCASE (SUPPORT, BUG, etc.); normalize to
  // lowercase for comparison against the constants (which are lowercase).
  const filteredIssues = useMemo(() => {
    const allItems: Issue[] = data?.items || [];
    const q = search.trim().toLowerCase();
    return allItems.filter((issue) => {
      const matchesType =
        !issueTypeFilter || issue.issue_type?.toLowerCase() === issueTypeFilter;
      const matchesSearch =
        !q ||
        issue.title.toLowerCase().includes(q) ||
        (issue.description || "").toLowerCase().includes(q);
      return matchesType && matchesSearch;
    });
  }, [data, search, issueTypeFilter]);

  const updateStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: IssueStatus }) =>
      api.updateIssue(id, { status }),
    onSuccess: async () => {
      await invalidateCache.issues(queryClient);
      toast.success("Issue status updated");
    },
    onError: () => {
      toast.error("Failed to update issue status");
    },
  });

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Issues</h1>
          <p className="mt-2 text-slate-400">
            Track and manage issues across your infrastructure
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Issue
        </Button>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search issues..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as IssueStatus | "")}
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="closed">Closed</option>
        </Select>
        <Select
          value={priorityFilter}
          onChange={(e) =>
            setPriorityFilter(e.target.value as IssuePriority | "")
          }
        >
          <option value="">All Priorities</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </Select>
        <Select
          value={issueTypeFilter}
          onChange={(e) => setIssueTypeFilter(e.target.value as IssueType | "")}
          data-testid="issue-type-filter"
        >
          <option value="">All Types</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Issues List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredIssues.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No issues found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first issue
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredIssues.map((issue: Issue) => (
            <Card
              key={issue.id}
              className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
              onClick={() => navigate(`/issues/${issue.id}`)}
              data-testid={`issue-card-${issue.id}`}
            >
              <CardContent>
                <div className="flex items-start gap-4">
                  {/* Issue Icon */}
                  <div className="flex-shrink-0 mt-1">
                    {issue.is_incident ? (
                      <AlertTriangle className="w-5 h-5 text-red-500" />
                    ) : (
                      <MessageSquare className="w-5 h-5 text-primary-400" />
                    )}
                  </div>

                  {/* Issue Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <h3 className="text-lg font-semibold text-white mb-1">
                          {issue.title}
                        </h3>
                        {issue.description && (
                          <p className="text-sm text-slate-400 line-clamp-2 mb-3">
                            {issue.description}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="text-xs text-slate-500">
                            #{issue.id}
                          </span>
                          {issue.is_incident === 1 && (
                            <span className="text-xs px-2 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30 font-semibold">
                              INCIDENT
                            </span>
                          )}
                          <span
                            className={`text-xs px-2 py-0.5 rounded border ${getStatusColor(issue.status)}`}
                          >
                            {formatStatusLabel(issue.status)}
                          </span>
                          <span
                            className={`text-xs px-2 py-0.5 rounded border ${getPriorityColor(issue.priority)}`}
                          >
                            {formatStatusLabel(issue.priority)}
                          </span>
                          <span className="text-xs px-2 py-0.5 rounded border border-slate-600 bg-slate-700/40 text-slate-300">
                            {issueTypeLabel(issue.issue_type)}
                          </span>
                          {issue.assignee_id && (
                            <span className="flex items-center gap-1 text-xs text-slate-400">
                              <User className="w-3 h-3" />
                              Assigned
                            </span>
                          )}
                          {issue.labels && issue.labels.length > 0 && (
                            <span className="flex items-center gap-1 text-xs text-slate-400">
                              <Tag className="w-3 h-3" />
                              {issue.labels.length} label(s)
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Quick Status Change */}
                      <div onClick={(e) => e.stopPropagation()}>
                        <Select
                          value={issue.status?.toLowerCase() || ""}
                          onChange={(e) =>
                            updateStatusMutation.mutate({
                              id: issue.id,
                              status: e.target.value as IssueStatus,
                            })
                          }
                          className="text-sm"
                        >
                          <option value="open">Open</option>
                          <option value="in_progress">In Progress</option>
                          <option value="closed">Closed</option>
                        </Select>
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <CreateIssueModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ["issues"],
              refetchType: "all",
            });
            setShowCreateModal(false);
          }}
          defaultOrganizationId={
            organizationId ? parseInt(organizationId) : undefined
          }
          defaultEntityId={entityId ? parseInt(entityId) : undefined}
        />
      )}
    </div>
  );
}

const ISSUE_RESOURCE_TYPES = [
  { value: "service", label: "Service" },
  { value: "networking", label: "Networking" },
  { value: "data_store", label: "Data Store" },
  { value: "software", label: "Software" },
  { value: "certificate", label: "Certificate" },
  { value: "project", label: "Project" },
  { value: "organization", label: "Organization" },
  { value: "identity", label: "Identity" },
] as const;

interface ResourceListItem {
  id: number;
  name?: string;
  title?: string;
  username?: string;
  email?: string;
  common_name?: string;
}

export interface CreateIssueModalProps {
  onClose: () => void;
  onSuccess: () => void;
  defaultOrganizationId?: number;
  defaultEntityId?: number;
  parentIssueId?: number;
}

export function CreateIssueModal({
  onClose,
  onSuccess,
  defaultOrganizationId,
  defaultEntityId,
  parentIssueId,
}: CreateIssueModalProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<IssuePriority>("medium");
  const [issueType, setIssueType] = useState<IssueType>("other");
  const [organizationId, setOrganizationId] = useState(
    defaultOrganizationId ? String(defaultOrganizationId) : "",
  );
  const [assignee, setAssignee] = useState<AssigneeValue | null>(null);
  const [entityIds, setEntityIds] = useState<number[]>(
    defaultEntityId ? [defaultEntityId] : [],
  );
  const [resourceType, setResourceType] = useState<string>("service");
  const [resourceSearch, setResourceSearch] = useState<string>("");
  const [selectedResources, setSelectedResources] = useState<
    Array<{ type: string; id: number; label: string }>
  >([]);
  const [projectId, setProjectId] = useState("");
  const [milestoneId, setMilestoneId] = useState("");
  const [selectedParentIssueId, setSelectedParentIssueId] = useState("");
  const [labelIds, setLabelIds] = useState<number[]>([]);
  const [showCreateLabel, setShowCreateLabel] = useState(false);
  const [newLabelName, setNewLabelName] = useState("");
  const [newLabelColor, setNewLabelColor] = useState("#3b82f6");
  const [newLabelDescription, setNewLabelDescription] = useState("");
  const [isIncident, setIsIncident] = useState(false);
  const [channel, setChannel] = useState("");
  const [category, setCategory] = useState("");
  const queryClient = useQueryClient();

  const { data: organizations } = useQuery({
    queryKey: ["organizations-all"],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  });

  const { data: entities } = useQuery({
    queryKey: ["entities-all"],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  });

  const { data: resourcesData, isLoading: resourcesLoading } = useQuery({
    queryKey: ["issue-resource-options", resourceType, resourceSearch],
    queryFn: async () => {
      const params: { per_page: number; search?: string } = { per_page: 50 };
      if (resourceSearch) params.search = resourceSearch;
      switch (resourceType) {
        case "service":
          return api.getServices(params);
        case "networking": {
          const resp = await api.listNetworks({});
          const items: ResourceListItem[] = Array.isArray(resp)
            ? resp
            : resp?.networks || resp?.items || [];
          const filtered = resourceSearch
            ? items.filter((n) =>
                (n.name || "")
                  .toLowerCase()
                  .includes(resourceSearch.toLowerCase()),
              )
            : items;
          return { items: filtered };
        }
        case "data_store":
          return api.getDataStores(params);
        case "software":
          return api.getSoftware(params);
        case "certificate":
          return api.getCertificates(params);
        case "project":
          return api.getProjects(params);
        case "organization":
          return api.getOrganizations(params);
        case "identity":
          return api.getIdentities(params);
        default:
          return { items: [] };
      }
    },
    enabled: !!resourceType,
  });

  const resourceOptions = useMemo(() => {
    const raw: ResourceListItem[] = Array.isArray(resourcesData)
      ? resourcesData
      : resourcesData?.items || resourcesData?.certificates || [];
    return raw
      .filter(
        (item) =>
          !selectedResources.some(
            (r) => r.type === resourceType && r.id === item.id,
          ),
      )
      .map((item) => ({
        value: String(item.id),
        label:
          item.name ||
          item.title ||
          item.common_name ||
          item.username ||
          item.email ||
          `#${item.id}`,
      }));
  }, [resourcesData, selectedResources, resourceType]);

  const { data: projectsData } = useQuery({
    queryKey: ["projects-all"],
    queryFn: () => api.getProjects({ per_page: 200 }),
  });

  const { data: milestonesData } = useQuery({
    queryKey: ["milestones-all"],
    queryFn: () => api.getMilestones({ per_page: 200 }),
  });

  const { data: labels } = useQuery({
    queryKey: ["labels-all"],
    queryFn: () => api.getLabels({ per_page: 1000 }),
  });

  const createLabelMutation = useMutation({
    mutationFn: (data: {
      name: string;
      color?: string;
      description?: string;
    }) => api.createLabel(data),
    onSuccess: async (created: IssueLabel) => {
      await queryClient.invalidateQueries({ queryKey: ["labels-all"] });
      await queryClient.invalidateQueries({ queryKey: ["labels"] });
      if (created?.id) {
        setLabelIds((prev) =>
          prev.includes(created.id) ? prev : [...prev, created.id],
        );
      }
      setNewLabelName("");
      setNewLabelDescription("");
      setShowCreateLabel(false);
      toast.success(`Label "${created?.name || "New label"}" created`);
    },
    onError: () => {
      toast.error("Failed to create label");
    },
  });

  const { data: existingIssues } = useQuery({
    queryKey: queryKeys.issues.list({}),
    queryFn: () => api.getIssues({ per_page: 200 }),
  });

  const channelOptions = Array.from(
    new Set([
      "email",
      "chat",
      "phone",
      "web",
      "slack",
      ...((existingIssues?.items || []) as Issue[])
        .map((i) => i.channel)
        .filter((c): c is string => Boolean(c)),
    ]),
  ).map((c) => ({ value: c, label: c }));

  const categoryOptions = Array.from(
    new Set([
      "billing",
      "technical",
      "account",
      "general",
      ...((existingIssues?.items || []) as Issue[])
        .map((i) => i.category)
        .filter((c): c is string => Boolean(c)),
    ]),
  ).map((c) => ({ value: c, label: c }));

  const createMutation = useMutation({
    mutationFn: async (data: {
      title: string;
      description?: string;
      priority: string;
      issue_type: string;
      organization_id: number;
      assignee_id?: number;
      assignee_type?: "identity" | "org_unit";
      is_incident: number;
      channel?: string;
      category?: string;
      parent_issue_id?: number;
    }) => {
      console.log("[CreateIssueModal] Submit", {
        title: data.title,
        issueType: data.issue_type,
        organizationId: data.organization_id,
      });
      // Create the issue first — this is the critical operation.
      const issue = await api.createIssue(data);

      // CreateIssueRequest has no entity_ids/label_ids field (see Task 2
      // recon) — link each selection as follow-up calls against the existing
      // per-item endpoints (same as IssueDetail.tsx's sidebar). These are
      // best-effort: if they fail, the issue is already created and the UI
      // must reflect that. Failures here must NOT reject the mutation or
      // prevent the modal from closing.
      const failedLinks: string[] = [];

      for (const entityId of entityIds) {
        try {
          await api.linkIssueEntity(issue.id, entityId);
        } catch (err) {
          console.warn("[CreateIssueModal] Failed to link entity", {
            entityId,
            error: err,
          });
          failedLinks.push(`entity ${entityId}`);
        }
      }

      for (const r of selectedResources) {
        try {
          await api.linkIssueResource(issue.id, r.type, r.id);
        } catch (err) {
          console.warn("[CreateIssueModal] Failed to link resource", {
            resource: r,
            error: err,
          });
          failedLinks.push(`${r.type} ${r.id}`);
        }
      }

      for (const labelId of labelIds) {
        try {
          await api.addIssueLabel(issue.id, labelId);
        } catch (err) {
          console.warn("[CreateIssueModal] Failed to link label", {
            labelId,
            error: err,
          });
          failedLinks.push(`label ${labelId}`);
        }
      }

      if (projectId) {
        try {
          await api.linkIssueToProject(issue.id, parseInt(projectId, 10));
        } catch (err) {
          console.warn("[CreateIssueModal] Failed to link project", {
            projectId,
            error: err,
          });
          failedLinks.push(`project ${projectId}`);
        }
      }

      if (milestoneId) {
        try {
          await api.linkIssueToMilestone(issue.id, parseInt(milestoneId, 10));
        } catch (err) {
          console.warn("[CreateIssueModal] Failed to link milestone", {
            milestoneId,
            error: err,
          });
          failedLinks.push(`milestone ${milestoneId}`);
        }
      }

      // Return issue + any failed links for the success handler to decide
      // whether to show a secondary warning.
      return { issue, failedLinks };
    },
    onSuccess: (result) => {
      toast.success(
        parentIssueId
          ? "Sub-task created successfully"
          : "Issue created successfully",
      );
      // If some entity/label links failed, show a secondary non-fatal warning.
      // The issue itself was created; this is just a heads-up that some
      // relationships couldn't be established.
      if (result.failedLinks.length > 0) {
        toast.error(
          `Issue created, but some links couldn't be established (${result.failedLinks.join(", ")})`,
        );
      }
      onSuccess();
    },
    onError: () => {
      toast.error(
        parentIssueId ? "Failed to create sub-task" : "Failed to create issue",
      );
    },
  });

  const toggleEntity = (id: number) => {
    setEntityIds((prev) =>
      prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id],
    );
  };

  const toggleLabel = (id: number) => {
    setLabelIds((prev) =>
      prev.includes(id) ? prev.filter((l) => l !== id) : [...prev, id],
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !organizationId) return;
    createMutation.mutate({
      title: title.trim(),
      description: description.trim() || undefined,
      priority,
      issue_type: issueType,
      organization_id: parseInt(organizationId),
      assignee_id: assignee?.assignee_id,
      assignee_type: assignee?.assignee_type,
      is_incident: isIncident ? 1 : 0,
      channel:
        issueType === SUPPORT_ISSUE_TYPE
          ? channel.trim() || undefined
          : undefined,
      category:
        issueType === SUPPORT_ISSUE_TYPE
          ? category.trim() || undefined
          : undefined,
      parent_issue_id:
        parentIssueId ??
        (selectedParentIssueId
          ? parseInt(selectedParentIssueId, 10)
          : undefined),
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">
            {parentIssueId ? "Create Sub-Task" : "Create Issue"}
          </h2>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={handleSubmit}
            className="space-y-4"
            data-testid="create-issue-form"
          >
            <Input
              label="Title"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter issue title"
              data-testid="issue-title-input"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Description
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Enter description (optional)"
                rows={4}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Select
                label="Priority"
                required
                value={priority}
                onChange={(e) => setPriority(e.target.value as IssuePriority)}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </Select>
              <Select
                label="Issue Type"
                required
                value={issueType}
                onChange={(e) => setIssueType(e.target.value as IssueType)}
                data-testid="issue-type-select"
              >
                {ISSUE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1.5">
                Organization <span className="text-red-400">*</span>
              </span>
              <SearchableSelect
                options={(organizations?.items || []).map(
                  (org: Organization) => ({
                    value: String(org.id),
                    label: org.name,
                  }),
                )}
                value={organizationId}
                onChange={(val) => setOrganizationId(String(val))}
                placeholder="Search organizations..."
                ariaLabel="Organization"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Assignee
              </label>
              <AssigneePicker value={assignee} onChange={setAssignee} />
            </div>
            {issueType === SUPPORT_ISSUE_TYPE && (
              <div
                className="grid grid-cols-2 gap-4 p-4 bg-slate-800/30 rounded-lg"
                data-testid="support-fields"
              >
                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1.5">
                    Channel
                  </span>
                  <SearchableSelect
                    options={channelOptions}
                    value={channel}
                    onChange={(val) => setChannel(String(val))}
                    placeholder="email, chat, phone..."
                    allowCustom
                    allowClear
                    ariaLabel="Channel"
                  />
                </div>
                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1.5">
                    Category
                  </span>
                  <SearchableSelect
                    options={categoryOptions}
                    value={category}
                    onChange={(val) => setCategory(String(val))}
                    placeholder="billing, technical..."
                    allowCustom
                    allowClear
                    ariaLabel="Category"
                  />
                </div>
              </div>
            )}
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1.5">
                Resources
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <Select
                  aria-label="Resource Type"
                  value={resourceType}
                  onChange={(e) => {
                    setResourceType(e.target.value);
                    setResourceSearch("");
                  }}
                >
                  {ISSUE_RESOURCE_TYPES.map((rt) => (
                    <option key={rt.value} value={rt.value}>
                      {rt.label}
                    </option>
                  ))}
                </Select>
                <div className="sm:col-span-2">
                  <SearchableSelect
                    options={resourceOptions}
                    value=""
                    onSearch={setResourceSearch}
                    isLoading={resourcesLoading}
                    onChange={(val, opt) => {
                      const id = parseInt(String(val), 10);
                      if (
                        !Number.isNaN(id) &&
                        !selectedResources.some(
                          (r) => r.type === resourceType && r.id === id,
                        )
                      ) {
                        setSelectedResources((prev) => [
                          ...prev,
                          { type: resourceType, id, label: opt.label },
                        ]);
                      }
                    }}
                    placeholder="Search and link resources..."
                    ariaLabel="Resources"
                  />
                </div>
              </div>
              {selectedResources.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selectedResources.map((r) => (
                    <span
                      key={`${r.type}:${r.id}`}
                      className="inline-flex items-center gap-1 rounded-full border border-slate-600 bg-slate-800 px-2.5 py-0.5 text-xs text-slate-200"
                    >
                      <span className="text-slate-400">{r.type}:</span>
                      {r.label}
                      <button
                        type="button"
                        onClick={() =>
                          setSelectedResources((prev) =>
                            prev.filter(
                              (item) =>
                                !(item.type === r.type && item.id === r.id),
                            ),
                          )
                        }
                        aria-label={`Remove ${r.type} ${r.label}`}
                        className="text-slate-400 hover:text-white"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1.5">
                Entities
              </span>
              <SearchableSelect
                options={((entities?.items || []) as Entity[])
                  .filter((e) => !entityIds.includes(e.id))
                  .map((e) => ({
                    value: String(e.id),
                    label: e.type ? `${e.name} (${e.type})` : e.name,
                  }))}
                value=""
                onChange={(val) => {
                  const id = parseInt(String(val), 10);
                  if (!Number.isNaN(id) && !entityIds.includes(id)) {
                    setEntityIds((prev) => [...prev, id]);
                  }
                }}
                placeholder="Search and link entities..."
                ariaLabel="Entities"
              />
              {entityIds.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {entityIds.map((id) => {
                    const ent = ((entities?.items || []) as Entity[]).find(
                      (e) => e.id === id,
                    );
                    return (
                      <span
                        key={id}
                        className="inline-flex items-center gap-1 rounded-full border border-slate-600 bg-slate-800 px-2.5 py-0.5 text-xs text-slate-200"
                      >
                        {ent ? ent.name : `Entity #${id}`}
                        <button
                          type="button"
                          onClick={() => toggleEntity(id)}
                          aria-label={`Remove ${ent ? ent.name : id}`}
                          className="text-slate-400 hover:text-white"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="block text-sm font-medium text-slate-300 mb-1.5">
                  Project (Optional)
                </span>
                <SearchableSelect
                  options={(projectsData?.items || []).map(
                    (p: { id: number; name: string }) => ({
                      value: String(p.id),
                      label: p.name,
                    }),
                  )}
                  value={projectId}
                  onChange={(val) => setProjectId(String(val))}
                  placeholder="Link to project..."
                  allowClear
                  ariaLabel="Project"
                />
              </div>
              <div>
                <span className="block text-sm font-medium text-slate-300 mb-1.5">
                  Milestone (Optional)
                </span>
                <SearchableSelect
                  options={(milestonesData?.items || []).map(
                    (m: { id: number; title: string }) => ({
                      value: String(m.id),
                      label: m.title,
                    }),
                  )}
                  value={milestoneId}
                  onChange={(val) => setMilestoneId(String(val))}
                  placeholder="Link to milestone..."
                  allowClear
                  ariaLabel="Milestone"
                />
              </div>
            </div>

            {!parentIssueId && (
              <div>
                <span className="block text-sm font-medium text-slate-300 mb-1.5">
                  Parent Issue (Optional)
                </span>
                <SearchableSelect
                  options={((existingIssues?.items || []) as Issue[]).map(
                    (iss) => ({
                      value: String(iss.id),
                      label: `#${iss.id} ${iss.title}`,
                    }),
                  )}
                  value={selectedParentIssueId}
                  onChange={(val) => setSelectedParentIssueId(String(val))}
                  placeholder="Search parent issue..."
                  allowClear
                  ariaLabel="Parent Issue"
                />
              </div>
            )}

            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="block text-sm font-medium text-slate-300">
                  Labels
                </span>
                <button
                  type="button"
                  onClick={() => setShowCreateLabel((v) => !v)}
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary-400 hover:text-primary-300"
                >
                  <Plus className="h-3.5 w-3.5" />
                  {showCreateLabel ? "Cancel" : "Create Label"}
                </button>
              </div>
              {showCreateLabel && (
                <div className="mb-2 space-y-2 rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {[
                      "#3b82f6",
                      "#ef4444",
                      "#f97316",
                      "#f59e0b",
                      "#10b981",
                      "#14b8a6",
                      "#06b6d4",
                      "#6366f1",
                      "#a855f7",
                      "#ec4899",
                      "#64748b",
                      "#84cc16",
                    ].map((hex) => (
                      <button
                        key={hex}
                        type="button"
                        aria-label={`Select label color ${hex}`}
                        onClick={() => setNewLabelColor(hex)}
                        className={`h-5 w-5 rounded-full border transition-transform ${
                          newLabelColor.toLowerCase() === hex.toLowerCase()
                            ? "scale-110 border-white ring-2 ring-primary-400"
                            : "border-slate-600 hover:scale-105"
                        }`}
                        style={{ backgroundColor: hex }}
                      />
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <Input
                      type="text"
                      value={newLabelName}
                      onChange={(e) => setNewLabelName(e.target.value)}
                      placeholder="Label name (e.g. bug, urgent)"
                      aria-label="New label name"
                    />
                    <input
                      type="color"
                      value={newLabelColor}
                      onChange={(e) => setNewLabelColor(e.target.value)}
                      aria-label="New label color"
                      className="h-9 w-12 cursor-pointer rounded border border-slate-600 bg-slate-800 p-0.5"
                    />
                  </div>
                  <div className="flex gap-2">
                    <Input
                      type="text"
                      value={newLabelDescription}
                      onChange={(e) => setNewLabelDescription(e.target.value)}
                      placeholder="Description (optional)"
                      aria-label="New label description"
                    />
                    <Button
                      type="button"
                      size="sm"
                      disabled={
                        !newLabelName.trim() || createLabelMutation.isPending
                      }
                      isLoading={createLabelMutation.isPending}
                      onClick={() => {
                        if (!newLabelName.trim()) return;
                        createLabelMutation.mutate({
                          name: newLabelName.trim(),
                          color: newLabelColor,
                          description: newLabelDescription.trim() || undefined,
                        });
                      }}
                    >
                      Add
                    </Button>
                  </div>
                </div>
              )}
              {(labels?.items || []).length === 0 ? (
                <div className="flex items-center justify-between rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-400">
                  <span>No labels available yet.</span>
                  <button
                    type="button"
                    onClick={() => setShowCreateLabel(true)}
                    className="font-medium text-primary-400 hover:text-primary-300"
                  >
                    Create Labels
                  </button>
                </div>
              ) : (
                <div className="max-h-32 overflow-y-auto space-y-1 border border-slate-700 rounded-lg p-2">
                  {labels?.items?.map((label: IssueLabel) => (
                    <label
                      key={label.id}
                      className="flex items-center gap-2 text-sm cursor-pointer"
                      style={{ color: label.color }}
                    >
                      <input
                        type="checkbox"
                        checked={labelIds.includes(label.id)}
                        onChange={() => toggleLabel(label.id)}
                        className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                      />
                      {label.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={isIncident}
                onChange={(e) => setIsIncident(e.target.checked)}
                className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
              />
              <span className="text-sm text-slate-300">Mark as Incident</span>
            </label>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                isLoading={createMutation.isPending}
                data-testid="submit-issue-button"
              >
                {parentIssueId ? "Create Sub-Task" : "Create Issue"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
