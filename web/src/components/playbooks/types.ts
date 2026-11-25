import type { Node } from "@xyflow/react";

export type NodeCatalogStage = "triggers" | "process" | "actions";

export interface PortDefinition {
  name: string;
  description?: string;
  required?: boolean;
  data_type?: string;
}

export interface ConfigFieldOption {
  value: string;
  label: string;
}

export interface ShowWhenCondition {
  field: string;
  equals?: unknown;
  not_equals?: unknown;
  in?: unknown[];
}

export interface ConfigFieldSchema {
  field: string;
  label: string;
  type:
    | "string"
    | "textarea"
    | "number"
    | "boolean"
    | "select"
    | "multiselect"
    | "json"
    | "kv"
    | "password"
    | "conditions"
    | "set_fields"
    | "switch_cases"
    | string;
  required: boolean;
  default?: unknown;
  placeholder?: string;
  description?: string;
  options?: ConfigFieldOption[] | null;
  supports_expressions?: boolean;
  show_when?: ShowWhenCondition | null;
}

export interface OutputFieldSchema {
  name: string;
  type: string;
  description?: string;
}

export interface NodeCatalogItem {
  node_type: string;
  name: string;
  description: string;
  category: "triggers" | "transforms" | "conditionals" | "actions" | string;
  stage: NodeCatalogStage;
  source: "builtin" | "elder";
  connector_id: string | null;
  module: string;
  required_module: string | null;
  module_label: string;
  subcategory: string;
  icon: string;
  color: string;
  inputs: PortDefinition[];
  outputs: PortDefinition[];
  config_schema: ConfigFieldSchema[];
  output_schema: OutputFieldSchema[];
  endpoint: string | null;
  method: string | null;
  enabled: boolean;
  disabled_code: string | null;
  disabled_reason: string | null;
}

export interface CatalogModuleSummary {
  name: string;
  display_name: string;
  enabled: boolean;
  node_count: number;
  disabled_reason: string | null;
}

export interface NodeCatalogResponse {
  nodes: NodeCatalogItem[];
  modules: CatalogModuleSummary[];
  operators: ConfigFieldOption[];
  total: number;
  enabled_count: number;
}

export interface PlaybookValidationIssue {
  code: string;
  message: string;
  node_id?: string;
  node_type?: string;
  module?: string;
  field?: string;
}

export interface PlaybookValidationResult {
  valid: boolean;
  errors: PlaybookValidationIssue[];
  warnings: PlaybookValidationIssue[];
  disabled_modules: string[];
  has_disabled_modules: boolean;
}

export interface PlaybookNodeData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  category?: string;
  stage?: NodeCatalogStage;
  module?: string;
  moduleLabel?: string;
  icon?: string;
  color?: string;
  resourceType?: string;
  resourceId?: number | string;
  resourceName?: string;
  config?: Record<string, unknown>;
  outputs?: PortDefinition[];
  inputs?: PortDefinition[];
  enabled?: boolean;
  disabledReason?: string | null;
  validationErrors?: string[];
}

export type PlaybookNode = Node<PlaybookNodeData>;

export interface UpstreamFieldPill {
  token: string;
  label: string;
  type: string;
  sourceNodeId: string;
  sourceNodeLabel: string;
  scope: "json" | "node" | "trigger" | "execution";
  description?: string;
}
