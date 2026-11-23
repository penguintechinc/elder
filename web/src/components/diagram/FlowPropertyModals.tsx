import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { MarkerType, type Node, type Edge } from "@xyflow/react";
import { X } from "lucide-react";
import toast from "react-hot-toast";
import SearchableSelect from "@/components/SearchableSelect";
import {
  PRIMARY_12_COLORS,
  RESOURCE_CATEGORY_OPTIONS,
  fetchCategoryResources,
  type ResourceCategory,
} from "./resourceLookup";

export type { ResourceCategory, ResourceOption } from "./resourceLookup";

const LINE_TYPE_OPTIONS = [
  { value: "virtual", label: "Virtual" },
  { value: "ethernet", label: "Ethernet" },
  { value: "oc3", label: "OC-3" },
  { value: "oc12", label: "OC-12" },
  { value: "oc48", label: "OC-48" },
  { value: "fiber", label: "Fiber" },
  { value: "mpls", label: "MPLS" },
  { value: "vpn", label: "VPN / Tunnel" },
  { value: "direct_connect", label: "AWS Direct Connect" },
  { value: "express_route", label: "Azure ExpressRoute" },
  { value: "peering", label: "Peering" },
  { value: "wireless", label: "Wireless" },
];

const LINE_SPEED_OPTIONS = [
  { value: "10Mbps", label: "10 Mbps" },
  { value: "100Mbps", label: "100 Mbps" },
  { value: "155Mbps", label: "155 Mbps (OC-3)" },
  { value: "622Mbps", label: "622 Mbps (OC-12)" },
  { value: "1Gbps", label: "1 Gbps" },
  { value: "2.5Gbps", label: "2.5 Gbps" },
  { value: "10Gbps", label: "10 Gbps" },
  { value: "25Gbps", label: "25 Gbps" },
  { value: "40Gbps", label: "40 Gbps" },
  { value: "100Gbps", label: "100 Gbps" },
  { value: "400Gbps", label: "400 Gbps" },
];

const LINE_PROVIDER_OPTIONS = [
  { value: "CenturyLink / Lumen", label: "CenturyLink / Lumen" },
  { value: "AT&T", label: "AT&T" },
  { value: "Verizon", label: "Verizon" },
  { value: "Zayo", label: "Zayo" },
  { value: "Cogent", label: "Cogent" },
  { value: "Comcast", label: "Comcast" },
  { value: "AWS", label: "AWS" },
  { value: "Azure", label: "Azure" },
  { value: "Google Cloud", label: "Google Cloud" },
  { value: "Cloudflare", label: "Cloudflare" },
  { value: "Internal", label: "Internal" },
];

type ArrowDirection = "right" | "left" | "both" | "none";
type LineStyle = "solid" | "dashed" | "dotted";

function dashArrayForStyle(style: LineStyle): string | undefined {
  if (style === "dashed") return "6 4";
  if (style === "dotted") return "2 4";
  return undefined;
}

interface NodePropertyModalProps {
  node: Node | null;
  onClose: () => void;
  onSave: (updatedNode: Node) => void;
}

export function NodePropertyModal({
  node,
  onClose,
  onSave,
}: NodePropertyModalProps) {
  const [label, setLabel] = useState("");
  const [color, setColor] = useState("#1f2937");
  const [resourceCategory, setResourceCategory] =
    useState<ResourceCategory>("");
  const [resourceId, setResourceId] = useState("");

  useEffect(() => {
    if (!node) return;
    const data = (node.data || {}) as Record<string, unknown>;
    setLabel(String(data.label || ""));
    setColor(String(data.color || "#1f2937"));
    setResourceCategory((data.resourceType as ResourceCategory) || "");
    setResourceId(data.resourceId ? String(data.resourceId) : "");
  }, [node]);

  const {
    data: categoryResources = [],
    isLoading: loadingResources,
    isError: resourcesError,
  } = useQuery({
    queryKey: ["flow-modal-resources", resourceCategory],
    enabled: node !== null && resourceCategory !== "",
    queryFn: () => fetchCategoryResources(resourceCategory),
  });

  if (!node) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const prevData = (node.data || {}) as Record<string, unknown>;
    const picked = categoryResources.find(
      (r: { value: string; label: string; rawName?: string }) =>
        r.value === resourceId,
    );
    const nextResourceName =
      resourceCategory && resourceId
        ? picked?.rawName ||
          picked?.label ||
          (prevData.resourceName as string | undefined)
        : undefined;

    onSave({
      ...node,
      data: {
        ...prevData,
        label: label.trim() || prevData.label,
        color: color.trim() || "#1f2937",
        resourceType: resourceCategory || undefined,
        resourceId:
          resourceCategory && resourceId
            ? Number(resourceId) || resourceId
            : undefined,
        resourceName: nextResourceName,
      },
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-label="Edit object"
    >
      <div className="w-full max-w-lg bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-5 text-white">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-amber-400">
            Edit Object
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded"
            aria-label="Close object modal"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="flow-node-label"
              className="block text-sm font-medium text-slate-300 mb-1"
            >
              Display Text
            </label>
            <input
              id="flow-node-label"
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
              placeholder="Object label"
            />
          </div>

          <div>
            <label
              htmlFor="flow-node-color-hex"
              className="block text-sm font-medium text-slate-300 mb-2"
            >
              Box Color
            </label>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              {PRIMARY_12_COLORS.map((c) => (
                <button
                  key={c.hex}
                  type="button"
                  onClick={() => setColor(c.hex)}
                  title={c.name}
                  aria-label={`Set box color ${c.name}`}
                  className={`w-6 h-6 rounded border-2 transition-transform ${
                    color.toLowerCase() === c.hex.toLowerCase()
                      ? "border-amber-400 scale-110"
                      : "border-slate-600"
                  }`}
                  style={{ backgroundColor: c.hex }}
                />
              ))}
              <button
                type="button"
                onClick={() => setColor("transparent")}
                className={`px-2 py-1 text-xs rounded border ${
                  color === "transparent"
                    ? "border-amber-400 text-amber-300 bg-slate-900"
                    : "border-slate-600 text-slate-300 bg-slate-900/50"
                }`}
              >
                Transparent
              </button>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={
                  color.startsWith("#") && color.length === 7
                    ? color
                    : "#1f2937"
                }
                onChange={(e) => setColor(e.target.value)}
                aria-label="Custom box color picker"
                className="w-9 h-9 rounded border border-slate-700 bg-slate-900 cursor-pointer"
              />
              <input
                id="flow-node-color-hex"
                type="text"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                placeholder="#1f2937 or transparent"
                className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-amber-500"
              />
            </div>
          </div>

          <div>
            <label
              htmlFor="flow-node-resource-category"
              className="block text-sm font-medium text-slate-300 mb-1"
            >
              Associate Resource Category
            </label>
            <select
              id="flow-node-resource-category"
              value={resourceCategory}
              onChange={(e) => {
                setResourceCategory(e.target.value as ResourceCategory);
                setResourceId("");
              }}
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
            >
              {RESOURCE_CATEGORY_OPTIONS.map((opt) => (
                <option key={opt.value || "none"} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {resourceCategory && (
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1">
                Resource
              </span>
              <SearchableSelect
                options={categoryResources}
                value={resourceId}
                onChange={(val) => setResourceId(String(val))}
                isLoading={loadingResources}
                placeholder="Search and select resource..."
                allowClear
                ariaLabel="Associate resource"
              />
              {resourcesError && (
                <p className="text-xs text-rose-400 mt-1">
                  Failed to load resources for this category.
                </p>
              )}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 text-sm rounded bg-amber-500 hover:bg-amber-600 text-slate-900 font-semibold"
            >
              Apply
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

interface EdgePropertyModalProps {
  edge: Edge | null;
  onClose: () => void;
  onSave: (updatedEdge: Edge) => void;
}

export function EdgePropertyModal({
  edge,
  onClose,
  onSave,
}: EdgePropertyModalProps) {
  const [label, setLabel] = useState("");
  const [lineType, setLineType] = useState("virtual");
  const [speed, setSpeed] = useState("");
  const [provider, setProvider] = useState("");
  const [arrowDirection, setArrowDirection] = useState<ArrowDirection>("right");
  const [color, setColor] = useState("#F59E0B");
  const [lineStyle, setLineStyle] = useState<LineStyle>("solid");
  const [animated, setAnimated] = useState(false);
  const [metadataText, setMetadataText] = useState("");
  const [metadataError, setMetadataError] = useState<string | null>(null);

  useEffect(() => {
    if (!edge) return;
    const data = (edge.data || {}) as Record<string, unknown>;
    const stroke =
      (edge.style?.stroke as string) || (data.color as string) || "#F59E0B";
    const dash = edge.style?.strokeDasharray;
    let inferredStyle: LineStyle = (data.lineStyle as LineStyle) || "solid";
    if (!data.lineStyle && typeof dash === "string") {
      inferredStyle = dash.startsWith("2") ? "dotted" : "dashed";
    }
    let inferredDir: ArrowDirection =
      (data.arrowDirection as ArrowDirection) || "right";
    if (!data.arrowDirection) {
      if (edge.markerStart && edge.markerEnd) inferredDir = "both";
      else if (edge.markerStart) inferredDir = "left";
      else if (edge.markerEnd) inferredDir = "right";
      else inferredDir = "none";
    }

    setLabel(typeof edge.label === "string" ? edge.label : "");
    setLineType(String(data.lineType || "virtual"));
    setSpeed(String(data.speed || ""));
    setProvider(String(data.provider || ""));
    setArrowDirection(inferredDir);
    setColor(stroke);
    setLineStyle(inferredStyle);
    setAnimated(Boolean(edge.animated));
    setMetadataError(null);
    setMetadataText(
      data.metadata && typeof data.metadata === "object"
        ? JSON.stringify(data.metadata, null, 2)
        : "",
    );
  }, [edge]);

  if (!edge) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setMetadataError(null);

    let parsedMeta: Record<string, unknown> | undefined;
    if (metadataText.trim()) {
      try {
        const parsed = JSON.parse(metadataText);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          setMetadataError(
            'Metadata must be a JSON object (e.g. {"circuit_id": "CKT-101"})',
          );
          return;
        }
        parsedMeta = parsed as Record<string, unknown>;
      } catch {
        setMetadataError("Invalid JSON in metadata");
        return;
      }
    }

    const finalColor = color.trim() || "#F59E0B";
    const marker = { type: MarkerType.ArrowClosed, color: finalColor };
    const markerStart =
      arrowDirection === "left" || arrowDirection === "both"
        ? marker
        : undefined;
    const markerEnd =
      arrowDirection === "right" || arrowDirection === "both"
        ? marker
        : undefined;
    const autoSummary = [lineType.trim(), speed.trim(), provider.trim()]
      .filter(Boolean)
      .join(" · ");
    const finalLabel = label.trim() || autoSummary || undefined;

    onSave({
      ...edge,
      label: finalLabel,
      animated,
      markerStart,
      markerEnd,
      style: {
        ...edge.style,
        stroke: finalColor,
        strokeWidth: 2,
        strokeDasharray: dashArrayForStyle(lineStyle),
      },
      data: {
        ...(edge.data || {}),
        lineType: lineType.trim() || undefined,
        speed: speed.trim() || undefined,
        provider: provider.trim() || undefined,
        arrowDirection,
        lineStyle,
        color: finalColor,
        metadata: parsedMeta,
      },
    });
    toast.success("Line updated");
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-label="Edit line details"
    >
      <div className="w-full max-w-lg bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-5 text-white max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-amber-400">
            Edit Line Details
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded"
            aria-label="Close line modal"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="flow-edge-label"
              className="block text-sm font-medium text-slate-300 mb-1"
            >
              Line Label (optional, defaults to Type · Speed · Provider)
            </label>
            <input
              id="flow-edge-label"
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Primary Fiber Uplink"
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1">
                Line Type
              </span>
              <SearchableSelect
                options={LINE_TYPE_OPTIONS}
                value={lineType}
                onChange={(val) => setLineType(String(val))}
                placeholder="virtual, ethernet, oc3..."
                allowCustom
                allowClear
                ariaLabel="Line Type"
              />
            </div>

            <div>
              <span className="block text-sm font-medium text-slate-300 mb-1">
                Speed
              </span>
              <SearchableSelect
                options={LINE_SPEED_OPTIONS}
                value={speed}
                onChange={(val) => setSpeed(String(val))}
                placeholder="1Gbps, 10Gbps, 155Mbps..."
                allowCustom
                allowClear
                ariaLabel="Speed"
              />
            </div>

            <div className="sm:col-span-2">
              <span className="block text-sm font-medium text-slate-300 mb-1">
                Provider
              </span>
              <SearchableSelect
                options={LINE_PROVIDER_OPTIONS}
                value={provider}
                onChange={(val) => setProvider(String(val))}
                placeholder="CenturyLink / Lumen, AT&T, AWS..."
                allowCustom
                allowClear
                ariaLabel="Provider"
              />
            </div>

            <div>
              <label
                htmlFor="flow-edge-direction"
                className="block text-sm font-medium text-slate-300 mb-1"
              >
                Arrow Direction
              </label>
              <select
                id="flow-edge-direction"
                value={arrowDirection}
                onChange={(e) =>
                  setArrowDirection(e.target.value as ArrowDirection)
                }
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
              >
                <option value="right">Right (→)</option>
                <option value="left">Left (←)</option>
                <option value="both">Both (↔)</option>
                <option value="none">None (—)</option>
              </select>
            </div>

            <div>
              <label
                htmlFor="flow-edge-style"
                className="block text-sm font-medium text-slate-300 mb-1"
              >
                Line Style
              </label>
              <select
                id="flow-edge-style"
                value={lineStyle}
                onChange={(e) => setLineStyle(e.target.value as LineStyle)}
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
              >
                <option value="solid">Solid</option>
                <option value="dashed">Dashed</option>
                <option value="dotted">Dotted</option>
              </select>
            </div>
          </div>

          <div>
            <label
              htmlFor="flow-edge-color-hex"
              className="block text-sm font-medium text-slate-300 mb-2"
            >
              Line Color
            </label>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              {PRIMARY_12_COLORS.map((c) => (
                <button
                  key={c.hex}
                  type="button"
                  onClick={() => setColor(c.hex)}
                  title={c.name}
                  aria-label={`Set line color ${c.name}`}
                  className={`w-6 h-6 rounded border-2 transition-transform ${
                    color.toLowerCase() === c.hex.toLowerCase()
                      ? "border-amber-400 scale-110"
                      : "border-slate-600"
                  }`}
                  style={{ backgroundColor: c.hex }}
                />
              ))}
            </div>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={
                  color.startsWith("#") && color.length === 7
                    ? color
                    : "#F59E0B"
                }
                onChange={(e) => setColor(e.target.value)}
                aria-label="Custom line color picker"
                className="w-9 h-9 rounded border border-slate-700 bg-slate-900 cursor-pointer"
              />
              <input
                id="flow-edge-color-hex"
                type="text"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                placeholder="#F59E0B"
                className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-amber-500"
              />
            </div>
          </div>

          <label className="inline-flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={animated}
              onChange={(e) => setAnimated(e.target.checked)}
            />
            <span>Animated flow</span>
          </label>

          <div>
            <label
              htmlFor="flow-edge-metadata"
              className="block text-sm font-medium text-slate-300 mb-1"
            >
              Metadata (JSON)
            </label>
            <textarea
              id="flow-edge-metadata"
              rows={3}
              value={metadataText}
              onChange={(e) => {
                setMetadataText(e.target.value);
                setMetadataError(null);
              }}
              placeholder='{"circuit_id": "CKT-1234", "vlan": 100}'
              className="w-full font-mono bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs text-white focus:outline-none focus:border-amber-500"
            />
            {metadataError && (
              <p className="text-xs text-red-400 mt-1">{metadataError}</p>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 text-sm rounded bg-amber-500 hover:bg-amber-600 text-slate-900 font-semibold"
            >
              Apply
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
