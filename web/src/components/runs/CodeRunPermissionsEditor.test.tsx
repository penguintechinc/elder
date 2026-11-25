import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import CodeRunPermissionsEditor from "./CodeRunPermissionsEditor";
import {
  formatEgressDomains,
  getHelperSdkSnippet,
  parseEgressDomains,
} from "@/modules/runs/permissions";
import api, {
  DEFAULT_RUN_PERMISSIONS_CATALOG,
  type RunPermissionsCatalogResponse,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    default: {
      getRunPermissionsCatalog: vi.fn(),
    },
  };
});

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function renderEditor(
  props: Partial<React.ComponentProps<typeof CodeRunPermissionsEditor>> = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  const defaultProps: React.ComponentProps<typeof CodeRunPermissionsEditor> = {
    runtime: "python",
    grantedScopes: [],
    onGrantedScopesChange: vi.fn(),
    allowedEgressDomains: "",
    onAllowedEgressDomainsChange: vi.fn(),
    ...props,
  };
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <CodeRunPermissionsEditor {...defaultProps} />
      </QueryClientProvider>,
    ),
    props: defaultProps,
  };
}

describe("CodeRunPermissionsEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getRunPermissionsCatalog).mockResolvedValue(
      DEFAULT_RUN_PERMISSIONS_CATALOG,
    );
  });

  it("renders all four permission groups and least-privilege explanation", async () => {
    renderEditor();

    await waitFor(() => {
      expect(screen.getByText("Resources")).toBeTruthy();
      expect(screen.getByText("Integrations")).toBeTruthy();
      expect(screen.getByText("Workstreams & Webhooks")).toBeTruthy();
      expect(screen.getByText("Work Management")).toBeTruthy();
    });

    expect(
      screen.getByText(/Least-privilege: zero Elder API access unless granted/i),
    ).toBeTruthy();
    expect(screen.getByTestId("permissions-editor-count").textContent).toBe(
      "Isolated",
    );
  });

  it("toggles individual scopes and supports Select All / Clear per group while respecting grantable=false", async () => {
    const customCatalog: RunPermissionsCatalogResponse = {
      source: "api",
      groups: [
        {
          id: "resources",
          label: "Resources",
          scopes: [
            {
              scope: "entities:read",
              label: "Read Entities",
              description: "Read entities",
              grantable: true,
            },
            {
              scope: "entities:write",
              label: "Write Entities",
              description: "Write entities",
              grantable: true,
            },
            {
              scope: "entities:delete",
              label: "Delete Entities",
              description: "Delete entities",
              grantable: false,
            },
          ],
        },
        {
          id: "integrations",
          label: "Integrations",
          scopes: [
            {
              scope: "integrations:sync",
              label: "Trigger Integration Sync",
              description: "Trigger sync",
              grantable: true,
            },
          ],
        },
      ],
    };
    vi.mocked(api.getRunPermissionsCatalog).mockResolvedValue(customCatalog);

    const onGrantedScopesChange = vi.fn();
    renderEditor({
      grantedScopes: ["integrations:sync"],
      onGrantedScopesChange,
    });

    await waitFor(() => {
      expect(screen.getByText("Not held")).toBeTruthy();
    });

    const deleteCheckbox = screen.getByLabelText(
      /Delete Entities/i,
    ) as HTMLInputElement;
    expect(deleteCheckbox.disabled).toBe(true);

    // Toggle individual grantable scope
    const readCheckbox = screen.getByLabelText(
      /Read Entities/i,
    ) as HTMLInputElement;
    expect(readCheckbox.disabled).toBe(false);
    fireEvent.click(readCheckbox);
    expect(onGrantedScopesChange).toHaveBeenCalledWith([
      "integrations:sync",
      "entities:read",
    ]);

    // Select All in Resources should only add grantable scopes (skipping entities:delete)
    fireEvent.click(
      screen.getByRole("button", { name: "Select all Resources scopes" }),
    );
    expect(onGrantedScopesChange).toHaveBeenLastCalledWith([
      "integrations:sync",
      "entities:read",
      "entities:write",
    ]);

    // Clear in Integrations should remove only Integrations scopes
    fireEvent.click(
      screen.getByRole("button", { name: "Clear Integrations scopes" }),
    );
    expect(onGrantedScopesChange).toHaveBeenLastCalledWith([]);
  });

  it("updates Allowed External Egress Domains input", async () => {
    const onAllowedEgressDomainsChange = vi.fn();
    renderEditor({
      allowedEgressDomains: "",
      onAllowedEgressDomainsChange,
    });

    const input = screen.getByLabelText(/Allowed External Egress Domains/i);
    fireEvent.change(input, {
      target: { value: "api.github.com, hooks.slack.com" },
    });

    expect(onAllowedEgressDomainsChange).toHaveBeenCalledWith(
      "api.github.com, hooks.slack.com",
    );
  });

  it("expands Built-In Helper SDK quick reference and adapts snippet to each runtime", async () => {
    const { rerender } = renderEditor({ runtime: "python" });

    expect(screen.queryByTestId("helper-sdk-panel")).toBeNull();
    fireEvent.click(screen.getByTestId("toggle-helper-sdk-ref"));

    expect(screen.getByTestId("helper-sdk-panel")).toBeTruthy();
    expect(screen.getByTestId("helper-sdk-snippet").textContent).toContain(
      "import elder",
    );
    expect(screen.getByTestId("helper-sdk-snippet").textContent).toContain(
      "elder.update_entity_metadata",
    );
    expect(screen.getByTestId("helper-sdk-snippet").textContent).toContain(
      'elder.trigger_integration_sync("fleetdm")',
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const runtimes: Array<{ runtime: string; expected: string[] }> = [
      {
        runtime: "nodejs",
        expected: [
          "import elder from './elder.mjs'",
          "elder.update_entity_metadata",
          "elder.trigger_integration_sync('fleetdm')",
        ],
      },
      {
        runtime: "ruby",
        expected: [
          "require_relative 'elder'",
          "elder.update_entity_metadata",
          "elder.trigger_integration_sync('fleetdm')",
        ],
      },
      {
        runtime: "golang",
        expected: ["ELDER_GATEWAY_URL", "ELDER_GATEWAY_TOKEN", "fleetdm"],
      },
      {
        runtime: "rust",
        expected: ["ELDER_GATEWAY_URL", "ELDER_GATEWAY_TOKEN", "fleetdm"],
      },
    ];

    for (const { runtime, expected } of runtimes) {
      rerender(
        <QueryClientProvider client={queryClient}>
          <CodeRunPermissionsEditor
            runtime={runtime}
            grantedScopes={[]}
            onGrantedScopesChange={vi.fn()}
            allowedEgressDomains=""
            onAllowedEgressDomainsChange={vi.fn()}
          />
        </QueryClientProvider>,
      );
      const snippetText =
        screen.getByTestId("helper-sdk-snippet").textContent ?? "";
      for (const fragment of expected) {
        expect(snippetText).toContain(fragment);
      }
    }
  });

  it("parses and formats egress domains cleanly", () => {
    expect(parseEgressDomains("")).toEqual([]);
    expect(
      parseEgressDomains(" API.GitHub.com, hooks.slack.com , api.github.com, "),
    ).toEqual(["api.github.com", "hooks.slack.com"]);
    expect(formatEgressDomains(["api.github.com", "hooks.slack.com"])).toBe(
      "api.github.com, hooks.slack.com",
    );
    expect(formatEgressDomains([])).toBe("");
    expect(formatEgressDomains(null)).toBe("");
    expect(getHelperSdkSnippet("unknown").snippet).toContain(
      "ELDER_GATEWAY_URL",
    );
  });
});
