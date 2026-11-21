import { describe, it, expect } from "vitest";
import infrastructureModule from "../index";
import type { FrontendModule } from "../../types";

describe("Infrastructure Module", () => {
  it("should belong to entities group", () => {
    expect(infrastructureModule.id).toBe("infrastructure");
    expect(infrastructureModule.group).toBe("entities");
  });

  it("should include Data Inventory in Assets navigation", () => {
    const assetsCategory = infrastructureModule.nav.find(
      (cat) => cat.header === "Assets",
    );
    expect(assetsCategory).toBeDefined();
    expect(assetsCategory?.items).toContainEqual(
      expect.objectContaining({
        name: "Data Inventory",
        href: "/data-inventory",
      }),
    );
  });

  it("should register data-inventory and data-stores routes", () => {
    const routePaths = infrastructureModule.routes.map((route) => route.path);
    expect(routePaths).toContain("data-inventory");
    expect(routePaths).toContain("data-stores");
  });

  it("should conform to FrontendModule interface", () => {
    const isValid = (mod: FrontendModule): boolean =>
      typeof mod.id === "string" &&
      typeof mod.name === "string" &&
      Array.isArray(mod.nav) &&
      Array.isArray(mod.routes) &&
      (mod.adminNav === undefined || Array.isArray(mod.adminNav));
    expect(isValid(infrastructureModule)).toBe(true);
  });
});
