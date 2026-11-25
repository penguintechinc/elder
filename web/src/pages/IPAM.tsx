import { useState, useMemo, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  Trash2,
  Edit,
  ChevronRight,
  ChevronDown,
  Network,
  Globe,
  Wifi,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import ModalFormBuilder from "@/components/ModalFormBuilder";
import VillageIdBadge from "@/components/VillageIdBadge";
import type { FormField } from "@/types/form";

// Types
interface Organization {
  id: number;
  name: string;
}

interface IpamPrefix {
  id: number;
  prefix: string;
  description?: string;
  status: string;
  is_pool: boolean;
  parent_id?: number;
  organization_id: number;
  vrf_id?: number;
  vlan_id?: number;
  role?: string;
  children_count?: number;
  utilization?: number;
  created_at: string;
  updated_at: string;
  children?: IpamPrefix[];
  village_id?: string | null;
}

interface IpamAddress {
  id: number;
  address: string;
  description?: string;
  status: string;
  dns_name?: string;
  prefix_id?: number;
  organization_id: number;
  created_at: string;
  updated_at: string;
  village_id?: string | null;
}

interface IpamVlan {
  id: number;
  vid: number;
  name: string;
  description?: string;
  status: string;
  group_id?: number;
  organization_id: number;
  created_at: string;
  updated_at: string;
  village_id?: string | null;
}

type TabType = "prefixes" | "addresses" | "vlans";

interface PendingOverwriteState {
  kind: "prefix" | "address" | "vlan";
  payload: Record<string, unknown>;
  message: string;
}

function getConflictMessage(error: unknown, fallback: string): string | null {
  if (!error || typeof error !== "object") return null;
  const err = error as {
    response?: {
      status?: number;
      data?: { error?: string; message?: string; code?: string };
    };
  };
  if (err.response?.status !== 409) return null;
  return err.response?.data?.error || err.response?.data?.message || fallback;
}

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/20 text-green-400",
  reserved: "bg-yellow-500/20 text-yellow-400",
  deprecated: "bg-red-500/20 text-red-400",
  container: "bg-blue-500/20 text-blue-400",
  available: "bg-slate-500/20 text-slate-400",
};

// Status options for forms
const prefixStatusOptions = [
  { value: "active", label: "Active" },
  { value: "reserved", label: "Reserved" },
  { value: "deprecated", label: "Deprecated" },
  { value: "container", label: "Container" },
];

const addressStatusOptions = [
  { value: "active", label: "Active" },
  { value: "reserved", label: "Reserved" },
  { value: "deprecated", label: "Deprecated" },
];

const vlanStatusOptions = [
  { value: "active", label: "Active" },
  { value: "reserved", label: "Reserved" },
  { value: "deprecated", label: "Deprecated" },
];

function parseIpamTab(raw: string | null): TabType {
  if (raw === "addresses" || raw === "vlans" || raw === "prefixes") {
    return raw;
  }
  return "prefixes";
}

export interface IPAMProps {
  embedded?: boolean;
}

export default function IPAM({ embedded = false }: IPAMProps = {}) {
  const [searchParams] = useSearchParams();
  const subTabParam = searchParams.get("subtab") || searchParams.get("tab");
  const highlightId = searchParams.get("highlight");
  const [activeTab, setActiveTab] = useState<TabType>(() =>
    parseIpamTab(subTabParam),
  );
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (
      subTabParam === "addresses" ||
      subTabParam === "vlans" ||
      subTabParam === "prefixes"
    ) {
      setActiveTab(subTabParam);
    }
  }, [subTabParam]);
  const [organizationFilter, setOrganizationFilter] = useState<
    number | undefined
  >();
  const [prefixFilter, setPrefixFilter] = useState<number | undefined>();

  // Modals
  const [showCreatePrefixModal, setShowCreatePrefixModal] = useState(false);
  const [showCreateAddressModal, setShowCreateAddressModal] = useState(false);
  const [showCreateVlanModal, setShowCreateVlanModal] = useState(false);
  const [editingPrefix, setEditingPrefix] = useState<IpamPrefix | null>(null);
  const [editingAddress, setEditingAddress] = useState<IpamAddress | null>(
    null,
  );
  const [editingVlan, setEditingVlan] = useState<IpamVlan | null>(null);
  const [pendingOverwrite, setPendingOverwrite] =
    useState<PendingOverwriteState | null>(null);

  const queryClient = useQueryClient();

  // Fetch organizations for filters
  const { data: organizations } = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api.getOrganizations(),
  });

  // Prefixes query
  const { data: prefixesData, isLoading: prefixesLoading } = useQuery({
    queryKey: ["ipam-prefixes", organizationFilter],
    queryFn: () => api.getIpamPrefixes({ organization_id: organizationFilter }),
    enabled:
      activeTab === "prefixes" || showCreateAddressModal || !!editingAddress,
  });

  // Addresses query
  const { data: addressesData, isLoading: addressesLoading } = useQuery({
    queryKey: ["ipam-addresses", organizationFilter, prefixFilter],
    queryFn: () =>
      api.getIpamAddresses({
        organization_id: organizationFilter,
        prefix_id: prefixFilter,
      }),
    enabled: activeTab === "addresses",
  });

  // VLANs query
  const { data: vlansData, isLoading: vlansLoading } = useQuery({
    queryKey: ["ipam-vlans", organizationFilter],
    queryFn: () => api.getIpamVlans({ organization_id: organizationFilter }),
    enabled: activeTab === "vlans" || showCreatePrefixModal || !!editingPrefix,
  });

  // Networks & Services for cross-reference suggestions
  const { data: networksData } = useQuery({
    queryKey: ["networks-for-ipam"],
    queryFn: () => api.listNetworks({}),
    enabled: showCreatePrefixModal || !!editingPrefix,
  });

  const { data: servicesData } = useQuery({
    queryKey: ["services-for-ipam"],
    queryFn: () => api.getServices({ per_page: 1000 }),
    enabled: showCreateAddressModal || !!editingAddress,
  });

  // Create mutations
  const createPrefixMutation = useMutation({
    mutationFn: (data: unknown) =>
      api.createIpamPrefix(data as Parameters<typeof api.createIpamPrefix>[0]),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-prefixes"],
        refetchType: "all",
      });
      setShowCreatePrefixModal(false);
      toast.success("Prefix created successfully");
    },
    onError: (error, variables) => {
      const conflictMsg = getConflictMessage(
        error,
        "A matching prefix already exists.",
      );
      if (conflictMsg) {
        setPendingOverwrite({
          kind: "prefix",
          payload: (variables as Record<string, unknown>) ?? {},
          message: conflictMsg,
        });
        return;
      }
      toast.error("Failed to create prefix");
    },
  });

  const createAddressMutation = useMutation({
    mutationFn: (data: unknown) =>
      api.createIpamAddress(
        data as Parameters<typeof api.createIpamAddress>[0],
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-addresses"],
        refetchType: "all",
      });
      setShowCreateAddressModal(false);
      toast.success("Address created successfully");
    },
    onError: (error, variables) => {
      const conflictMsg = getConflictMessage(
        error,
        "A matching IP address already exists.",
      );
      if (conflictMsg) {
        setPendingOverwrite({
          kind: "address",
          payload: (variables as Record<string, unknown>) ?? {},
          message: conflictMsg,
        });
        return;
      }
      toast.error("Failed to create address");
    },
  });

  const createVlanMutation = useMutation({
    mutationFn: (data: unknown) =>
      api.createIpamVlan(data as Parameters<typeof api.createIpamVlan>[0]),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-vlans"],
        refetchType: "all",
      });
      setShowCreateVlanModal(false);
      toast.success("VLAN created successfully");
    },
    onError: (error, variables) => {
      const conflictMsg = getConflictMessage(
        error,
        "A matching VLAN already exists.",
      );
      if (conflictMsg) {
        setPendingOverwrite({
          kind: "vlan",
          payload: (variables as Record<string, unknown>) ?? {},
          message: conflictMsg,
        });
        return;
      }
      toast.error("Failed to create VLAN");
    },
  });

  // Update mutations
  const updatePrefixMutation = useMutation({
    mutationFn: (data: unknown) =>
      api.updateIpamPrefix(editingPrefix!.id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-prefixes"],
        refetchType: "all",
      });
      setEditingPrefix(null);
      toast.success("Prefix updated successfully");
    },
    onError: () => toast.error("Failed to update prefix"),
  });

  const updateAddressMutation = useMutation({
    mutationFn: (data: unknown) =>
      api.updateIpamAddress(editingAddress!.id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-addresses"],
        refetchType: "all",
      });
      setEditingAddress(null);
      toast.success("Address updated successfully");
    },
    onError: () => toast.error("Failed to update address"),
  });

  const updateVlanMutation = useMutation({
    mutationFn: (data: unknown) => api.updateIpamVlan(editingVlan!.id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-vlans"],
        refetchType: "all",
      });
      setEditingVlan(null);
      toast.success("VLAN updated successfully");
    },
    onError: () => toast.error("Failed to update VLAN"),
  });

  // Delete mutations
  const deletePrefixMutation = useMutation({
    mutationFn: (id: number) => api.deleteIpamPrefix(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-prefixes"],
        refetchType: "all",
      });
      toast.success("Prefix deleted successfully");
    },
    onError: () => toast.error("Failed to delete prefix"),
  });

  const deleteAddressMutation = useMutation({
    mutationFn: (id: number) => api.deleteIpamAddress(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-addresses"],
        refetchType: "all",
      });
      toast.success("Address deleted successfully");
    },
    onError: () => toast.error("Failed to delete address"),
  });

  const deleteVlanMutation = useMutation({
    mutationFn: (id: number) => api.deleteIpamVlan(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["ipam-vlans"],
        refetchType: "all",
      });
      toast.success("VLAN deleted successfully");
    },
    onError: () => toast.error("Failed to delete VLAN"),
  });

  const handleDelete = (type: string, id: number, name: string) => {
    if (window.confirm(`Are you sure you want to delete "${name}"?`)) {
      if (type === "prefix") deletePrefixMutation.mutate(id);
      else if (type === "address") deleteAddressMutation.mutate(id);
      else if (type === "vlan") deleteVlanMutation.mutate(id);
    }
  };

  // Organization options for forms
  const organizationOptions = useMemo(
    () =>
      organizations?.items?.map((org: Organization) => ({
        value: org.id,
        label: org.name,
      })) || [],
    [organizations?.items],
  );

  // Cross-reference options
  const networkCidrOptions = useMemo(() => {
    const networks = (networksData?.networks || []) as Array<{
      id: number;
      name: string;
      network_type: string;
      cidr?: string;
    }>;
    const seen = new Set<string>();
    const opts: Array<{ value: string; label: string }> = [];
    for (const net of networks) {
      if (!net.cidr || seen.has(net.cidr)) continue;
      seen.add(net.cidr);
      opts.push({
        value: net.cidr,
        label: `${net.cidr} — ${net.name} (${net.network_type})`,
      });
    }
    return opts;
  }, [networksData]);

  const prefixOptions = useMemo(() => {
    const prefixes = (prefixesData?.items ||
      prefixesData?.prefixes ||
      []) as IpamPrefix[];
    return prefixes.map((p) => ({
      value: p.id,
      label: p.description ? `${p.prefix} — ${p.description}` : p.prefix,
    }));
  }, [prefixesData]);

  const vlanOptions = useMemo(() => {
    const vlans = (vlansData?.items || vlansData?.vlans || []) as IpamVlan[];
    return vlans.map((v) => ({
      value: v.id,
      label: `VLAN ${v.vid} — ${v.name}`,
    }));
  }, [vlansData]);

  const dnsNameOptions = useMemo(() => {
    const seen = new Set<string>();
    const opts: Array<{ value: string; label: string }> = [];
    const services = (servicesData?.items || []) as Array<{
      id: number;
      name: string;
      domains?: string[];
    }>;
    for (const svc of services) {
      for (const domain of svc.domains || []) {
        if (!domain || seen.has(domain)) continue;
        seen.add(domain);
        opts.push({
          value: domain,
          label: `${domain} — ${svc.name} [Service]`,
        });
      }
    }
    const addresses = (addressesData?.items ||
      addressesData?.addresses ||
      []) as IpamAddress[];
    for (const addr of addresses) {
      if (!addr.dns_name || seen.has(addr.dns_name)) continue;
      seen.add(addr.dns_name);
      opts.push({ value: addr.dns_name, label: addr.dns_name });
    }
    return opts;
  }, [servicesData, addressesData]);

  const normalizeNumericIds = (
    data: Record<string, unknown>,
    keys: string[],
  ) => {
    const result: Record<string, unknown> = { ...data };
    for (const key of keys) {
      const val = result[key];
      if (val === "" || val === null || val === undefined) {
        delete result[key];
      } else {
        result[key] = Number(val);
      }
    }
    return result;
  };

  // Form field configurations using useMemo
  const createPrefixFields: FormField[] = useMemo(
    () => [
      {
        name: "prefix",
        label: "Prefix (CIDR)",
        type: "text",
        required: true,
        searchable: true,
        allowCustom: true,
        options: networkCidrOptions,
        placeholder: "e.g., 192.168.1.0/24",
      },
      {
        name: "organization_id",
        label: "Organization",
        type: "select",
        searchable: true,
        required: true,
        options: organizationOptions,
      },
      {
        name: "parent_id",
        label: "Parent Prefix (optional)",
        type: "select",
        searchable: true,
        options: prefixOptions,
        placeholder: "Search parent prefix...",
      },
      {
        name: "vlan_id",
        label: "VLAN (optional)",
        type: "select",
        searchable: true,
        options: vlanOptions,
        placeholder: "Search VLAN...",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        defaultValue: "active",
        options: prefixStatusOptions,
      },
      {
        name: "is_pool",
        label: "Is Pool (can allocate from this prefix)",
        type: "checkbox",
        defaultValue: false,
      },
    ],
    [organizationOptions, networkCidrOptions, prefixOptions, vlanOptions],
  );

  const editPrefixFields: FormField[] = useMemo(
    () => [
      {
        name: "prefix",
        label: "Prefix (CIDR)",
        type: "text",
        required: true,
        searchable: true,
        allowCustom: true,
        options: networkCidrOptions,
        placeholder: "e.g., 192.168.1.0/24",
        defaultValue: editingPrefix?.prefix || "",
      },
      {
        name: "parent_id",
        label: "Parent Prefix (optional)",
        type: "select",
        searchable: true,
        options: prefixOptions.filter((p) => p.value !== editingPrefix?.id),
        placeholder: "Search parent prefix...",
        defaultValue: editingPrefix?.parent_id ?? "",
      },
      {
        name: "vlan_id",
        label: "VLAN (optional)",
        type: "select",
        searchable: true,
        options: vlanOptions,
        placeholder: "Search VLAN...",
        defaultValue: editingPrefix?.vlan_id ?? "",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
        defaultValue: editingPrefix?.description || "",
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        options: prefixStatusOptions,
        defaultValue: editingPrefix?.status || "active",
      },
      {
        name: "is_pool",
        label: "Is Pool",
        type: "checkbox",
        defaultValue: editingPrefix?.is_pool || false,
      },
    ],
    [editingPrefix, networkCidrOptions, prefixOptions, vlanOptions],
  );

  const createAddressFields: FormField[] = useMemo(
    () => [
      {
        name: "address",
        label: "IP Address",
        type: "text",
        required: true,
        placeholder: "e.g., 192.168.1.1/32",
      },
      {
        name: "organization_id",
        label: "Organization",
        type: "select",
        searchable: true,
        required: true,
        options: organizationOptions,
      },
      {
        name: "prefix_id",
        label: "Prefix (optional)",
        type: "select",
        searchable: true,
        options: prefixOptions,
        placeholder: "Search prefix...",
      },
      {
        name: "dns_name",
        label: "DNS Name",
        type: "text",
        searchable: true,
        allowCustom: true,
        options: dnsNameOptions,
        placeholder: "e.g., server1.example.com",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        defaultValue: "active",
        options: addressStatusOptions,
      },
    ],
    [organizationOptions, prefixOptions, dnsNameOptions],
  );

  const editAddressFields: FormField[] = useMemo(
    () => [
      {
        name: "address",
        label: "IP Address",
        type: "text",
        required: true,
        placeholder: "e.g., 192.168.1.1/32",
        defaultValue: editingAddress?.address || "",
      },
      {
        name: "prefix_id",
        label: "Prefix (optional)",
        type: "select",
        searchable: true,
        options: prefixOptions,
        placeholder: "Search prefix...",
        defaultValue: editingAddress?.prefix_id ?? "",
      },
      {
        name: "dns_name",
        label: "DNS Name",
        type: "text",
        searchable: true,
        allowCustom: true,
        options: dnsNameOptions,
        placeholder: "e.g., server1.example.com",
        defaultValue: editingAddress?.dns_name || "",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
        defaultValue: editingAddress?.description || "",
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        options: addressStatusOptions,
        defaultValue: editingAddress?.status || "active",
      },
    ],
    [editingAddress, prefixOptions, dnsNameOptions],
  );

  const createVlanFields: FormField[] = useMemo(
    () => [
      {
        name: "vid",
        label: "VLAN ID",
        type: "number",
        required: true,
        placeholder: "e.g., 100",
      },
      {
        name: "name",
        label: "Name",
        type: "text",
        required: true,
        placeholder: "e.g., Production",
      },
      {
        name: "organization_id",
        label: "Organization",
        type: "select",
        searchable: true,
        required: true,
        options: organizationOptions,
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        defaultValue: "active",
        options: vlanStatusOptions,
      },
    ],
    [organizationOptions],
  );

  const editVlanFields: FormField[] = useMemo(
    () => [
      {
        name: "vid",
        label: "VLAN ID",
        type: "number",
        required: true,
        placeholder: "e.g., 100",
        defaultValue: editingVlan?.vid,
      },
      {
        name: "name",
        label: "Name",
        type: "text",
        required: true,
        placeholder: "e.g., Production",
        defaultValue: editingVlan?.name || "",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "Enter description (optional)",
        defaultValue: editingVlan?.description || "",
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        options: vlanStatusOptions,
        defaultValue: editingVlan?.status || "active",
      },
    ],
    [editingVlan],
  );

  const tabs = [
    { id: "prefixes" as TabType, label: "Prefixes", icon: Network },
    { id: "addresses" as TabType, label: "Addresses", icon: Globe },
    { id: "vlans" as TabType, label: "VLANs", icon: Wifi },
  ];

  const createButton = (
    <Button
      onClick={() => {
        if (activeTab === "prefixes") setShowCreatePrefixModal(true);
        else if (activeTab === "addresses") setShowCreateAddressModal(true);
        else if (activeTab === "vlans") setShowCreateVlanModal(true);
      }}
    >
      <Plus className="w-4 h-4 mr-2" />
      Create{" "}
      {activeTab === "prefixes"
        ? "Prefix"
        : activeTab === "addresses"
          ? "Address"
          : "VLAN"}
    </Button>
  );

  return (
    <div className={embedded ? undefined : "p-8"}>
      {/* Header (omitted when embedded inside Networking) */}
      {!embedded && (
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">
              IP Address Management
            </h1>
            <p className="mt-2 text-slate-400">
              Manage IP prefixes, addresses, and VLANs
            </p>
          </div>
          {createButton}
        </div>
      )}

      {/* Tabs */}
      <div
        className={
          embedded
            ? "flex flex-wrap items-center justify-between gap-4 mb-6"
            : "mb-6"
        }
      >
        <div className="flex gap-1 bg-slate-800 p-1 rounded-lg w-fit">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? "bg-primary-600 text-white"
                  : "text-slate-400 hover:text-white hover:bg-slate-700"
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>
        {embedded && createButton}
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-6">
        <div className="relative max-w-xs">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={organizationFilter || ""}
          onChange={(e) =>
            setOrganizationFilter(
              e.target.value ? Number(e.target.value) : undefined,
            )
          }
          className="max-w-xs"
        >
          <option value="">All Organizations</option>
          {organizations?.items?.map((org: Organization) => (
            <option key={org.id} value={org.id}>
              {org.name}
            </option>
          ))}
        </Select>
        {activeTab === "addresses" && (
          <Select
            value={prefixFilter || ""}
            onChange={(e) =>
              setPrefixFilter(
                e.target.value ? Number(e.target.value) : undefined,
              )
            }
            className="max-w-xs"
          >
            <option value="">All Prefixes</option>
            {prefixesData?.items?.map((prefix: IpamPrefix) => (
              <option key={prefix.id} value={prefix.id}>
                {prefix.prefix}
              </option>
            ))}
          </Select>
        )}
      </div>

      {/* Content */}
      {activeTab === "prefixes" && (
        <PrefixesTab
          data={prefixesData}
          isLoading={prefixesLoading}
          search={search}
          highlightId={highlightId}
          onEdit={setEditingPrefix}
          onDelete={(id, name) => handleDelete("prefix", id, name)}
          onCreate={() => setShowCreatePrefixModal(true)}
        />
      )}

      {activeTab === "addresses" && (
        <AddressesTab
          data={addressesData}
          isLoading={addressesLoading}
          search={search}
          highlightId={highlightId}
          onEdit={setEditingAddress}
          onDelete={(id, name) => handleDelete("address", id, name)}
          onCreate={() => setShowCreateAddressModal(true)}
        />
      )}

      {activeTab === "vlans" && (
        <VlansTab
          data={vlansData}
          isLoading={vlansLoading}
          search={search}
          highlightId={highlightId}
          onEdit={setEditingVlan}
          onDelete={(id, name) => handleDelete("vlan", id, name)}
          onCreate={() => setShowCreateVlanModal(true)}
        />
      )}

      {/* Modals */}
      {showCreatePrefixModal && (
        <ModalFormBuilder
          isOpen={showCreatePrefixModal}
          onClose={() => setShowCreatePrefixModal(false)}
          title="Create Prefix"
          fields={createPrefixFields}
          onSubmit={(data) =>
            createPrefixMutation.mutate(
              normalizeNumericIds(data, [
                "organization_id",
                "parent_id",
                "vlan_id",
              ]),
            )
          }
          submitLabel="Create"
        />
      )}

      {showCreateAddressModal && (
        <ModalFormBuilder
          isOpen={showCreateAddressModal}
          onClose={() => setShowCreateAddressModal(false)}
          title="Create Address"
          fields={createAddressFields}
          onSubmit={(data) =>
            createAddressMutation.mutate(
              normalizeNumericIds(data, ["organization_id", "prefix_id"]),
            )
          }
          submitLabel="Create"
        />
      )}

      {showCreateVlanModal && (
        <ModalFormBuilder
          isOpen={showCreateVlanModal}
          onClose={() => setShowCreateVlanModal(false)}
          title="Create VLAN"
          fields={createVlanFields}
          onSubmit={(data) =>
            createVlanMutation.mutate(
              normalizeNumericIds(data, ["organization_id"]),
            )
          }
          submitLabel="Create"
        />
      )}

      {editingPrefix && (
        <ModalFormBuilder
          isOpen={!!editingPrefix}
          onClose={() => setEditingPrefix(null)}
          title="Edit Prefix"
          fields={editPrefixFields}
          onSubmit={(data) =>
            updatePrefixMutation.mutate(
              normalizeNumericIds(data, ["parent_id", "vlan_id"]),
            )
          }
          submitLabel="Update"
        />
      )}

      {editingAddress && (
        <ModalFormBuilder
          isOpen={!!editingAddress}
          onClose={() => setEditingAddress(null)}
          title="Edit Address"
          fields={editAddressFields}
          onSubmit={(data) =>
            updateAddressMutation.mutate(
              normalizeNumericIds(data, ["prefix_id"]),
            )
          }
          submitLabel="Update"
        />
      )}

      {editingVlan && (
        <ModalFormBuilder
          isOpen={!!editingVlan}
          onClose={() => setEditingVlan(null)}
          title="Edit VLAN"
          fields={editVlanFields}
          onSubmit={(data) => updateVlanMutation.mutate(data as unknown)}
          submitLabel="Update"
        />
      )}

      {pendingOverwrite && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Duplicate IPAM Entry"
        >
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
            <h2 className="text-lg font-semibold text-white">
              Duplicate Entry Detected
            </h2>
            <p className="mt-2 text-sm text-slate-300">
              {pendingOverwrite.message}
            </p>
            <p className="mt-2 text-sm text-slate-400">
              Do you want to overwrite the existing entry?
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setPendingOverwrite(null)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => {
                  const payload = {
                    ...pendingOverwrite.payload,
                    overwrite: true,
                  };
                  if (pendingOverwrite.kind === "prefix") {
                    createPrefixMutation.mutate(payload);
                  } else if (pendingOverwrite.kind === "address") {
                    createAddressMutation.mutate(payload);
                  } else {
                    createVlanMutation.mutate(payload);
                  }
                  setPendingOverwrite(null);
                }}
              >
                Overwrite Existing
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Prefixes Tab with Tree View
interface PrefixesTabProps {
  data: { items?: IpamPrefix[] };
  isLoading: boolean;
  search: string;
  highlightId: string | null;
  onEdit: (prefix: IpamPrefix) => void;
  onDelete: (id: number, name: string) => void;
  onCreate: () => void;
}

function hasHighlightedDescendant(
  prefixId: number,
  childrenMap: Map<number, IpamPrefix[]>,
  highlightId: string | null,
): boolean {
  if (!highlightId) return false;
  const children = childrenMap.get(prefixId) || [];
  for (const child of children) {
    if (String(child.id) === highlightId || child.village_id === highlightId) {
      return true;
    }
    if (hasHighlightedDescendant(child.id, childrenMap, highlightId)) {
      return true;
    }
  }
  return false;
}

function PrefixesTab({
  data,
  isLoading,
  search,
  highlightId,
  onEdit,
  onDelete,
  onCreate,
}: PrefixesTabProps) {
  useEffect(() => {
    if (!highlightId || !data?.items?.length) return;
    const match = data.items.find(
      (p) => String(p.id) === highlightId || p.village_id === highlightId,
    );
    if (!match) return;
    const el = document.getElementById(`ipam-prefix-${match.id}`);
    el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlightId, data?.items]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Build tree structure from flat list
  const prefixes = data?.items || [];
  const filteredPrefixes = search
    ? prefixes.filter(
        (p: IpamPrefix) =>
          p.prefix.toLowerCase().includes(search.toLowerCase()) ||
          p.description?.toLowerCase().includes(search.toLowerCase()),
      )
    : prefixes;

  // Group by parent to build tree
  const rootPrefixes = filteredPrefixes.filter((p: IpamPrefix) => !p.parent_id);
  const childrenMap = new Map<number, IpamPrefix[]>();

  filteredPrefixes.forEach((p: IpamPrefix) => {
    if (p.parent_id) {
      const children = childrenMap.get(p.parent_id) || [];
      children.push(p);
      childrenMap.set(p.parent_id, children);
    }
  });

  if (filteredPrefixes.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No prefixes found</p>
          <Button className="mt-4" onClick={onCreate}>
            Create your first prefix
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="divide-y divide-slate-700">
          {rootPrefixes.map((prefix: IpamPrefix) => (
            <PrefixTreeNode
              key={prefix.id}
              prefix={prefix}
              childrenMap={childrenMap}
              depth={0}
              highlightId={highlightId}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// Recursive tree node component
interface PrefixTreeNodeProps {
  prefix: IpamPrefix;
  childrenMap: Map<number, IpamPrefix[]>;
  depth: number;
  highlightId: string | null;
  onEdit: (prefix: IpamPrefix) => void;
  onDelete: (id: number, name: string) => void;
}

function PrefixTreeNode({
  prefix,
  childrenMap,
  depth,
  highlightId,
  onEdit,
  onDelete,
}: PrefixTreeNodeProps) {
  const shouldAutoExpand = hasHighlightedDescendant(
    prefix.id,
    childrenMap,
    highlightId,
  );
  const [expanded, setExpanded] = useState(shouldAutoExpand);
  useEffect(() => {
    if (shouldAutoExpand) {
      setExpanded(true);
    }
  }, [shouldAutoExpand]);

  const children = childrenMap.get(prefix.id) || [];
  const hasChildren = children.length > 0;
  const isHighlighted = Boolean(
    highlightId &&
      (String(prefix.id) === highlightId || prefix.village_id === highlightId),
  );

  return (
    <div>
      <div
        id={`ipam-prefix-${prefix.id}`}
        data-highlighted={isHighlighted ? "true" : undefined}
        className={`flex items-center gap-3 px-4 py-3 hover:bg-slate-700/50 transition-colors ${
          isHighlighted
            ? "bg-primary-500/15 ring-2 ring-inset ring-primary-500"
            : ""
        }`}
        style={{ paddingLeft: `${16 + depth * 24}px` }}
      >
        {/* Expand/collapse button */}
        <button
          onClick={() => setExpanded(!expanded)}
          className={`p-1 rounded hover:bg-slate-600 ${!hasChildren ? "invisible" : ""}`}
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-slate-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-400" />
          )}
        </button>

        {/* Prefix info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <Network className="w-4 h-4 text-primary-400 flex-shrink-0" />
            <span className="font-mono font-medium text-white">
              {prefix.prefix}
            </span>
            <span
              className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[prefix.status] || STATUS_COLORS.available}`}
            >
              {prefix.status}
            </span>
            {prefix.village_id && (
              <VillageIdBadge villageId={prefix.village_id} />
            )}
            {hasChildren && (
              <span className="text-xs text-slate-500">
                {children.length} child{children.length !== 1 ? "ren" : ""}
              </span>
            )}
            {prefix.utilization !== undefined && (
              <span className="text-xs text-slate-500">
                {prefix.utilization}% utilized
              </span>
            )}
          </div>
          {prefix.description && (
            <p className="text-sm text-slate-400 mt-1 truncate">
              {prefix.description}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            onClick={() => onEdit(prefix)}
            className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
          >
            <Edit className="w-4 h-4" />
          </button>
          <button
            onClick={() => onDelete(prefix.id, prefix.prefix)}
            className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Children */}
      {expanded && hasChildren && (
        <div>
          {children.map((child) => (
            <PrefixTreeNode
              key={child.id}
              prefix={child}
              childrenMap={childrenMap}
              depth={depth + 1}
              highlightId={highlightId}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Addresses Tab
interface AddressesTabProps {
  data: { items?: IpamAddress[] };
  isLoading: boolean;
  search: string;
  highlightId: string | null;
  onEdit: (address: IpamAddress) => void;
  onDelete: (id: number, name: string) => void;
  onCreate: () => void;
}

function AddressesTab({
  data,
  isLoading,
  search,
  highlightId,
  onEdit,
  onDelete,
  onCreate,
}: AddressesTabProps) {
  useEffect(() => {
    if (!highlightId || !data?.items?.length) return;
    const match = data.items.find(
      (a) => String(a.id) === highlightId || a.village_id === highlightId,
    );
    if (!match) return;
    const el = document.getElementById(`ipam-address-${match.id}`);
    el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlightId, data?.items]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const addresses = data?.items || [];
  const filteredAddresses = search
    ? addresses.filter(
        (a: IpamAddress) =>
          a.address.toLowerCase().includes(search.toLowerCase()) ||
          a.description?.toLowerCase().includes(search.toLowerCase()) ||
          a.dns_name?.toLowerCase().includes(search.toLowerCase()),
      )
    : addresses;

  if (filteredAddresses.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No addresses found</p>
          <Button className="mt-4" onClick={onCreate}>
            Create your first address
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <table className="w-full">
          <thead>
            <tr className="border-b border-slate-700 text-left">
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Address
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                DNS Name
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Status
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Description
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {filteredAddresses.map((address: IpamAddress) => {
              const isHighlighted = Boolean(
                highlightId &&
                  (String(address.id) === highlightId ||
                    address.village_id === highlightId),
              );
              return (
                <tr
                  key={address.id}
                  id={`ipam-address-${address.id}`}
                  data-highlighted={isHighlighted ? "true" : undefined}
                  className={`hover:bg-slate-700/50 transition-colors ${
                    isHighlighted
                      ? "bg-primary-500/15 ring-2 ring-inset ring-primary-500"
                      : ""
                  }`}
                >
                  <td className="px-4 py-3">
                    <div>
                      <span className="font-mono text-white">
                        {address.address}
                      </span>
                      {address.village_id && (
                        <div className="mt-1">
                          <VillageIdBadge villageId={address.village_id} />
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-300">
                    {address.dns_name || "-"}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[address.status] || STATUS_COLORS.available}`}
                    >
                      {address.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-sm truncate max-w-xs">
                    {address.description || "-"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => onEdit(address)}
                        className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => onDelete(address.id, address.address)}
                        className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
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
      </CardContent>
    </Card>
  );
}

// VLANs Tab
interface VlansTabProps {
  data: { items?: IpamVlan[] };
  isLoading: boolean;
  search: string;
  highlightId: string | null;
  onEdit: (vlan: IpamVlan) => void;
  onDelete: (id: number, name: string) => void;
  onCreate: () => void;
}

function VlansTab({
  data,
  isLoading,
  search,
  highlightId,
  onEdit,
  onDelete,
  onCreate,
}: VlansTabProps) {
  useEffect(() => {
    if (!highlightId || !data?.items?.length) return;
    const match = data.items.find(
      (v) => String(v.id) === highlightId || v.village_id === highlightId,
    );
    if (!match) return;
    const el = document.getElementById(`ipam-vlan-${match.id}`);
    el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [highlightId, data?.items]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const vlans = data?.items || [];
  const filteredVlans = search
    ? vlans.filter(
        (v: IpamVlan) =>
          v.name.toLowerCase().includes(search.toLowerCase()) ||
          v.description?.toLowerCase().includes(search.toLowerCase()) ||
          v.vid.toString().includes(search),
      )
    : vlans;

  if (filteredVlans.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No VLANs found</p>
          <Button className="mt-4" onClick={onCreate}>
            Create your first VLAN
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <table className="w-full">
          <thead>
            <tr className="border-b border-slate-700 text-left">
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                VID
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Name
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Status
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Description
              </th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {filteredVlans.map((vlan: IpamVlan) => {
              const isHighlighted = Boolean(
                highlightId &&
                  (String(vlan.id) === highlightId ||
                    vlan.village_id === highlightId),
              );
              return (
                <tr
                  key={vlan.id}
                  id={`ipam-vlan-${vlan.id}`}
                  data-highlighted={isHighlighted ? "true" : undefined}
                  className={`hover:bg-slate-700/50 transition-colors ${
                    isHighlighted
                      ? "bg-primary-500/15 ring-2 ring-inset ring-primary-500"
                      : ""
                  }`}
                >
                  <td className="px-4 py-3">
                    <span className="font-mono text-white">{vlan.vid}</span>
                  </td>
                  <td className="px-4 py-3 text-white font-medium">
                    <div>
                      <span>{vlan.name}</span>
                      {vlan.village_id && (
                        <div className="mt-1">
                          <VillageIdBadge villageId={vlan.village_id} />
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[vlan.status] || STATUS_COLORS.available}`}
                    >
                      {vlan.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-sm truncate max-w-xs">
                    {vlan.description || "-"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => onEdit(vlan)}
                        className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => onDelete(vlan.id, vlan.name)}
                        className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
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
      </CardContent>
    </Card>
  );
}
