import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  Edit,
  Trash2,
  Database,
  Tag,
  X,
  Check,
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

interface Organization {
  id: number;
  name: string;
}

interface Identity {
  id: number;
  username: string;
  full_name?: string;
}

interface DataLabel {
  id: number;
  name: string;
  color: string;
  description?: string | null;
}

interface DataStore {
  id: number;
  name: string;
  description?: string;
  organization_id: number;
  data_classification: string;
  storage_type: string;
  location_region?: string;
  contains_pii?: boolean;
  contains_phi?: boolean;
  contains_pci?: boolean;
  compliance_framework?: string;
  poc_identity_id?: number;
  metadata?: Record<string, unknown> | null;
  labels?: DataLabel[];
}

const DATA_CLASSIFICATIONS = [
  { value: "public", label: "Public", color: "bg-green-500/20 text-green-400" },
  {
    value: "internal",
    label: "Internal",
    color: "bg-yellow-500/20 text-yellow-400",
  },
  {
    value: "confidential",
    label: "Confidential",
    color: "bg-orange-500/20 text-orange-400",
  },
  {
    value: "restricted",
    label: "Restricted",
    color: "bg-red-500/20 text-red-400",
  },
];

const STORAGE_TYPES = [
  { value: "database", label: "Database" },
  { value: "file_storage", label: "File Storage" },
  { value: "data_warehouse", label: "Data Warehouse" },
  { value: "data_lake", label: "Data Lake" },
  { value: "cache", label: "Cache" },
  { value: "message_queue", label: "Message Queue" },
  { value: "search_index", label: "Search Index" },
  { value: "time_series", label: "Time Series" },
  { value: "blob_storage", label: "Blob Storage" },
  { value: "other", label: "Other" },
];

const COMPLIANCE_FRAMEWORKS = [
  { value: "gdpr", label: "GDPR" },
  { value: "hipaa", label: "HIPAA" },
  { value: "pci_dss", label: "PCI-DSS" },
  { value: "sox", label: "SOX" },
  { value: "ccpa", label: "CCPA" },
  { value: "none", label: "None" },
];

const REGIONS = [
  { value: "us-east-1", label: "US East (N. Virginia)" },
  { value: "us-east-2", label: "AWS US East (Ohio)" },
  { value: "us-west-1", label: "AWS US West (N. California)" },
  { value: "us-west-2", label: "US West (Oregon)" },
  { value: "eu-west-1", label: "EU (Ireland)" },
  { value: "eu-west-2", label: "AWS EU (London)" },
  { value: "eu-central-1", label: "EU (Frankfurt)" },
  { value: "ap-south-1", label: "AWS Asia Pacific (Mumbai)" },
  { value: "ap-southeast-1", label: "Asia Pacific (Singapore)" },
  { value: "ap-northeast-1", label: "Asia Pacific (Tokyo)" },
  { value: "us-central1", label: "GCP US Central (Iowa)" },
  { value: "us-east1", label: "GCP US East (South Carolina)" },
  { value: "us-west1", label: "GCP US West (Oregon)" },
  { value: "europe-west1", label: "GCP Europe West (Belgium)" },
  { value: "europe-west4", label: "GCP Europe West (Netherlands)" },
  { value: "asia-east1", label: "GCP Asia East (Taiwan)" },
  { value: "asia-southeast1", label: "GCP Asia Southeast (Singapore)" },
  { value: "eastus", label: "Azure East US" },
  { value: "eastus2", label: "Azure East US 2" },
  { value: "westus2", label: "Azure West US 2" },
  { value: "westeurope", label: "Azure West Europe" },
  { value: "northeurope", label: "Azure North Europe" },
  { value: "southeastasia", label: "Azure Southeast Asia" },
  { value: "aws", label: "AWS" },
  { value: "gcp", label: "GCP" },
  { value: "azure", label: "Azure" },
  { value: "kubernetes", label: "Kubernetes" },
  { value: "vmware", label: "VMware" },
  { value: "proxmox", label: "Proxmox" },
  { value: "bare-metal", label: "Bare Metal" },
  { value: "on-prem", label: "On-Prem" },
  { value: "on-premises", label: "On-Premises" },
  { value: "hybrid", label: "Hybrid" },
  { value: "multi-region", label: "Multi-Region" },
  { value: "other", label: "Other" },
];

const LABEL_COLOR_PRESETS = [
  "#a855f7",
  "#3b82f6",
  "#06b6d4",
  "#f59e0b",
  "#10b981",
  "#ec4899",
  "#ef4444",
  "#6366f1",
];

export default function DataStores() {
  const [activeTab, setActiveTab] = useState<"inventory" | "labels">(
    "inventory",
  );
  const [search, setSearch] = useState("");
  const [organizationFilter, setOrganizationFilter] = useState<string>("");
  const [classificationFilter, setClassificationFilter] = useState<string>("");
  const [storageTypeFilter, setStorageTypeFilter] = useState<string>("");
  const [regionFilter, setRegionFilter] = useState<string>("");
  const [labelFilter, setLabelFilter] = useState<string>("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingDataStore, setEditingDataStore] = useState<DataStore | null>(
    null,
  );
  const [viewingDataStore, setViewingDataStore] = useState<DataStore | null>(
    null,
  );
  const queryClient = useQueryClient();

  const { data: organizations } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  });

  const { data: identities } = useQuery({
    queryKey: queryKeys.identities.list({}),
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  });

  const { data: geoRegionsData } = useQuery({
    queryKey: ["geo-regions"],
    queryFn: () => api.getGeoRegions(),
  });

  const { data: labelsData, refetch: refetchLabels } = useQuery({
    queryKey: ["labels-all"],
    queryFn: () => api.getLabels({ per_page: 1000 }),
  });

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.dataStores.list({
      search,
      organization_id: organizationFilter,
      data_classification: classificationFilter,
      storage_type: storageTypeFilter,
      location_region: regionFilter,
      label_id: labelFilter,
    }),
    queryFn: () =>
      api.getDataStores({
        search: search || undefined,
        organization_id: organizationFilter
          ? parseInt(organizationFilter)
          : undefined,
        data_classification: classificationFilter || undefined,
        storage_type: storageTypeFilter || undefined,
        location_region: regionFilter || undefined,
        label_id: labelFilter ? parseInt(labelFilter) : undefined,
      }),
  });

  // Ensure pre-seeded labels created on first /data-stores call show up in labels dropdown
  useEffect(() => {
    if (data && (!labelsData?.items || labelsData.items.length === 0)) {
      refetchLabels();
    }
  }, [data, labelsData?.items, refetchLabels]);

  const allLabels: DataLabel[] = useMemo(
    () => labelsData?.items || [],
    [labelsData?.items],
  );

  const createMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.createDataStore(
        payload as Parameters<typeof api.createDataStore>[0],
      ),
    onSuccess: async () => {
      await invalidateCache.dataStores(queryClient);
      toast.success("Data store created successfully");
      setShowCreateModal(false);
    },
    onError: () => {
      toast.error("Failed to create data store");
    },
  });

  const updateMutation = useMutation({
    mutationFn: (payload: unknown) =>
      api.updateDataStore(
        editingDataStore!.id,
        payload as Parameters<typeof api.updateDataStore>[1],
      ),
    onSuccess: async () => {
      await invalidateCache.dataStores(queryClient);
      toast.success("Data store updated successfully");
      setEditingDataStore(null);
    },
    onError: () => {
      toast.error("Failed to update data store");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDataStore(id),
    onSuccess: async () => {
      await invalidateCache.dataStores(queryClient);
      toast.success("Data store deleted successfully");
    },
    onError: () => {
      toast.error("Failed to delete data store");
    },
  });

  const handleDelete = (id: number, name: string) => {
    if (confirmDelete(name, "data store")) {
      deleteMutation.mutate(id);
    }
  };

  const regionOptions = useMemo(() => {
    const preset = REGIONS.filter((r) => r.value !== "other");
    const seen = new Set(preset.map((r) => r.value));
    const defined = (geoRegionsData?.regions || [])
      .filter((r) => r.code && !seen.has(r.code))
      .map((r) => {
        seen.add(r.code);
        return {
          value: r.code,
          label: r.name
            ? `${r.code} — ${r.name} [Defined]`
            : `${r.code} [Defined]`,
        };
      });
    const existing = ((data?.items || []) as DataStore[])
      .map((ds) => ds.location_region)
      .filter((r): r is string => Boolean(r) && !seen.has(r));
    const uniqueExisting = Array.from(new Set(existing)).map((r) => ({
      value: r,
      label: r,
    }));
    return [...preset, ...defined, ...uniqueExisting];
  }, [geoRegionsData?.regions, data?.items]);

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-white">Data Inventory</h1>
          <p className="mt-2 text-slate-400">
            Catalog, label, and manage all data repositories and storage systems
          </p>
        </div>
        {activeTab === "inventory" && (
          <Button onClick={() => setShowCreateModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Add Data Store
          </Button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-700 mb-6">
        <button
          type="button"
          onClick={() => setActiveTab("inventory")}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "inventory"
              ? "border-primary-500 text-white"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Database className="w-4 h-4" />
          Inventory
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("labels")}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "labels"
              ? "border-primary-500 text-white"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Tag className="w-4 h-4" />
          Data Labels
          {allLabels.length > 0 && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">
              {allLabels.length}
            </span>
          )}
        </button>
      </div>

      {activeTab === "labels" ? (
        <DataLabelsTab
          labels={allLabels}
          onFilterByLabel={(labelId) => {
            setLabelFilter(String(labelId));
            setActiveTab("inventory");
          }}
        />
      ) : (
        <>
          {/* Filters */}
          <div className="flex flex-wrap gap-4 mb-6">
            <div className="flex-1 min-w-[200px] max-w-md">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
                <Input
                  type="text"
                  placeholder="Search data inventory..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-10"
                />
              </div>
            </div>
            <Select
              aria-label="Filter by Data Label"
              value={labelFilter}
              onChange={(e) => setLabelFilter(e.target.value)}
              className="w-48"
            >
              <option value="">All Data Labels</option>
              {allLabels.map((lbl) => (
                <option key={lbl.id} value={lbl.id}>
                  {lbl.name}
                </option>
              ))}
            </Select>
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
              value={classificationFilter}
              onChange={(e) => setClassificationFilter(e.target.value)}
              className="w-48"
            >
              <option value="">All Classifications</option>
              {DATA_CLASSIFICATIONS.map((classif) => (
                <option key={classif.value} value={classif.value}>
                  {classif.label}
                </option>
              ))}
            </Select>
            <Select
              value={storageTypeFilter}
              onChange={(e) => setStorageTypeFilter(e.target.value)}
              className="w-48"
            >
              <option value="">All Storage Types</option>
              {STORAGE_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </Select>
            <Select
              value={regionFilter}
              onChange={(e) => setRegionFilter(e.target.value)}
              className="w-48"
            >
              <option value="">All Regions</option>
              {REGIONS.map((region) => (
                <option key={region.value} value={region.value}>
                  {region.label}
                </option>
              ))}
            </Select>
          </div>

          {/* Data Inventory List */}
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : data?.items?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Database className="w-16 h-16 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No data stores found</p>
                <Button
                  className="mt-4"
                  onClick={() => setShowCreateModal(true)}
                >
                  Create your first data store
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {data?.items?.map((dataStore: DataStore) => (
                <Card
                  key={dataStore.id}
                  className="cursor-pointer hover:border-primary-500/50 transition-colors"
                  onClick={() => setViewingDataStore(dataStore)}
                >
                  <CardContent>
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <Database className="w-5 h-5 text-primary-400 flex-shrink-0" />
                        <h3 className="text-lg font-semibold text-white truncate">
                          {dataStore.name}
                        </h3>
                      </div>
                      <div className="flex gap-2 flex-shrink-0">
                        <button
                          type="button"
                          aria-label={`Edit ${dataStore.name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingDataStore(dataStore);
                          }}
                          className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                        >
                          <Edit className="w-4 h-4" />
                        </button>
                        <button
                          type="button"
                          aria-label={`Delete ${dataStore.name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(dataStore.id, dataStore.name);
                          }}
                          className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {dataStore.description && (
                      <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                        {dataStore.description}
                      </p>
                    )}

                    <div className="flex flex-wrap gap-2 mb-3">
                      <span
                        className={`text-xs px-2 py-1 rounded ${getClassificationColor(dataStore.data_classification)}`}
                      >
                        {DATA_CLASSIFICATIONS.find(
                          (c) => c.value === dataStore.data_classification,
                        )?.label || "Unknown"}
                      </span>
                      <span className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-300">
                        {STORAGE_TYPES.find(
                          (t) => t.value === dataStore.storage_type,
                        )?.label || dataStore.storage_type}
                      </span>
                    </div>

                    {dataStore.labels && dataStore.labels.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mb-3">
                        {dataStore.labels.map((lbl) => (
                          <span
                            key={lbl.id}
                            className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border"
                            style={{
                              backgroundColor: `${lbl.color}20`,
                              color: lbl.color,
                              borderColor: `${lbl.color}40`,
                            }}
                          >
                            <Tag className="w-3 h-3" />
                            {lbl.name}
                          </span>
                        ))}
                      </div>
                    )}

                    {dataStore.location_region && (
                      <div className="text-xs text-slate-500 mb-1">
                        Region:{" "}
                        {REGIONS.find(
                          (r) => r.value === dataStore.location_region,
                        )?.label || dataStore.location_region}
                      </div>
                    )}

                    {dataStore.compliance_framework &&
                      dataStore.compliance_framework !== "none" && (
                        <div className="text-xs text-slate-500">
                          Compliance:{" "}
                          {COMPLIANCE_FRAMEWORKS.find(
                            (f) => f.value === dataStore.compliance_framework,
                          )?.label || dataStore.compliance_framework}
                        </div>
                      )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <DataStoreFormModal
          title="Add Data Store"
          submitLabel="Create"
          organizations={organizations?.items || []}
          identities={identities?.items || []}
          regionOptions={regionOptions}
          allLabels={allLabels}
          isLoading={createMutation.isPending}
          onClose={() => setShowCreateModal(false)}
          onSubmit={(payload) => createMutation.mutate(payload)}
        />
      )}

      {/* Edit Modal */}
      {!!editingDataStore && (
        <DataStoreFormModal
          title="Edit Data Store"
          submitLabel="Save"
          initialData={editingDataStore}
          organizations={organizations?.items || []}
          identities={identities?.items || []}
          regionOptions={regionOptions}
          allLabels={allLabels}
          isLoading={updateMutation.isPending}
          onClose={() => setEditingDataStore(null)}
          onSubmit={(payload) => updateMutation.mutate(payload)}
        />
      )}

      {/* View Details Modal */}
      {viewingDataStore && (
        <DataStoreDetailModal
          dataStore={viewingDataStore}
          identities={identities?.items || []}
          allLabels={allLabels}
          onClose={() => setViewingDataStore(null)}
          onEdit={() => {
            setEditingDataStore(viewingDataStore);
            setViewingDataStore(null);
          }}
        />
      )}
    </div>
  );
}

interface DataLabelsTabProps {
  labels: DataLabel[];
  onFilterByLabel: (labelId: number) => void;
}

function DataLabelsTab({ labels, onFilterByLabel }: DataLabelsTabProps) {
  const [name, setName] = useState("");
  const [color, setColor] = useState("#3b82f6");
  const [description, setDescription] = useState("");
  const [editingLabel, setEditingLabel] = useState<DataLabel | null>(null);
  const queryClient = useQueryClient();

  const createLabelMutation = useMutation({
    mutationFn: (payload: {
      name: string;
      color: string;
      description?: string;
    }) => api.createLabel(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["labels-all"] });
      toast.success("Data label created");
      setName("");
      setDescription("");
    },
    onError: () => toast.error("Failed to create data label"),
  });

  const updateLabelMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: { name?: string; color?: string; description?: string };
    }) => api.updateLabel(id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["labels-all"] });
      await invalidateCache.dataStores(queryClient);
      toast.success("Data label updated");
      setEditingLabel(null);
    },
    onError: () => toast.error("Failed to update data label"),
  });

  const deleteLabelMutation = useMutation({
    mutationFn: (id: number) => api.deleteLabel(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["labels-all"] });
      await invalidateCache.dataStores(queryClient);
      toast.success("Data label deleted");
    },
    onError: () => toast.error("Failed to delete data label"),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    createLabelMutation.mutate({
      name: name.trim(),
      color,
      description: description.trim() || undefined,
    });
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">
            Create Custom Data Label
          </h2>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={handleCreate}
            className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end"
          >
            <Input
              label="Label Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. HIPAA, GDPR, Secret"
            />
            <Input
              label="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Color
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-9 w-12 rounded bg-slate-900 border border-slate-700 cursor-pointer"
                />
                <div className="flex flex-wrap gap-1">
                  {LABEL_COLOR_PRESETS.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      onClick={() => setColor(preset)}
                      className={`w-5 h-5 rounded-full border ${
                        color === preset
                          ? "border-white scale-110"
                          : "border-transparent"
                      }`}
                      style={{ backgroundColor: preset }}
                      aria-label={`Select color ${preset}`}
                    />
                  ))}
                </div>
              </div>
            </div>
            <Button type="submit" disabled={createLabelMutation.isPending}>
              <Plus className="w-4 h-4 mr-2" />
              Add Label
            </Button>
          </form>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {labels.map((lbl) => (
          <Card key={lbl.id}>
            <CardContent className="space-y-3">
              {editingLabel?.id === lbl.id ? (
                <div className="space-y-3">
                  <Input
                    label="Name"
                    value={editingLabel.name}
                    onChange={(e) =>
                      setEditingLabel({ ...editingLabel, name: e.target.value })
                    }
                  />
                  <Input
                    label="Description"
                    value={editingLabel.description || ""}
                    onChange={(e) =>
                      setEditingLabel({
                        ...editingLabel,
                        description: e.target.value,
                      })
                    }
                  />
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={editingLabel.color}
                      onChange={(e) =>
                        setEditingLabel({
                          ...editingLabel,
                          color: e.target.value,
                        })
                      }
                      className="h-8 w-10 rounded bg-slate-900 border border-slate-700"
                    />
                    <Button
                      size="sm"
                      onClick={() =>
                        updateLabelMutation.mutate({
                          id: lbl.id,
                          payload: {
                            name: editingLabel.name.trim(),
                            color: editingLabel.color,
                            description:
                              editingLabel.description?.trim() || undefined,
                          },
                        })
                      }
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditingLabel(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className="inline-flex items-center gap-1.5 text-sm font-medium px-2.5 py-1 rounded border"
                      style={{
                        backgroundColor: `${lbl.color}20`,
                        color: lbl.color,
                        borderColor: `${lbl.color}40`,
                      }}
                    >
                      <Tag className="w-3.5 h-3.5" />
                      {lbl.name}
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setEditingLabel(lbl)}
                        className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                        aria-label={`Edit label ${lbl.name}`}
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (confirmDelete(lbl.name, "data label")) {
                            deleteLabelMutation.mutate(lbl.id);
                          }
                        }}
                        className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                        aria-label={`Delete label ${lbl.name}`}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  {lbl.description && (
                    <p className="text-sm text-slate-400">{lbl.description}</p>
                  )}
                  <div className="pt-2 border-t border-slate-700/50">
                    <button
                      type="button"
                      onClick={() => onFilterByLabel(lbl.id)}
                      className="text-xs text-primary-400 hover:text-primary-300 font-medium"
                    >
                      Filter Inventory by {lbl.name} →
                    </button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

interface DataStoreFormModalProps {
  title: string;
  submitLabel: string;
  initialData?: DataStore;
  organizations: Organization[];
  identities: Identity[];
  regionOptions: Array<{ value: string; label: string }>;
  allLabels: DataLabel[];
  isLoading?: boolean;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}

function DataStoreFormModal({
  title,
  submitLabel,
  initialData,
  organizations,
  identities,
  regionOptions,
  allLabels,
  isLoading = false,
  onClose,
  onSubmit,
}: DataStoreFormModalProps) {
  const [name, setName] = useState(initialData?.name || "");
  const [description, setDescription] = useState(
    initialData?.description || "",
  );
  const [organizationId, setOrganizationId] = useState(
    initialData?.organization_id
      ? String(initialData.organization_id)
      : organizations[0]?.id
        ? String(organizations[0].id)
        : "",
  );
  const [dataClassification, setDataClassification] = useState(
    initialData?.data_classification || "internal",
  );
  const [storageType, setStorageType] = useState(
    initialData?.storage_type || "database",
  );
  const [locationRegion, setLocationRegion] = useState(
    initialData?.location_region || "us-east-1",
  );
  const [customRegion, setCustomRegion] = useState("");
  const [complianceFramework, setComplianceFramework] = useState(
    initialData?.compliance_framework || "none",
  );
  const [pocIdentityId, setPocIdentityId] = useState(
    initialData?.poc_identity_id ? String(initialData.poc_identity_id) : "",
  );
  const [selectedLabelIds, setSelectedLabelIds] = useState<number[]>(
    () => initialData?.labels?.map((l) => l.id) || [],
  );
  const [metadataText, setMetadataText] = useState(() =>
    initialData?.metadata
      ? JSON.stringify(initialData.metadata, null, 2)
      : "",
  );

  const toggleLabel = (labelId: number) => {
    setSelectedLabelIds((prev) =>
      prev.includes(labelId)
        ? prev.filter((id) => id !== labelId)
        : [...prev, labelId],
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !organizationId) return;

    let parsedMetadata: Record<string, unknown> | undefined;
    if (metadataText.trim()) {
      try {
        const parsed = JSON.parse(metadataText);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          toast.error("Metadata must be a valid JSON object");
          return;
        }
        parsedMetadata = parsed as Record<string, unknown>;
      } catch {
        toast.error("Invalid JSON in Metadata field");
        return;
      }
    }

    const resolvedRegion =
      locationRegion === "other" ? customRegion.trim() : locationRegion;

    onSubmit({
      name: name.trim(),
      description: description.trim() || undefined,
      organization_id: parseInt(organizationId, 10),
      data_classification: dataClassification,
      storage_type: storageType,
      location_region: resolvedRegion || undefined,
      compliance_framework: complianceFramework || undefined,
      poc_identity_id: pocIdentityId ? parseInt(pocIdentityId, 10) : undefined,
      label_ids: selectedLabelIds,
      metadata: parsedMetadata,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader className="flex flex-row items-center justify-between">
          <h2 className="text-xl font-semibold text-white">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-white rounded"
            aria-label="Close modal"
          >
            <X className="w-5 h-5" />
          </button>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Production Customer Database"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Description
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                placeholder="Brief description of the data store"
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Select
                label="Organization"
                required
                value={organizationId}
                onChange={(e) => setOrganizationId(e.target.value)}
              >
                <option value="">Select organization</option>
                {organizations.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </Select>

              <Select
                label="Data Classification"
                required
                value={dataClassification}
                onChange={(e) => setDataClassification(e.target.value)}
              >
                {DATA_CLASSIFICATIONS.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </Select>

              <Select
                label="Storage Type"
                required
                value={storageType}
                onChange={(e) => setStorageType(e.target.value)}
              >
                {STORAGE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>

              <Select
                label="Location/Region"
                value={locationRegion}
                onChange={(e) => setLocationRegion(e.target.value)}
              >
                {regionOptions.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
                <option value="other">Other</option>
              </Select>
            </div>

            {locationRegion === "other" && (
              <Input
                label="Custom Region/Location"
                value={customRegion}
                onChange={(e) => setCustomRegion(e.target.value)}
                placeholder="Enter custom location or region"
              />
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Select
                label="Compliance Framework"
                value={complianceFramework}
                onChange={(e) => setComplianceFramework(e.target.value)}
              >
                {COMPLIANCE_FRAMEWORKS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </Select>

              <Select
                label="Point of Contact"
                value={pocIdentityId}
                onChange={(e) => setPocIdentityId(e.target.value)}
              >
                <option value="">Select contact (optional)</option>
                {identities.map((id) => (
                  <option key={id.id} value={id.id}>
                    {id.full_name || id.username}
                  </option>
                ))}
              </Select>
            </div>

            {/* Data Labels */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Data Labels
              </label>
              <div className="flex flex-wrap gap-2">
                {allLabels.map((lbl) => {
                  const selected = selectedLabelIds.includes(lbl.id);
                  return (
                    <button
                      key={lbl.id}
                      type="button"
                      onClick={() => toggleLabel(lbl.id)}
                      className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded border transition-all ${
                        selected ? "ring-1 ring-white/40 font-medium" : "opacity-60 hover:opacity-100"
                      }`}
                      style={{
                        backgroundColor: `${lbl.color}20`,
                        color: lbl.color,
                        borderColor: `${lbl.color}60`,
                      }}
                    >
                      {selected ? (
                        <Check className="w-3 h-3" />
                      ) : (
                        <Tag className="w-3 h-3" />
                      )}
                      {lbl.name}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Metadata JSON */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Metadata (JSON, Optional)
              </label>
              <textarea
                value={metadataText}
                onChange={(e) => setMetadataText(e.target.value)}
                rows={3}
                placeholder='{"owner_team": "platform", "retention_policy": "7-years"}'
                className="block w-full px-4 py-2 font-mono text-xs bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading}>
                {submitLabel}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

interface DataStoreDetailModalProps {
  dataStore: DataStore;
  identities: Identity[];
  allLabels: DataLabel[];
  onClose: () => void;
  onEdit: () => void;
}

function DataStoreDetailModal({
  dataStore,
  identities,
  allLabels,
  onClose,
  onEdit,
}: DataStoreDetailModalProps) {
  const queryClient = useQueryClient();
  const [attachedLabels, setAttachedLabels] = useState<DataLabel[]>(
    dataStore.labels || [],
  );

  const addLabelMutation = useMutation({
    mutationFn: (labelId: number) =>
      api.addDataStoreLabel(dataStore.id, labelId),
    onSuccess: async (_, labelId) => {
      const added = allLabels.find((l) => l.id === labelId);
      if (added) {
        setAttachedLabels((prev) =>
          prev.some((l) => l.id === labelId) ? prev : [...prev, added],
        );
      }
      await invalidateCache.dataStores(queryClient);
    },
    onError: () => toast.error("Failed to attach label"),
  });

  const removeLabelMutation = useMutation({
    mutationFn: (labelId: number) =>
      api.removeDataStoreLabel(dataStore.id, labelId),
    onSuccess: async (_, labelId) => {
      setAttachedLabels((prev) => prev.filter((l) => l.id !== labelId));
      await invalidateCache.dataStores(queryClient);
    },
    onError: () => toast.error("Failed to remove label"),
  });

  const getPOCName = (pocId: number): string => {
    const identity = identities.find((i: Identity) => i.id === pocId);
    return identity?.full_name || identity?.username || "Unknown";
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database className="w-5 h-5 text-primary-400" />
              <h2 className="text-xl font-semibold text-white">
                {dataStore.name}
              </h2>
            </div>
            <button
              onClick={onEdit}
              className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
            >
              <Edit className="w-4 h-4" />
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {dataStore.description && (
              <p className="text-sm text-slate-400">{dataStore.description}</p>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-slate-500 uppercase">
                  Classification
                </label>
                <div className="mt-1">
                  <span
                    className={`text-xs px-2 py-1 rounded ${getClassificationColor(dataStore.data_classification)}`}
                  >
                    {
                      DATA_CLASSIFICATIONS.find(
                        (c) => c.value === dataStore.data_classification,
                      )?.label
                    }
                  </span>
                </div>
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase">
                  Storage Type
                </label>
                <p className="text-sm text-white mt-1">
                  {
                    STORAGE_TYPES.find(
                      (t) => t.value === dataStore.storage_type,
                    )?.label
                  }
                </p>
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase">
                  Region
                </label>
                <p className="text-sm text-white mt-1">
                  {REGIONS.find((r) => r.value === dataStore.location_region)
                    ?.label ||
                    dataStore.location_region ||
                    "-"}
                </p>
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase">
                  Compliance
                </label>
                <p className="text-sm text-white mt-1">
                  {COMPLIANCE_FRAMEWORKS.find(
                    (f) => f.value === dataStore.compliance_framework,
                  )?.label ||
                    dataStore.compliance_framework ||
                    "-"}
                </p>
              </div>
            </div>

            <div className="bg-slate-800/50 rounded-lg p-4 space-y-2">
              <label className="text-xs text-slate-500 uppercase block">
                Data Labels
              </label>
              <div className="flex flex-wrap gap-2">
                {allLabels.map((lbl) => {
                  const isAttached = attachedLabels.some(
                    (al) => al.id === lbl.id,
                  );
                  return (
                    <button
                      key={lbl.id}
                      type="button"
                      onClick={() =>
                        isAttached
                          ? removeLabelMutation.mutate(lbl.id)
                          : addLabelMutation.mutate(lbl.id)
                      }
                      className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border transition-all ${
                        isAttached ? "font-medium" : "opacity-50 hover:opacity-100"
                      }`}
                      style={{
                        backgroundColor: `${lbl.color}20`,
                        color: lbl.color,
                        borderColor: `${lbl.color}50`,
                      }}
                    >
                      {isAttached ? (
                        <Check className="w-3 h-3" />
                      ) : (
                        <Plus className="w-3 h-3" />
                      )}
                      {lbl.name}
                    </button>
                  );
                })}
              </div>
            </div>

            {dataStore.metadata && Object.keys(dataStore.metadata).length > 0 && (
              <div>
                <label className="text-xs text-slate-500 uppercase block mb-1">
                  Metadata
                </label>
                <pre className="text-xs font-mono text-slate-300 bg-slate-900 border border-slate-700 rounded-lg p-3 overflow-x-auto">
                  {JSON.stringify(dataStore.metadata, null, 2)}
                </pre>
              </div>
            )}

            {dataStore.poc_identity_id && (
              <div>
                <label className="text-xs text-slate-500 uppercase">
                  Point of Contact
                </label>
                <p className="text-sm text-white mt-1">
                  {getPOCName(dataStore.poc_identity_id)}
                </p>
              </div>
            )}

            <div className="flex justify-end pt-4">
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function getClassificationColor(classification: string): string {
  const classif = DATA_CLASSIFICATIONS.find((c) => c.value === classification);
  return classif?.color || "bg-slate-500/20 text-slate-400";
}
