import { describe, it, expect } from "vitest";
import issuesModule from "../index";
import type { FrontendModule } from "../../types";

describe("Issues Module", () => {
  it("should have correct module metadata", () => {
    expect(issuesModule.id).toBe("issues");
    expect(issuesModule.name).toBe("Work Management");
    expect(issuesModule.group).toBe("issues");
  });

  it("should have Work Management navigation without Data Inventory", () => {
    expect(issuesModule.nav.length).toBeGreaterThan(0);
    expect(issuesModule.nav[0].header).toBe("Work Management");
    const itemNames = issuesModule.nav[0].items.map((item) => item.name);
    const itemHrefs = issuesModule.nav[0].items.map((item) => item.href);
    expect(itemNames).toEqual(["Issues", "Labels", "Milestones", "Projects"]);
    expect(itemNames).not.toContain("Data Inventory");
    expect(itemHrefs).not.toContain("/data-inventory");
    expect(itemHrefs).not.toContain("/data-stores");
  });

  it("should have required routes including issue detail and exclude data inventory", () => {
    const routePaths = issuesModule.routes.map((route) => route.path);
    expect(routePaths).toContain("issues");
    expect(routePaths).toContain("issues/:id");
    expect(routePaths).toContain("projects");
    expect(routePaths).toContain("projects/:id");
    expect(routePaths).toContain("milestones");
    expect(routePaths).toContain("labels");
    expect(routePaths).not.toContain("data-inventory");
    expect(routePaths).not.toContain("data-stores");
  });

  it("should conform to FrontendModule interface", () => {
    const isValid = (mod: FrontendModule): boolean =>
      typeof mod.id === "string" &&
      typeof mod.name === "string" &&
      Array.isArray(mod.nav) &&
      Array.isArray(mod.routes) &&
      (mod.adminNav === undefined || Array.isArray(mod.adminNav));
    expect(isValid(issuesModule)).toBe(true);
  });
});
