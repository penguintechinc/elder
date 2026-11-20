import { useState, useEffect, useMemo, type FormEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Plus, Search, Trash2, Edit, X } from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { invalidateCache } from "@/lib/invalidateCache";
import { confirmDelete } from "@/lib/confirmActions";
import type { Organization } from "@/types";
import Button from "@/components/Button";
import Card, { CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import SearchableSelect from "@/components/SearchableSelect";
import VillageIdBadge from "@/components/VillageIdBadge";
import {
  FormModalBuilder,
  FormField,
} from "@penguintechinc/react-libs/components";
import { ORGANIZATION_TYPES } from "@/lib/constants/organizationTypes";

// Form fields for organization creation
const orgFields: FormField[] = [
  {
    name: "name",
    label: "Name",
    type: "text",
    required: true,
    placeholder: "Enter organization name",
  },
  {
    name: "organization_type",
    label: "Type",
    type: "select",
    required: true,
    defaultValue: "organization",
    options: ORGANIZATION_TYPES,
  },
  {
    name: "description",
    label: "Description",
    type: "textarea",
    placeholder: "Enter description (optional)",
    rows: 3,
  },
];

export default function Organizations() {
  const [search, setSearch] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingOrg, setEditingOrg] = useState<Organization | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const initialParentId = searchParams.get("parent_id");

  const [createName, setCreateName] = useState("");
  const [createType, setCreateType] = useState("organization");
  const [createParentId, setCreateParentId] = useState<string | number>(
    initialParentId ? Number(initialParentId) : "",
  );
  const [createDescription, setCreateDescription] = useState("");

  const resetCreateForm = () => {
    setCreateName("");
    setCreateType("organization");
    setCreateParentId("");
    setCreateDescription("");
  };

  // Auto-open create modal if parent_id is in query params
  useEffect(() => {
    if (initialParentId) {
      setCreateParentId(Number(initialParentId));
      setShowCreateModal(true);
    }
  }, [initialParentId]);

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.organizations.list({ search }),
    queryFn: () => api.getOrganizations({ search }),
  });

  const { data: allOrgsData } = useQuery({
    queryKey: queryKeys.organizations.list({}),
    queryFn: () => api.getOrganizations({}),
    enabled: showCreateModal,
  });

  const parentOrgOptions = useMemo(() => {
    const items = allOrgsData?.items ?? data?.items ?? [];
    return [
      { value: "", label: "None (Top-level organization)" },
      ...items.map((org: Organization) => ({
        value: org.id,
        label: org.name,
      })),
    ];
  }, [allOrgsData, data]);

  const createMutation = useMutation({
    mutationFn: (data: {
      name: string;
      description?: string;
      organization_type?: string;
      parent_id?: number;
    }) => api.createOrganization(data),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient);
      setShowCreateModal(false);
      resetCreateForm();
      toast.success("Organization created successfully");
      if (initialParentId) {
        navigate("/organizations", { replace: true });
      }
    },
    onError: (error) => {
      console.error("Create organization error:", error);
      toast.error("Failed to create organization");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: number;
      data: { name: string; description?: string; organization_type?: string };
    }) => api.updateOrganization(id, data),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient);
      setEditingOrg(null);
      toast.success("Organization updated successfully");
    },
    onError: () => {
      toast.error("Failed to update organization");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteOrganization(id),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient);
      toast.success("Organization deleted successfully");
    },
    onError: () => {
      toast.error("Failed to delete organization");
    },
  });

  const handleDelete = (id: number, name: string) => {
    confirmDelete(name, () => deleteMutation.mutate(id));
  };

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingOrg) return orgFields;
    return [
      {
        name: "name",
        label: "Name",
        type: "text",
        required: true,
        placeholder: "Enter organization name",
        defaultValue: editingOrg.name,
      },
      {
        name: "organization_type",
        label: "Type",
        type: "select",
        required: true,
        defaultValue: editingOrg.organization_type || "organization",
        options: ORGANIZATION_TYPES,
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
        rows: 3,
        defaultValue: editingOrg.description || "",
      },
    ];
  }, [editingOrg]);

  const handleCreate = (e: FormEvent) => {
    e.preventDefault();
    if (!createName.trim()) return;
    const payload: {
      name: string;
      description?: string;
      organization_type?: string;
      parent_id?: number;
    } = {
      name: createName.trim(),
      description: createDescription.trim() || undefined,
      organization_type: createType,
    };
    if (
      createParentId !== "" &&
      createParentId !== null &&
      createParentId !== undefined
    ) {
      payload.parent_id = Number(createParentId);
    }
    createMutation.mutate(payload);
  };

  const handleUpdate = (formData: Record<string, unknown>) => {
    if (!editingOrg) return;
    updateMutation.mutate({
      id: editingOrg.id,
      data: {
        name: formData.name as string,
        description: (formData.description as string) || undefined,
        organization_type: formData.organization_type as string,
      },
    });
  };

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Organization Units</h1>
          <p className="mt-2 text-slate-400">
            Manage your organizational hierarchy
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Organization Unit
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search organizations..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Organizations List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No organization units found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first organization unit
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {data?.items?.map((org: Organization) => (
            <Card
              key={org.id}
              className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
            >
              <CardContent onClick={() => navigate(`/organizations/${org.id}`)}>
                <div className="flex items-start justify-between mb-3">
                  <h3 className="text-lg font-semibold text-white">
                    {org.name}
                  </h3>
                  <div
                    className="flex gap-2"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={() => setEditingOrg(org)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(org.id, org.name)}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                {org.description && (
                  <p className="text-sm text-slate-400 mb-4">
                    {org.description}
                  </p>
                )}
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>ID: {org.id}</span>
                  <span>{new Date(org.created_at).toLocaleDateString()}</span>
                </div>
                {org.village_id && (
                  <div className="mt-3 pt-2 border-t border-slate-700/50">
                    <VillageIdBadge villageId={org.village_id} compact />
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Create Organization Unit"
        >
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-800 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-700 px-6 py-4">
              <h2 className="text-lg font-semibold text-white">
                Create Organization Unit
              </h2>
              <button
                type="button"
                onClick={() => {
                  setShowCreateModal(false);
                  resetCreateForm();
                  if (initialParentId) {
                    navigate("/organizations", { replace: true });
                  }
                }}
                className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"
                aria-label="Close modal"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <form onSubmit={handleCreate} className="space-y-4 p-6">
              <div>
                <label
                  htmlFor="create-org-name"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Name <span className="text-red-400">*</span>
                </label>
                <Input
                  id="create-org-name"
                  type="text"
                  required
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="Enter organization name"
                />
              </div>

              <div>
                <label
                  htmlFor="create-org-type"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Type <span className="text-red-400">*</span>
                </label>
                <Select
                  id="create-org-type"
                  value={createType}
                  onChange={(e) => setCreateType(e.target.value)}
                  options={ORGANIZATION_TYPES}
                />
              </div>

              <div>
                <span className="mb-1.5 block text-sm font-medium text-slate-300">
                  Parent Organization (Optional)
                </span>
                <SearchableSelect
                  options={parentOrgOptions}
                  value={createParentId}
                  onChange={(val) =>
                    setCreateParentId(val === "" ? "" : Number(val))
                  }
                  placeholder="Search parent organization..."
                  ariaLabel="Parent Organization"
                />
              </div>

              <div>
                <label
                  htmlFor="create-org-description"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Description
                </label>
                <textarea
                  id="create-org-description"
                  rows={3}
                  value={createDescription}
                  onChange={(e) => setCreateDescription(e.target.value)}
                  placeholder="Enter description (optional)"
                  className="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setShowCreateModal(false);
                    resetCreateForm();
                    if (initialParentId) {
                      navigate("/organizations", { replace: true });
                    }
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createMutation.isPending || !createName.trim()}
                >
                  {createMutation.isPending ? "Creating..." : "Create"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editingOrg && (
        <FormModalBuilder
          title="Edit Organization Unit"
          fields={editFields}
          isOpen={!!editingOrg}
          onClose={() => setEditingOrg(null)}
          onSubmit={handleUpdate}
          submitButtonText="Update"
        />
      )}
    </div>
  );
}
