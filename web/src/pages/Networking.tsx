import { useState, useMemo, type FormEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Network,
  Trash2,
  Link2,
  Activity,
  Cloud,
  LayoutGrid,
  Shield,
  Globe,
  Router,
  ToggleLeft,
  Circle,
  Waypoints,
  Table,
  Layers,
  Box,
  Server,
  HelpCircle,
  X,
  LucideIcon,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import Select from "@/components/Select";
import SearchableSelect from "@/components/SearchableSelect";
import NetworkTopologyGraph from "@/components/NetworkTopologyGraph";
import {
  FormModalBuilder,
  FormField,
} from "@penguintechinc/react-libs/components";

interface Organization {
  id: number;
  name: string;
}

interface NetworkResource {
  id: number;
  name: string;
  network_type: string;
  cidr?: string;
  description?: string;
  region?: string;
  location?: string;
  is_active: boolean;
  organization_id: number;
}

interface TopologyConnection {
  id: number;
  source_network_id: number;
  target_network_id: number;
  connection_type: string;
  bandwidth?: string;
  latency?: number;
  description?: string;
}

// Icon mapping for network types
const NETWORK_TYPE_ICONS: Record<string, LucideIcon> = {
  vpc: Cloud,
  subnet: LayoutGrid,
  firewall: Shield,
  proxy: Globe,
  router: Router,
  switch: ToggleLeft,
  hub: Circle,
  tunnel: Waypoints,
  route_table: Table,
  vrrf: Layers,
  vxlan: Box,
  vlan: Server,
  namespace: Box,
  other: HelpCircle,
};

const NETWORK_TYPES = [
  { value: "vpc", label: "VPC" },
  { value: "subnet", label: "Subnet" },
  { value: "firewall", label: "Firewall" },
  { value: "proxy", label: "Proxy" },
  { value: "router", label: "Router" },
  { value: "switch", label: "Switch" },
  { value: "hub", label: "Hub" },
  { value: "tunnel", label: "Tunnel" },
  { value: "route_table", label: "Route Table" },
  { value: "vrrf", label: "VRRF" },
  { value: "vxlan", label: "VXLAN" },
  { value: "vlan", label: "VLAN" },
  { value: "namespace", label: "Namespace" },
  { value: "other", label: "Other" },
];

const CONNECTION_TYPES = [
  { value: "peering", label: "Peering" },
  { value: "vpn", label: "VPN" },
  { value: "direct_connect", label: "Direct Connect" },
  { value: "transit_gateway", label: "Transit Gateway" },
  { value: "route", label: "Route" },
  { value: "nat", label: "NAT" },
  { value: "load_balancer", label: "Load Balancer" },
  { value: "other", label: "Other" },
];

const TABS = ["Networks", "Topology", "Connections"] as const;
type Tab = (typeof TABS)[number];

export default function Networking() {
  const [activeTab, setActiveTab] = useState<Tab>("Networks");
  const [selectedNetworkType, setSelectedNetworkType] = useState<string>("all");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showCreateConnectionModal, setShowCreateConnectionModal] =
    useState(false);
  const [showTopologyModal, setShowTopologyModal] = useState(false);
  const [selectedOrg, setSelectedOrg] = useState<number | null>(null);
  const [createOrgId, setCreateOrgId] = useState<string>("");
  const [createName, setCreateName] = useState("");
  const [createNetworkType, setCreateNetworkType] = useState("vpc");
  const [createCidr, setCreateCidr] = useState("");
  const [addCidrToIpam, setAddCidrToIpam] = useState(true);
  const [createDescription, setCreateDescription] = useState("");
  const [createRegion, setCreateRegion] = useState("");
  const [createLocation, setCreateLocation] = useState("");
  const queryClient = useQueryClient();

  const resetCreateForm = () => {
    setCreateOrgId(selectedOrg ? String(selectedOrg) : "");
    setCreateName("");
    setCreateNetworkType("vpc");
    setCreateCidr("");
    setAddCidrToIpam(true);
    setCreateDescription("");
    setCreateRegion("");
    setCreateLocation("");
  };

  const openCreateModal = () => {
    setCreateOrgId(selectedOrg ? String(selectedOrg) : "");
    setShowCreateModal(true);
  };

  const { data: networks, isLoading: networksLoading } = useQuery({
    queryKey: ["networks", selectedOrg],
    queryFn: () =>
      api.listNetworks({ organization_id: selectedOrg || undefined }),
    enabled: !!selectedOrg,
  });

  const { data: connections } = useQuery({
    queryKey: ["networkConnections"],
    queryFn: () => api.listTopologyConnections(),
    enabled: activeTab === "Connections",
  });

  const { data: orgs } = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api.getOrganizations(),
  });

  const { data: allNetworks } = useQuery({
    queryKey: ["allNetworks"],
    queryFn: () => api.listNetworks({}),
    enabled: showCreateConnectionModal,
  });

  const { data: ipamPrefixesData, isLoading: ipamPrefixesLoading } = useQuery({
    queryKey: ["ipam-prefixes", createOrgId],
    queryFn: () =>
      api.getIpamPrefixes(
        createOrgId ? { organization_id: parseInt(createOrgId, 10) } : {},
      ),
    enabled: showCreateModal,
  });

  const ipamPrefixList = useMemo(
    () =>
      (ipamPrefixesData?.prefixes || ipamPrefixesData?.items || []) as Array<{
        id: number;
        prefix: string;
        description?: string;
        status?: string;
      }>,
    [ipamPrefixesData],
  );

  const ipamPrefixOptions = useMemo(
    () =>
      ipamPrefixList.map((p) => ({
        value: p.prefix,
        label: p.description ? `${p.prefix} — ${p.description}` : p.prefix,
      })),
    [ipamPrefixList],
  );

  const cidrExistsInIpam = useMemo(() => {
    const normalized = createCidr.trim();
    if (!normalized) return false;
    return ipamPrefixList.some((p) => p.prefix === normalized);
  }, [createCidr, ipamPrefixList]);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteNetwork(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["networks"],
        refetchType: "all",
      });
      toast.success("Network deleted");
    },
  });

  const createNetworkMutation = useMutation({
    mutationFn: async (vars: {
      networkData: Parameters<typeof api.createNetwork>[0] & { cidr?: string };
      syncIpam: boolean;
    }) => {
      const created = await api.createNetwork(vars.networkData);
      if (vars.syncIpam && vars.networkData.cidr) {
        try {
          await api.createIpamPrefix({
            prefix: vars.networkData.cidr,
            organization_id: vars.networkData.organization_id,
            description: vars.networkData.description || vars.networkData.name,
            status: "active",
          });
          await queryClient.invalidateQueries({
            queryKey: ["ipam-prefixes"],
            refetchType: "all",
          });
        } catch (err) {
          console.warn("Failed to add CIDR to IPAM:", err);
          toast.error("Network created, but adding CIDR to IPAM failed");
        }
      }
      return created;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["networks"],
        refetchType: "all",
      });
      toast.success("Network created");
      setShowCreateModal(false);
      resetCreateForm();
    },
    onError: (error: { response?: { data?: { error?: string } } }) => {
      toast.error(error.response?.data?.error || "Failed to create network");
    },
  });

  const createConnectionMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.createTopologyConnection(
        data as Parameters<typeof api.createTopologyConnection>[0],
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["networkConnections"],
        refetchType: "all",
      });
      toast.success("Connection created");
      setShowCreateConnectionModal(false);
    },
    onError: (error: { response?: { data?: { error?: string } } }) => {
      toast.error(error.response?.data?.error || "Failed to create connection");
    },
  });

  // Form fields for creating connections
  const connectionFields: FormField[] = useMemo(
    () => [
      {
        name: "source_network_id",
        label: "Source Network",
        type: "select",
        required: true,
        options: (allNetworks?.networks || []).map((n: NetworkResource) => ({
          value: n.id,
          label: `${n.name} (${n.network_type})`,
        })),
      },
      {
        name: "target_network_id",
        label: "Target Network",
        type: "select",
        required: true,
        options: (allNetworks?.networks || []).map((n: NetworkResource) => ({
          value: n.id,
          label: `${n.name} (${n.network_type})`,
        })),
      },
      {
        name: "connection_type",
        label: "Connection Type",
        type: "select",
        required: true,
        options: CONNECTION_TYPES,
      },
      {
        name: "bandwidth",
        label: "Bandwidth",
        type: "text",
        placeholder: "1 Gbps",
      },
      {
        name: "latency",
        label: "Latency (ms)",
        type: "number",
        placeholder: "5",
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        placeholder: "VPC peering between prod and staging",
      },
    ],
    [allNetworks?.networks],
  );

  const supportsCidr = ["vpc", "subnet", "vlan", "vxlan", "namespace"].includes(
    createNetworkType,
  );

  const handleCreateNetwork = (e: FormEvent) => {
    e.preventDefault();
    if (!createOrgId) {
      toast.error("Please select an organization first");
      return;
    }
    if (!createName.trim()) {
      toast.error("Please enter a network name");
      return;
    }
    const trimmedCidr = supportsCidr ? createCidr.trim() : "";
    if (supportsCidr && !trimmedCidr) {
      toast.error("Please enter or select a CIDR block");
      return;
    }

    const networkData: Parameters<typeof api.createNetwork>[0] & {
      cidr?: string;
    } = {
      name: createName.trim(),
      network_type: createNetworkType,
      organization_id: parseInt(createOrgId, 10),
      cidr: trimmedCidr || undefined,
      description: createDescription.trim() || undefined,
      region: createRegion.trim() || undefined,
      location: createLocation.trim() || undefined,
    };
    createNetworkMutation.mutate({
      networkData,
      syncIpam: Boolean(trimmedCidr && addCidrToIpam && !cidrExistsInIpam),
    });
  };

  const handleCreateConnection = (data: Record<string, unknown>) => {
    const connData: Parameters<typeof api.createTopologyConnection>[0] = {
      source_network_id: parseInt(data.source_network_id as string),
      target_network_id: parseInt(data.target_network_id as string),
      connection_type: data.connection_type as string,
      bandwidth: data.bandwidth as string | undefined,
      latency: data.latency ? parseInt(data.latency as string) : undefined,
      metadata: data.metadata as Record<string, unknown> | undefined,
    };
    createConnectionMutation.mutate(connData);
  };

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Network Topology</h1>
          <p className="mt-2 text-slate-400">
            Manage networking resources and visualize topology
          </p>
        </div>
        <div className="flex gap-3">
          <Button variant="ghost" onClick={() => setShowTopologyModal(true)}>
            <Activity className="w-4 h-4 mr-2" />
            View Topology
          </Button>
          <Button onClick={openCreateModal}>
            <Plus className="w-4 h-4 mr-2" />
            Add Network
          </Button>
        </div>
      </div>

      {/* Organization Selector */}
      <div className="mb-6 max-w-md">
        <Select
          label="Organization"
          value={selectedOrg?.toString() || ""}
          onChange={(e) => setSelectedOrg(parseInt(e.target.value))}
          options={[
            { value: "", label: "Select organization" },
            ...(orgs?.items || []).map((o: Organization) => ({
              value: o.id,
              label: o.name,
            })),
          ]}
        />
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-6 border-b border-slate-700">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 font-medium transition-colors border-b-2 ${
              activeTab === tab
                ? "text-primary-400 border-primary-400"
                : "text-slate-400 border-transparent hover:text-slate-300"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "Networks" && (
        <div>
          {!selectedOrg ? (
            <Card>
              <CardContent className="text-center py-12">
                <Network className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">
                  Select an organization to view networks
                </p>
              </CardContent>
            </Card>
          ) : networksLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : networks?.networks?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Network className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No networks configured</p>
                <Button className="mt-4" onClick={openCreateModal}>
                  Add your first network
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Network Type Filter */}
              <div className="flex flex-wrap gap-2 mb-6">
                <button
                  onClick={() => setSelectedNetworkType("all")}
                  className={`px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                    selectedNetworkType === "all"
                      ? "bg-primary-600 text-white"
                      : "bg-slate-700 text-slate-300 hover:bg-slate-600"
                  }`}
                >
                  All ({networks?.networks?.length || 0})
                </button>
                {NETWORK_TYPES.map((type) => {
                  const count =
                    networks?.networks?.filter(
                      (n: NetworkResource) => n.network_type === type.value,
                    ).length || 0;
                  if (count === 0) return null;
                  return (
                    <button
                      key={type.value}
                      onClick={() => setSelectedNetworkType(type.value)}
                      className={`px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                        selectedNetworkType === type.value
                          ? "bg-primary-600 text-white"
                          : "bg-slate-700 text-slate-300 hover:bg-slate-600"
                      }`}
                    >
                      {type.label} ({count})
                    </button>
                  );
                })}
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
                {networks?.networks
                  ?.filter(
                    (network: NetworkResource) =>
                      selectedNetworkType === "all" ||
                      network.network_type === selectedNetworkType,
                  )
                  .map((network: NetworkResource) => (
                    <Card key={network.id}>
                      <CardHeader>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            {(() => {
                              const TypeIcon =
                                NETWORK_TYPE_ICONS[network.network_type] ||
                                Network;
                              return (
                                <TypeIcon className="w-5 h-5 text-primary-400" />
                              );
                            })()}
                            <div>
                              <h3 className="text-lg font-semibold text-white">
                                {network.name}
                              </h3>
                              <p className="text-sm text-slate-400">
                                {
                                  NETWORK_TYPES.find(
                                    (t) => t.value === network.network_type,
                                  )?.label
                                }
                              </p>
                            </div>
                          </div>
                          <span
                            className={`px-2 py-1 text-xs font-medium rounded ${
                              network.is_active
                                ? "bg-green-500/20 text-green-400"
                                : "bg-slate-500/20 text-slate-400"
                            }`}
                          >
                            {network.is_active ? "Active" : "Inactive"}
                          </span>
                        </div>
                      </CardHeader>
                      <CardContent>
                        {network.description && (
                          <p className="text-sm text-slate-400 mb-3">
                            {network.description}
                          </p>
                        )}
                        {network.region && (
                          <p className="text-sm text-slate-500">
                            Region: {network.region}
                          </p>
                        )}
                        {network.location && (
                          <p className="text-sm text-slate-500">
                            Location: {network.location}
                          </p>
                        )}
                        <div className="flex gap-2 mt-4">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => deleteMutation.mutate(network.id)}
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === "Topology" && (
        <div>
          {!selectedOrg ? (
            <Card>
              <CardContent className="text-center py-12">
                <Activity className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">
                  Select an organization to view network topology
                </p>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="p-0">
                <NetworkTopologyGraph organizationId={selectedOrg} />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {activeTab === "Connections" && (
        <div className="space-y-3">
          <div className="flex justify-end mb-4">
            <Button onClick={() => setShowCreateConnectionModal(true)}>
              <Plus className="w-4 h-4 mr-2" />
              Add Connection
            </Button>
          </div>
          {connections?.connections?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Link2 className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">
                  No network connections configured
                </p>
                <Button
                  className="mt-4"
                  onClick={() => setShowCreateConnectionModal(true)}
                >
                  Create your first connection
                </Button>
              </CardContent>
            </Card>
          ) : (
            connections?.connections?.map((conn: TopologyConnection) => (
              <Card key={conn.id}>
                <CardContent>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <Link2 className="w-5 h-5 text-primary-400" />
                      <div>
                        <p className="text-white font-medium">
                          {conn.connection_type}
                        </p>
                        <p className="text-sm text-slate-400">
                          Network {conn.source_network_id} → Network{" "}
                          {conn.target_network_id}
                        </p>
                      </div>
                    </div>
                    <div className="text-right text-sm">
                      {conn.bandwidth && (
                        <p className="text-slate-400">
                          Bandwidth: {conn.bandwidth}
                        </p>
                      )}
                      {conn.latency && (
                        <p className="text-slate-400">
                          Latency: {conn.latency}ms
                        </p>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </div>
      )}

      {showCreateModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Add Network Resource"
        >
          <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-slate-700 bg-slate-800 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-700 px-6 py-4">
              <h2 className="text-lg font-semibold text-white">
                Add Network Resource
              </h2>
              <button
                type="button"
                onClick={() => {
                  setShowCreateModal(false);
                  resetCreateForm();
                }}
                className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"
                aria-label="Close modal"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <form onSubmit={handleCreateNetwork} className="space-y-4 p-6">
              <div>
                <label
                  htmlFor="create-net-org"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Organization Unit <span className="text-red-400">*</span>
                </label>
                <Select
                  id="create-net-org"
                  value={createOrgId}
                  onChange={(e) => setCreateOrgId(e.target.value)}
                  options={[
                    { value: "", label: "Select organization unit..." },
                    ...(orgs?.items || []).map((org: Organization) => ({
                      value: String(org.id),
                      label: org.name,
                    })),
                  ]}
                />
              </div>

              <div>
                <label
                  htmlFor="create-net-name"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Name <span className="text-red-400">*</span>
                </label>
                <Input
                  id="create-net-name"
                  type="text"
                  required
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="Production VPC"
                />
              </div>

              <div>
                <label
                  htmlFor="create-net-type"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Network Type <span className="text-red-400">*</span>
                </label>
                <Select
                  id="create-net-type"
                  value={createNetworkType}
                  onChange={(e) => setCreateNetworkType(e.target.value)}
                  options={NETWORK_TYPES}
                />
              </div>

              {supportsCidr && (
                <div className="space-y-3 rounded-lg border border-slate-700 bg-slate-900/50 p-3">
                  <div>
                    <span className="mb-1.5 block text-sm font-medium text-slate-300">
                      Select CIDR from IPAM (Optional)
                    </span>
                    <SearchableSelect
                      options={ipamPrefixOptions}
                      value={cidrExistsInIpam ? createCidr.trim() : ""}
                      onChange={(val) => setCreateCidr(String(val))}
                      placeholder="Search existing IPAM prefixes..."
                      isLoading={ipamPrefixesLoading}
                      ariaLabel="Select CIDR from IPAM"
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="create-net-cidr"
                      className="mb-1.5 block text-sm font-medium text-slate-300"
                    >
                      CIDR Block <span className="text-red-400">*</span>
                    </label>
                    <Input
                      id="create-net-cidr"
                      type="text"
                      required
                      value={createCidr}
                      onChange={(e) => setCreateCidr(e.target.value)}
                      placeholder="10.0.0.0/16"
                    />
                    <p className="mt-1 text-xs text-slate-400">
                      IPv4 CIDR notation (e.g., 10.0.1.0/24, 172.16.0.0/12)
                    </p>
                  </div>

                  {!cidrExistsInIpam && (
                    <label className="flex items-center gap-2 text-sm text-slate-300">
                      <input
                        type="checkbox"
                        checked={addCidrToIpam}
                        onChange={(e) => setAddCidrToIpam(e.target.checked)}
                        className="h-4 w-4 rounded border-slate-600 bg-slate-700 text-primary-600 focus:ring-primary-500"
                      />
                      Also add this CIDR to IPAM
                    </label>
                  )}
                </div>
              )}

              <div>
                <label
                  htmlFor="create-net-desc"
                  className="mb-1.5 block text-sm font-medium text-slate-300"
                >
                  Description
                </label>
                <textarea
                  id="create-net-desc"
                  rows={2}
                  value={createDescription}
                  onChange={(e) => setCreateDescription(e.target.value)}
                  placeholder="Main production network"
                  className="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label
                    htmlFor="create-net-region"
                    className="mb-1.5 block text-sm font-medium text-slate-300"
                  >
                    Region
                  </label>
                  <Input
                    id="create-net-region"
                    type="text"
                    value={createRegion}
                    onChange={(e) => setCreateRegion(e.target.value)}
                    placeholder="us-east-1"
                  />
                </div>
                <div>
                  <label
                    htmlFor="create-net-location"
                    className="mb-1.5 block text-sm font-medium text-slate-300"
                  >
                    Location
                  </label>
                  <Input
                    id="create-net-location"
                    type="text"
                    value={createLocation}
                    onChange={(e) => setCreateLocation(e.target.value)}
                    placeholder="AWS Virginia"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setShowCreateModal(false);
                    resetCreateForm();
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createNetworkMutation.isPending}
                >
                  {createNetworkMutation.isPending ? "Creating..." : "Create"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showTopologyModal && selectedOrg && (
        <TopologyModal
          organizationId={selectedOrg}
          onClose={() => setShowTopologyModal(false)}
        />
      )}

      {showCreateConnectionModal && (
        <FormModalBuilder
          isOpen={showCreateConnectionModal}
          onClose={() => setShowCreateConnectionModal(false)}
          title="Create Network Connection"
          fields={connectionFields}
          onSubmit={handleCreateConnection}
          submitButtonText="Create"
        />
      )}
    </div>
  );
}

interface TopologyModalProps {
  organizationId: number;
  onClose: () => void;
}

function TopologyModal({ organizationId, onClose }: TopologyModalProps) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-6xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">
              Network Topology Visualization
            </h2>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </CardHeader>
        <CardContent className="flex-1 p-0">
          <NetworkTopologyGraph organizationId={organizationId} />
        </CardContent>
      </Card>
    </div>
  );
}
