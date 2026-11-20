import { useState, useEffect, useMemo } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Server,
  Box,
  Boxes,
  Layers,
  Network,
  HardDrive,
  Shield,
  Ship,
  Container,
  Monitor,
  RefreshCw,
  Plus,
  Cpu,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import Input from "@/components/Input";
import SearchableSelect from "@/components/SearchableSelect";
import VillageIdBadge from "@/components/VillageIdBadge";
import { normalizeTags } from "@/lib/entityTags";
import type { Dependency, EntityMetadata, EntityTags } from "@/types";

// ── API response record types ─────────────────────────────────
// Lightweight shapes matching what the API returns for each resource.
// EntityMetadata/EntityTags are shared with the Entity type in src/types —
// metadata lives on entity.metadata (not entity.attributes.metadata).
interface ComputeEntity {
  id: number;
  name: string;
  sub_type: string;
  entity_type: string;
  organization_id?: number;
  parent_id?: number;
  created_at?: string;
  metadata?: EntityMetadata;
  tags?: EntityTags;
  village_id?: string | null;
}

interface K8sService {
  id: number;
  name: string;
  port?: string;
  status?: string;
  deployment_method?: string;
  village_id?: string | null;
}

interface NetworkResource {
  id: number;
  name: string;
  network_type?: string;
  region?: string;
  tags?: string[];
  village_id?: string | null;
}

interface DataStoreResource {
  id: number;
  name: string;
  storage_type?: string;
  storage_provider?: string;
  location_region?: string;
  tags?: string[];
  village_id?: string | null;
}

interface IdentityResource {
  id: number;
  username: string;
  full_name?: string;
  auth_provider?: string;
  identity_type?: string;
  is_active: boolean;
  village_id?: string | null;
}

// ── Primary tabs ──────────────────────────────────────────────
const PRIMARY_TABS = ["VMs", "Kubernetes", "LXD/LXC"] as const;
type PrimaryTab = (typeof PRIMARY_TABS)[number];

const PRIMARY_ICONS: Record<PrimaryTab, typeof Server> = {
  VMs: Monitor,
  Kubernetes: Ship,
  "LXD/LXC": Container,
};

// ── K8s sub-tabs ──────────────────────────────────────────────
const K8S_TABS = [
  "Clusters",
  "Nodes",
  "Pods",
  "Deployments",
  "Services",
  "Namespaces",
  "Storage",
  "Service Accounts",
] as const;
type K8sTab = (typeof K8S_TABS)[number];

const K8S_ICONS: Record<K8sTab, typeof Ship> = {
  Clusters: Ship,
  Nodes: Server,
  Pods: Box,
  Deployments: Boxes,
  Services: Network,
  Namespaces: Layers,
  Storage: HardDrive,
  "Service Accounts": Shield,
};

// ── LXD sub-tabs ──────────────────────────────────────────────
const LXD_TABS = [
  "Overview",
  "Containers",
  "VMs",
  "Storage Pools",
  "Networks",
] as const;
type LxdTab = (typeof LXD_TABS)[number];

const LXD_ICONS: Record<LxdTab, typeof Container> = {
  Overview: Container,
  Containers: Container,
  VMs: Monitor,
  "Storage Pools": HardDrive,
  Networks: Network,
};

// ── Shared components ─────────────────────────────────────────
function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Server;
  title: string;
  description: string;
}) {
  return (
    <Card>
      <CardContent>
        <div className="flex flex-col items-center justify-center py-12 text-slate-400">
          <Icon className="h-12 w-12 mb-4 opacity-50" />
          <p className="text-lg font-medium">{title}</p>
          <p className="text-sm mt-1">{description}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <RefreshCw className="h-6 w-6 text-amber-400 animate-spin" />
      <span className="ml-2 text-slate-400">Loading...</span>
    </div>
  );
}

// Fires the given callback on Enter/Space — for making non-button elements
// (Card tiles) keyboard-activatable like a real button.
function onActivateKey(callback: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      callback();
    }
  };
}

type LucideIcon = typeof Server;

function SubTabNav<T extends string>({
  tabs,
  icons,
  active,
  onChange,
}: {
  tabs: readonly T[];
  icons: Record<T, LucideIcon>;
  active: T;
  onChange: (tab: T) => void;
}) {
  return (
    <div className="border-b border-slate-700/50 mb-4">
      <nav className="flex gap-1 -mb-px">
        {tabs.map((tab) => {
          const Icon: LucideIcon = icons[tab];
          return (
            <button
              key={tab}
              onClick={() => onChange(tab)}
              className={`flex items-center gap-2 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                active === tab
                  ? "border-amber-400/70 text-amber-300"
                  : "border-transparent text-slate-500 hover:text-slate-300 hover:border-slate-600"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab}
            </button>
          );
        })}
      </nav>
    </div>
  );
}

// ── VM sub-type filters ───────────────────────────────────────
const VM_SUB_TYPES = [
  "ec2_instance",
  "gce_instance",
  "azure_vm",
  "lxd_vm",
  "vmware_vm",
  "proxmox_vm",
  "bare_metal",
  "vm",
] as const;

const COMPUTE_SUB_TYPE_OPTIONS = [
  { value: "ec2_instance", label: "VM — AWS EC2 Instance" },
  { value: "gce_instance", label: "VM — GCP Compute Engine" },
  { value: "azure_vm", label: "VM — Azure Virtual Machine" },
  { value: "vmware_vm", label: "VM — VMware Virtual Machine" },
  { value: "proxmox_vm", label: "VM — Proxmox Virtual Machine" },
  { value: "bare_metal", label: "Compute — Bare Metal Server" },
  { value: "vm", label: "VM — Generic Virtual Machine" },
  { value: "kubernetes_cluster", label: "Kubernetes — Cluster" },
  { value: "k8s_node", label: "Kubernetes — Node" },
  { value: "k8s_deployment", label: "Kubernetes — Deployment" },
  { value: "k8s_pod", label: "Kubernetes — Pod" },
  { value: "lxd_container", label: "LXD/LXC — Container" },
  { value: "lxd_vm", label: "LXD/LXC — Virtual Machine" },
];

const COMPUTE_PROVIDER_OPTIONS = [
  "AWS",
  "GCP",
  "Azure",
  "VMware",
  "Proxmox",
  "Kubernetes",
  "EKS",
  "GKE",
  "AKS",
  "k3s",
  "MicroK8s",
  "OpenShift",
  "DigitalOcean",
  "Bare Metal",
  "On-Prem",
].map((p) => ({ value: p, label: p }));

const COMPUTE_REGION_OPTIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "us-central1",
  "eastus",
  "eu-west-1",
  "eu-central-1",
  "ap-southeast-1",
  "on-prem",
  "global",
].map((r) => ({ value: r, label: r }));

// ── VMs Tab ───────────────────────────────────────────────────
function VMsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["compute-vms"],
    queryFn: () => api.getEntities({ entity_type: "compute" }),
  });

  const allEntities: ComputeEntity[] = data?.items || data?.data || [];
  const vms = allEntities.filter((e) =>
    (VM_SUB_TYPES as readonly string[]).includes(e.sub_type),
  );

  if (isLoading) return <LoadingSpinner />;

  if (vms.length === 0) {
    return (
      <EmptyState
        icon={Monitor}
        title="No virtual machines discovered"
        description="Run a discovery job to see VM instances from AWS, GCP, Azure, or LXD"
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700">
            <th className="text-left py-3 px-4 text-slate-400 font-medium">
              Name
            </th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">
              Type
            </th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">
              Provider
            </th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">
              Organization
            </th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">
              Village ID
            </th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">
              Created
            </th>
          </tr>
        </thead>
        <tbody>
          {vms.map((vm) => {
            const metaProvider =
              vm.metadata && typeof vm.metadata === "object"
                ? (vm.metadata as Record<string, unknown>).provider
                : undefined;
            const provider =
              typeof metaProvider === "string" && metaProvider.trim()
                ? metaProvider
                : vm.sub_type?.startsWith("ec2")
                  ? "AWS"
                  : vm.sub_type?.startsWith("gce")
                    ? "GCP"
                    : vm.sub_type?.startsWith("azure")
                      ? "Azure"
                      : vm.sub_type?.startsWith("lxd")
                        ? "LXD"
                        : vm.sub_type?.startsWith("vmware")
                          ? "VMware"
                          : vm.sub_type?.startsWith("proxmox")
                            ? "Proxmox"
                            : vm.sub_type === "bare_metal"
                              ? "Bare Metal"
                              : "Compute";
            return (
              <tr
                key={vm.id}
                className="border-b border-slate-800 hover:bg-slate-800/50"
              >
                <td className="py-3 px-4 text-white font-medium">{vm.name}</td>
                <td className="py-3 px-4 text-slate-300">
                  {vm.sub_type?.replace(/_/g, " ")}
                </td>
                <td className="py-3 px-4">
                  <span className="px-2 py-1 text-xs rounded-full bg-slate-700 text-slate-300">
                    {provider}
                  </span>
                </td>
                <td className="py-3 px-4 text-slate-300">
                  {vm.organization_id || "\u2014"}
                </td>
                <td className="py-3 px-4">
                  {vm.village_id ? (
                    <VillageIdBadge villageId={vm.village_id} compact />
                  ) : (
                    <span className="text-slate-500">{"\u2014"}</span>
                  )}
                </td>
                <td className="py-3 px-4 text-slate-300">
                  {vm.created_at
                    ? new Date(vm.created_at).toLocaleDateString()
                    : "\u2014"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Kubernetes Tab ────────────────────────────────────────────
function KubernetesTab() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [subTab, setSubTab] = useState<K8sTab>("Clusters");

  // Cluster drill-down: ?tab=Kubernetes&cluster=<id> scopes Nodes/Pods/
  // Deployments/Services to that cluster's children (parent_id / dependency
  // edges). Namespaces stays global.
  const clusterIdParam = searchParams.get("cluster");
  const clusterId = clusterIdParam ? parseInt(clusterIdParam, 10) : undefined;

  const selectCluster = (id: number) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("cluster", String(id));
      return next;
    });
  };

  const clearCluster = () => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("cluster");
      return next;
    });
  };

  const {
    data: clustersData,
    isLoading: loadingClusters,
    refetch: refetchClusters,
  } = useQuery({
    queryKey: ["k8s-clusters"],
    queryFn: () =>
      api.getEntities({
        entity_type: "compute",
        sub_type: "kubernetes_cluster",
      }),
    enabled: subTab === "Clusters" && !clusterId,
  });

  // Fetched independent of subTab/clustersData so the "Cluster: <name>"
  // header works from any sub-tab, not just the Clusters grid.
  const { data: selectedCluster } = useQuery({
    queryKey: ["k8s-cluster", clusterId],
    queryFn: () => api.getEntity(clusterId!),
    enabled: !!clusterId,
  });

  const {
    data: nodesData,
    isLoading: loadingNodes,
    refetch: refetchNodes,
  } = useQuery({
    queryKey: ["k8s-nodes", clusterId],
    queryFn: () =>
      api.getEntities({
        entity_type: "compute",
        sub_type: "k8s_node",
        per_page: 500,
        ...(clusterId ? { parent_id: clusterId } : {}),
      }),
    enabled: subTab === "Nodes",
  });

  const {
    data: podsData,
    isLoading: loadingPods,
    refetch: refetchPods,
  } = useQuery({
    queryKey: ["k8s-pods", clusterId],
    queryFn: () =>
      api.getEntities({
        entity_type: "compute",
        sub_type: "k8s_pod",
        per_page: 500,
        ...(clusterId ? { parent_id: clusterId } : {}),
      }),
    enabled: subTab === "Pods",
  });

  const {
    data: deploymentsData,
    isLoading: loadingDeployments,
    refetch: refetchDeployments,
  } = useQuery({
    queryKey: ["k8s-deployments", clusterId],
    queryFn: () =>
      api.getEntities({
        entity_type: "compute",
        sub_type: "k8s_deployment",
        per_page: 500,
        ...(clusterId ? { parent_id: clusterId } : {}),
      }),
    enabled: subTab === "Deployments",
  });

  // Global K8s services list — cross-referenced against cluster-scoped
  // dependency edges below when a cluster is selected.
  const {
    data: servicesData,
    isLoading: loadingServices,
    refetch: refetchServices,
  } = useQuery({
    queryKey: ["k8s-services"],
    queryFn: () => api.getServices({ deployment_method: "kubernetes" }),
    enabled: subTab === "Services",
  });

  // The Dependency DTO never enriches source/target with a name, so we
  // resolve names by intersecting these edges' source_id against the
  // already-fetched global services list rather than re-fetching per-id.
  const {
    data: clusterServiceDepsData,
    isLoading: loadingClusterServiceDeps,
    refetch: refetchClusterServiceDeps,
  } = useQuery({
    queryKey: ["k8s-cluster-service-deps", clusterId],
    queryFn: () =>
      api.getDependencies({
        target_type: "entity",
        target_id: clusterId!,
        per_page: 500,
      }),
    enabled: subTab === "Services" && !!clusterId,
  });

  const {
    data: namespacesData,
    isLoading: loadingNamespaces,
    refetch: refetchNamespaces,
  } = useQuery({
    queryKey: ["k8s-namespaces"],
    queryFn: () => api.listNetworks({ network_type: "namespace" }),
    enabled: subTab === "Namespaces",
  });

  const {
    data: storageData,
    isLoading: loadingStorage,
    refetch: refetchStorage,
  } = useQuery({
    queryKey: ["k8s-storage"],
    queryFn: () => api.getDataStores({ storage_type: "disk" }),
    enabled: subTab === "Storage",
  });

  const {
    data: saData,
    isLoading: loadingSA,
    refetch: refetchSA,
  } = useQuery({
    queryKey: ["k8s-service-accounts"],
    queryFn: () => api.getIdentities({ identity_type: "serviceAccount" }),
    enabled: subTab === "Service Accounts",
  });

  const handleRefresh = () => {
    const refetchMap: Record<K8sTab, () => void> = {
      Clusters: () => refetchClusters(),
      Nodes: () => refetchNodes(),
      Pods: () => refetchPods(),
      Deployments: () => refetchDeployments(),
      Services: () => {
        refetchServices();
        if (clusterId) refetchClusterServiceDeps();
      },
      Namespaces: () => refetchNamespaces(),
      Storage: () => refetchStorage(),
      "Service Accounts": () => refetchSA(),
    };
    refetchMap[subTab]();
  };

  const isLoading = {
    Clusters: loadingClusters,
    Nodes: loadingNodes,
    Pods: loadingPods,
    Deployments: loadingDeployments,
    Services: loadingServices || (!!clusterId && loadingClusterServiceDeps),
    Namespaces: loadingNamespaces,
    Storage: loadingStorage,
    "Service Accounts": loadingSA,
  }[subTab];

  const clusters: ComputeEntity[] =
    clustersData?.items || clustersData?.data || [];
  const nodes: ComputeEntity[] = nodesData?.items || nodesData?.data || [];
  const pods: ComputeEntity[] = podsData?.items || podsData?.data || [];
  const deployments: ComputeEntity[] =
    deploymentsData?.items || deploymentsData?.data || [];
  const allServices: K8sService[] =
    servicesData?.items || servicesData?.data || [];
  const clusterServiceDeps: Dependency[] =
    clusterServiceDepsData?.items || clusterServiceDepsData?.data || [];
  const clusterServiceIds = new Set(
    clusterServiceDeps
      .filter((dep) => dep.source_type === "service")
      .map((dep) => dep.source_id),
  );
  const services: K8sService[] = clusterId
    ? allServices.filter((s) => clusterServiceIds.has(s.id))
    : allServices;
  const namespaces: NetworkResource[] =
    namespacesData?.items || namespacesData?.data || [];
  const storage: DataStoreResource[] =
    storageData?.items || storageData?.data || [];
  const serviceAccounts: IdentityResource[] =
    saData?.items || saData?.data || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <SubTabNav
          tabs={K8S_TABS}
          icons={K8S_ICONS}
          active={subTab}
          onChange={setSubTab}
        />
        <button
          onClick={handleRefresh}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-700 text-slate-200 rounded-lg hover:bg-slate-600 transition-colors text-sm"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      </div>

      {clusterId && (
        <div className="flex flex-wrap items-center gap-3 px-1 -mt-2">
          <Ship className="h-4 w-4 text-amber-400 flex-shrink-0" />
          <span className="text-sm text-slate-300 truncate">
            Cluster:{" "}
            <span className="font-semibold text-white">
              {selectedCluster?.name || `#${clusterId}`}
            </span>
          </span>
          {selectedCluster?.village_id && (
            <VillageIdBadge villageId={selectedCluster.village_id} compact />
          )}
          <button
            onClick={clearCluster}
            className="ml-auto flex-shrink-0 text-xs text-primary-400 hover:text-primary-300 transition-colors"
          >
            ← All clusters
          </button>
        </div>
      )}

      {isLoading && <LoadingSpinner />}

      {subTab === "Clusters" && !loadingClusters && clusterId && (
        <Card>
          <CardContent>
            <div className="flex flex-col items-center justify-center py-10 text-slate-400 gap-2">
              <Ship className="h-10 w-10 text-amber-400 opacity-70" />
              <p className="text-lg font-medium text-white">
                {selectedCluster?.name || `Cluster #${clusterId}`}
              </p>
              {selectedCluster?.village_id && (
                <VillageIdBadge villageId={selectedCluster.village_id} />
              )}
              <p className="text-sm text-center max-w-md">
                Browse the Nodes, Pods, Deployments, or Services tabs above to
                see this cluster&apos;s resources.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {subTab === "Clusters" &&
        !loadingClusters &&
        !clusterId &&
        (clusters.length === 0 ? (
          <EmptyState
            icon={Ship}
            title="No Kubernetes clusters discovered"
            description="Run a Kubernetes discovery job to see clusters here"
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {clusters.map((cluster) => {
              const chips = normalizeTags(cluster.tags).slice(0, 2);
              return (
                <Card
                  key={cluster.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => selectCluster(cluster.id)}
                  onKeyDown={onActivateKey(() => selectCluster(cluster.id))}
                  className="cursor-pointer hover:border-primary-500 transition-colors"
                >
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <Ship className="h-5 w-5 text-amber-400" />
                      <h3 className="font-semibold text-white truncate">
                        {cluster.name}
                      </h3>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-slate-400">Type</span>
                        <span className="text-slate-200">
                          {cluster.sub_type}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Organization</span>
                        <span className="text-slate-200">
                          {cluster.organization_id || "\u2014"}
                        </span>
                      </div>
                      {chips.length > 0 && (
                        <div className="flex flex-wrap gap-1 pt-1">
                          {chips.map((chip) => (
                            <span
                              key={chip.key}
                              className="px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300"
                            >
                              {chip.label}
                            </span>
                          ))}
                        </div>
                      )}
                      {cluster.village_id && (
                        <div className="pt-2 border-t border-slate-700/50">
                          <VillageIdBadge
                            villageId={cluster.village_id}
                            compact
                          />
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ))}

      {subTab === "Nodes" &&
        !loadingNodes &&
        (nodes.length === 0 ? (
          <EmptyState
            icon={Server}
            title="No nodes discovered"
            description="Nodes appear after running Kubernetes discovery"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    CPU
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Memory
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Kubelet
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    OS
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Labels
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => {
                  const meta: EntityMetadata = node.metadata || {};
                  const chips = normalizeTags(node.tags).slice(0, 2);
                  return (
                    <tr
                      key={node.id}
                      className="border-b border-slate-800 hover:bg-slate-800/50 cursor-pointer"
                      onClick={() => navigate(`/entities/${node.id}`)}
                    >
                      <td className="py-3 px-4 text-white font-medium">
                        {node.name}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.capacity_cpu || "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.capacity_memory || "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.kubelet_version || "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.os_image || "\u2014"}
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex flex-wrap gap-1">
                          {chips.length > 0 ? (
                            chips.map((chip) => (
                              <span
                                key={chip.key}
                                className="px-1.5 py-0.5 text-xs rounded bg-slate-700 text-slate-300"
                              >
                                {chip.label}
                              </span>
                            ))
                          ) : (
                            <span className="text-slate-500">{"\u2014"}</span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        {node.village_id ? (
                          <VillageIdBadge villageId={node.village_id} compact />
                        ) : (
                          <span className="text-slate-500">{"\u2014"}</span>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-1 text-xs rounded-full bg-green-900/50 text-green-400">
                          {(meta.conditions || []).includes("Ready")
                            ? "Ready"
                            : "Unknown"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "Pods" &&
        !loadingPods &&
        (pods.length === 0 ? (
          <EmptyState
            icon={Box}
            title="No pods discovered"
            description="Pods appear after running Kubernetes discovery"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Namespace
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Node
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    IP
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Containers
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Labels
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Phase
                  </th>
                </tr>
              </thead>
              <tbody>
                {pods.map((pod) => {
                  const meta: EntityMetadata = pod.metadata || {};
                  const chips = normalizeTags(pod.tags).slice(0, 2);
                  const phase = meta.phase || "Unknown";
                  const phaseColor =
                    phase === "Running"
                      ? "bg-green-900/50 text-green-400"
                      : phase === "Succeeded"
                        ? "bg-blue-900/50 text-blue-400"
                        : phase === "Pending"
                          ? "bg-yellow-900/50 text-yellow-400"
                          : "bg-red-900/50 text-red-400";
                  return (
                    <tr
                      key={pod.id}
                      className="border-b border-slate-800 hover:bg-slate-800/50 cursor-pointer"
                      onClick={() => navigate(`/entities/${pod.id}`)}
                    >
                      <td className="py-3 px-4 text-white font-medium">
                        {pod.name}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.namespace || "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.node_name || "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-300 font-mono text-xs">
                        {meta.pod_ip || "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {meta.containers_count || 0}
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex flex-wrap gap-1">
                          {chips.length > 0 ? (
                            chips.map((chip) => (
                              <span
                                key={chip.key}
                                className="px-1.5 py-0.5 text-xs rounded bg-slate-700 text-slate-300"
                              >
                                {chip.label}
                              </span>
                            ))
                          ) : (
                            <span className="text-slate-500">{"\u2014"}</span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        {pod.village_id ? (
                          <VillageIdBadge villageId={pod.village_id} compact />
                        ) : (
                          <span className="text-slate-500">{"\u2014"}</span>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`px-2 py-1 text-xs rounded-full ${phaseColor}`}
                        >
                          {phase}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "Deployments" &&
        !loadingDeployments &&
        (deployments.length === 0 ? (
          <EmptyState
            icon={Boxes}
            title="No Deployments discovered"
            description="Deployments appear after running Kubernetes discovery"
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {deployments.map((dep) => {
              const meta: EntityMetadata = dep.metadata || {};
              const chips = normalizeTags(dep.tags).slice(0, 2);
              const images = meta.images || [];
              const ready = meta.available_replicas ?? meta.ready_replicas;
              const desired = meta.replicas;
              return (
                <Card
                  key={dep.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/entities/${dep.id}`)}
                  onKeyDown={onActivateKey(() =>
                    navigate(`/entities/${dep.id}`),
                  )}
                  className="cursor-pointer hover:border-primary-500 transition-colors"
                >
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <Boxes className="h-5 w-5 text-amber-400" />
                      <h3 className="font-semibold text-white truncate">
                        {dep.name}
                      </h3>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-slate-400">Namespace</span>
                        <span className="text-slate-200">
                          {meta.namespace || "\u2014"}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Replicas</span>
                        <span className="text-slate-200">
                          {ready ?? "\u2014"}/{desired ?? "\u2014"}
                        </span>
                      </div>
                      {images.length > 0 && (
                        <div>
                          <span className="text-slate-400 block mb-1">
                            Images
                          </span>
                          <div className="space-y-0.5">
                            {images.slice(0, 2).map((img, i) => (
                              <p
                                key={i}
                                className="text-xs text-slate-300 font-mono truncate"
                              >
                                {img}
                              </p>
                            ))}
                            {images.length > 2 && (
                              <p className="text-xs text-slate-500">
                                +{images.length - 2} more
                              </p>
                            )}
                          </div>
                        </div>
                      )}
                      {chips.length > 0 && (
                        <div className="flex flex-wrap gap-1 pt-1">
                          {chips.map((chip) => (
                            <span
                              key={chip.key}
                              className="px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300"
                            >
                              {chip.label}
                            </span>
                          ))}
                        </div>
                      )}
                      {dep.village_id && (
                        <div className="pt-2 border-t border-slate-700/50">
                          <VillageIdBadge villageId={dep.village_id} compact />
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ))}

      {subTab === "Services" &&
        !loadingServices &&
        (services.length === 0 ? (
          <EmptyState
            icon={Network}
            title="No Kubernetes services found"
            description="K8s services appear after running discovery"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Port
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Status
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Deployment
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                </tr>
              </thead>
              <tbody>
                {services.map((svc) => (
                  <tr
                    key={svc.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50 cursor-pointer"
                    onClick={() => navigate("/services")}
                  >
                    <td className="py-3 px-4 text-white font-medium">
                      {svc.name}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {svc.port || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`px-2 py-1 text-xs rounded-full ${
                          svc.status === "active"
                            ? "bg-green-900/50 text-green-400"
                            : "bg-slate-700 text-slate-400"
                        }`}
                      >
                        {svc.status || "unknown"}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {svc.deployment_method || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {svc.village_id ? (
                        <VillageIdBadge villageId={svc.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "Namespaces" &&
        !loadingNamespaces &&
        (namespaces.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No namespaces discovered"
            description="Namespaces appear after running Kubernetes discovery"
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {namespaces.map((ns) => (
              <Card
                key={ns.id}
                role="button"
                tabIndex={0}
                onClick={() => navigate("/networking")}
                onKeyDown={onActivateKey(() => navigate("/networking"))}
                className="cursor-pointer hover:border-primary-500 transition-colors"
              >
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Layers className="h-5 w-5 text-blue-400" />
                    <h3 className="font-semibold text-white">{ns.name}</h3>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Type</span>
                      <span className="text-slate-200">{ns.network_type}</span>
                    </div>
                    {ns.tags && ns.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {ns.tags.map((tag, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {ns.village_id && (
                      <div className="pt-2 border-t border-slate-700/50">
                        <VillageIdBadge villageId={ns.village_id} compact />
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ))}

      {subTab === "Storage" &&
        !loadingStorage &&
        (storage.length === 0 ? (
          <EmptyState
            icon={HardDrive}
            title="No Kubernetes storage found"
            description="Persistent volumes appear after running discovery"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Type
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Provider
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Region
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                </tr>
              </thead>
              <tbody>
                {storage.map((ds) => (
                  <tr
                    key={ds.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50 cursor-pointer"
                    onClick={() => navigate("/data-stores")}
                  >
                    <td className="py-3 px-4 text-white font-medium">
                      {ds.name}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {ds.storage_type || "\u2014"}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {ds.storage_provider || "\u2014"}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {ds.location_region || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {ds.village_id ? (
                        <VillageIdBadge villageId={ds.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "Service Accounts" &&
        !loadingSA &&
        (serviceAccounts.length === 0 ? (
          <EmptyState
            icon={Shield}
            title="No service accounts discovered"
            description="K8s service accounts appear after running discovery"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Username
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Provider
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Type
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Active
                  </th>
                </tr>
              </thead>
              <tbody>
                {serviceAccounts.map((sa) => (
                  <tr
                    key={sa.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50 cursor-pointer"
                    onClick={() => navigate("/iam")}
                  >
                    <td className="py-3 px-4 text-white font-mono text-xs">
                      {sa.username}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {sa.full_name || sa.username}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {sa.auth_provider || "\u2014"}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {sa.identity_type || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {sa.village_id ? (
                        <VillageIdBadge villageId={sa.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`px-2 py-1 text-xs rounded-full ${
                          sa.is_active
                            ? "bg-green-900/50 text-green-400"
                            : "bg-red-900/50 text-red-400"
                        }`}
                      >
                        {sa.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}

// ── LXD/LXC Tab ──────────────────────────────────────────────
function LxdTab() {
  const [subTab, setSubTab] = useState<LxdTab>("Overview");

  const { data: containersData, isLoading: loadingContainers } = useQuery({
    queryKey: ["lxd-containers"],
    queryFn: () =>
      api.getEntities({ entity_type: "compute", sub_type: "lxd_container" }),
    enabled: subTab === "Containers" || subTab === "Overview",
  });

  const { data: vmsData, isLoading: loadingVMs } = useQuery({
    queryKey: ["lxd-vms"],
    queryFn: () =>
      api.getEntities({ entity_type: "compute", sub_type: "lxd_vm" }),
    enabled: subTab === "VMs" || subTab === "Overview",
  });

  const { data: storageData, isLoading: loadingStorage } = useQuery({
    queryKey: ["lxd-storage"],
    queryFn: () => api.getDataStores({ storage_type: "other" }),
    enabled: subTab === "Storage Pools" || subTab === "Overview",
  });

  const { data: networksData, isLoading: loadingNetworks } = useQuery({
    queryKey: ["lxd-networks"],
    queryFn: () => api.listNetworks({ network_type: "other" }),
    enabled: subTab === "Networks" || subTab === "Overview",
  });

  const allContainers: ComputeEntity[] =
    containersData?.items || containersData?.data || [];
  const containers = allContainers.filter(
    (c) => c.sub_type === "lxd_container",
  );
  const allVms: ComputeEntity[] = vmsData?.items || vmsData?.data || [];
  const vms = allVms.filter((v) => v.sub_type === "lxd_vm");
  const allStorage: DataStoreResource[] =
    storageData?.items || storageData?.data || [];
  const storagePools = allStorage.filter((s) => (s.tags || []).includes("lxd"));
  const allNetworks: NetworkResource[] =
    networksData?.items || networksData?.data || [];
  const networks = allNetworks.filter((n) => (n.tags || []).includes("lxd"));

  const totalResources =
    containers.length + vms.length + storagePools.length + networks.length;
  const isAnyLoading =
    loadingContainers || loadingVMs || loadingStorage || loadingNetworks;

  return (
    <div className="space-y-4">
      <SubTabNav
        tabs={LXD_TABS}
        icons={LXD_ICONS}
        active={subTab}
        onChange={setSubTab}
      />

      {subTab === "Overview" && (
        <div className="space-y-6">
          {totalResources === 0 && !isAnyLoading ? (
            <Card>
              <CardContent>
                <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                  <Container className="h-16 w-16 mb-6 opacity-30" />
                  <h2 className="text-xl font-semibold text-white mb-2">
                    No LXD resources discovered yet
                  </h2>
                  <p className="text-sm text-slate-400 text-center max-w-md">
                    Connect an LXD cluster to get started. Once an LXD discovery
                    connector is configured, containers, VMs, storage pools, and
                    networks will appear here.
                  </p>
                  <div className="mt-6 flex items-center gap-2 px-4 py-2 bg-slate-800 rounded-lg text-sm text-slate-300">
                    <Plus className="h-4 w-4" />
                    LXD discovery connector coming soon
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <Card>
                <CardContent>
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-900/50 rounded-lg">
                      <Container className="h-5 w-5 text-blue-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-white">
                        {containers.length}
                      </p>
                      <p className="text-sm text-slate-400">Containers</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent>
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-purple-900/50 rounded-lg">
                      <Monitor className="h-5 w-5 text-purple-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-white">
                        {vms.length}
                      </p>
                      <p className="text-sm text-slate-400">Virtual Machines</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent>
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-green-900/50 rounded-lg">
                      <HardDrive className="h-5 w-5 text-green-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-white">
                        {storagePools.length}
                      </p>
                      <p className="text-sm text-slate-400">Storage Pools</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent>
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-amber-900/50 rounded-lg">
                      <Network className="h-5 w-5 text-amber-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-white">
                        {networks.length}
                      </p>
                      <p className="text-sm text-slate-400">Networks</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      )}

      {subTab === "Containers" &&
        !loadingContainers &&
        (containers.length === 0 ? (
          <EmptyState
            icon={Container}
            title="No LXD containers discovered"
            description="Containers will appear once an LXD discovery connector is configured"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Type
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Organization
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Created
                  </th>
                </tr>
              </thead>
              <tbody>
                {containers.map((c) => (
                  <tr
                    key={c.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50"
                  >
                    <td className="py-3 px-4 text-white font-medium">
                      {c.name}
                    </td>
                    <td className="py-3 px-4 text-slate-300">{c.sub_type}</td>
                    <td className="py-3 px-4 text-slate-300">
                      {c.organization_id || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {c.village_id ? (
                        <VillageIdBadge villageId={c.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {c.created_at
                        ? new Date(c.created_at).toLocaleDateString()
                        : "\u2014"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "VMs" &&
        !loadingVMs &&
        (vms.length === 0 ? (
          <EmptyState
            icon={Monitor}
            title="No LXD virtual machines discovered"
            description="VMs will appear once an LXD discovery connector is configured"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Type
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Organization
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Created
                  </th>
                </tr>
              </thead>
              <tbody>
                {vms.map((vm) => (
                  <tr
                    key={vm.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50"
                  >
                    <td className="py-3 px-4 text-white font-medium">
                      {vm.name}
                    </td>
                    <td className="py-3 px-4 text-slate-300">{vm.sub_type}</td>
                    <td className="py-3 px-4 text-slate-300">
                      {vm.organization_id || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {vm.village_id ? (
                        <VillageIdBadge villageId={vm.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {vm.created_at
                        ? new Date(vm.created_at).toLocaleDateString()
                        : "\u2014"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "Storage Pools" &&
        !loadingStorage &&
        (storagePools.length === 0 ? (
          <EmptyState
            icon={HardDrive}
            title="No LXD storage pools found"
            description="Storage pools will appear once an LXD discovery connector is configured"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Type
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Region
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                </tr>
              </thead>
              <tbody>
                {storagePools.map((s) => (
                  <tr
                    key={s.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50"
                  >
                    <td className="py-3 px-4 text-white font-medium">
                      {s.name}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {s.storage_type || "\u2014"}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {s.location_region || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {s.village_id ? (
                        <VillageIdBadge villageId={s.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {subTab === "Networks" &&
        !loadingNetworks &&
        (networks.length === 0 ? (
          <EmptyState
            icon={Network}
            title="No LXD networks found"
            description="Networks will appear once an LXD discovery connector is configured"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Name
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Type
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Region
                  </th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">
                    Village ID
                  </th>
                </tr>
              </thead>
              <tbody>
                {networks.map((n) => (
                  <tr
                    key={n.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50"
                  >
                    <td className="py-3 px-4 text-white font-medium">
                      {n.name}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {n.network_type || "\u2014"}
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {n.region || "\u2014"}
                    </td>
                    <td className="py-3 px-4">
                      {n.village_id ? (
                        <VillageIdBadge villageId={n.village_id} compact />
                      ) : (
                        <span className="text-slate-500">{"\u2014"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {isAnyLoading && subTab !== "Overview" && <LoadingSpinner />}
    </div>
  );
}

// ── Main Compute Page ─────────────────────────────────────────
export default function Compute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") as PrimaryTab | null;
  const [activeTab, setActiveTab] = useState<PrimaryTab>(
    tabParam && PRIMARY_TABS.includes(tabParam) ? tabParam : "VMs",
  );
  const queryClient = useQueryClient();

  const [showAddModal, setShowAddModal] = useState(false);
  const [formName, setFormName] = useState("");
  const [formSubType, setFormSubType] = useState("ec2_instance");
  const [formOrgId, setFormOrgId] = useState("");
  const [formParentId, setFormParentId] = useState("");
  const [formProvider, setFormProvider] = useState("");
  const [formRegion, setFormRegion] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formMetadataText, setFormMetadataText] = useState("");
  const [formMetadataError, setFormMetadataError] = useState<string | null>(
    null,
  );

  const { data: orgsData } = useQuery({
    queryKey: ["organizations-dropdown"],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
    enabled: showAddModal,
  });

  const { data: k8sClustersForParent } = useQuery({
    queryKey: ["k8s-clusters"],
    queryFn: () =>
      api.getEntities({
        entity_type: "compute",
        sub_type: "kubernetes_cluster",
      }),
    enabled: showAddModal && formSubType.startsWith("k8s_"),
  });

  const orgOptions = useMemo(
    () =>
      (orgsData?.items || []).map((o: { id: number; name: string }) => ({
        value: String(o.id),
        label: o.name,
      })),
    [orgsData],
  );

  const clusterParentOptions = useMemo(
    () =>
      (k8sClustersForParent?.items || k8sClustersForParent?.data || []).map(
        (c: ComputeEntity) => ({
          value: String(c.id),
          label: c.name,
        }),
      ),
    [k8sClustersForParent],
  );

  const createComputeMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.createEntity>[0]) =>
      api.createEntity(payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["compute-vms"] }),
        queryClient.invalidateQueries({ queryKey: ["k8s-clusters"] }),
        queryClient.invalidateQueries({ queryKey: ["k8s-nodes"] }),
        queryClient.invalidateQueries({ queryKey: ["k8s-pods"] }),
        queryClient.invalidateQueries({ queryKey: ["k8s-deployments"] }),
        queryClient.invalidateQueries({ queryKey: ["lxd-containers"] }),
        queryClient.invalidateQueries({ queryKey: ["lxd-vms"] }),
        queryClient.invalidateQueries({ queryKey: ["entities"] }),
      ]);
      toast.success("Compute resource created");
      setShowAddModal(false);
    },
    onError: () => {
      toast.error("Failed to create compute resource");
    },
  });

  const openAddModal = () => {
    const defaultSubType =
      activeTab === "Kubernetes"
        ? "kubernetes_cluster"
        : activeTab === "LXD/LXC"
          ? "lxd_container"
          : "ec2_instance";
    setFormName("");
    setFormSubType(defaultSubType);
    setFormOrgId(orgOptions[0]?.value || "");
    setFormParentId("");
    setFormProvider(activeTab === "Kubernetes" ? "Kubernetes" : "");
    setFormRegion("");
    setFormDescription("");
    setFormMetadataText("");
    setFormMetadataError(null);
    setShowAddModal(true);
  };

  const handleCreateSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormMetadataError(null);
    if (!formName.trim()) {
      toast.error("Name is required");
      return;
    }
    const orgIdNum = parseInt(formOrgId || orgOptions[0]?.value || "", 10);
    if (!orgIdNum || Number.isNaN(orgIdNum)) {
      toast.error("Organization is required");
      return;
    }

    let meta: Record<string, unknown> = {};
    if (formMetadataText.trim()) {
      try {
        const parsed = JSON.parse(formMetadataText);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          setFormMetadataError("Metadata must be a JSON object");
          return;
        }
        meta = parsed as Record<string, unknown>;
      } catch {
        setFormMetadataError("Invalid JSON in metadata");
        return;
      }
    }
    if (formProvider.trim()) meta.provider = formProvider.trim();
    if (formRegion.trim()) meta.region = formRegion.trim();

    createComputeMutation.mutate({
      name: formName.trim(),
      description: formDescription.trim() || undefined,
      entity_type: "compute",
      sub_type: formSubType,
      organization_id: orgIdNum,
      parent_id:
        formSubType.startsWith("k8s_") && formParentId
          ? parseInt(formParentId, 10)
          : undefined,
      metadata: Object.keys(meta).length > 0 ? meta : undefined,
    });
  };

  // Sync URL query param with tab state
  useEffect(() => {
    if (tabParam && PRIMARY_TABS.includes(tabParam) && tabParam !== activeTab) {
      setActiveTab(tabParam);
    }
  }, [tabParam, activeTab]);

  const handleTabChange = (tab: PrimaryTab) => {
    setActiveTab(tab);
    setSearchParams({ tab });
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="flex items-center gap-3">
          <Cpu className="h-8 w-8 text-amber-400" />
          <div>
            <h1 className="text-2xl font-bold text-white">Compute</h1>
            <p className="text-sm text-slate-400">
              Virtual machines, containers, and orchestration platforms
            </p>
          </div>
        </div>
        <Button onClick={openAddModal}>
          <Plus className="h-4 w-4 mr-2" />
          Add{" "}
          {activeTab === "Kubernetes"
            ? "Kubernetes"
            : activeTab === "LXD/LXC"
              ? "LXD"
              : "Compute"}{" "}
          Resource
        </Button>
      </div>

      <div className="border-b border-slate-700">
        <nav className="flex gap-1 -mb-px">
          {PRIMARY_TABS.map((tab) => {
            const Icon = PRIMARY_ICONS[tab];
            return (
              <button
                key={tab}
                onClick={() => handleTabChange(tab)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? "border-amber-400 text-amber-400"
                    : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600"
                }`}
              >
                <Icon className="h-4 w-4" />
                {tab}
              </button>
            );
          })}
        </nav>
      </div>

      <div>
        {activeTab === "VMs" && <VMsTab />}
        {activeTab === "Kubernetes" && <KubernetesTab />}
        {activeTab === "LXD/LXC" && <LxdTab />}
      </div>

      {showAddModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-label="Add compute or Kubernetes resource"
        >
          <div className="w-full max-w-lg bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-5 text-white max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-amber-400">
                Add Compute / Kubernetes Resource
              </h2>
              <button
                type="button"
                onClick={() => setShowAddModal(false)}
                className="text-slate-400 hover:text-white p-1 rounded"
                aria-label="Close modal"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCreateSubmit} className="space-y-4">
              <div>
                <span className="block text-sm font-medium text-slate-300 mb-1">
                  Resource Type *
                </span>
                <SearchableSelect
                  options={COMPUTE_SUB_TYPE_OPTIONS}
                  value={formSubType}
                  onChange={(val) => setFormSubType(String(val))}
                  placeholder="Select compute or Kubernetes type..."
                  ariaLabel="Resource Type"
                />
              </div>

              <div>
                <label
                  htmlFor="compute-create-name"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Name *
                </label>
                <Input
                  id="compute-create-name"
                  required
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="e.g. dal2-alpha-k8s or prod-web-01"
                />
              </div>

              <div>
                <span className="block text-sm font-medium text-slate-300 mb-1">
                  Organization *
                </span>
                <SearchableSelect
                  options={orgOptions}
                  value={formOrgId || orgOptions[0]?.value || ""}
                  onChange={(val) => setFormOrgId(String(val))}
                  placeholder="Select organization..."
                  ariaLabel="Organization"
                />
              </div>

              {formSubType.startsWith("k8s_") && (
                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1">
                    Parent Cluster (optional)
                  </span>
                  <SearchableSelect
                    options={clusterParentOptions}
                    value={formParentId}
                    onChange={(val) => setFormParentId(String(val))}
                    placeholder="Select Kubernetes cluster..."
                    allowClear
                    ariaLabel="Parent Cluster"
                  />
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1">
                    Provider / Platform
                  </span>
                  <SearchableSelect
                    options={COMPUTE_PROVIDER_OPTIONS}
                    value={formProvider}
                    onChange={(val) => setFormProvider(String(val))}
                    placeholder="AWS, GCP, VMware, EKS..."
                    allowCustom
                    allowClear
                    ariaLabel="Provider"
                  />
                </div>

                <div>
                  <span className="block text-sm font-medium text-slate-300 mb-1">
                    Region / Location
                  </span>
                  <SearchableSelect
                    options={COMPUTE_REGION_OPTIONS}
                    value={formRegion}
                    onChange={(val) => setFormRegion(String(val))}
                    placeholder="us-east-1, on-prem..."
                    allowCustom
                    allowClear
                    ariaLabel="Region"
                  />
                </div>
              </div>

              <div>
                <label
                  htmlFor="compute-create-description"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Description
                </label>
                <textarea
                  id="compute-create-description"
                  rows={2}
                  value={formDescription}
                  onChange={(e) => setFormDescription(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white"
                />
              </div>

              <div>
                <label
                  htmlFor="compute-create-metadata"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Metadata (JSON)
                </label>
                <textarea
                  id="compute-create-metadata"
                  rows={3}
                  value={formMetadataText}
                  onChange={(e) => {
                    setFormMetadataText(e.target.value);
                    setFormMetadataError(null);
                  }}
                  placeholder='{"version": "1.31", "vcpu": 8, "memory_gb": 32}'
                  className="w-full font-mono rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white"
                />
                {formMetadataError && (
                  <p className="text-xs text-red-400 mt-1">
                    {formMetadataError}
                  </p>
                )}
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setShowAddModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createComputeMutation.isPending}
                >
                  {createComputeMutation.isPending
                    ? "Creating..."
                    : "Create Resource"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
