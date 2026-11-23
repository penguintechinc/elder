import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Code2,
  Link2,
  Lock,
  Palette,
  Plus,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import SearchableSelect from "@/components/SearchableSelect";
import {
  PRIMARY_12_COLORS,
  RESOURCE_CATEGORY_OPTIONS,
  RESOURCE_STATE_FIELD_HINTS,
  fetchCategoryResources,
  type ResourceCategory,
} from "@/components/diagram/FlowPropertyModals";
import type {
  ConfigFieldOption,
  ConfigFieldSchema,
  NodeCatalogItem,
  PlaybookNode,
  PlaybookNodeData,
  UpstreamFieldPill,
} from "./types";

interface NodeInspectorProps {
  node: PlaybookNode;
  catalogItem?: NodeCatalogItem;
  upstreamFields: UpstreamFieldPill[];
  operatorOptions: ConfigFieldOption[];
  onChangeLabel: (nodeId: string, label: string) => void;
  onChangeConfig: (nodeId: string, config: Record<string, unknown>) => void;
  onUpdateNodeData?: (nodeId: string, patch: Partial<PlaybookNodeData>) => void;
  onDeleteNode: (nodeId: string) => void;
  onClose: () => void;
}

type ActiveTarget =
  | { kind: "field"; field: string }
  | { kind: "condition_field"; field: string; index: number }
  | { kind: "condition_value"; field: string; index: number }
  | { kind: "set_field_value"; field: string; index: number }
  | { kind: "kv_value"; field: string; rowKey: string }
  | { kind: "raw_json" };

const DEFAULT_OPERATORS: ConfigFieldOption[] = [
  { value: "eq", label: "Equals (=)" },
  { value: "ne", label: "Not equals (!=)" },
  { value: "gt", label: "Greater than (>)" },
  { value: "gte", label: "Greater or equal (>=)" },
  { value: "lt", label: "Less than (<)" },
  { value: "lte", label: "Less or equal (<=)" },
  { value: "contains", label: "Contains" },
  { value: "not_contains", label: "Does not contain" },
  { value: "starts_with", label: "Starts with" },
  { value: "ends_with", label: "Ends with" },
  { value: "regex", label: "Matches regex" },
  { value: "is_null", label: "Is null / empty" },
  { value: "is_not_null", label: "Is not null" },
  { value: "in", label: "In list" },
  { value: "not_in", label: "Not in list" },
];

function shouldShowSchemaField(
  fs: ConfigFieldSchema,
  config: Record<string, unknown>,
  schema: ConfigFieldSchema[],
): boolean {
  const sw = fs.show_when;
  if (!sw || !sw.field) return true;
  let val = config[sw.field];
  if (val === undefined) {
    const dep = schema.find((item) => item.field === sw.field);
    val = dep?.default;
  }
  if ("equals" in sw && sw.equals !== undefined) {
    return val === sw.equals;
  }
  if ("not_equals" in sw && sw.not_equals !== undefined) {
    return val !== sw.not_equals;
  }
  if ("in" in sw && Array.isArray(sw.in)) {
    return sw.in.includes(val);
  }
  return true;
}

function formatJsonFieldValue(val: unknown): string {
  if (val === undefined || val === null) return "";
  if (typeof val === "string") return val;
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

interface ConditionResourcePickerProps {
  idPrefix: string;
  resourceType: string;
  resourceId: string;
  nodeResourceType?: string;
  nodeResourceId?: number | string;
  nodeResourceName?: string;
  onChange: (patch: { resource_type?: string; resource_id?: string }) => void;
}

function ConditionResourcePicker({
  idPrefix,
  resourceType,
  resourceId,
  nodeResourceType,
  nodeResourceId,
  nodeResourceName,
  onChange,
}: ConditionResourcePickerProps) {
  const effectiveCategory = (resourceType || "") as ResourceCategory;
  const {
    data: options = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["flow-modal-resources", effectiveCategory],
    enabled: effectiveCategory !== "",
    queryFn: () => fetchCategoryResources(effectiveCategory),
  });

  const usingNodeLinked =
    !resourceType && !resourceId && Boolean(nodeResourceType && nodeResourceId);

  return (
    <div className="space-y-1.5 rounded border border-slate-700/80 bg-slate-950/60 p-2">
      {usingNodeLinked && (
        <div className="text-[10px] text-emerald-300 flex items-center gap-1">
          <Link2 className="w-3 h-3 shrink-0" />
          <span>
            Using node&apos;s linked {nodeResourceType}:{" "}
            <strong className="font-semibold">
              {nodeResourceName || `#${nodeResourceId}`}
            </strong>
          </span>
        </div>
      )}
      <div className="grid grid-cols-1 gap-1.5">
        <div>
          <label htmlFor={`${idPrefix}-rtype`} className="sr-only">
            Condition target resource category
          </label>
          <select
            id={`${idPrefix}-rtype`}
            value={resourceType}
            onChange={(e) =>
              onChange({ resource_type: e.target.value, resource_id: "" })
            }
            className="w-full bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-[11px] focus:outline-none focus:border-amber-500"
          >
            <option value="">
              {nodeResourceType && nodeResourceId
                ? `Use node's linked resource (${nodeResourceName || nodeResourceType})`
                : "-- Select resource category --"}
            </option>
            {RESOURCE_CATEGORY_OPTIONS.filter((o) => o.value !== "").map(
              (opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ),
            )}
          </select>
        </div>

        {effectiveCategory !== "" && (
          <div>
            <SearchableSelect
              options={options}
              value={resourceId}
              onChange={(val) => onChange({ resource_id: String(val) })}
              isLoading={isLoading}
              placeholder="Pick resource or type {{$json.id}}..."
              allowCustom
              allowClear
              ariaLabel="Condition target resource"
            />
            {isError && (
              <p className="text-[10px] text-rose-400 mt-1">
                Failed to load resources for this category.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function NodeInspector({
  node,
  catalogItem,
  upstreamFields,
  operatorOptions,
  onChangeLabel,
  onChangeConfig,
  onUpdateNodeData,
  onDeleteNode,
  onClose,
}: NodeInspectorProps) {
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState("");
  const [rawError, setRawError] = useState<string | null>(null);
  const [activeTarget, setActiveTarget] = useState<ActiveTarget | null>(null);
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({});
  const [pillFilter, setPillFilter] = useState<
    "all" | "json" | "node" | "trigger" | "execution"
  >("all");
  const [showAppearance, setShowAppearance] = useState(false);

  const nodeResourceCategory = (node.data?.resourceType ||
    "") as ResourceCategory;
  const nodeResourceId =
    node.data?.resourceId !== undefined && node.data?.resourceId !== null
      ? String(node.data.resourceId)
      : "";
  const nodeColor =
    typeof node.data?.color === "string" && node.data.color
      ? node.data.color
      : catalogItem?.color || "#1f2937";

  const {
    data: nodeCategoryResources = [],
    isLoading: loadingNodeResources,
    isError: nodeResourcesError,
  } = useQuery({
    queryKey: ["flow-modal-resources", nodeResourceCategory],
    enabled: nodeResourceCategory !== "",
    queryFn: () => fetchCategoryResources(nodeResourceCategory),
  });

  const upstreamFieldPaths = useMemo(
    () =>
      Array.from(
        new Set(
          upstreamFields.map((p) =>
            p.label.startsWith("$json.") ? p.label.slice(6) : p.token,
          ),
        ),
      ),
    [upstreamFields],
  );

  const config = useMemo(
    () => (node.data?.config || {}) as Record<string, unknown>,
    [node.data?.config],
  );
  const schema = useMemo(
    () => catalogItem?.config_schema || [],
    [catalogItem?.config_schema],
  );
  const operators =
    operatorOptions.length > 0 ? operatorOptions : DEFAULT_OPERATORS;

  const nodeConfigRef = useRef(node.data?.config);
  nodeConfigRef.current = node.data?.config;

  useEffect(() => {
    setRawText(JSON.stringify(nodeConfigRef.current || {}, null, 2));
    setRawError(null);
    setJsonDrafts({});
    setActiveTarget(null);
  }, [node.id]);

  const visibleSchema = useMemo(
    () => schema.filter((fs) => shouldShowSchemaField(fs, config, schema)),
    [schema, config],
  );

  const visiblePills = useMemo(() => {
    if (pillFilter === "all") return upstreamFields;
    return upstreamFields.filter((p) => p.scope === pillFilter);
  }, [upstreamFields, pillFilter]);

  const updateField = (fieldName: string, value: unknown) => {
    const next = { ...config, [fieldName]: value };
    onChangeConfig(node.id, next);
    setRawText(JSON.stringify(next, null, 2));
  };

  const handleInsertPill = (pill: UpstreamFieldPill) => {
    if (rawMode || activeTarget?.kind === "raw_json") {
      const updated = `${rawText}${pill.token}`;
      setRawText(updated);
      try {
        const parsed = JSON.parse(updated);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          onChangeConfig(node.id, parsed);
          setRawError(null);
        }
      } catch {
        // User may still be mid-edit in JSON
      }
      return;
    }

    if (activeTarget?.kind === "field") {
      const cur = config[activeTarget.field];
      const base = cur === undefined || cur === null ? "" : String(cur);
      updateField(activeTarget.field, `${base}${pill.token}`);
      return;
    }

    if (activeTarget?.kind === "condition_field") {
      const list = Array.isArray(config[activeTarget.field])
        ? [...(config[activeTarget.field] as Array<Record<string, unknown>>)]
        : [];
      const row = { ...(list[activeTarget.index] || {}) };
      const plainPath = pill.label.startsWith("$json.")
        ? pill.label.slice(6)
        : pill.token;
      row.field = plainPath;
      list[activeTarget.index] = row;
      updateField(activeTarget.field, list);
      return;
    }

    if (activeTarget?.kind === "condition_value") {
      const list = Array.isArray(config[activeTarget.field])
        ? [...(config[activeTarget.field] as Array<Record<string, unknown>>)]
        : [];
      const row = { ...(list[activeTarget.index] || {}) };
      const base =
        row.value === undefined || row.value === null ? "" : String(row.value);
      row.value = `${base}${pill.token}`;
      list[activeTarget.index] = row;
      updateField(activeTarget.field, list);
      return;
    }

    if (activeTarget?.kind === "set_field_value") {
      const list = Array.isArray(config[activeTarget.field])
        ? [...(config[activeTarget.field] as Array<Record<string, unknown>>)]
        : [];
      const row = { ...(list[activeTarget.index] || {}) };
      const base =
        row.value === undefined || row.value === null ? "" : String(row.value);
      row.value = `${base}${pill.token}`;
      list[activeTarget.index] = row;
      updateField(activeTarget.field, list);
      return;
    }

    if (activeTarget?.kind === "kv_value") {
      const obj =
        config[activeTarget.field] &&
        typeof config[activeTarget.field] === "object" &&
        !Array.isArray(config[activeTarget.field])
          ? { ...(config[activeTarget.field] as Record<string, unknown>) }
          : {};
      const cur = obj[activeTarget.rowKey];
      obj[activeTarget.rowKey] = `${cur ?? ""}${pill.token}`;
      updateField(activeTarget.field, obj);
      return;
    }

    // Default: append to first visible text/expression field
    const firstTextField = visibleSchema.find(
      (fs) =>
        fs.supports_expressions ||
        fs.type === "string" ||
        fs.type === "textarea",
    );
    if (firstTextField) {
      if (firstTextField.type === "conditions") {
        const list = Array.isArray(config[firstTextField.field])
          ? [
              ...(config[firstTextField.field] as Array<
                Record<string, unknown>
              >),
            ]
          : [{ field: "", operator: "eq", value: "" }];
        const plainPath = pill.label.startsWith("$json.")
          ? pill.label.slice(6)
          : pill.token;
        list[0] = {
          ...(list[0] || { operator: "eq", value: "" }),
          field: plainPath,
        };
        updateField(firstTextField.field, list);
        return;
      }
      if (firstTextField.type === "set_fields") {
        const list = Array.isArray(config[firstTextField.field])
          ? [
              ...(config[firstTextField.field] as Array<
                Record<string, unknown>
              >),
            ]
          : [];
        const fieldName = pill.label.startsWith("$json.")
          ? pill.label.slice(6)
          : "value";
        list.push({
          name: fieldName,
          value: pill.token,
          type: pill.type || "string",
        });
        updateField(firstTextField.field, list);
        return;
      }
      const cur = config[firstTextField.field];
      const base = cur === undefined || cur === null ? "" : String(cur);
      updateField(firstTextField.field, `${base}${pill.token}`);
    }
  };

  const renderConditionBuilder = (fs: ConfigFieldSchema) => {
    const rawList = Array.isArray(config[fs.field])
      ? (config[fs.field] as Array<Record<string, unknown>>)
      : [];
    const conditions = rawList.length > 0 ? rawList : [];

    const setConditions = (next: Array<Record<string, unknown>>) =>
      updateField(fs.field, next);

    const upstreamDatalistId = `cond-upstream-fields-${node.id}-${fs.field}`;

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-200">
            {fs.label}
          </span>
          <button
            type="button"
            onClick={() =>
              setConditions([
                ...conditions,
                { source: "input", field: "", operator: "eq", value: "" },
              ])
            }
            className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-amber-300"
          >
            <Plus className="w-3 h-3" />
            Add Condition
          </button>
        </div>

        {fs.description && (
          <p className="text-[11px] text-slate-400">{fs.description}</p>
        )}

        {upstreamFieldPaths.length > 0 && (
          <datalist id={upstreamDatalistId}>
            {upstreamFieldPaths.map((path) => (
              <option key={path} value={path} />
            ))}
          </datalist>
        )}

        {conditions.length === 0 ? (
          <div className="text-[11px] text-slate-400 bg-slate-900/60 border border-dashed border-slate-700 rounded p-2.5 space-y-1.5">
            <p>
              No conditions configured yet. Click &ldquo;Add Condition&rdquo; to
              match upstream fields, freeform values, or associated resource
              states &amp; relationships.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {conditions.map((cond, idx) => {
              const rawSource = String(cond.source || "input");
              const sourceVal =
                rawSource === "resource"
                  ? "resource"
                  : rawSource === "any_field"
                    ? "any_field"
                    : "input";
              const opVal = String(cond.operator || "eq");
              const hidesValue = opVal === "is_null" || opVal === "is_not_null";
              const sourceInputId = `cond-src-${node.id}-${fs.field}-${idx}`;
              const fieldInputId = `cond-field-${node.id}-${fs.field}-${idx}`;
              const opInputId = `cond-op-${node.id}-${fs.field}-${idx}`;
              const valInputId = `cond-val-${node.id}-${fs.field}-${idx}`;
              const resTypeForHints =
                String(cond.resource_type || "") || nodeResourceCategory || "";
              const resourceHints =
                RESOURCE_STATE_FIELD_HINTS[resTypeForHints] || [
                  "status",
                  "is_active",
                  "relationships.count",
                  "relationships.related_ids",
                ];
              const resDatalistId = `cond-res-fields-${node.id}-${fs.field}-${idx}`;

              return (
                <div
                  key={`${fs.field}-${idx}`}
                  className="bg-slate-900/80 border border-slate-700 rounded-lg p-2 space-y-1.5"
                >
                  <div className="flex items-center gap-1.5">
                    <label htmlFor={sourceInputId} className="sr-only">
                      Condition {idx + 1} source
                    </label>
                    <select
                      id={sourceInputId}
                      value={sourceVal}
                      onChange={(e) => {
                        const next = [...conditions];
                        next[idx] = { ...cond, source: e.target.value };
                        setConditions(next);
                      }}
                      className="flex-1 min-w-0 bg-slate-800 text-amber-200 border border-slate-600 rounded px-2 py-1 text-[11px] font-medium focus:outline-none focus:border-amber-500"
                    >
                      <option value="input">Upstream / Prior Field</option>
                      <option value="any_field">
                        Any Field (Bare String Search)
                      </option>
                      <option value="resource">
                        Resource State / Relationship
                      </option>
                    </select>
                    <button
                      type="button"
                      aria-label={`Remove condition ${idx + 1}`}
                      onClick={() =>
                        setConditions(conditions.filter((_, i) => i !== idx))
                      }
                      className="text-slate-400 hover:text-rose-400 p-1"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  {sourceVal === "any_field" && (
                    <p className="text-[10px] text-slate-400 px-1">
                      Searches across all incoming &amp; prior node field values
                      (e.g. &ldquo;contains&rdquo; + bare string).
                    </p>
                  )}

                  {sourceVal === "resource" && (
                    <>
                      <ConditionResourcePicker
                        idPrefix={`cond-res-${node.id}-${fs.field}-${idx}`}
                        resourceType={String(cond.resource_type || "")}
                        resourceId={String(cond.resource_id || "")}
                        nodeResourceType={node.data?.resourceType}
                        nodeResourceId={node.data?.resourceId}
                        nodeResourceName={node.data?.resourceName}
                        onChange={(patch) => {
                          const next = [...conditions];
                          next[idx] = { ...cond, ...patch };
                          setConditions(next);
                        }}
                      />
                      <datalist id={resDatalistId}>
                        {resourceHints.map((hint) => (
                          <option key={hint} value={hint} />
                        ))}
                      </datalist>
                    </>
                  )}

                  {sourceVal !== "any_field" && (
                    <div>
                      <label htmlFor={fieldInputId} className="sr-only">
                        Condition {idx + 1} field path
                      </label>
                      <input
                        id={fieldInputId}
                        type="text"
                        list={
                          sourceVal === "resource"
                            ? resDatalistId
                            : upstreamFieldPaths.length > 0
                              ? upstreamDatalistId
                              : undefined
                        }
                        value={String(cond.field || "")}
                        placeholder={
                          sourceVal === "resource"
                            ? "state or relationship (e.g. is_locked, status, relationships.count)"
                            : "upstream/prior field (e.g. severity or {{$node.step1.status}})"
                        }
                        onFocus={() =>
                          setActiveTarget({
                            kind: "condition_field",
                            field: fs.field,
                            index: idx,
                          })
                        }
                        onChange={(e) => {
                          const next = [...conditions];
                          next[idx] = { ...cond, field: e.target.value };
                          setConditions(next);
                        }}
                        className="w-full bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs focus:outline-none focus:border-amber-500"
                      />
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-1.5">
                    <div>
                      <label htmlFor={opInputId} className="sr-only">
                        Condition {idx + 1} operator
                      </label>
                      <select
                        id={opInputId}
                        value={opVal}
                        onChange={(e) => {
                          const next = [...conditions];
                          next[idx] = { ...cond, operator: e.target.value };
                          setConditions(next);
                        }}
                        className="w-full bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs focus:outline-none focus:border-amber-500"
                      >
                        {operators.map((op) => (
                          <option key={op.value} value={op.value}>
                            {op.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    {!hidesValue && (
                      <div>
                        <label htmlFor={valInputId} className="sr-only">
                          Condition {idx + 1} expected value
                        </label>
                        <input
                          id={valInputId}
                          type="text"
                          value={
                            cond.value === undefined || cond.value === null
                              ? ""
                              : String(cond.value)
                          }
                          placeholder={
                            sourceVal === "resource"
                              ? "true, locked, or {{$json.*}}"
                              : "freeform value or {{$json.*}}"
                          }
                          onFocus={() =>
                            setActiveTarget({
                              kind: "condition_value",
                              field: fs.field,
                              index: idx,
                            })
                          }
                          onChange={(e) => {
                            const next = [...conditions];
                            next[idx] = { ...cond, value: e.target.value };
                            setConditions(next);
                          }}
                          className="w-full bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs focus:outline-none focus:border-amber-500"
                        />
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const renderSetFieldsBuilder = (fs: ConfigFieldSchema) => {
    const assignments = Array.isArray(config[fs.field])
      ? (config[fs.field] as Array<Record<string, unknown>>)
      : [];

    const setAssignments = (next: Array<Record<string, unknown>>) =>
      updateField(fs.field, next);

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-200">
            {fs.label}
          </span>
          <button
            type="button"
            onClick={() =>
              setAssignments([
                ...assignments,
                { name: "", value: "", type: "string" },
              ])
            }
            className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-amber-300"
          >
            <Plus className="w-3 h-3" />
            Add Field
          </button>
        </div>

        {assignments.length === 0 ? (
          <div className="text-[11px] text-slate-400 bg-slate-900/60 border border-dashed border-slate-700 rounded p-2.5">
            No field assignments yet. Click &ldquo;Add Field&rdquo; or click an
            upstream pill.
          </div>
        ) : (
          <div className="space-y-2">
            {assignments.map((row, idx) => {
              const nameId = `sf-name-${node.id}-${idx}`;
              const typeId = `sf-type-${node.id}-${idx}`;
              const valId = `sf-val-${node.id}-${idx}`;
              return (
                <div
                  key={`sf-${idx}`}
                  className="bg-slate-900/80 border border-slate-700 rounded-lg p-2 space-y-1.5"
                >
                  <div className="flex items-center gap-1.5">
                    <label htmlFor={nameId} className="sr-only">
                      Assignment {idx + 1} target key
                    </label>
                    <input
                      id={nameId}
                      type="text"
                      value={String(row.name || "")}
                      placeholder="output key (e.g. summary)"
                      onChange={(e) => {
                        const next = [...assignments];
                        next[idx] = { ...row, name: e.target.value };
                        setAssignments(next);
                      }}
                      className="flex-1 min-w-0 bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs focus:outline-none focus:border-amber-500"
                    />
                    <label htmlFor={typeId} className="sr-only">
                      Assignment {idx + 1} value type
                    </label>
                    <select
                      id={typeId}
                      value={String(row.type || "string")}
                      onChange={(e) => {
                        const next = [...assignments];
                        next[idx] = { ...row, type: e.target.value };
                        setAssignments(next);
                      }}
                      className="bg-slate-800 text-slate-200 border border-slate-600 rounded px-1.5 py-1 text-xs"
                    >
                      <option value="string">string</option>
                      <option value="number">number</option>
                      <option value="boolean">boolean</option>
                      <option value="json">json</option>
                    </select>
                    <button
                      type="button"
                      aria-label={`Remove field assignment ${idx + 1}`}
                      onClick={() =>
                        setAssignments(assignments.filter((_, i) => i !== idx))
                      }
                      className="text-slate-400 hover:text-rose-400 p-1"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  <div>
                    <label htmlFor={valId} className="sr-only">
                      Assignment {idx + 1} value or expression
                    </label>
                    <input
                      id={valId}
                      type="text"
                      value={
                        row.value === undefined || row.value === null
                          ? ""
                          : typeof row.value === "string"
                            ? row.value
                            : JSON.stringify(row.value)
                      }
                      placeholder="value or {{$json.field}}"
                      onFocus={() =>
                        setActiveTarget({
                          kind: "set_field_value",
                          field: fs.field,
                          index: idx,
                        })
                      }
                      onChange={(e) => {
                        const next = [...assignments];
                        next[idx] = { ...row, value: e.target.value };
                        setAssignments(next);
                      }}
                      className="w-full bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-amber-500"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const renderSwitchCasesBuilder = (fs: ConfigFieldSchema) => {
    const cases = Array.isArray(config[fs.field])
      ? (config[fs.field] as Array<Record<string, unknown>>)
      : [];

    const setCases = (next: Array<Record<string, unknown>>) =>
      updateField(fs.field, next);

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-200">
            {fs.label}
          </span>
          {cases.length < 4 && (
            <button
              type="button"
              onClick={() =>
                setCases([
                  ...cases,
                  { value: "", output: `case${cases.length + 1}` },
                ])
              }
              className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-amber-300"
            >
              <Plus className="w-3 h-3" />
              Add Case
            </button>
          )}
        </div>

        <div className="space-y-1.5">
          {cases.map((c, idx) => {
            const valId = `sw-val-${node.id}-${idx}`;
            const outId = `sw-out-${node.id}-${idx}`;
            return (
              <div
                key={`sw-${idx}`}
                className="flex items-center gap-1.5 bg-slate-900/80 border border-slate-700 rounded-lg p-2"
              >
                <label htmlFor={valId} className="sr-only">
                  Case {idx + 1} match value
                </label>
                <input
                  id={valId}
                  type="text"
                  value={String(c.value ?? "")}
                  placeholder="Match value"
                  onChange={(e) => {
                    const next = [...cases];
                    next[idx] = { ...c, value: e.target.value };
                    setCases(next);
                  }}
                  className="flex-1 min-w-0 bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs focus:outline-none focus:border-amber-500"
                />
                <label htmlFor={outId} className="sr-only">
                  Case {idx + 1} output port
                </label>
                <select
                  id={outId}
                  value={String(c.output || `case${idx + 1}`)}
                  onChange={(e) => {
                    const next = [...cases];
                    next[idx] = { ...c, output: e.target.value };
                    setCases(next);
                  }}
                  className="bg-slate-800 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs"
                >
                  <option value="case1">case1</option>
                  <option value="case2">case2</option>
                  <option value="case3">case3</option>
                  <option value="case4">case4</option>
                </select>
                <button
                  type="button"
                  aria-label={`Remove switch case ${idx + 1}`}
                  onClick={() => setCases(cases.filter((_, i) => i !== idx))}
                  className="text-slate-400 hover:text-rose-400 p-1"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const renderKvEditor = (fs: ConfigFieldSchema) => {
    const obj =
      config[fs.field] &&
      typeof config[fs.field] === "object" &&
      !Array.isArray(config[fs.field])
        ? (config[fs.field] as Record<string, unknown>)
        : {};
    const entries = Object.entries(obj);

    const setKv = (nextObj: Record<string, unknown>) =>
      updateField(fs.field, nextObj);

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-200">
            {fs.label}
          </span>
          <button
            type="button"
            onClick={() => {
              let k = `key_${entries.length + 1}`;
              while (k in obj) {
                k = `${k}_1`;
              }
              setKv({ ...obj, [k]: "" });
            }}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-amber-300"
          >
            <Plus className="w-3 h-3" />
            Add Entry
          </button>
        </div>

        {entries.length === 0 ? (
          <div className="text-[11px] text-slate-400 bg-slate-900/60 border border-dashed border-slate-700 rounded p-2">
            No {fs.label.toLowerCase()} entries.
          </div>
        ) : (
          <div className="space-y-1.5">
            {entries.map(([k, v], idx) => {
              const keyInputId = `kv-k-${node.id}-${fs.field}-${idx}`;
              const valInputId = `kv-v-${node.id}-${fs.field}-${idx}`;
              return (
                <div key={`${fs.field}-${idx}`} className="flex gap-1.5">
                  <label htmlFor={keyInputId} className="sr-only">
                    {fs.label} entry {idx + 1} key
                  </label>
                  <input
                    id={keyInputId}
                    type="text"
                    value={k}
                    placeholder="Key"
                    onChange={(e) => {
                      const nextKey = e.target.value;
                      const rebuilt: Record<string, unknown> = {};
                      entries.forEach(([ek, ev], i) => {
                        rebuilt[i === idx ? nextKey : ek] = ev;
                      });
                      setKv(rebuilt);
                    }}
                    className="w-2/5 bg-slate-900 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs font-mono"
                  />
                  <label htmlFor={valInputId} className="sr-only">
                    {fs.label} entry {idx + 1} value
                  </label>
                  <input
                    id={valInputId}
                    type="text"
                    value={v === undefined || v === null ? "" : String(v)}
                    placeholder="Value or {{$json.*}}"
                    onFocus={() =>
                      setActiveTarget({
                        kind: "kv_value",
                        field: fs.field,
                        rowKey: k,
                      })
                    }
                    onChange={(e) => setKv({ ...obj, [k]: e.target.value })}
                    className="flex-1 min-w-0 bg-slate-900 text-slate-100 border border-slate-600 rounded px-2 py-1 text-xs font-mono"
                  />
                  <button
                    type="button"
                    aria-label={`Remove ${fs.label} entry ${idx + 1}`}
                    onClick={() => {
                      const next = { ...obj };
                      delete next[k];
                      setKv(next);
                    }}
                    className="text-slate-400 hover:text-rose-400 p-1"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const renderSchemaField = (fs: ConfigFieldSchema) => {
    if (fs.type === "conditions") return renderConditionBuilder(fs);
    if (fs.type === "set_fields") return renderSetFieldsBuilder(fs);
    if (fs.type === "switch_cases") return renderSwitchCasesBuilder(fs);
    if (fs.type === "kv") return renderKvEditor(fs);

    const inputId = `inspector-field-${node.id}-${fs.field}`;
    const rawVal = config[fs.field] ?? fs.default;

    if (fs.type === "boolean") {
      return (
        <div className="flex items-start gap-2.5 pt-1">
          <input
            id={inputId}
            type="checkbox"
            checked={Boolean(rawVal)}
            onChange={(e) => updateField(fs.field, e.target.checked)}
            className="mt-0.5 rounded border-slate-600 bg-slate-900 text-amber-500 focus:ring-amber-500"
          />
          <div className="min-w-0">
            <label
              htmlFor={inputId}
              className="block text-xs font-semibold text-slate-200 cursor-pointer"
            >
              {fs.label}
            </label>
            {fs.description && (
              <p className="text-[11px] text-slate-400 mt-0.5">
                {fs.description}
              </p>
            )}
          </div>
        </div>
      );
    }

    if (fs.type === "select" && fs.options) {
      return (
        <div className="space-y-1">
          <label
            htmlFor={inputId}
            className="block text-xs font-semibold text-slate-200"
          >
            {fs.label}
            {fs.required && <span className="text-rose-400 ml-0.5">*</span>}
          </label>
          <select
            id={inputId}
            value={
              rawVal === undefined || rawVal === null ? "" : String(rawVal)
            }
            onChange={(e) => updateField(fs.field, e.target.value)}
            className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-amber-500"
          >
            {!fs.required && <option value="">-- Select --</option>}
            {fs.options.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          {fs.description && (
            <p className="text-[11px] text-slate-400">{fs.description}</p>
          )}
        </div>
      );
    }

    if (fs.type === "json") {
      const draftKey = `${node.id}:${fs.field}`;
      const displayText =
        draftKey in jsonDrafts
          ? jsonDrafts[draftKey]
          : formatJsonFieldValue(rawVal);
      return (
        <div className="space-y-1">
          <label
            htmlFor={inputId}
            className="block text-xs font-semibold text-slate-200"
          >
            {fs.label}
            {fs.required && <span className="text-rose-400 ml-0.5">*</span>}
          </label>
          <textarea
            id={inputId}
            rows={4}
            value={displayText}
            placeholder={fs.placeholder || "{}"}
            onFocus={() => setActiveTarget({ kind: "field", field: fs.field })}
            onChange={(e) => {
              const text = e.target.value;
              setJsonDrafts((prev) => ({ ...prev, [draftKey]: text }));
              if (!text.trim()) {
                updateField(fs.field, undefined);
                return;
              }
              try {
                updateField(fs.field, JSON.parse(text));
              } catch {
                // Keep text draft while editing or if expression string
                if (text.includes("{{")) {
                  updateField(fs.field, text);
                }
              }
            }}
            className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg p-2 text-xs font-mono focus:outline-none focus:border-amber-500"
          />
          {fs.description && (
            <p className="text-[11px] text-slate-400">{fs.description}</p>
          )}
        </div>
      );
    }

    if (fs.type === "textarea") {
      return (
        <div className="space-y-1">
          <label
            htmlFor={inputId}
            className="block text-xs font-semibold text-slate-200"
          >
            {fs.label}
            {fs.required && <span className="text-rose-400 ml-0.5">*</span>}
          </label>
          <textarea
            id={inputId}
            rows={3}
            value={
              rawVal === undefined || rawVal === null ? "" : String(rawVal)
            }
            placeholder={fs.placeholder}
            onFocus={() => setActiveTarget({ kind: "field", field: fs.field })}
            onChange={(e) => updateField(fs.field, e.target.value)}
            className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg p-2 text-xs font-mono focus:outline-none focus:border-amber-500"
          />
          {fs.description && (
            <p className="text-[11px] text-slate-400">{fs.description}</p>
          )}
        </div>
      );
    }

    const htmlInputType =
      fs.type === "password"
        ? "password"
        : fs.type === "number"
          ? "text"
          : "text";

    return (
      <div className="space-y-1">
        <label
          htmlFor={inputId}
          className="block text-xs font-semibold text-slate-200"
        >
          {fs.label}
          {fs.required && <span className="text-rose-400 ml-0.5">*</span>}
        </label>
        <input
          id={inputId}
          type={htmlInputType}
          value={rawVal === undefined || rawVal === null ? "" : String(rawVal)}
          placeholder={fs.placeholder}
          onFocus={() => setActiveTarget({ kind: "field", field: fs.field })}
          onChange={(e) => {
            const v = e.target.value;
            if (fs.type === "number" && v !== "" && !v.includes("{{")) {
              const num = Number(v);
              updateField(fs.field, Number.isNaN(num) ? v : num);
            } else {
              updateField(fs.field, v);
            }
          }}
          className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:border-amber-500"
        />
        {fs.description && (
          <p className="text-[11px] text-slate-400">{fs.description}</p>
        )}
      </div>
    );
  };

  return (
    <aside
      aria-label="Node Inspector"
      className="w-96 bg-slate-800 border-l border-slate-700 flex flex-col shrink-0 overflow-hidden"
    >
      {/* Header */}
      <div className="p-3.5 border-b border-slate-700 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider font-semibold text-amber-400">
            {catalogItem?.module_label ||
              node.data.moduleLabel ||
              "Node Config"}
          </div>
          <h3 className="text-sm font-bold text-slate-100 truncate">
            {node.data.label}
          </h3>
          <div className="text-[11px] font-mono text-slate-400 truncate">
            {node.data.nodeType}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            onClick={() => setRawMode((prev) => !prev)}
            title={rawMode ? "Switch to visual form" : "Edit raw JSON"}
            aria-label="Toggle Raw JSON mode"
            className={`px-2 py-1 rounded text-xs font-medium inline-flex items-center gap-1 border ${
              rawMode
                ? "bg-amber-500/20 text-amber-300 border-amber-500/50"
                : "bg-slate-700 text-slate-300 border-slate-600 hover:text-white"
            }`}
          >
            <Code2 className="w-3.5 h-3.5" />
            JSON
          </button>
          <button
            type="button"
            onClick={() => onDeleteNode(node.id)}
            aria-label="Delete node"
            title="Delete node"
            className="p-1.5 rounded text-rose-400 hover:text-rose-300 hover:bg-slate-700"
          >
            <Trash2 className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close inspector"
            className="p-1.5 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Scrollable Inspector Body */}
      <div className="flex-1 overflow-y-auto p-3.5 space-y-4">
        {node.data.enabled === false && (
          <div className="rounded-lg bg-amber-950/50 border border-amber-700/60 p-2.5 text-xs text-amber-200 flex items-start gap-2">
            <Lock className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
            <div>
              <div className="font-semibold">Module Unavailable</div>
              <p className="text-[11px] text-amber-200/80 mt-0.5">
                {node.data.disabledReason ||
                  "This module is disabled or unlicensed for this tenant."}
              </p>
            </div>
          </div>
        )}

        {(node.data.validationErrors?.length || 0) > 0 && (
          <div className="rounded-lg bg-rose-950/50 border border-rose-700/60 p-2.5 text-xs text-rose-200 space-y-1">
            <div className="flex items-center gap-1.5 font-semibold text-rose-300">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              Validation Issues
            </div>
            {node.data.validationErrors?.map((err, i) => (
              <p key={i} className="text-[11px] text-rose-200/90">
                {err}
              </p>
            ))}
          </div>
        )}

        {/* Label Input */}
        <div className="space-y-1">
          <label
            htmlFor="inspector-node-label"
            className="block text-xs font-semibold text-slate-200"
          >
            Node Label
          </label>
          <input
            id="inspector-node-label"
            type="text"
            value={node.data.label || ""}
            onChange={(e) => onChangeLabel(node.id, e.target.value)}
            className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-amber-500"
          />
        </div>

        {/* Merged Resource Association & Box Color (formerly double-click modal) */}
        {onUpdateNodeData && (
          <div className="rounded-lg bg-slate-900/60 border border-slate-700 p-2.5 space-y-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-slate-200">
                <Link2 className="w-3.5 h-3.5 text-amber-400" />
                Associated Resource &amp; Appearance
              </span>
              <button
                type="button"
                onClick={() => setShowAppearance((prev) => !prev)}
                className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-600"
              >
                <Palette className="w-3 h-3" />
                {showAppearance ? "Hide Color" : "Box Color"}
              </button>
            </div>

            <div className="space-y-1">
              <label
                htmlFor={`inspector-resource-category-${node.id}`}
                className="block text-[11px] font-medium text-slate-300"
              >
                Resource Category
              </label>
              <select
                id={`inspector-resource-category-${node.id}`}
                value={nodeResourceCategory}
                onChange={(e) => {
                  const nextCat = e.target.value as ResourceCategory;
                  onUpdateNodeData(node.id, {
                    resourceType: nextCat || undefined,
                    resourceId: undefined,
                    resourceName: undefined,
                  });
                }}
                className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-amber-500"
              >
                {RESOURCE_CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value || "none"} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {nodeResourceCategory && (
              <div className="space-y-1">
                <span className="block text-[11px] font-medium text-slate-300">
                  Resource
                </span>
                <SearchableSelect
                  options={nodeCategoryResources}
                  value={nodeResourceId}
                  onChange={(val) => {
                    const strVal = String(val);
                    const picked = nodeCategoryResources.find(
                      (r) => r.value === strVal,
                    );
                    onUpdateNodeData(node.id, {
                      resourceId: strVal
                        ? Number(strVal) || strVal
                        : undefined,
                      resourceName: strVal
                        ? picked?.rawName || picked?.label
                        : undefined,
                    });
                  }}
                  isLoading={loadingNodeResources}
                  placeholder="Search and select resource..."
                  allowClear
                  ariaLabel="Associate resource"
                />
                {nodeResourcesError && (
                  <p className="text-[11px] text-rose-400">
                    Failed to load resources for this category.
                  </p>
                )}
              </div>
            )}

            {showAppearance && (
              <div className="pt-2 border-t border-slate-700/80 space-y-2">
                <label
                  htmlFor={`inspector-color-hex-${node.id}`}
                  className="block text-[11px] font-medium text-slate-300"
                >
                  Box Color
                </label>
                <div className="flex flex-wrap items-center gap-1.5">
                  {PRIMARY_12_COLORS.map((c) => (
                    <button
                      key={c.hex}
                      type="button"
                      onClick={() =>
                        onUpdateNodeData(node.id, { color: c.hex })
                      }
                      title={c.name}
                      aria-label={`Set box color ${c.name}`}
                      className={`w-5 h-5 rounded border-2 transition-transform ${
                        nodeColor.toLowerCase() === c.hex.toLowerCase()
                          ? "border-amber-400 scale-110"
                          : "border-slate-600"
                      }`}
                      style={{ backgroundColor: c.hex }}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() =>
                      onUpdateNodeData(node.id, { color: "transparent" })
                    }
                    className={`px-1.5 py-0.5 text-[10px] rounded border ${
                      nodeColor === "transparent"
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
                      nodeColor.startsWith("#") && nodeColor.length === 7
                        ? nodeColor
                        : "#1f2937"
                    }
                    onChange={(e) =>
                      onUpdateNodeData(node.id, { color: e.target.value })
                    }
                    aria-label="Custom box color picker"
                    className="w-8 h-7 rounded border border-slate-700 bg-slate-900 cursor-pointer"
                  />
                  <input
                    id={`inspector-color-hex-${node.id}`}
                    type="text"
                    value={nodeColor}
                    onChange={(e) =>
                      onUpdateNodeData(node.id, { color: e.target.value })
                    }
                    placeholder="#1f2937 or transparent"
                    className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-white focus:outline-none focus:border-amber-500"
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Upstream Data / Variable Propagation Pills */}
        {upstreamFields.length > 0 && (
          <div className="rounded-lg bg-slate-900/70 border border-slate-700 p-2.5 space-y-2">
            <div className="flex items-center justify-between gap-1">
              <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-400">
                <Sparkles className="w-3.5 h-3.5" />
                Upstream Fields
              </span>
              <div className="flex gap-1">
                {(["all", "json", "node", "trigger"] as const).map((sc) => (
                  <button
                    key={sc}
                    type="button"
                    onClick={() => setPillFilter(sc)}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                      pillFilter === sc
                        ? "bg-amber-500 text-slate-900"
                        : "bg-slate-800 text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    {sc === "all" ? "All" : `$${sc}`}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-[10px] text-slate-400">
              Click a field pill to insert into the focused input.
            </p>
            <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto">
              {visiblePills.map((pill) => (
                <button
                  key={`${pill.scope}:${pill.token}`}
                  type="button"
                  onClick={() => handleInsertPill(pill)}
                  title={`${pill.sourceNodeLabel} (${pill.type})${pill.description ? ` — ${pill.description}` : ""}`}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-800 hover:bg-amber-500/20 border border-slate-600 hover:border-amber-400 text-[11px] font-mono text-amber-300 transition-colors"
                >
                  <span>{pill.label}</span>
                  <span className="text-[9px] text-slate-400">{pill.type}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Raw JSON vs Typed Schema Form */}
        {rawMode || visibleSchema.length === 0 ? (
          <div className="space-y-1.5">
            <label
              htmlFor="inspector-raw-json"
              className="block text-xs font-semibold text-slate-200"
            >
              Configuration JSON
            </label>
            <textarea
              id="inspector-raw-json"
              rows={10}
              value={rawText}
              onFocus={() => setActiveTarget({ kind: "raw_json" })}
              onChange={(e) => {
                const next = e.target.value;
                setRawText(next);
                try {
                  const parsed = JSON.parse(next);
                  if (
                    parsed &&
                    typeof parsed === "object" &&
                    !Array.isArray(parsed)
                  ) {
                    onChangeConfig(node.id, parsed);
                    setRawError(null);
                  } else {
                    setRawError("Configuration must be a JSON object.");
                  }
                } catch {
                  setRawError("Invalid JSON syntax.");
                }
              }}
              className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg p-2.5 text-xs font-mono focus:outline-none focus:border-amber-500"
              placeholder="{}"
            />
            {rawError && (
              <p className="text-[11px] text-rose-400">{rawError}</p>
            )}
          </div>
        ) : (
          <div className="space-y-3.5">
            {visibleSchema.map((fs) => (
              <div key={fs.field}>{renderSchemaField(fs)}</div>
            ))}
          </div>
        )}

        {catalogItem?.endpoint && (
          <div className="pt-2 border-t border-slate-700/80 text-[11px] text-slate-400 font-mono">
            <span className="text-slate-500">API Route: </span>
            {catalogItem.method || "GET"} {catalogItem.endpoint}
          </div>
        )}
      </div>
    </aside>
  );
}
