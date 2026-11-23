import api from "@/lib/api";

export const PRIMARY_12_COLORS = [
  { hex: "#1f2937", name: "Slate" },
  { hex: "#3b82f6", name: "Blue" },
  { hex: "#6366f1", name: "Indigo" },
  { hex: "#8b5cf6", name: "Purple" },
  { hex: "#ec4899", name: "Pink" },
  { hex: "#ef4444", name: "Red" },
  { hex: "#f97316", name: "Orange" },
  { hex: "#f59e0b", name: "Amber" },
  { hex: "#eab308", name: "Yellow" },
  { hex: "#22c55e", name: "Green" },
  { hex: "#14b8a6", name: "Teal" },
  { hex: "#06b6d4", name: "Cyan" },
];

export type ResourceCategory =
  | ""
  | "identity"
  | "entity"
  | "network"
  | "data_store"
  | "service"
  | "software"
  | "ai_resource"
  | "organization"
  | "project"
  | "issue";

export const RESOURCE_CATEGORY_OPTIONS: {
  value: ResourceCategory;
  label: string;
}[] = [
  { value: "", label: "None" },
  { value: "identity", label: "Identities / Users" },
  { value: "entity", label: "Entities / Systems" },
  { value: "network", label: "Network Resources" },
  { value: "data_store", label: "Data Stores" },
  { value: "service", label: "Services" },
  { value: "software", label: "Software" },
  { value: "ai_resource", label: "AI Resources" },
  { value: "organization", label: "Organizations" },
  { value: "project", label: "Projects" },
  { value: "issue", label: "Issues" },
];

export interface ResourceOption {
  value: string;
  label: string;
  rawName: string;
}

export const RESOURCE_STATE_FIELD_HINTS: Record<string, string[]> = {
  identity: [
    "is_locked",
    "is_active",
    "status",
    "identity_type",
    "email",
    "organization_id",
    "relationships.count",
    "relationships.related_ids",
    "relationships.dependency_types",
  ],
  entity: [
    "status",
    "is_active",
    "entity_type",
    "environment",
    "organization_id",
    "parent_id",
    "relationships.count",
    "relationships.related_ids",
    "relationships.dependency_types",
  ],
  network: [
    "status",
    "network_type",
    "provider",
    "region",
    "cidr",
    "relationships.count",
    "relationships.related_ids",
  ],
  data_store: [
    "status",
    "storage_type",
    "data_classification",
    "contains_pii",
    "contains_phi",
    "contains_pci",
    "relationships.count",
    "relationships.related_ids",
  ],
  service: [
    "status",
    "language",
    "deployment_method",
    "relationships.count",
    "relationships.related_ids",
  ],
  software: [
    "is_active",
    "vendor",
    "version",
    "software_type",
    "relationships.count",
  ],
  ai_resource: [
    "status",
    "is_active",
    "provider",
    "model_name",
    "agreement_level",
    "relationships.count",
  ],
  organization: ["is_active", "name", "relationships.count"],
  project: ["status", "is_active", "relationships.count"],
  issue: ["status", "priority", "severity", "issue_type", "relationships.count"],
};

export async function fetchCategoryResources(
  resourceCategory: ResourceCategory | string,
): Promise<ResourceOption[]> {
  if (resourceCategory === "network") {
    const res = await api.listNetworks();
    const items = Array.isArray(res)
      ? res
      : res?.networks || res?.items || [];
    return items.map(
      (n: {
        id: number;
        name?: string;
        network_type?: string;
        provider?: string;
        region?: string;
      }) => ({
        value: String(n.id),
        label: [
          n.name || `Network #${n.id}`,
          n.provider,
          n.network_type,
          n.region,
        ]
          .filter(Boolean)
          .join(" · "),
        rawName: n.name || `Network #${n.id}`,
      }),
    );
  }
  if (resourceCategory === "identity") {
    const res = await api.getIdentities({ per_page: 250 });
    const items = Array.isArray(res)
      ? res
      : res?.items || res?.identities || [];
    return items.map(
      (u: {
        id: number;
        name?: string;
        full_name?: string;
        username?: string;
        email?: string;
        identity_type?: string;
      }) => {
        const display =
          u.name || u.full_name || u.username || u.email || `Identity #${u.id}`;
        return {
          value: String(u.id),
          label: [
            display,
            u.email && u.email !== display ? u.email : null,
            u.identity_type,
          ]
            .filter(Boolean)
            .join(" · "),
          rawName: display,
        };
      },
    );
  }
  if (resourceCategory === "entity") {
    const res = await api.getEntities({ per_page: 250 });
    const items = Array.isArray(res) ? res : res?.items || res?.entities || [];
    return items.map(
      (e: { id: number; name: string; entity_type?: string; type?: string }) => {
        const kind = e.entity_type || e.type;
        return {
          value: String(e.id),
          label: kind ? `${e.name} (${kind})` : e.name,
          rawName: e.name,
        };
      },
    );
  }
  if (resourceCategory === "data_store") {
    const res = await api.getDataStores({ per_page: 250 });
    const items = Array.isArray(res)
      ? res
      : res?.items || res?.data_stores || [];
    return items.map(
      (d: {
        id: number;
        name: string;
        storage_type?: string;
        store_type?: string;
      }) => {
        const kind = d.storage_type || d.store_type;
        return {
          value: String(d.id),
          label: kind ? `${d.name} (${kind})` : d.name,
          rawName: d.name,
        };
      },
    );
  }
  if (resourceCategory === "service") {
    const res = await api.getServices({ per_page: 250 });
    const items = Array.isArray(res) ? res : res?.items || res?.services || [];
    return items.map((s: { id: number; name: string }) => ({
      value: String(s.id),
      label: s.name,
      rawName: s.name,
    }));
  }
  if (resourceCategory === "software") {
    const res = await api.getSoftware({ per_page: 250 });
    const items = Array.isArray(res) ? res : res?.items || res?.software || [];
    return items.map((s: { id: number; name: string; vendor?: string }) => ({
      value: String(s.id),
      label: s.vendor ? `${s.name} (${s.vendor})` : s.name,
      rawName: s.name,
    }));
  }
  if (resourceCategory === "ai_resource") {
    const res = await api.getAIResources({ per_page: 250 });
    const items = Array.isArray(res) ? res : res?.items || res?.resources || [];
    return items.map(
      (a: { id: number; name: string; provider?: string | null }) => ({
        value: String(a.id),
        label: a.provider ? `${a.name} (${a.provider})` : a.name,
        rawName: a.name,
      }),
    );
  }
  if (resourceCategory === "organization") {
    const res = await api.getOrganizations({ per_page: 250 });
    const items = Array.isArray(res)
      ? res
      : res?.items || res?.organizations || [];
    return items.map((o: { id: number; name: string }) => ({
      value: String(o.id),
      label: o.name,
      rawName: o.name,
    }));
  }
  if (resourceCategory === "project") {
    const res = await api.getProjects({ per_page: 250 });
    const items = Array.isArray(res) ? res : res?.items || res?.projects || [];
    return items.map((p: { id: number; name: string; status?: string }) => ({
      value: String(p.id),
      label: p.status ? `${p.name} (${p.status})` : p.name,
      rawName: p.name,
    }));
  }
  if (resourceCategory === "issue") {
    const res = await api.getIssues({ per_page: 250 });
    const items = Array.isArray(res) ? res : res?.items || res?.issues || [];
    return items.map(
      (i: { id: number; title?: string; name?: string; status?: string }) => {
        const title = i.title || i.name || `Issue #${i.id}`;
        return {
          value: String(i.id),
          label: i.status ? `${title} (${i.status})` : title,
          rawName: title,
        };
      },
    );
  }
  return [];
}
