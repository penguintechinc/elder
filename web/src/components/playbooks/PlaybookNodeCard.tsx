import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangle, Lock } from "lucide-react";
import { resolvePlaybookIcon } from "./iconMap";
import type { PlaybookNode, PortDefinition } from "./types";

const DEFAULT_OUTPUTS: PortDefinition[] = [
  { name: "out", description: "Output", data_type: "any" },
];

const HANDLE_COLORS: Record<string, string> = {
  true: "!bg-emerald-500 border-emerald-300",
  false: "!bg-rose-500 border-rose-300",
  rejected: "!bg-rose-500 border-rose-300",
  loop: "!bg-violet-500 border-violet-300",
  case1: "!bg-violet-500 border-violet-300",
  case2: "!bg-indigo-500 border-indigo-300",
  case3: "!bg-sky-500 border-sky-300",
  case4: "!bg-cyan-500 border-cyan-300",
  default: "!bg-slate-400 border-slate-200",
  done: "!bg-slate-400 border-slate-200",
};

const HANDLE_LABEL_COLORS: Record<string, string> = {
  true: "text-emerald-300",
  false: "text-rose-300",
  rejected: "text-rose-300",
  case1: "text-violet-300",
  case2: "text-indigo-300",
  case3: "text-sky-300",
  case4: "text-cyan-300",
};

function getFallbackOutputs(nodeType: string): PortDefinition[] {
  if (
    nodeType === "conditional_if_then" ||
    nodeType === "conditional_equals" ||
    nodeType === "conditional_greater_than" ||
    nodeType === "conditional_less_than" ||
    nodeType === "conditional_contains" ||
    nodeType === "conditional_regex" ||
    nodeType === "conditional_and" ||
    nodeType === "conditional_or" ||
    nodeType === "conditional_not"
  ) {
    return [{ name: "true" }, { name: "false" }];
  }
  if (nodeType === "conditional_switch") {
    return [
      { name: "case1" },
      { name: "case2" },
      { name: "case3" },
      { name: "case4" },
      { name: "default" },
    ];
  }
  if (nodeType === "transform_filter") {
    return [{ name: "out" }, { name: "rejected" }];
  }
  if (nodeType === "conditional_while") {
    return [{ name: "loop" }, { name: "done" }];
  }
  if (nodeType === "conditional_for_each") {
    return [{ name: "item" }, { name: "index" }, { name: "done" }];
  }
  return DEFAULT_OUTPUTS;
}

function getFallbackInputs(
  nodeType: string,
  category?: string,
): PortDefinition[] {
  if (category === "triggers" || nodeType.startsWith("trigger_")) {
    return [];
  }
  if (nodeType === "conditional_and" || nodeType === "conditional_or") {
    return [{ name: "in1" }, { name: "in2" }];
  }
  if (nodeType === "transform_merge") {
    return [{ name: "in1" }, { name: "in2" }];
  }
  return [{ name: "in" }];
}

export default function PlaybookNodeCard({
  data,
  selected,
}: NodeProps<PlaybookNode>) {
  const Icon = resolvePlaybookIcon(data.icon);
  const accentColor =
    data.color && data.color !== "transparent" ? data.color : "#f59e0b";
  const isDisabled = data.enabled === false;
  const validationErrors = data.validationErrors || [];
  const hasErrors = validationErrors.length > 0;

  const inputs =
    data.inputs !== undefined
      ? data.inputs
      : getFallbackInputs(data.nodeType || "", data.category);
  const outputs =
    data.outputs && data.outputs.length > 0
      ? data.outputs
      : getFallbackOutputs(data.nodeType || "");

  const widthClass = outputs.length >= 4 ? "min-w-[240px]" : "min-w-[205px]";

  return (
    <div
      className={`${widthClass} max-w-[260px] rounded-xl bg-slate-800/95 border shadow-lg transition-all relative ${
        hasErrors
          ? "border-rose-500 ring-2 ring-rose-500/30"
          : selected
            ? "border-amber-400 ring-2 ring-amber-400/30"
            : isDisabled
              ? "border-slate-600/80 opacity-75"
              : "border-slate-600 hover:border-slate-500"
      }`}
    >
      {/* Target Handles (top edge) */}
      {inputs.map((inp, idx) => {
        const leftPct =
          inputs.length === 1 ? 50 : ((idx + 1) / (inputs.length + 1)) * 100;
        return (
          <Handle
            key={`in-${inp.name}`}
            type="target"
            id={inp.name}
            position={Position.Top}
            style={{ left: `${leftPct}%` }}
            title={inp.description || inp.name}
            className="!w-3 !h-3 !bg-amber-400 !border-2 !border-slate-900"
          />
        );
      })}

      {/* Accent header bar */}
      <div
        className="h-1.5 rounded-t-[10px]"
        style={{ backgroundColor: accentColor }}
      />

      <div className="px-3 py-2.5">
        <div className="flex items-start gap-2.5">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border border-white/10"
            style={{ backgroundColor: `${accentColor}22`, color: accentColor }}
          >
            <Icon className="w-4 h-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-1">
              <span className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 truncate">
                {data.moduleLabel || data.module || data.category || "Node"}
              </span>
              {isDisabled && (
                <span
                  title={data.disabledReason || "Module or route disabled"}
                  className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-amber-950/70 text-amber-300 border border-amber-700/50 shrink-0"
                >
                  <Lock className="w-2.5 h-2.5" />
                  Off
                </span>
              )}
              {!isDisabled && hasErrors && (
                <span
                  title={validationErrors.join("\n")}
                  className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-rose-950/70 text-rose-300 border border-rose-700/50 shrink-0"
                >
                  <AlertTriangle className="w-2.5 h-2.5" />
                  {validationErrors.length}
                </span>
              )}
            </div>
            <div className="text-sm font-semibold text-slate-100 truncate leading-tight mt-0.5">
              {data.label}
            </div>
            <div className="text-[11px] font-mono text-slate-400 truncate mt-0.5">
              {data.nodeType}
            </div>
          </div>
        </div>

        {data.resourceName && (
          <div className="mt-2 text-[10px] px-2 py-0.5 rounded bg-slate-900/80 text-amber-300 border border-slate-700 truncate">
            {data.resourceName}
          </div>
        )}

        {outputs.length > 1 && (
          <div className="mt-2 pt-1.5 border-t border-slate-700/70 flex items-center justify-around gap-1">
            {outputs.map((out) => (
              <span
                key={`lbl-${out.name}`}
                className={`text-[10px] font-mono font-medium ${
                  HANDLE_LABEL_COLORS[out.name] || "text-slate-300"
                }`}
              >
                {out.name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Source Handles (bottom edge) */}
      {outputs.map((out, idx) => {
        const leftPct =
          outputs.length === 1 ? 50 : ((idx + 1) / (outputs.length + 1)) * 100;
        const customCls =
          HANDLE_COLORS[out.name] || "!bg-amber-400 !border-slate-900";
        return (
          <Handle
            key={`out-${out.name}`}
            type="source"
            id={out.name}
            position={Position.Bottom}
            style={{ left: `${leftPct}%` }}
            title={out.description || out.name}
            className={`!w-3 !h-3 !border-2 ${customCls}`}
          />
        );
      })}
    </div>
  );
}
