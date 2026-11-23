import type { RunRuntime } from "@/lib/api";

export interface RuntimeOption {
  value: RunRuntime;
  label: string;
  badgeClass: string;
  template: string;
}

export const RUN_RUNTIMES: RuntimeOption[] = [
  {
    value: "nodejs",
    label: "Node.js",
    badgeClass:
      "bg-emerald-900/70 text-emerald-200 border border-emerald-700/60",
    template: `// Elder Run function (Node.js)
export async function handler(input) {
  const name = input?.name ?? "world";
  return {
    message: \`Hello, \${name}!\`,
    timestamp: new Date().toISOString(),
  };
}
`,
  },
  {
    value: "rust",
    label: "Rust",
    badgeClass: "bg-orange-900/70 text-orange-200 border border-orange-700/60",
    template: `// Elder Run function (Rust)
use serde_json::{json, Value};

pub fn handler(input: Value) -> Value {
    let name = input
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("world");
    json!({
        "message": format!("Hello, {name}!"),
        "status": "ok"
    })
}
`,
  },
  {
    value: "python",
    label: "Python",
    badgeClass: "bg-sky-900/70 text-sky-200 border border-sky-700/60",
    template: `# Elder Run function (Python)
from datetime import datetime, timezone


def handler(input_data: dict) -> dict:
    name = (input_data or {}).get("name", "world")
    return {
        "message": f"Hello, {name}!",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
`,
  },
  {
    value: "golang",
    label: "Go",
    badgeClass: "bg-cyan-900/70 text-cyan-200 border border-cyan-700/60",
    template: `// Elder Run function (Go)
package main

import (
	"fmt"
	"time"
)

func Handler(input map[string]any) (map[string]any, error) {
	name, _ := input["name"].(string)
	if name == "" {
		name = "world"
	}
	return map[string]any{
		"message":   fmt.Sprintf("Hello, %s!", name),
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
`,
  },
  {
    value: "ruby",
    label: "Ruby",
    badgeClass: "bg-rose-900/70 text-rose-200 border border-rose-700/60",
    template: `# Elder Run function (Ruby)
require "time"

def handler(input)
  name = (input || {})["name"] || "world"
  {
    message: "Hello, #{name}!",
    timestamp: Time.now.utc.iso8601
  }
end
`,
  },
];

const RUNTIME_MAP = new Map<string, RuntimeOption>(
  RUN_RUNTIMES.map((opt) => [opt.value, opt]),
);

export function getRuntimeMeta(runtime: string): {
  label: string;
  badgeClass: string;
} {
  const key = runtime.toLowerCase();
  const match = RUNTIME_MAP.get(key);
  if (match) {
    return { label: match.label, badgeClass: match.badgeClass };
  }
  return {
    label: runtime,
    badgeClass: "bg-slate-700 text-slate-200 border border-slate-600",
  };
}

export function getStarterCode(runtime: string): string {
  return (
    RUNTIME_MAP.get(runtime.toLowerCase())?.template ??
    RUN_RUNTIMES[0].template
  );
}

export function getStorageBackendMeta(backend: "database" | "s3"): {
  shortLabel: string;
  label: string;
  badgeClass: string;
} {
  if (backend === "s3") {
    return {
      shortLabel: "S3",
      label: "S3 Bucket",
      badgeClass:
        "bg-indigo-900/70 text-indigo-200 border border-indigo-700/60",
    };
  }
  return {
    shortLabel: "DB",
    label: "Database",
    badgeClass: "bg-slate-900 text-slate-300 border border-slate-700",
  };
}

export function getExecutionStatusBadgeClass(status: string): string {
  switch (status.toLowerCase()) {
    case "success":
    case "completed":
      return "bg-emerald-900/70 text-emerald-200 border border-emerald-700/60";
    case "failed":
    case "error":
    case "timeout":
      return "bg-rose-900/70 text-rose-200 border border-rose-700/60";
    case "running":
      return "bg-sky-900/70 text-sky-200 border border-sky-700/60";
    case "pending":
    case "queued":
      return "bg-amber-900/70 text-amber-200 border border-amber-700/60";
    default:
      return "bg-slate-700 text-slate-300 border border-slate-600";
  }
}
