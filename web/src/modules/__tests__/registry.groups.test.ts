import { describe, it, expect } from "vitest";
import { MODULES, navFor } from "../registry";

const VALID_GROUPS = [
  "workstreams",
  "issues",
  "relationships",
  "entities",
  "documents",
];
const EXPECTED: Record<string, string> = {
  // W — Workstreams
  nav_streams: "workstreams",
  webhooks_alerting: "workstreams",
  // I — Issues
  issues: "issues",
  // R — Relationships
  discovery: "relationships",
  access_reviews: "relationships",
  // E — Entities
  infrastructure: "entities",
  ipam: "entities",
  sbom: "entities",
  services_oncall: "entities",
  secrets: "entities",
  ai: "entities",
  // D — Documents
  nav_documents: "documents",
  nav_pages: "documents",
  nav_diagrams: "documents",
};

describe("Module Grouping (WIRED)", () => {
  it("every module has a valid WIRED group", () => {
    for (const module of MODULES) {
      expect(VALID_GROUPS).toContain(module.group);
    }
  });

  it("group mapping matches spec", () => {
    const got = Object.fromEntries(MODULES.map((m) => [m.id, m.group]));
    expect(got).toEqual(EXPECTED);
  });

  it("issues module is in the issues group", () => {
    const issuesModule = MODULES.find((m) => m.id === "issues");
    expect(issuesModule?.group).toBe("issues");
  });

  it("diagrams module is in the documents group", () => {
    const diagramsModule = MODULES.find((m) => m.id === "nav_diagrams");
    expect(diagramsModule?.group).toBe("documents");
  });

  it("nav headers render in WIRED acronym order", () => {
    const all = new Set(MODULES.map((m) => m.id));
    const headers = navFor(all)
      .filter((c) => c.key?.startsWith("group-"))
      .map((c) => c.header);
    expect(headers).toEqual([
      "Workstreams",
      "Work Management",
      "Relationships",
      "Entities",
      "Documents",
    ]);
  });

  it("exposes Data Inventory under Entities and keeps Work Management key as group-issues", () => {
    const all = new Set(MODULES.map((m) => m.id));
    const nav = navFor(all);

    const entities = nav.find((c) => c.header === "Entities");
    const workMgmt = nav.find((c) => c.header === "Work Management");

    expect(entities).toBeDefined();
    expect(workMgmt).toBeDefined();
    expect(workMgmt?.key).toBe("group-issues");

    expect(entities?.items.map((i) => i.href)).toContain("/data-inventory");
    expect(entities?.items.map((i) => i.name)).toContain("Data Inventory");
    expect(workMgmt?.items.map((i) => i.href)).not.toContain("/data-inventory");
    expect(workMgmt?.items.map((i) => i.name)).not.toContain("Data Inventory");
  });
});
