import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  Edit,
  Trash2,
  Flag,
  Calendar,
  X,
  AlertCircle,
  ArrowRight,
  GitBranch,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import { Organization } from "@/types";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import ModalFormBuilder from "@/components/ModalFormBuilder";
import { FormField } from "@/types/form";

const MILESTONE_STATUSES = [
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
];

interface Milestone {
  id: number;
  title: string;
  description?: string;
  status: string;
  organization_id: number;
  project_id?: number;
  due_date?: string;
  created_at: string;
}

interface Project {
  id: number;
  name: string;
  status?: string;
}

interface MilestoneIssue {
  id: number;
  title: string;
  status: string;
  priority?: string;
  issue_type?: string;
}

const getIssueStatusColor = (status: string) => {
  switch (status?.toLowerCase()) {
    case "open":
    case "new":
      return "bg-blue-500/20 text-blue-400";
    case "in_progress":
    case "in progress":
      return "bg-yellow-500/20 text-yellow-400";
    case "resolved":
    case "closed":
    case "done":
      return "bg-green-500/20 text-green-400";
    default:
      return "bg-slate-500/20 text-slate-400";
  }
};

export default function Milestones() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [projectFilter, setProjectFilter] = useState<string>("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingMilestone, setEditingMilestone] = useState<Milestone | null>(
    null,
  );
  const [viewingMilestone, setViewingMilestone] = useState<Milestone | null>(
    null,
  );
  const queryClient = useQueryClient();

  const {
    data: milestoneIssuesData,
    isLoading: isLoadingIssues,
    isError: isErrorIssues,
  } = useQuery<{ issues: MilestoneIssue[] }>({
    queryKey: ["milestone-issues", viewingMilestone?.id],
    queryFn: () => api.getMilestoneIssues(viewingMilestone!.id),
    enabled: !!viewingMilestone,
  });

  const { data, isLoading } = useQuery({
    queryKey: [
      "milestones",
      { search, status: statusFilter, project_id: projectFilter },
    ],
    queryFn: () =>
      api.getMilestones({
        search,
        status: statusFilter || undefined,
        project_id: projectFilter ? parseInt(projectFilter) : undefined,
      }),
  });

  const { data: projects } = useQuery({
    queryKey: ["projects-all"],
    queryFn: () => api.getProjects({ per_page: 1000 }),
  });

  const { data: organizations } = useQuery({
    queryKey: ["organizations-all"],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteMilestone(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["milestones"],
        refetchType: "all",
      });
      toast.success("Milestone deleted successfully");
    },
    onError: () => {
      toast.error("Failed to delete milestone");
    },
  });

  interface MilestoneData {
    title: string;
    description?: string;
    status: string;
    organization_id: number;
    project_id?: number;
    due_date?: string;
  }

  const createMutation = useMutation({
    mutationFn: (data: MilestoneData) => api.createMilestone(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["milestones"],
        refetchType: "all",
      });
      toast.success("Milestone created successfully");
      setShowCreateModal(false);
    },
    onError: () => {
      toast.error("Failed to create milestone");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: MilestoneData }) =>
      api.updateMilestone(id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["milestones"],
        refetchType: "all",
      });
      toast.success("Milestone updated successfully");
      setEditingMilestone(null);
    },
    onError: () => {
      toast.error("Failed to update milestone");
    },
  });

  const handleDelete = (id: number, title: string) => {
    if (
      window.confirm(`Are you sure you want to delete milestone "${title}"?`)
    ) {
      deleteMutation.mutate(id);
    }
  };

  const handleCreate = (formData: Record<string, unknown>) => {
    createMutation.mutate({
      title: formData.title as string,
      description: (formData.description as string) || undefined,
      status: formData.status as string,
      organization_id: parseInt(formData.organization_id as string),
      project_id: formData.project_id
        ? parseInt(formData.project_id as string)
        : undefined,
      due_date: (formData.due_date as string) || undefined,
    });
  };

  const handleUpdate = (formData: Record<string, unknown>) => {
    if (editingMilestone) {
      updateMutation.mutate({
        id: editingMilestone.id,
        data: {
          title: formData.title as string,
          description: (formData.description as string) || undefined,
          status: formData.status as string,
          organization_id: parseInt(formData.organization_id as string),
          project_id: formData.project_id
            ? parseInt(formData.project_id as string)
            : undefined,
          due_date: (formData.due_date as string) || undefined,
        },
      });
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "open":
        return "bg-green-500/20 text-green-400";
      case "closed":
        return "bg-slate-500/20 text-slate-400";
      default:
        return "bg-slate-500/20 text-slate-400";
    }
  };

  const isOverdue = (dueDate: string, status: string) => {
    if (status === "closed") return false;
    if (!dueDate) return false;
    return new Date(dueDate) < new Date();
  };

  // Form fields for milestone creation
  const milestoneFields: FormField[] = useMemo(
    () => [
      {
        name: "title",
        label: "Title",
        type: "text",
        required: true,
        placeholder: "Beta Release",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Milestone description (optional)",
        rows: 3,
      },
      {
        name: "organization_id",
        label: "Organization",
        type: "select",
        required: true,
        searchable: true,
        options: [
          {
            value: "",
            label: organizations?.items?.length
              ? "Select organization"
              : "No organizations found - create one first",
          },
          ...(organizations?.items?.map((org: Organization) => ({
            value: org.id.toString(),
            label: org.name,
          })) || []),
        ],
      },
      {
        name: "project_id",
        label: "Project (Optional)",
        type: "select",
        searchable: true,
        options: [
          { value: "", label: "No project" },
          ...(projects?.items?.map((project: Project) => ({
            value: project.id.toString(),
            label: project.name,
          })) || []),
        ],
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        defaultValue: "open",
        options: MILESTONE_STATUSES.map((s) => ({
          value: s.value,
          label: s.label,
        })),
      },
      {
        name: "due_date",
        label: "Due Date",
        type: "date",
      },
    ],
    [organizations?.items, projects?.items],
  );

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingMilestone) return milestoneFields;
    return [
      {
        name: "title",
        label: "Title",
        type: "text",
        required: true,
        placeholder: "Beta Release",
        defaultValue: editingMilestone.title || "",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Milestone description (optional)",
        rows: 3,
        defaultValue: editingMilestone.description || "",
      },
      {
        name: "organization_id",
        label: "Organization",
        type: "select",
        required: true,
        searchable: true,
        defaultValue: editingMilestone.organization_id?.toString() || "",
        options: [
          {
            value: "",
            label: organizations?.items?.length
              ? "Select organization"
              : "No organizations found - create one first",
          },
          ...(organizations?.items?.map((org: Organization) => ({
            value: org.id.toString(),
            label: org.name,
          })) || []),
        ],
      },
      {
        name: "project_id",
        label: "Project (Optional)",
        type: "select",
        searchable: true,
        defaultValue: editingMilestone.project_id?.toString() || "",
        options: [
          { value: "", label: "No project" },
          ...(projects?.items?.map((project: Project) => ({
            value: project.id.toString(),
            label: project.name,
          })) || []),
        ],
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        defaultValue: editingMilestone.status || "open",
        options: MILESTONE_STATUSES.map((s) => ({
          value: s.value,
          label: s.label,
        })),
      },
      {
        name: "due_date",
        label: "Due Date",
        type: "date",
        defaultValue: editingMilestone.due_date || "",
      },
    ];
  }, [
    editingMilestone,
    organizations?.items,
    projects?.items,
    milestoneFields,
  ]);

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Milestones</h1>
          <p className="mt-2 text-slate-400">
            Track project milestones and deadlines
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            to="/admin/sync-config"
            className="inline-flex items-center px-3.5 py-2 text-sm font-medium rounded-lg border border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <GitBranch className="w-4 h-4 mr-2 text-yellow-500" />
            GitHub & Jira Sync
          </Link>
          <Button onClick={() => setShowCreateModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Create Milestone
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-6">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search milestones..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Projects</option>
          {projects?.items?.map((project: Project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </Select>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Statuses</option>
          {MILESTONE_STATUSES.map((status) => (
            <option key={status.value} value={status.value}>
              {status.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Milestones List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Flag className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No milestones found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first milestone
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((milestone: Milestone) => (
            <Card
              key={milestone.id}
              className="cursor-pointer hover:border-slate-600 transition-colors"
              onClick={() => setViewingMilestone(milestone)}
            >
              <CardContent>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Flag
                      className={`w-5 h-5 flex-shrink-0 ${
                        isOverdue(milestone.due_date, milestone.status)
                          ? "text-red-400"
                          : "text-primary-400"
                      }`}
                    />
                    <h3 className="text-lg font-semibold text-white truncate">
                      {milestone.title}
                    </h3>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      type="button"
                      aria-label={`Edit ${milestone.title}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingMilestone(milestone);
                      }}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${milestone.title}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(milestone.id, milestone.title);
                      }}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {milestone.description && (
                  <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                    {milestone.description}
                  </p>
                )}

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span
                      className={`text-xs px-2 py-1 rounded ${getStatusColor(milestone.status)}`}
                    >
                      {milestone.status}
                    </span>
                    {milestone.due_date && (
                      <div
                        className={`flex items-center gap-1 text-xs ${
                          isOverdue(milestone.due_date, milestone.status)
                            ? "text-red-400 font-semibold"
                            : "text-slate-500"
                        }`}
                      >
                        <Calendar className="w-3 h-3" />
                        {new Date(milestone.due_date).toLocaleDateString()}
                        {isOverdue(milestone.due_date, milestone.status) &&
                          " (Overdue)"}
                      </div>
                    )}
                  </div>

                  {milestone.project_id && (
                    <div className="text-xs text-slate-500">
                      Project:{" "}
                      {projects?.items?.find(
                        (p: Project) => p.id === milestone.project_id,
                      )?.name || milestone.project_id}
                    </div>
                  )}

                  <div className="pt-2 border-t border-slate-700/50 flex items-center justify-between">
                    <span className="text-xs text-primary-400 font-medium inline-flex items-center gap-1">
                      View Issues
                      <ArrowRight className="w-3.5 h-3.5" />
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <ModalFormBuilder
          title="Create Milestone"
          fields={milestoneFields}
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSubmit={handleCreate}
          submitLabel="Create"
        />
      )}

      {/* Edit Modal */}
      {editingMilestone && (
        <ModalFormBuilder
          title="Edit Milestone"
          fields={editFields}
          isOpen={!!editingMilestone}
          onClose={() => setEditingMilestone(null)}
          onSubmit={handleUpdate}
          submitLabel="Update"
        />
      )}

      {/* Milestone Issues Modal */}
      {viewingMilestone && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setViewingMilestone(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="milestone-issues-modal-title"
        >
          <Card
            className="w-full max-w-2xl max-h-[85vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <CardHeader className="flex flex-row items-center justify-between">
              <div className="min-w-0 pr-4">
                <div className="flex items-center gap-2">
                  <Flag className="w-5 h-5 text-primary-400 flex-shrink-0" />
                  <h2
                    id="milestone-issues-modal-title"
                    className="text-xl font-semibold text-white truncate"
                  >
                    {viewingMilestone.title}
                  </h2>
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${getStatusColor(viewingMilestone.status)}`}
                  >
                    {viewingMilestone.status}
                  </span>
                </div>
                {viewingMilestone.description && (
                  <p className="text-sm text-slate-400 mt-1 line-clamp-2">
                    {viewingMilestone.description}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setViewingMilestone(null)}
                aria-label="Close milestone issues"
                className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors flex-shrink-0"
              >
                <X className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent className="overflow-y-auto flex-1 py-4">
              {isLoadingIssues ? (
                <div className="flex items-center justify-center py-10">
                  <div className="w-7 h-7 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : isErrorIssues ? (
                <div className="text-center py-10 text-red-400">
                  <AlertCircle className="w-10 h-10 mx-auto mb-2 opacity-80" />
                  <p>Failed to load issues for this milestone.</p>
                </div>
              ) : (milestoneIssuesData?.issues?.length ?? 0) === 0 ? (
                <div className="text-center py-10 text-slate-400">
                  <AlertCircle className="w-10 h-10 mx-auto mb-2 text-slate-600" />
                  <p>No issues linked to this milestone yet.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {milestoneIssuesData?.issues?.map((issue) => (
                    <Link
                      key={issue.id}
                      to={`/issues/${issue.id}`}
                      className="flex items-center justify-between gap-3 p-3 rounded-lg bg-slate-800/60 hover:bg-slate-700/60 border border-slate-700 hover:border-slate-600 transition-colors"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono text-slate-400">
                            #{issue.id}
                          </span>
                          <span className="text-sm font-medium text-white truncate">
                            {issue.title}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {issue.priority && (
                          <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">
                            {issue.priority}
                          </span>
                        )}
                        <span
                          className={`text-xs px-2 py-0.5 rounded ${getIssueStatusColor(issue.status)}`}
                        >
                          {issue.status}
                        </span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
