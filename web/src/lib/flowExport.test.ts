import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  ELDER_EXPORT_VERSION,
  buildFlowSvg,
  downloadJsonFile,
  escapeXml,
  exportFlowAsJpg,
  exportFlowAsPng,
  exportFlowAsSvg,
  parseDiagramBackup,
  parsePlaybookBackup,
  serializeDiagramBackup,
  serializePlaybookBackup,
  slugifyFilename,
  type FlowExportEdge,
  type FlowExportNode,
} from "./flowExport";

const SAMPLE_NODES: FlowExportNode[] = [
  {
    id: "n1",
    type: "cloudProvider",
    position: { x: 100, y: 120 },
    data: {
      label: "AWS <Prod> & Primary",
      provider: "aws",
      color: "#3b82f6",
      resourceName: "us-east-1",
    },
  },
  {
    id: "n2",
    type: "infrastructure",
    position: { x: 420, y: 260 },
    data: {
      label: "Postgres DB",
      type: "database",
      color: "#10b981",
      resourceName: "pg-main",
    },
  },
];

const SAMPLE_EDGES: FlowExportEdge[] = [
  {
    id: "e1",
    source: "n1",
    target: "n2",
    label: "TLS & mTLS",
    animated: true,
    style: { stroke: "#f59e0b", strokeWidth: 2 },
  },
];

describe("flowExport helpers", () => {
  it("escapes XML special characters", () => {
    expect(escapeXml(`A & B <tag attr="1">'ok'</tag>`)).toBe(
      "A &amp; B &lt;tag attr=&quot;1&quot;&gt;&apos;ok&apos;&lt;/tag&gt;",
    );
  });

  it("slugifies filenames with safe fallback", () => {
    expect(slugifyFilename("Production Network (US-East)!")).toBe(
      "production-network-us-east",
    );
    expect(slugifyFilename("   ", "diagram")).toBe("diagram");
    expect(slugifyFilename(undefined, "playbook")).toBe("playbook");
  });
});

describe("buildFlowSvg", () => {
  it("handles an empty canvas gracefully", () => {
    const svg = buildFlowSvg({
      title: "Empty Flow",
      nodes: [],
      edges: [],
    });

    expect(svg).toContain('<svg xmlns="http://www.w3.org/2000/svg"');
    expect(svg).toContain("Empty Flow");
    expect(svg).toContain("Empty canvas");
  });

  it("renders nodes, curved edges, arrowheads, labels, and XML-escaped text", () => {
    const svg = buildFlowSvg({
      title: 'Core "Arch" & Flow',
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
      background: "#ffffff",
    });

    expect(svg).toContain('<svg xmlns="http://www.w3.org/2000/svg"');
    expect(svg).toContain("Core &quot;Arch&quot; &amp; Flow");
    expect(svg).toContain("AWS &lt;Prod&gt; &amp; Primary");
    expect(svg).toContain("Postgres DB");
    expect(svg).toContain("us-east-1");
    expect(svg).toContain("pg-main");
    expect(svg).toContain("<marker id=");
    expect(svg).toContain('marker-end="url(#flow-arrow-f59e0b)"');
    expect(svg).toContain("TLS &amp; mTLS");
    expect(svg).toContain('stroke-dasharray="6 4"');
  });

  it("renders shape (circle, diamond) and text nodes and ignores dangling edges", () => {
    const nodes: FlowExportNode[] = [
      {
        id: "c1",
        type: "shape",
        position: { x: -50, y: -40 },
        data: { label: "Start", shape: "circle", color: "#10b981" },
      },
      {
        id: "d1",
        type: "shape",
        position: { x: 180, y: 40 },
        data: { label: "Check", shape: "diamond", color: "#8b5cf6" },
      },
      {
        id: "t1",
        type: "text",
        position: { x: 400, y: 80 },
        data: { label: "Annotation" },
      },
    ];
    const edges: FlowExportEdge[] = [
      { id: "e-valid", source: "c1", target: "d1" },
      { id: "e-dangling", source: "d1", target: "missing-node" },
    ];

    const svg = buildFlowSvg({ nodes, edges, background: "#0f172a" });
    expect(svg).toContain("<circle");
    expect(svg).toContain("<polygon");
    expect(svg).toContain("Annotation");
    expect(svg).toContain('fill="#0f172a"');
  });
});

describe("browser file exports (SVG, PNG, JPG, JSON)", () => {
  let createObjectURLMock: ReturnType<typeof vi.fn>;
  let revokeObjectURLMock: ReturnType<typeof vi.fn>;
  let anchorClicks: Array<{ download: string; href: string }>;
  const originalCreateElement = document.createElement.bind(document);
  const originalImage = globalThis.Image;

  beforeEach(() => {
    anchorClicks = [];
    createObjectURLMock = vi.fn(() => "blob:mock-url");
    revokeObjectURLMock = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      value: createObjectURLMock,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: revokeObjectURLMock,
      configurable: true,
      writable: true,
    });

    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
      const el = originalCreateElement(tag);
      if (tag === "a") {
        vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {
          anchorClicks.push({
            download: (el as HTMLAnchorElement).download,
            href: (el as HTMLAnchorElement).href,
          });
        });
      }
      if (tag === "canvas") {
        const ctx = {
          scale: vi.fn(),
          fillStyle: "",
          fillRect: vi.fn(),
          drawImage: vi.fn(),
        };
        vi.spyOn(el as HTMLCanvasElement, "getContext").mockReturnValue(
          ctx as unknown as CanvasRenderingContext2D,
        );
        vi.spyOn(el as HTMLCanvasElement, "toBlob").mockImplementation(
          (cb, mimeType, quality) => {
            cb(
              new Blob([`mock-${mimeType}-${quality ?? ""}`], {
                type: mimeType || "image/png",
              }),
            );
          },
        );
      }
      return el;
    }) as typeof document.createElement);

    class MockImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_val: string) {
        queueMicrotask(() => {
          this.onload?.();
        });
      }
    }
    globalThis.Image = MockImage as unknown as typeof Image;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    globalThis.Image = originalImage;
  });

  it("exports SVG and triggers a .svg download", () => {
    const svg = exportFlowAsSvg({
      title: "My Infra Diagram",
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    });

    expect(svg).toContain("<svg");
    expect(anchorClicks).toHaveLength(1);
    expect(anchorClicks[0].download).toBe("my-infra-diagram.svg");
    expect(revokeObjectURLMock).toHaveBeenCalledWith("blob:mock-url");
  });

  it("exports 2x-DPI PNG with white background and downloads .png", async () => {
    await exportFlowAsPng({
      title: "Incident Playbook",
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    });

    expect(anchorClicks).toHaveLength(1);
    expect(anchorClicks[0].download).toBe("incident-playbook.png");
  });

  it("exports 2x-DPI JPG with white background and quality 0.95 and downloads .jpg", async () => {
    await exportFlowAsJpg({
      title: "Incident Playbook",
      nodes: SAMPLE_NODES,
      edges: SAMPLE_EDGES,
    });

    expect(anchorClicks).toHaveLength(1);
    expect(anchorClicks[0].download).toBe("incident-playbook.jpg");
  });

  it("downloads JSON payload as .json", () => {
    downloadJsonFile({ hello: "world" }, "Shared Backup");
    expect(anchorClicks).toHaveLength(1);
    expect(anchorClicks[0].download).toBe("shared-backup.json");
  });
});

describe("diagram JSON backup serialize & parse", () => {
  it("round-trips a diagram backup and strips tenant/DB/ephemeral fields", () => {
    const dirtyNodes: FlowExportNode[] = [
      {
        id: "1",
        type: "cloudProvider",
        position: { x: 10, y: 20 },
        selected: true,
        dragging: false,
        measured: { width: 180, height: 70 },
        tenant_id: 99,
        owner_identity_id: 7,
        data: {
          label: "API Gateway",
          provider: "aws",
          color: "#3b82f6",
          resourceId: 42,
          resourceName: "gw-prod",
          validationErrors: ["temp"],
        },
      },
    ];
    const dirtyEdges: FlowExportEdge[] = [
      {
        id: "e1",
        source: "1",
        target: "1",
        selected: true,
        tenant_id: 99,
      },
    ];

    const serialized = serializeDiagramBackup({
      name: "Core Topology",
      description: "Production VPC layout",
      tags: ["prod", "network"],
      viewport: { x: 10, y: 20, zoom: 1.2 },
      nodes: dirtyNodes,
      edges: dirtyEdges,
    });

    expect(serialized.elder_export_version).toBe(ELDER_EXPORT_VERSION);
    expect(serialized.kind).toBe("diagram");
    expect(serialized.nodes[0]).not.toHaveProperty("tenant_id");
    expect(serialized.nodes[0]).not.toHaveProperty("selected");
    expect(serialized.nodes[0]).not.toHaveProperty("measured");
    expect(serialized.nodes[0].data).not.toHaveProperty("resourceId");
    expect(serialized.nodes[0].data).not.toHaveProperty("validationErrors");
    expect(serialized.edges[0]).not.toHaveProperty("tenant_id");
    expect(serialized.edges[0]).not.toHaveProperty("selected");

    const parsed = parseDiagramBackup(JSON.stringify(serialized));
    expect(parsed.name).toBe("Core Topology");
    expect(parsed.description).toBe("Production VPC layout");
    expect(parsed.tags).toEqual(["prod", "network"]);
    expect(parsed.viewport).toEqual({ x: 10, y: 20, zoom: 1.2 });
    expect(parsed.nodes).toHaveLength(1);
    expect(parsed.edges).toHaveLength(1);
  });

  it("rejects malformed JSON, wrong version, wrong kind, and invalid arrays", () => {
    expect(() => parseDiagramBackup("")).toThrow(/empty/i);
    expect(() => parseDiagramBackup("{not-json")).toThrow(/Invalid JSON/i);
    expect(() => parseDiagramBackup("[]")).toThrow(/root value must be a JSON object/i);
    expect(() =>
      parseDiagramBackup(
        JSON.stringify({ elder_export_version: 99, kind: "diagram", nodes: [], edges: [] }),
      ),
    ).toThrow(/elder_export_version/i);

    const playbookJson = JSON.stringify(
      serializePlaybookBackup({
        name: "My Playbook",
        nodes: [],
        edges: [],
      }),
    );
    expect(() => parseDiagramBackup(playbookJson)).toThrow(
      /expected a diagram backup, but received a playbook backup/i,
    );

    expect(() =>
      parseDiagramBackup(
        JSON.stringify({ elder_export_version: 1, kind: "diagram", nodes: "bad", edges: [] }),
      ),
    ).toThrow(/"nodes" must be an array/i);

    expect(() =>
      parseDiagramBackup(
        JSON.stringify({
          elder_export_version: 1,
          kind: "diagram",
          nodes: [{ id: "1" }],
          edges: [],
        }),
      ),
    ).toThrow(/position\.x/i);

    expect(() =>
      parseDiagramBackup(
        JSON.stringify({
          elder_export_version: 1,
          kind: "diagram",
          nodes: [],
          edges: [{ id: "e1", source: "1" }],
        }),
      ),
    ).toThrow(/target/i);
  });
});

describe("playbook JSON backup serialize & parse", () => {
  it("round-trips a playbook backup including trigger metadata", () => {
    const serialized = serializePlaybookBackup({
      name: "Auto Remediate Alert",
      description: "Creates issue and pages on-call",
      trigger_type: "webhook",
      trigger_config: { path: "/hook/alert" },
      is_active: true,
      nodes: [
        {
          id: "n1",
          type: "stream",
          position: { x: 50, y: 80 },
          data: {
            label: "Webhook Trigger",
            nodeType: "trigger_webhook",
            category: "triggers",
            validationErrors: ["stale"],
          },
        },
      ],
      edges: [],
    });

    expect(serialized.elder_export_version).toBe(ELDER_EXPORT_VERSION);
    expect(serialized.kind).toBe("playbook");
    expect(serialized.trigger_type).toBe("webhook");
    expect(serialized.trigger_config).toEqual({ path: "/hook/alert" });
    expect(serialized.is_active).toBe(true);
    expect(serialized.nodes[0].data).not.toHaveProperty("validationErrors");

    const parsed = parsePlaybookBackup(JSON.stringify(serialized));
    expect(parsed.name).toBe("Auto Remediate Alert");
    expect(parsed.description).toBe("Creates issue and pages on-call");
    expect(parsed.trigger_type).toBe("webhook");
    expect(parsed.trigger_config).toEqual({ path: "/hook/alert" });
    expect(parsed.is_active).toBe(true);
    expect(parsed.nodes).toHaveLength(1);
  });

  it("rejects a diagram backup passed to parsePlaybookBackup", () => {
    const diagramJson = JSON.stringify(
      serializeDiagramBackup({
        name: "My Diagram",
        nodes: [],
        edges: [],
      }),
    );

    expect(() => parsePlaybookBackup(diagramJson)).toThrow(
      /expected a playbook backup, but received a diagram backup/i,
    );
  });
});
