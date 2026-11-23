/**
 * Pure-browser SVG/PNG/JPG export and versioned JSON backup/import utilities
 * for Elder Diagrams and Playbooks (zero external dependencies).
 */

export const ELDER_EXPORT_VERSION = 1 as const;

export interface FlowPosition {
  x: number;
  y: number;
}

export interface FlowViewport {
  x: number;
  y: number;
  zoom: number;
}

export interface FlowExportNode {
  id: string;
  type?: string;
  position: FlowPosition;
  width?: number;
  height?: number;
  measured?: { width?: number; height?: number };
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface FlowExportEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  label?: string | unknown;
  animated?: boolean;
  style?: Record<string, unknown>;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface BuildFlowSvgOptions {
  title?: string;
  nodes: FlowExportNode[];
  edges: FlowExportEdge[];
  background?: string;
  padding?: number;
}

export interface ExportImageOptions extends BuildFlowSvgOptions {
  filename?: string;
  scale?: number;
  quality?: number;
}

export interface DiagramBackup {
  elder_export_version: typeof ELDER_EXPORT_VERSION;
  kind: "diagram";
  exported_at: string;
  name: string;
  description?: string;
  tags?: string[];
  viewport?: FlowViewport;
  nodes: FlowExportNode[];
  edges: FlowExportEdge[];
}

export interface PlaybookBackup {
  elder_export_version: typeof ELDER_EXPORT_VERSION;
  kind: "playbook";
  exported_at: string;
  name: string;
  description?: string;
  trigger_type?: string;
  trigger_config?: Record<string, unknown>;
  is_active?: boolean;
  nodes: FlowExportNode[];
  edges: FlowExportEdge[];
}

export interface DiagramBackupInput {
  name?: string;
  description?: string;
  tags?: string[];
  viewport?: FlowViewport;
  nodes: FlowExportNode[];
  edges: FlowExportEdge[];
}

export interface PlaybookBackupInput {
  name?: string;
  description?: string;
  trigger_type?: string;
  trigger_config?: Record<string, unknown>;
  is_active?: boolean;
  nodes: FlowExportNode[];
  edges: FlowExportEdge[];
}

const DEFAULT_NODE_WIDTH = 200;
const DEFAULT_NODE_HEIGHT = 80;
const CIRCLE_SIZE = 96;
const DIAMOND_SIZE = 104;
const TEXT_NODE_HEIGHT = 40;
const EMPTY_CANVAS_WIDTH = 640;
const EMPTY_CANVAS_HEIGHT = 360;

const STRIPPED_NODE_KEYS = new Set([
  "selected",
  "dragging",
  "resizing",
  "measured",
  "tenant_id",
  "owner_identity_id",
  "village_id",
]);

const STRIPPED_NODE_DATA_KEYS = new Set([
  "validationErrors",
  "tenant_id",
  "owner_identity_id",
  "village_id",
  "resourceId",
]);

const STRIPPED_EDGE_KEYS = new Set([
  "selected",
  "tenant_id",
  "owner_identity_id",
  "village_id",
]);

const CATEGORY_ACCENT_COLORS: Record<string, string> = {
  triggers: "#10b981",
  trigger: "#10b981",
  actions: "#f97316",
  action: "#f97316",
  conditions: "#8b5cf6",
  condition: "#8b5cf6",
  logic: "#8b5cf6",
  transforms: "#06b6d4",
  transform: "#06b6d4",
  notifications: "#ec4899",
  cloudprovider: "#3b82f6",
  infrastructure: "#06b6d4",
  shape: "#f59e0b",
  stream: "#f59e0b",
  text: "#64748b",
};

/**
 * Escapes XML special characters so arbitrary user labels never break SVG markup.
 */
export function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * Converts a display title into a safe lowercase filename slug.
 */
export function slugifyFilename(name?: string, fallback = "flow"): string {
  const cleaned = (name || "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned || fallback;
}

function truncateText(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(1, maxChars - 1))}…`;
}

function isSafeColor(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (!trimmed || trimmed.toLowerCase() === "transparent") return false;
  return /^#[0-9a-fA-F]{3,8}$|^rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)$|^hsl\(\s*\d+\s*,\s*\d+%\s*,\s*\d+%\s*\)$/.test(
    trimmed,
  );
}

function resolveNodeAccent(node: FlowExportNode): string {
  const data = node.data || {};
  if (isSafeColor(data.color)) {
    return data.color.trim();
  }
  const category =
    typeof data.category === "string" ? data.category.toLowerCase() : "";
  if (category && CATEGORY_ACCENT_COLORS[category]) {
    return CATEGORY_ACCENT_COLORS[category];
  }
  const stage = typeof data.stage === "string" ? data.stage.toLowerCase() : "";
  if (stage && CATEGORY_ACCENT_COLORS[stage]) {
    return CATEGORY_ACCENT_COLORS[stage];
  }
  const nodeType = (node.type || "").toLowerCase();
  if (nodeType && CATEGORY_ACCENT_COLORS[nodeType]) {
    return CATEGORY_ACCENT_COLORS[nodeType];
  }
  return "#3b82f6";
}

function resolveNodeDimensions(node: FlowExportNode): {
  width: number;
  height: number;
} {
  const measuredW = node.measured?.width ?? node.width;
  const measuredH = node.measured?.height ?? node.height;
  if (
    typeof measuredW === "number" &&
    measuredW > 0 &&
    typeof measuredH === "number" &&
    measuredH > 0
  ) {
    return { width: measuredW, height: measuredH };
  }
  if (node.type === "shape") {
    const shape =
      typeof node.data?.shape === "string" ? node.data.shape : "rectangle";
    if (shape === "circle") {
      return { width: CIRCLE_SIZE, height: CIRCLE_SIZE };
    }
    if (shape === "diamond") {
      return { width: DIAMOND_SIZE, height: DIAMOND_SIZE };
    }
  }
  if (node.type === "text") {
    return { width: 160, height: TEXT_NODE_HEIGHT };
  }
  return { width: DEFAULT_NODE_WIDTH, height: DEFAULT_NODE_HEIGHT };
}

function resolveNodeTitle(node: FlowExportNode): string {
  const label = node.data?.label;
  if (typeof label === "string" && label.trim()) {
    return label.trim();
  }
  return node.id || "Node";
}

function resolveNodeSubtitle(node: FlowExportNode): string | null {
  const data = node.data || {};
  const candidates = [
    data.resourceName,
    data.moduleLabel,
    data.category,
    data.nodeType,
    data.provider,
    data.type,
    node.type && node.type !== "stream" ? node.type : undefined,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return null;
}

function isDarkBackground(bg: string): boolean {
  const hex = bg.trim().toLowerCase();
  if (!/^#[0-9a-f]{6}$/.test(hex)) return false;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance < 0.5;
}

function sanitizeMarkerId(color: string): string {
  return `flow-arrow-${color.replace(/[^a-zA-Z0-9]/g, "")}`;
}

/**
 * Builds a crisp, standalone, XML-escaped vector SVG covering the full bounding
 * box of all nodes and edges, with rounded cards, accent headers, curved edges,
 * arrowheads, and optional edge labels.
 */
export function buildFlowSvg({
  title,
  nodes,
  edges,
  background = "#ffffff",
  padding = 60,
}: BuildFlowSvgOptions): string {
  const safeBg = isSafeColor(background) ? background : "#ffffff";
  const darkBg = isDarkBackground(safeBg);
  const cardFill = darkBg ? "#1e293b" : "#ffffff";
  const cardBorder = darkBg ? "#334155" : "#cbd5e1";
  const primaryText = darkBg ? "#f8fafc" : "#0f172a";
  const mutedText = darkBg ? "#94a3b8" : "#475569";
  const badgeBg = darkBg ? "#0f172a" : "#f1f5f9";
  const defaultEdgeStroke = darkBg ? "#f59e0b" : "#64748b";
  const titleOffset = title && title.trim() ? 44 : 0;

  if (!nodes || nodes.length === 0) {
    const titleSvg =
      title && title.trim()
        ? `<text x="32" y="42" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="20" font-weight="700" fill="${primaryText}">${escapeXml(title.trim())}</text>`
        : "";
    return [
      `<svg xmlns="http://www.w3.org/2000/svg" width="${EMPTY_CANVAS_WIDTH}" height="${EMPTY_CANVAS_HEIGHT}" viewBox="0 0 ${EMPTY_CANVAS_WIDTH} ${EMPTY_CANVAS_HEIGHT}">`,
      `  <rect width="100%" height="100%" fill="${safeBg}" />`,
      titleSvg ? `  ${titleSvg}` : "",
      `  <text x="${EMPTY_CANVAS_WIDTH / 2}" y="${EMPTY_CANVAS_HEIGHT / 2}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="15" fill="${mutedText}">Empty canvas</text>`,
      `</svg>`,
    ]
      .filter(Boolean)
      .join("\n");
  }

  interface LayoutRect {
    node: FlowExportNode;
    x: number;
    y: number;
    width: number;
    height: number;
    cx: number;
    cy: number;
  }

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  const rawRects = nodes.map((node) => {
    const x = Number.isFinite(node.position?.x) ? node.position.x : 0;
    const y = Number.isFinite(node.position?.y) ? node.position.y : 0;
    const { width, height } = resolveNodeDimensions(node);
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + width);
    maxY = Math.max(maxY, y + height);
    return { node, x, y, width, height };
  });

  const contentWidth = Math.max(maxX - minX, DEFAULT_NODE_WIDTH);
  const contentHeight = Math.max(maxY - minY, DEFAULT_NODE_HEIGHT);
  const svgWidth = Math.ceil(contentWidth + padding * 2);
  const svgHeight = Math.ceil(contentHeight + padding * 2 + titleOffset);
  const offsetX = padding - minX;
  const offsetY = padding + titleOffset - minY;

  const rectById = new Map<string, LayoutRect>();
  const layoutRects: LayoutRect[] = rawRects.map((r) => {
    const x = r.x + offsetX;
    const y = r.y + offsetY;
    const rect: LayoutRect = {
      node: r.node,
      x,
      y,
      width: r.width,
      height: r.height,
      cx: x + r.width / 2,
      cy: y + r.height / 2,
    };
    rectById.set(r.node.id, rect);
    return rect;
  });

  // Collect unique edge colors for SVG arrowhead markers
  const markerColors = new Set<string>([defaultEdgeStroke]);
  for (const edge of edges || []) {
    const stroke = edge.style?.stroke;
    if (isSafeColor(stroke)) {
      markerColors.add(stroke.trim());
    }
  }

  const markerDefs = Array.from(markerColors)
    .map((color) => {
      const id = sanitizeMarkerId(color);
      return [
        `    <marker id="${id}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">`,
        `      <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="${color}" />`,
        `    </marker>`,
      ].join("\n");
    })
    .join("\n");

  const edgeElements: string[] = [];
  for (const edge of edges || []) {
    const src = rectById.get(edge.source);
    const tgt = rectById.get(edge.target);
    if (!src || !tgt) continue;

    const strokeColor = isSafeColor(edge.style?.stroke)
      ? edge.style.stroke.trim()
      : defaultEdgeStroke;
    const strokeWidth =
      typeof edge.style?.strokeWidth === "number" && edge.style.strokeWidth > 0
        ? edge.style.strokeWidth
        : 2;
    const dashArray =
      typeof edge.style?.strokeDasharray === "string"
        ? edge.style.strokeDasharray
        : edge.animated
          ? "6 4"
          : "";

    const dx = tgt.cx - src.cx;
    const dy = tgt.cy - src.cy;
    const horizontal = Math.abs(dx) >= Math.abs(dy);

    let x1: number;
    let y1: number;
    let x2: number;
    let y2: number;
    let cx1: number;
    let cy1: number;
    let cx2: number;
    let cy2: number;

    if (horizontal) {
      const dir = dx >= 0 ? 1 : -1;
      x1 = src.cx + (src.width / 2) * dir;
      y1 = src.cy;
      x2 = tgt.cx - (tgt.width / 2) * dir;
      y2 = tgt.cy;
      const curve = Math.max(Math.abs(x2 - x1) * 0.45, 40);
      cx1 = x1 + curve * dir;
      cy1 = y1;
      cx2 = x2 - curve * dir;
      cy2 = y2;
    } else {
      const dir = dy >= 0 ? 1 : -1;
      x1 = src.cx;
      y1 = src.cy + (src.height / 2) * dir;
      x2 = tgt.cx;
      y2 = tgt.cy - (tgt.height / 2) * dir;
      const curve = Math.max(Math.abs(y2 - y1) * 0.45, 40);
      cx1 = x1;
      cy1 = y1 + curve * dir;
      cx2 = x2;
      cy2 = y2 - curve * dir;
    }

    const markerId = sanitizeMarkerId(strokeColor);
    const dashAttr = dashArray
      ? ` stroke-dasharray="${escapeXml(dashArray)}"`
      : "";

    edgeElements.push(
      `  <path d="M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${cx1.toFixed(1)} ${cy1.toFixed(1)}, ${cx2.toFixed(1)} ${cy2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}" fill="none" stroke="${strokeColor}" stroke-width="${strokeWidth}"${dashAttr} marker-end="url(#${markerId})" />`,
    );

    const rawLabel =
      typeof edge.label === "string"
        ? edge.label.trim()
        : typeof edge.data?.label === "string"
          ? edge.data.label.trim()
          : "";
    if (rawLabel) {
      const mx = (x1 + 3 * cx1 + 3 * cx2 + x2) / 8;
      const my = (y1 + 3 * cy1 + 3 * cy2 + y2) / 8;
      const clipped = truncateText(rawLabel, 28);
      const pillW = Math.max(44, clipped.length * 7 + 16);
      const pillH = 22;
      edgeElements.push(
        `  <g>`,
        `    <rect x="${(mx - pillW / 2).toFixed(1)}" y="${(my - pillH / 2).toFixed(1)}" width="${pillW}" height="${pillH}" rx="11" fill="${cardFill}" stroke="${strokeColor}" stroke-width="1" />`,
        `    <text x="${mx.toFixed(1)}" y="${(my + 4).toFixed(1)}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="11" font-weight="600" fill="${primaryText}">${escapeXml(clipped)}</text>`,
        `  </g>`,
      );
    }
  }

  const nodeElements: string[] = [];
  for (const rect of layoutRects) {
    const { node, x, y, width, height, cx, cy } = rect;
    const accent = resolveNodeAccent(node);
    const nodeTitle = truncateText(resolveNodeTitle(node), 26);
    const subtitle = resolveNodeSubtitle(node);
    const clippedSub = subtitle ? truncateText(subtitle, 24) : null;

    if (node.type === "text") {
      nodeElements.push(
        `  <g>`,
        `    <text x="${cx.toFixed(1)}" y="${(cy + 5).toFixed(1)}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="14" font-weight="600" fill="${primaryText}">${escapeXml(nodeTitle)}</text>`,
        `  </g>`,
      );
      continue;
    }

    if (node.type === "shape" && node.data?.shape === "circle") {
      const r = Math.min(width, height) / 2;
      nodeElements.push(
        `  <g filter="url(#flow-card-shadow)">`,
        `    <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="${accent}" stroke="${cardBorder}" stroke-width="1.5" />`,
        `    <text x="${cx.toFixed(1)}" y="${(cy + 4).toFixed(1)}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="12" font-weight="600" fill="#ffffff">${escapeXml(truncateText(resolveNodeTitle(node), 14))}</text>`,
        `  </g>`,
      );
      continue;
    }

    if (node.type === "shape" && node.data?.shape === "diamond") {
      const pts = `${cx.toFixed(1)},${y.toFixed(1)} ${(x + width).toFixed(1)},${cy.toFixed(1)} ${cx.toFixed(1)},${(y + height).toFixed(1)} ${x.toFixed(1)},${cy.toFixed(1)}`;
      nodeElements.push(
        `  <g filter="url(#flow-card-shadow)">`,
        `    <polygon points="${pts}" fill="${accent}" stroke="${cardBorder}" stroke-width="1.5" />`,
        `    <text x="${cx.toFixed(1)}" y="${(cy + 4).toFixed(1)}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="12" font-weight="600" fill="#ffffff">${escapeXml(truncateText(resolveNodeTitle(node), 14))}</text>`,
        `  </g>`,
      );
      continue;
    }

    // Standard rounded card with accent top stripe, title, and optional subtitle badge
    const radius = 10;
    const headerH = 8;
    const titleY = clippedSub ? y + 36 : cy + 5;
    const parts = [
      `  <g filter="url(#flow-card-shadow)">`,
      `    <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${width.toFixed(1)}" height="${height.toFixed(1)}" rx="${radius}" fill="${cardFill}" stroke="${accent}" stroke-width="2" />`,
      `    <path d="M ${(x + radius).toFixed(1)} ${y.toFixed(1)} H ${(x + width - radius).toFixed(1)} Q ${(x + width).toFixed(1)} ${y.toFixed(1)} ${(x + width).toFixed(1)} ${(y + headerH).toFixed(1)} V ${(y + headerH).toFixed(1)} H ${x.toFixed(1)} V ${(y + headerH).toFixed(1)} Q ${x.toFixed(1)} ${y.toFixed(1)} ${(x + radius).toFixed(1)} ${y.toFixed(1)} Z" fill="${accent}" />`,
      `    <text x="${cx.toFixed(1)}" y="${titleY.toFixed(1)}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="13" font-weight="700" fill="${primaryText}">${escapeXml(nodeTitle)}</text>`,
    ];
    if (clippedSub) {
      const badgeW = Math.min(width - 20, Math.max(54, clippedSub.length * 6.5 + 16));
      const badgeX = cx - badgeW / 2;
      const badgeY = y + 46;
      parts.push(
        `    <rect x="${badgeX.toFixed(1)}" y="${badgeY.toFixed(1)}" width="${badgeW.toFixed(1)}" height="20" rx="10" fill="${badgeBg}" />`,
        `    <text x="${cx.toFixed(1)}" y="${(badgeY + 13.5).toFixed(1)}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="10.5" font-weight="500" fill="${mutedText}">${escapeXml(clippedSub)}</text>`,
      );
    }
    parts.push(`  </g>`);
    nodeElements.push(parts.join("\n"));
  }

  const titleElement =
    title && title.trim()
      ? `  <text x="${padding}" y="${Math.round(padding * 0.65)}" font-family="Inter, system-ui, -apple-system, sans-serif" font-size="20" font-weight="700" fill="${primaryText}">${escapeXml(title.trim())}</text>`
      : "";

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${svgWidth}" height="${svgHeight}" viewBox="0 0 ${svgWidth} ${svgHeight}">`,
    `  <defs>`,
    `    <filter id="flow-card-shadow" x="-10%" y="-10%" width="120%" height="125%">`,
    `      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.12" />`,
    `    </filter>`,
    markerDefs,
    `  </defs>`,
    `  <rect width="100%" height="100%" fill="${safeBg}" />`,
    titleElement,
    ...edgeElements,
    ...nodeElements,
    `</svg>`,
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * Triggers a browser download for a Blob with the specified filename.
 */
export function triggerFileDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Exports a flow canvas as a standalone `.svg` file download and returns the SVG string.
 */
export function exportFlowAsSvg(options: ExportImageOptions): string {
  const svg = buildFlowSvg(options);
  const slug = slugifyFilename(options.filename || options.title, "flow");
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  triggerFileDownload(blob, `${slug}.svg`);
  return svg;
}

function parseSvgDimensions(svg: string): { width: number; height: number } {
  const widthMatch = svg.match(/\bwidth="(\d+(?:\.\d+)?)"/);
  const heightMatch = svg.match(/\bheight="(\d+(?:\.\d+)?)"/);
  return {
    width: widthMatch ? Math.ceil(Number(widthMatch[1])) : EMPTY_CANVAS_WIDTH,
    height: heightMatch
      ? Math.ceil(Number(heightMatch[1]))
      : EMPTY_CANVAS_HEIGHT,
  };
}

async function rasterizeSvgToBlob(
  svg: string,
  mimeType: "image/png" | "image/jpeg",
  scale = 2,
  background = "#ffffff",
  quality?: number,
): Promise<Blob> {
  const { width, height } = parseSvgDimensions(svg);
  const svgBlob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const svgUrl = URL.createObjectURL(svgBlob);

  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () =>
        reject(new Error("Failed to render SVG image for export."));
      image.src = svgUrl;
    });

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("Canvas 2D context is unavailable in this browser.");
    }
    ctx.scale(scale, scale);
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);

    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, mimeType, quality);
    });
    if (!blob) {
      throw new Error(`Failed to encode canvas as ${mimeType}.`);
    }
    return blob;
  } finally {
    URL.revokeObjectURL(svgUrl);
  }
}

/**
 * Exports a flow canvas as a 2x-DPI PNG with a white background.
 */
export async function exportFlowAsPng(
  options: ExportImageOptions,
): Promise<void> {
  const background = options.background ?? "#ffffff";
  const svg = buildFlowSvg({ ...options, background });
  const blob = await rasterizeSvgToBlob(
    svg,
    "image/png",
    options.scale ?? 2,
    background,
  );
  const slug = slugifyFilename(options.filename || options.title, "flow");
  triggerFileDownload(blob, `${slug}.png`);
}

/**
 * Exports a flow canvas as a 2x-DPI JPG with a white background (default quality 0.95).
 */
export async function exportFlowAsJpg(
  options: ExportImageOptions,
): Promise<void> {
  const background = options.background ?? "#ffffff";
  const svg = buildFlowSvg({ ...options, background });
  const blob = await rasterizeSvgToBlob(
    svg,
    "image/jpeg",
    options.scale ?? 2,
    background,
    options.quality ?? 0.95,
  );
  const slug = slugifyFilename(options.filename || options.title, "flow");
  triggerFileDownload(blob, `${slug}.jpg`);
}

/**
 * Downloads a serializable JSON payload as `<slug>.json`.
 */
export function downloadJsonFile(payload: unknown, filename: string): void {
  const slug = slugifyFilename(filename, "backup");
  const json = JSON.stringify(payload, null, 2);
  const blob = new Blob([json], { type: "application/json;charset=utf-8" });
  triggerFileDownload(blob, `${slug}.json`);
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function sanitizeNodeForExport(node: FlowExportNode): FlowExportNode {
  const cloned = deepClone(node);
  const cleanNode: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(cloned)) {
    if (STRIPPED_NODE_KEYS.has(key)) continue;
    cleanNode[key] = val;
  }
  if (cloned.data && typeof cloned.data === "object") {
    const cleanData: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(cloned.data)) {
      if (STRIPPED_NODE_DATA_KEYS.has(key)) continue;
      cleanData[key] = val;
    }
    cleanNode.data = cleanData;
  }
  return cleanNode as FlowExportNode;
}

function sanitizeEdgeForExport(edge: FlowExportEdge): FlowExportEdge {
  const cloned = deepClone(edge);
  const cleanEdge: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(cloned)) {
    if (STRIPPED_EDGE_KEYS.has(key)) continue;
    cleanEdge[key] = val;
  }
  return cleanEdge as FlowExportEdge;
}

/**
 * Serializes a diagram into the versioned, tenant-free shareable JSON backup format.
 */
export function serializeDiagramBackup(
  input: DiagramBackupInput,
): DiagramBackup {
  const payload: DiagramBackup = {
    elder_export_version: ELDER_EXPORT_VERSION,
    kind: "diagram",
    exported_at: new Date().toISOString(),
    name: (input.name || "Untitled Diagram").trim() || "Untitled Diagram",
    nodes: (input.nodes || []).map(sanitizeNodeForExport),
    edges: (input.edges || []).map(sanitizeEdgeForExport),
  };
  if (input.description !== undefined) {
    payload.description = input.description;
  }
  if (Array.isArray(input.tags) && input.tags.length > 0) {
    payload.tags = [...input.tags];
  }
  if (input.viewport) {
    payload.viewport = { ...input.viewport };
  }
  return payload;
}

/**
 * Serializes a playbook into the versioned, tenant-free shareable JSON backup format.
 */
export function serializePlaybookBackup(
  input: PlaybookBackupInput,
): PlaybookBackup {
  const payload: PlaybookBackup = {
    elder_export_version: ELDER_EXPORT_VERSION,
    kind: "playbook",
    exported_at: new Date().toISOString(),
    name: (input.name || "Untitled Playbook").trim() || "Untitled Playbook",
    nodes: (input.nodes || []).map(sanitizeNodeForExport),
    edges: (input.edges || []).map(sanitizeEdgeForExport),
  };
  if (input.description !== undefined) {
    payload.description = input.description;
  }
  if (input.trigger_type !== undefined) {
    payload.trigger_type = input.trigger_type;
  }
  if (input.trigger_config !== undefined) {
    payload.trigger_config = deepClone(input.trigger_config);
  }
  if (input.is_active !== undefined) {
    payload.is_active = Boolean(input.is_active);
  }
  return payload;
}

function parseCommonBackupObject(
  raw: string,
  expectedKind: "diagram" | "playbook",
): Record<string, unknown> {
  if (typeof raw !== "string" || !raw.trim()) {
    throw new Error("Backup file is empty.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("Invalid JSON file: unable to parse backup content.");
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Invalid backup format: root value must be a JSON object.");
  }

  const record = parsed as Record<string, unknown>;

  if (record.elder_export_version !== ELDER_EXPORT_VERSION) {
    throw new Error(
      `Unsupported or missing backup version (expected elder_export_version: ${ELDER_EXPORT_VERSION}).`,
    );
  }

  if (record.kind !== expectedKind) {
    if (record.kind === "diagram" || record.kind === "playbook") {
      throw new Error(
        `Wrong backup type: expected a ${expectedKind} backup, but received a ${record.kind} backup.`,
      );
    }
    throw new Error(
      `Invalid backup kind: expected "${expectedKind}".`,
    );
  }

  if (!Array.isArray(record.nodes)) {
    throw new Error('Invalid backup format: "nodes" must be an array.');
  }

  if (!Array.isArray(record.edges)) {
    throw new Error('Invalid backup format: "edges" must be an array.');
  }

  for (let i = 0; i < record.nodes.length; i += 1) {
    const node = record.nodes[i];
    if (!node || typeof node !== "object" || Array.isArray(node)) {
      throw new Error(`Invalid node at index ${i}: expected an object.`);
    }
    const n = node as Record<string, unknown>;
    if (typeof n.id !== "string" || !n.id.trim()) {
      throw new Error(`Invalid node at index ${i}: missing string "id".`);
    }
    if (
      !n.position ||
      typeof n.position !== "object" ||
      typeof (n.position as Record<string, unknown>).x !== "number" ||
      typeof (n.position as Record<string, unknown>).y !== "number"
    ) {
      throw new Error(
        `Invalid node "${n.id}": missing numeric "position.x" and "position.y".`,
      );
    }
  }

  for (let i = 0; i < record.edges.length; i += 1) {
    const edge = record.edges[i];
    if (!edge || typeof edge !== "object" || Array.isArray(edge)) {
      throw new Error(`Invalid edge at index ${i}: expected an object.`);
    }
    const e = edge as Record<string, unknown>;
    if (typeof e.source !== "string" || !e.source.trim()) {
      throw new Error(`Invalid edge at index ${i}: missing string "source".`);
    }
    if (typeof e.target !== "string" || !e.target.trim()) {
      throw new Error(`Invalid edge at index ${i}: missing string "target".`);
    }
  }

  return record;
}

/**
 * Strictly validates and parses a diagram JSON backup string.
 */
export function parseDiagramBackup(raw: string): DiagramBackup {
  const record = parseCommonBackupObject(raw, "diagram");

  const name =
    typeof record.name === "string" && record.name.trim()
      ? record.name.trim()
      : "Imported Diagram";

  const nodes = (record.nodes as FlowExportNode[]).map(sanitizeNodeForExport);
  const edges = (record.edges as FlowExportEdge[]).map((edge, idx) =>
    sanitizeEdgeForExport({
      ...edge,
      id:
        typeof edge.id === "string" && edge.id.trim()
          ? edge.id
          : `edge-${idx + 1}`,
    }),
  );

  const result: DiagramBackup = {
    elder_export_version: ELDER_EXPORT_VERSION,
    kind: "diagram",
    exported_at:
      typeof record.exported_at === "string"
        ? record.exported_at
        : new Date().toISOString(),
    name,
    nodes,
    edges,
  };

  if (typeof record.description === "string") {
    result.description = record.description;
  }

  if (
    Array.isArray(record.tags) &&
    record.tags.every((t) => typeof t === "string")
  ) {
    result.tags = [...record.tags];
  }

  if (
    record.viewport &&
    typeof record.viewport === "object" &&
    !Array.isArray(record.viewport)
  ) {
    const vp = record.viewport as Record<string, unknown>;
    if (
      typeof vp.x === "number" &&
      typeof vp.y === "number" &&
      typeof vp.zoom === "number"
    ) {
      result.viewport = { x: vp.x, y: vp.y, zoom: vp.zoom };
    }
  }

  return result;
}

/**
 * Strictly validates and parses a playbook JSON backup string.
 */
export function parsePlaybookBackup(raw: string): PlaybookBackup {
  const record = parseCommonBackupObject(raw, "playbook");

  const name =
    typeof record.name === "string" && record.name.trim()
      ? record.name.trim()
      : "Imported Playbook";

  const nodes = (record.nodes as FlowExportNode[]).map(sanitizeNodeForExport);
  const edges = (record.edges as FlowExportEdge[]).map((edge, idx) =>
    sanitizeEdgeForExport({
      ...edge,
      id:
        typeof edge.id === "string" && edge.id.trim()
          ? edge.id
          : `edge-${idx + 1}`,
    }),
  );

  const result: PlaybookBackup = {
    elder_export_version: ELDER_EXPORT_VERSION,
    kind: "playbook",
    exported_at:
      typeof record.exported_at === "string"
        ? record.exported_at
        : new Date().toISOString(),
    name,
    nodes,
    edges,
  };

  if (typeof record.description === "string") {
    result.description = record.description;
  }

  if (typeof record.trigger_type === "string") {
    result.trigger_type = record.trigger_type;
  }

  if (
    record.trigger_config &&
    typeof record.trigger_config === "object" &&
    !Array.isArray(record.trigger_config)
  ) {
    result.trigger_config = deepClone(
      record.trigger_config as Record<string, unknown>,
    );
  }

  if (typeof record.is_active === "boolean") {
    result.is_active = record.is_active;
  }

  return result;
}
