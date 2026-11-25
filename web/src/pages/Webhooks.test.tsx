import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import Webhooks from "./Webhooks";
import api from "@/lib/api";

function hasExampleHostname(placeholder: string): boolean {
  try {
    const { hostname } = new URL(placeholder);
    return hostname === "example.com" || hostname.endsWith(".example.com");
  } catch {
    return false;
  }
}

// Mock AssigneePicker with buttons to trigger onChange — allows tests to verify filter wiring
vi.mock("@/components/AssigneePicker", () => ({
  default: ({
    onChange,
  }: {
    onChange: (
      val: {
        assignee_type: "identity" | "org_unit";
        assignee_id: number;
      } | null,
    ) => void;
  }) => (
    <div data-testid="assignee-picker">
      <button
        type="button"
        data-testid="assignee-picker-select-button"
        onClick={() => onChange({ assignee_type: "org_unit", assignee_id: 7 })}
      >
        Select Assignee
      </button>
      <button
        type="button"
        data-testid="assignee-picker-clear-button"
        onClick={() => onChange(null)}
      >
        Clear Assignee
      </button>
    </div>
  ),
  AssigneeValue: {},
}));

vi.mock("@/lib/api", () => ({
  default: {
    getPortalProfile: vi.fn(),
    getWebhooks: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({
      items: [
        {
          id: 1,
          name: "Acme",
          created_at: "2026-01-01",
          updated_at: "2026-01-01",
        },
      ],
    }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    createWebhook: vi.fn(),
    updateWebhook: vi.fn(),
    deleteWebhook: vi.fn(),
    testWebhook: vi.fn(),
    getInboundWebhooks: vi.fn(),
    getInboundWebhookScopes: vi.fn(),
    createInboundWebhook: vi.fn(),
    updateInboundWebhook: vi.fn(),
    deleteInboundWebhook: vi.fn(),
    rotateInboundWebhookCredentials: vi.fn(),
    getInboundWebhookEvents: vi.fn(),
    listStreams: vi.fn(),
  },
}));
const DEFAULT_SCOPES_CATALOG = [
  {
    scope: "events:log",
    label: "Log Events",
    category: "General",
    description: "Record incoming webhook payloads.",
  },
  {
    scope: "playbooks:trigger",
    label: "Trigger Playbooks",
    category: "Automation",
    description: "Trigger Stream playbooks.",
  },
  {
    scope: "issues:create",
    label: "Create Issues",
    category: "Issues",
    description: "Create issues.",
  },
  {
    scope: "issues:update",
    label: "Update Issues",
    category: "Issues",
    description: "Update issues.",
  },
  {
    scope: "vulnerabilities:create",
    label: "Create Vulnerabilities",
    category: "Security",
    description: "Create vulnerabilities.",
  },
];

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <Webhooks />
    </QueryClientProvider>,
  );
}

describe("Webhooks admin page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.pushState({}, "", "/webhooks");
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: "admin",
      tenant_role: null,
    });
    vi.mocked(api.getWebhooks).mockResolvedValue({ webhooks: [] });
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [
        {
          id: 1,
          name: "Acme",
          created_at: "2026-01-01",
          updated_at: "2026-01-01",
        },
      ],
    });
    vi.mocked(api.getInboundWebhooks).mockResolvedValue({
      inbound_webhooks: [],
      count: 0,
    });
    vi.mocked(api.getInboundWebhookScopes).mockResolvedValue({
      scopes: DEFAULT_SCOPES_CATALOG,
    });
    vi.mocked(api.getInboundWebhookEvents).mockResolvedValue({
      events: [],
      count: 0,
    });
    vi.mocked(api.listStreams).mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    window.history.pushState({}, "", "/webhooks");
  });
  it("shows an admin-required message for non-admins", async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: "viewer",
      tenant_role: null,
    });
    renderPage();
    await waitFor(() => {
      const msg = screen.getByText(/admin access required/i);
      expect(msg).toBeDefined();
    });
  });

  it("opens edit modal and pre-populates webhook data including is_active and filter fields", async () => {
    vi.mocked(api.getWebhooks).mockResolvedValue({
      webhooks: [
        {
          id: 1,
          name: "Support bot",
          url: "https://x.example.com",
          organization_id: 1,
          events: ["issue.assigned"],
          is_active: false,
          filter_issue_type: "bug",
          filter_assignee_type: "identity",
          filter_assignee_id: 42,
        },
      ],
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Outbound (Basic Events)")).toBeDefined();
    });
    fireEvent.click(screen.getByText("Outbound (Basic Events)"));
    await waitFor(() => {
      const name = screen.getByText("Support bot");
      expect(name).toBeDefined();
    });
    fireEvent.click(screen.getByTitle("Edit webhook"));
    const form = await screen.findByTestId("edit-webhook-form");
    expect(form).toBeDefined();
    // Verify pre-populated values: name and URL inputs
    const inputs = screen.getAllByDisplayValue("Support bot");
    expect(inputs.length).toBeGreaterThan(0);
    const urlInputs = screen.getAllByDisplayValue("https://x.example.com");
    expect(urlInputs.length).toBeGreaterThan(0);
    // Verify is_active toggle reflects false
    const activeToggle = screen.getByTestId(
      "is-active-toggle",
    ) as HTMLInputElement;
    expect(activeToggle.checked).toBe(false);
  });

  it("submits create webhook with all filter fields, is_active, and events", async () => {
    vi.mocked(api.createWebhook).mockResolvedValue({ id: 2 });
    renderPage();
    await waitFor(() => {
      const h1 = screen.getByText("Webhooks");
      expect(h1).toBeDefined();
    });
    fireEvent.click(screen.getByText("Outbound (Basic Events)"));
    fireEvent.click(screen.getByText("Create Outbound Webhook"));
    // Wait for modal to appear
    await waitFor(
      () => {
        const createBtn = screen.queryByText("Create");
        expect(createBtn).toBeDefined();
      },
      { timeout: 3000 },
    );
    // Fill required fields using input elements (bypass label linking issues)
    const inputs = screen.getAllByPlaceholderText(
      /Production Webhook|example.com/,
    );
    const nameInput = inputs.find((i) =>
      (i as HTMLInputElement).placeholder.includes("Production"),
    );
    const urlInput = inputs.find((i) =>
      hasExampleHostname((i as HTMLInputElement).placeholder),
    );
    if (nameInput)
      fireEvent.change(nameInput, {
        target: { value: "Support bot assignments" },
      });
    if (urlInput) {
      fireEvent.focus(urlInput);
      fireEvent.change(urlInput, {
        target: { value: "https://hooks.example.com/webhook" },
      });
      fireEvent.keyDown(urlInput, { key: "Enter" });
    }
    // Select issue.assigned event
    fireEvent.click(screen.getByTestId("event-checkbox-issue.assigned"));
    // Select issue type filter ('support')
    const issueTypeSelect = screen.getAllByRole("combobox").find((s) => {
      const options = (s as HTMLSelectElement).options;
      return (
        options &&
        Array.from(options).some((o) => o.textContent?.includes("Support"))
      );
    });
    if (issueTypeSelect)
      fireEvent.change(issueTypeSelect, { target: { value: "support" } });
    // Select assignee filter via mock button
    fireEvent.click(screen.getByTestId("assignee-picker-select-button"));
    // Submit form
    fireEvent.click(screen.getByText("Create"));
    // Verify API was called with correct payload
    await waitFor(
      () => {
        expect(vi.mocked(api.createWebhook)).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );
    const call = vi.mocked(api.createWebhook).mock.calls[0][0];
    expect(call.name).toBe("Support bot assignments");
    expect(call.url).toBe("https://hooks.example.com/webhook");
    expect(call.events).toContain("issue.assigned");
    expect(call.is_active).toBe(true);
    expect(call.filter_issue_type).toBe("support");
    expect(call.filter_assignee_type).toBe("org_unit");
    expect(call.filter_assignee_id).toBe(7);
  });

  it("submits create webhook with null assignee filter when no assignee selected", async () => {
    vi.mocked(api.createWebhook).mockResolvedValue({ id: 3 });
    renderPage();
    await waitFor(() => {
      const h1 = screen.getByText("Webhooks");
      expect(h1).toBeDefined();
    });
    fireEvent.click(screen.getByText("Outbound (Basic Events)"));
    fireEvent.click(screen.getByText("Create Outbound Webhook"));
    await waitFor(
      () => {
        const createBtn = screen.queryByText("Create");
        expect(createBtn).toBeDefined();
      },
      { timeout: 3000 },
    );
    // Fill required fields
    const inputs = screen.getAllByPlaceholderText(
      /Production Webhook|example.com/,
    );
    const nameInput = inputs.find((i) =>
      (i as HTMLInputElement).placeholder.includes("Production"),
    );
    const urlInput = inputs.find((i) =>
      hasExampleHostname((i as HTMLInputElement).placeholder),
    );
    if (nameInput)
      fireEvent.change(nameInput, { target: { value: "General webhook" } });
    if (urlInput) {
      fireEvent.focus(urlInput);
      fireEvent.change(urlInput, {
        target: { value: "https://hooks.example.com/general" },
      });
      fireEvent.keyDown(urlInput, { key: "Enter" });
    }
    // Select issue.assigned event
    fireEvent.click(screen.getByTestId("event-checkbox-issue.assigned"));
    // Do NOT select an assignee filter — leave it empty
    // Submit form
    fireEvent.click(screen.getByText("Create"));
    // Verify API was called with null/undefined assignee filter fields
    await waitFor(
      () => {
        expect(vi.mocked(api.createWebhook)).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );
    const call = vi.mocked(api.createWebhook).mock.calls[0][0];
    expect(call.name).toBe("General webhook");
    expect(call.filter_assignee_type).toBeUndefined();
    expect(call.filter_assignee_id).toBeUndefined();
  });

  it("renders scope badges on inbound tab and creates an inbound webhook with selected scopes", async () => {
    vi.mocked(api.getInboundWebhooks).mockResolvedValue({
      inbound_webhooks: [
        {
          id: 10,
          name: "Alertmanager Receiver",
          slug: "alertmgr-slug",
          receive_path: "/api/v1/webhooks/receive/alertmgr-slug",
          auth_type: "bearer",
          bearer_token_preview: "wh_abc...xyz",
          is_active: true,
          scopes: ["events:log", "issues:create"],
          receive_count: 3,
        },
      ],
      count: 1,
    });
    vi.mocked(api.createInboundWebhook).mockResolvedValue({
      webhook: {
        id: 11,
        name: "PagerDuty Issues",
        slug: "pd-slug",
        receive_path: "/api/v1/webhooks/receive/pd-slug",
        auth_type: "bearer",
        is_active: true,
        organization_id: 1,
        scopes: ["events:log", "issues:create", "issues:update"],
        receive_count: 0,
      },
      credentials: {
        auth_type: "bearer",
        bearer_token: "wh_secret_token_123",
      },
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Alertmanager Receiver")).toBeDefined();
    });
    expect(screen.getByText("events:log")).toBeDefined();
    expect(screen.getByText("issues:create")).toBeDefined();

    fireEvent.click(screen.getByText("Create Inbound Webhook"));
    const form = await screen.findByTestId("create-inbound-webhook-form");
    expect(form).toBeDefined();

    const nameInput = screen.getByPlaceholderText("Alertmanager Receiver");
    fireEvent.change(nameInput, { target: { value: "PagerDuty Issues" } });

    const orgSelect = screen.getByTestId("inbound-organization-select");
    fireEvent.change(orgSelect, { target: { value: "1" } });

    fireEvent.click(screen.getByTestId("inbound-scope-issues:create"));
    fireEvent.click(screen.getByTestId("inbound-scope-issues:update"));

    fireEvent.submit(form);

    await waitFor(() => {
      expect(vi.mocked(api.createInboundWebhook)).toHaveBeenCalled();
    });
    const payload = vi.mocked(api.createInboundWebhook).mock.calls[0][0];
    expect(payload.name).toBe("PagerDuty Issues");
    expect(payload.organization_id).toBe(1);
    expect(payload.scopes).toEqual([
      "events:log",
      "issues:create",
      "issues:update",
    ]);

    // Credentials modal reveals granted scopes & scope-aware cURL payload
    await waitFor(() => {
      expect(
        screen.getByText(/Inbound Webhook Credentials — PagerDuty Issues/),
      ).toBeDefined();
    });
    expect(screen.getByText(/"action":"issues:create"/)).toBeDefined();
  });

  it("auto-opens Create Inbound Webhook modal pre-linked from Playbook Editor soft-link query params", async () => {
    window.history.pushState(
      {},
      "",
      "/webhooks?tab=inbound&create=1&stream_id=42&stream_name=Auto+Triage",
    );
    vi.mocked(api.createInboundWebhook).mockResolvedValue({
      webhook: {
        id: 21,
        name: "Auto Triage Webhook",
        slug: "triage-slug",
        receive_path: "/api/v1/webhooks/receive/triage-slug",
        auth_type: "bearer",
        is_active: true,
        stream_id: 42,
        scopes: ["events:log", "playbooks:trigger"],
        receive_count: 0,
      },
      credentials: {
        auth_type: "bearer",
        bearer_token: "wh_triage_secret",
      },
    });

    renderPage();

    const banner = await screen.findByTestId("playbook-softlink-banner");
    expect(banner.textContent).toContain("Auto Triage");
    expect(banner.textContent).toContain("playbooks:trigger");

    const nameInput = screen.getByDisplayValue(
      "Auto Triage Webhook",
    ) as HTMLInputElement;
    expect(nameInput).toBeDefined();

    const streamSelect = screen.getByTestId(
      "inbound-stream-select",
    ) as HTMLSelectElement;
    expect(streamSelect.value).toBe("42");

    const logBox = screen.getByTestId(
      "inbound-scope-events:log",
    ) as HTMLInputElement;
    const triggerBox = screen.getByTestId(
      "inbound-scope-playbooks:trigger",
    ) as HTMLInputElement;
    expect(logBox.checked).toBe(true);
    expect(triggerBox.checked).toBe(true);

    fireEvent.submit(screen.getByTestId("create-inbound-webhook-form"));
    await waitFor(() => {
      expect(vi.mocked(api.createInboundWebhook)).toHaveBeenCalled();
    });
    const payload = vi.mocked(api.createInboundWebhook).mock.calls[0][0];
    expect(payload.stream_id).toBe(42);
    expect(payload.scopes).toEqual(["events:log", "playbooks:trigger"]);
  });

  it("opens Edit Inbound Webhook modal and saves updated scopes", async () => {
    vi.mocked(api.getInboundWebhooks).mockResolvedValue({
      inbound_webhooks: [
        {
          id: 30,
          name: "Scanner Feed",
          description: "Trivy ingestion",
          slug: "scanner-slug",
          receive_path: "/api/v1/webhooks/receive/scanner-slug",
          auth_type: "bearer",
          bearer_token_preview: "wh_scan...999",
          is_active: true,
          organization_id: 1,
          stream_id: null,
          scopes: ["events:log"],
          receive_count: 0,
        },
      ],
      count: 1,
    });
    vi.mocked(api.updateInboundWebhook).mockResolvedValue({
      webhook: {
        id: 30,
        name: "Scanner Feed",
        slug: "scanner-slug",
        receive_path: "/api/v1/webhooks/receive/scanner-slug",
        auth_type: "bearer",
        is_active: true,
        organization_id: 1,
        stream_id: null,
        scopes: ["events:log", "vulnerabilities:create"],
        receive_count: 0,
      },
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Scanner Feed")).toBeDefined();
    });

    fireEvent.click(screen.getByTitle("Edit Inbound Webhook"));
    const editForm = await screen.findByTestId("edit-inbound-webhook-form");
    expect(editForm).toBeDefined();

    fireEvent.click(screen.getByTestId("inbound-scope-vulnerabilities:create"));
    fireEvent.click(screen.getByTestId("save-inbound-webhook-button"));

    await waitFor(() => {
      expect(vi.mocked(api.updateInboundWebhook)).toHaveBeenCalledWith(
        30,
        expect.objectContaining({
          name: "Scanner Feed",
          organization_id: 1,
          stream_id: null,
          scopes: ["events:log", "vulnerabilities:create"],
        }),
      );
    });
  });

  it("displays event action badge and action_result in InboundEventsModal", async () => {
    vi.mocked(api.getInboundWebhooks).mockResolvedValue({
      inbound_webhooks: [
        {
          id: 40,
          name: "Audit Hook",
          slug: "audit-slug",
          receive_path: "/api/v1/webhooks/receive/audit-slug",
          auth_type: "bearer",
          is_active: true,
          scopes: ["events:log"],
          receive_count: 1,
        },
      ],
      count: 1,
    });
    vi.mocked(api.getInboundWebhookEvents).mockResolvedValue({
      events: [
        {
          id: 501,
          inbound_webhook_id: 40,
          source_ip: "203.0.113.5",
          payload: { action: "issues:create", data: { title: "Test" } },
          action: "issues:create",
          action_result: {
            error: "Webhook lacks required scope: issues:create",
          },
          status: "forbidden",
          received_at: "2026-11-20T12:00:00Z",
        },
      ],
      count: 1,
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Audit Hook")).toBeDefined();
    });
    fireEvent.click(screen.getByTitle("View Events"));
    await waitFor(() => {
      expect(screen.getByText("forbidden")).toBeDefined();
      expect(screen.getByText("issues:create")).toBeDefined();
      expect(
        screen.getByText(/Webhook lacks required scope: issues:create/),
      ).toBeDefined();
    });
  });

  it("renders user.created and admin_user.created event checkboxes in Create & Edit modals and submits them", async () => {
    vi.mocked(api.createWebhook).mockResolvedValue({ id: 4 });
    vi.mocked(api.updateWebhook).mockResolvedValue({ id: 10 });
    vi.mocked(api.getWebhooks).mockResolvedValue({
      webhooks: [
        {
          id: 10,
          name: "User lifecycle hook",
          url: "https://hooks.example.com/users",
          organization_id: 1,
          events: ["user.created"],
          is_active: true,
        },
      ],
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Outbound (Basic Events)")).toBeDefined();
    });
    fireEvent.click(screen.getByText("Outbound (Basic Events)"));

    // Create modal: select both user.created and admin_user.created
    fireEvent.click(screen.getByText("Create Outbound Webhook"));
    await waitFor(() => {
      expect(screen.getByTestId("event-checkbox-user.created")).toBeDefined();
      expect(
        screen.getByTestId("event-checkbox-admin_user.created"),
      ).toBeDefined();
    });
    const inputs = screen.getAllByPlaceholderText(
      /Production Webhook|example.com/,
    );
    const nameInput = inputs.find((i) =>
      (i as HTMLInputElement).placeholder.includes("Production"),
    );
    const urlInput = inputs.find((i) =>
      hasExampleHostname((i as HTMLInputElement).placeholder),
    );
    if (nameInput)
      fireEvent.change(nameInput, {
        target: { value: "User Onboarding Hook" },
      });
    if (urlInput) {
      fireEvent.focus(urlInput);
      fireEvent.change(urlInput, {
        target: { value: "https://hooks.example.com/onboarding" },
      });
      fireEvent.keyDown(urlInput, { key: "Enter" });
    }
    fireEvent.click(screen.getByTestId("event-checkbox-user.created"));
    fireEvent.click(screen.getByTestId("event-checkbox-admin_user.created"));
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => {
      expect(vi.mocked(api.createWebhook)).toHaveBeenCalled();
    });
    const createPayload = vi.mocked(api.createWebhook).mock.calls[0][0];
    expect(createPayload.url).toBe("https://hooks.example.com/onboarding");
    expect(createPayload.events).toEqual([
      "user.created",
      "admin_user.created",
    ]);

    // Edit modal: verify both checkboxes render, user.created is pre-checked, toggle admin_user.created and save
    await waitFor(() => {
      expect(screen.getByText("User lifecycle hook")).toBeDefined();
    });
    fireEvent.click(screen.getByTitle("Edit webhook"));
    await screen.findByTestId("edit-webhook-form");

    const editUserCheckbox = screen.getByTestId(
      "event-checkbox-user.created",
    ) as HTMLInputElement;
    const editAdminCheckbox = screen.getByTestId(
      "event-checkbox-admin_user.created",
    ) as HTMLInputElement;
    expect(editUserCheckbox.checked).toBe(true);
    expect(editAdminCheckbox.checked).toBe(false);

    fireEvent.click(editAdminCheckbox);
    fireEvent.click(screen.getByTestId("save-webhook-button"));

    await waitFor(() => {
      expect(vi.mocked(api.updateWebhook)).toHaveBeenCalled();
    });
    const updateCall = vi.mocked(api.updateWebhook).mock.calls[0];
    expect(updateCall[0]).toBe(10);
    expect(updateCall[1].events).toEqual([
      "user.created",
      "admin_user.created",
    ]);
  });
});
