import { describe, it, expect } from "vitest";
import { navFor } from "../registry";

describe("navFor — one section per WIRED pillar", () => {
  it("emits exactly one category per populated pillar, in WIRED order", () => {
    const all = new Set([
      "infrastructure",
      "ipam",
      "sbom",
      "services_oncall",
      "secrets",
      "issues",
      "discovery",
      "access_reviews",
      "webhooks_alerting",
      "nav_streams",
      "nav_documents",
      "nav_pages",
      "nav_diagrams",
    ]);
    const nav = navFor(all);

    expect(nav.map((c) => c.header)).toEqual([
      "Workstreams",
      "Work Management",
      "Relationships",
      "Infrastructure",
      "Software",
      "Security",
      "Documents",
    ]);
  });

  it("every emitted category has items — a header-only category renders as nothing", () => {
    const all = new Set(["infrastructure", "issues", "nav_documents"]);
    for (const category of navFor(all)) {
      expect(category.items.length).toBeGreaterThan(0);
    }
  });

  it("merges items from every module in a Resources sub-section and routes cross-cutting items", () => {
    // sbom and services_oncall share Resources — Software; On-Call routes to Work Management
    // and Vulnerabilities routes to Resources — Security.
    const nav = navFor(new Set(["sbom", "services_oncall"]));
    const workstreams = nav.find((c) => c.header === "Workstreams");
    const software = nav.find((c) => c.header === "Software");
    const workMgmt = nav.find((c) => c.header === "Work Management");
    const security = nav.find((c) => c.header === "Security");

    expect(software?.items.map((i) => i.name)).toEqual(
      expect.arrayContaining(["SBOM Dashboard", "Services"]),
    );
    expect(workstreams?.items.map((i) => i.href)).toContain("/csv-templates");
    expect(software?.items.map((i) => i.href)).not.toContain("/csv-templates");
    expect(workMgmt?.items.map((i) => i.href)).toContain("/on-call-rotations");
    expect(security?.items.map((i) => i.href)).toContain("/vulnerabilities");
  });

  it("never emits the same header twice", () => {
    const all = new Set([
      "infrastructure",
      "ipam",
      "sbom",
      "services_oncall",
      "secrets",
      "issues",
      "discovery",
      "access_reviews",
      "webhooks_alerting",
      "nav_streams",
      "nav_documents",
      "nav_pages",
      "nav_diagrams",
    ]);
    const headers = navFor(all).map((c) => c.header);
    expect(new Set(headers).size).toBe(headers.length);
  });

  it("dedupes items that share an href across modules", () => {
    const all = new Set([
      "infrastructure",
      "ipam",
      "sbom",
      "services_oncall",
      "secrets",
      "issues",
      "discovery",
      "access_reviews",
      "webhooks_alerting",
      "nav_streams",
      "nav_documents",
      "nav_pages",
      "nav_diagrams",
    ]);
    for (const category of navFor(all)) {
      const hrefs = category.items.map((i) => i.href);
      expect(new Set(hrefs).size).toBe(hrefs.length);
    }
  });

  it("omits a pillar entirely when none of its modules are enabled", () => {
    const nav = navFor(new Set(["nav_documents"]));
    expect(nav.map((c) => c.header)).toEqual(["Documents"]);
  });

  it("sections are collapsible so a large pillar can be folded away", () => {
    const nav = navFor(new Set(["infrastructure", "ipam", "sbom"]));
    expect(nav[0].collapsible).toBe(true);
  });

  it("keys stay group-scoped so collapse state persists per pillar", () => {
    const nav = navFor(new Set(["issues", "nav_streams"]));
    expect(nav.map((c) => c.key)).toEqual([
      "group-workstreams",
      "group-issues",
    ]);
  });

  it("returns an empty array when no modules are enabled", () => {
    expect(navFor(new Set<string>())).toEqual([]);
  });

  it("places Data Inventory under Infrastructure, Dependencies under Relationships, and deduplicates Diagrams", () => {
    const nav = navFor(new Set(["infrastructure", "issues", "nav_diagrams"]));
    const infraSection = nav.find((c) => c.header === "Infrastructure");
    const relSection = nav.find((c) => c.header === "Relationships");
    const workMgmtSection = nav.find((c) => c.header === "Work Management");
    const docsSection = nav.find((c) => c.header === "Documents");

    expect(infraSection).toBeDefined();
    expect(relSection).toBeDefined();
    expect(workMgmtSection).toBeDefined();
    expect(workMgmtSection?.key).toBe("group-issues");

    const entityItems = infraSection!.items.map((i) => ({
      name: i.name,
      href: i.href,
    }));
    expect(entityItems).toContainEqual({
      name: "Data Inventory",
      href: "/data-inventory",
    });
    expect(infraSection!.items.map((i) => i.href)).not.toContain("/diagram");
    expect(infraSection!.items.map((i) => i.href)).not.toContain(
      "/dependencies",
    );
    expect(relSection!.items.map((i) => i.href)).toContain("/dependencies");
    expect(docsSection!.items.map((i) => i.href)).toContain("/diagrams");

    const workMgmtNames = workMgmtSection!.items.map((i) => i.name);
    const workMgmtHrefs = workMgmtSection!.items.map((i) => i.href);
    expect(workMgmtNames).not.toContain("Data Inventory");
    expect(workMgmtHrefs).not.toContain("/data-inventory");
    expect(workMgmtHrefs).not.toContain("/data-stores");
    expect(workMgmtNames).toEqual([
      "Projects",
      "Issues",
      "Milestones",
      "Labels",
    ]);
  });

  it("orders Workstreams items in the exact canonical order with CodeRuns label and Executions last", () => {
    const nav = navFor(
      new Set(["webhooks_alerting", "nav_streams", "runs", "sbom"]),
    );
    const workstreams = nav.find((c) => c.header === "Workstreams");

    expect(workstreams).toBeDefined();
    expect(workstreams!.items.map((i) => i.href)).toEqual([
      "/webhooks",
      "/streams",
      "/approvals",
      "/runs",
      "/backups",
      "/csv-templates",
      "/executions",
    ]);
    expect(workstreams!.items.map((i) => i.name)).toEqual([
      "Webhooks",
      "Playbooks",
      "Approvals",
      "CodeRuns",
      "Backups",
      "CSV Templates",
      "Executions",
    ]);
    expect(workstreams!.items[workstreams!.items.length - 1].href).toBe(
      "/executions",
    );
  });

  it("orders Work Management items in the exact canonical order", () => {
    const nav = navFor(new Set(["services_oncall", "issues"]));
    const workMgmt = nav.find((c) => c.header === "Work Management");

    expect(workMgmt).toBeDefined();
    expect(workMgmt!.items.map((i) => i.href)).toEqual([
      "/on-call-rotations",
      "/projects",
      "/issues",
      "/milestones",
      "/labels",
    ]);
  });

  it("consolidates IPAM under Networking without a standalone /ipam sidebar item", () => {
    const nav = navFor(new Set(["infrastructure", "ipam"]));
    const infra = nav.find((c) => c.header === "Infrastructure");

    expect(infra).toBeDefined();
    expect(infra!.items.map((i) => i.href)).toContain("/networking");
    expect(infra!.items.map((i) => i.href)).not.toContain("/ipam");
  });
});
