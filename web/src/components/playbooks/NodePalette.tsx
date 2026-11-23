import { useMemo, useState, type DragEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Lock,
  Plus,
  Search,
  Webhook,
} from "lucide-react";
import { resolvePlaybookIcon } from "./iconMap";
import type {
  CatalogModuleSummary,
  NodeCatalogItem,
  NodeCatalogStage,
} from "./types";

interface LinkedInboundWebhook {
  id: number;
  name: string;
  receive_path: string;
  scopes?: string[];
  is_active: boolean;
}

interface NodePaletteProps {
  nodes: NodeCatalogItem[];
  modules: CatalogModuleSummary[];
  isLoading: boolean;
  linkedWebhooks: LinkedInboundWebhook[];
  onAddNode: (item: NodeCatalogItem) => void;
  onOpenIncomingWebhook: () => void;
  onNavigateWebhooks: () => void;
}

const STAGE_TABS: Array<{ id: "all" | NodeCatalogStage; label: string }> = [
  { id: "all", label: "All" },
  { id: "triggers", label: "Triggers" },
  { id: "process", label: "Process" },
  { id: "actions", label: "Actions" },
];

export default function NodePalette({
  nodes,
  modules,
  isLoading,
  linkedWebhooks,
  onAddNode,
  onOpenIncomingWebhook,
  onNavigateWebhooks,
}: NodePaletteProps) {
  const [search, setSearch] = useState("");
  const [stageFilter, setStageFilter] = useState<"all" | NodeCatalogStage>(
    "all",
  );
  const [expandedModules, setExpandedModules] = useState<
    Record<string, boolean>
  >({
    core: true,
    infrastructure: true,
    issues: true,
  });
  const [showDisabledModules, setShowDisabledModules] = useState(false);

  const query = search.trim().toLowerCase();

  const filteredNodes = useMemo(() => {
    return nodes.filter((n) => {
      if (stageFilter !== "all" && n.stage !== stageFilter) return false;
      if (!query) return true;
      return (
        n.name.toLowerCase().includes(query) ||
        n.node_type.toLowerCase().includes(query) ||
        n.description.toLowerCase().includes(query) ||
        n.module_label.toLowerCase().includes(query) ||
        (n.subcategory || "").toLowerCase().includes(query)
      );
    });
  }, [nodes, stageFilter, query]);

  const { enabledGroups, disabledGroups } = useMemo(() => {
    const enabledMap = new Map<
      string,
      { label: string; items: NodeCatalogItem[] }
    >();
    const disabledMap = new Map<
      string,
      { label: string; reason: string; items: NodeCatalogItem[] }
    >();

    for (const item of filteredNodes) {
      if (item.enabled) {
        const existing = enabledMap.get(item.module) || {
          label: item.module_label,
          items: [],
        };
        existing.items.push(item);
        enabledMap.set(item.module, existing);
      } else {
        const existing = disabledMap.get(item.module) || {
          label: item.module_label,
          reason: item.disabled_reason || "Disabled or unlicensed module",
          items: [],
        };
        existing.items.push(item);
        disabledMap.set(item.module, existing);
      }
    }

    const moduleOrder = modules.map((m) => m.name);
    const sortEntries = <T,>(entries: Array<[string, T]>) =>
      entries.sort(([a], [b]) => {
        const ia = moduleOrder.indexOf(a);
        const ib = moduleOrder.indexOf(b);
        if (ia !== -1 && ib !== -1) return ia - ib;
        if (ia !== -1) return -1;
        if (ib !== -1) return 1;
        return a.localeCompare(b);
      });

    return {
      enabledGroups: sortEntries(Array.from(enabledMap.entries())),
      disabledGroups: sortEntries(Array.from(disabledMap.entries())),
    };
  }, [filteredNodes, modules]);

  const toggleModule = (modName: string) => {
    setExpandedModules((prev) => ({ ...prev, [modName]: !prev[modName] }));
  };

  const handleDragStart = (
    event: DragEvent<HTMLButtonElement>,
    item: NodeCatalogItem,
  ) => {
    if (!item.enabled) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(
      "application/elder-playbook-node",
      item.node_type,
    );
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside
      aria-label="Playbook Node Palette"
      className="w-56 md:w-64 xl:w-72 bg-slate-800 border-r border-slate-700 flex flex-col shrink-0 overflow-hidden"
    >
      {/* Search + Stage Tabs */}
      <div className="p-3 border-b border-slate-700 space-y-2.5">
        <div>
          <label htmlFor="playbook-node-search" className="sr-only">
            Search nodes
          </label>
          <div className="relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none" />
            <input
              id="playbook-node-search"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search nodes & modules..."
              className="w-full bg-slate-900 text-slate-100 placeholder-slate-400 border border-slate-600 rounded-lg pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:border-amber-500"
            />
          </div>
        </div>

        <div
          role="tablist"
          aria-label="Filter nodes by stage"
          className="grid grid-cols-4 gap-1 bg-slate-900/80 p-1 rounded-lg"
        >
          {STAGE_TABS.map((tab) => {
            const active = stageFilter === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setStageFilter(tab.id)}
                className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                  active
                    ? "bg-amber-500 text-slate-900 font-semibold"
                    : "text-slate-300 hover:text-white hover:bg-slate-800"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Scrollable Node Groups */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Incoming Webhook Soft-link section */}
        {(stageFilter === "all" || stageFilter === "triggers") && !query && (
          <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-2.5 space-y-2">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-400">
              Webhook Integration
            </div>
            <button
              type="button"
              onClick={onOpenIncomingWebhook}
              className="w-full flex items-center justify-between bg-slate-700 hover:bg-slate-600 text-amber-300 text-xs px-2.5 py-2 rounded transition-colors text-left border border-amber-500/30"
            >
              <span className="flex items-center gap-2 font-medium">
                <Webhook className="w-3.5 h-3.5 shrink-0" />
                Incoming Webhook
              </span>
              <ExternalLink className="w-3 h-3 opacity-75 shrink-0" />
            </button>
            {linkedWebhooks.length > 0 ? (
              <div className="space-y-1">
                {linkedWebhooks.map((wh) => (
                  <button
                    key={wh.id}
                    type="button"
                    onClick={onNavigateWebhooks}
                    className="w-full text-left px-2 py-1 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 transition-colors"
                  >
                    <div className="flex items-center justify-between text-[11px] text-slate-200 font-medium">
                      <span className="truncate">{wh.name}</span>
                      <span
                        className={
                          wh.is_active ? "text-emerald-400" : "text-slate-500"
                        }
                      >
                        {wh.is_active ? "Active" : "Off"}
                      </span>
                    </div>
                    <div className="text-[10px] font-mono text-slate-400 truncate">
                      {wh.receive_path}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-slate-400 leading-snug">
                Creates an authenticated Inbound Webhook with{" "}
                <code className="text-amber-300">playbooks:trigger</code> scope.
              </p>
            )}
          </div>
        )}

        {isLoading ? (
          <div className="text-xs text-slate-400 py-4 text-center">
            Loading node catalog...
          </div>
        ) : enabledGroups.length === 0 && disabledGroups.length === 0 ? (
          <div className="text-xs text-slate-400 py-6 text-center">
            No nodes match &ldquo;{search}&rdquo;.
          </div>
        ) : null}

        {/* Enabled Modules */}
        {enabledGroups.map(([modName, group]) => {
          const isOpen = Boolean(query) || Boolean(expandedModules[modName]);
          return (
            <div key={modName} className="space-y-1.5">
              <button
                type="button"
                onClick={() => toggleModule(modName)}
                className="w-full flex items-center justify-between text-left px-1 py-1 text-xs font-semibold text-amber-400 hover:text-amber-300 transition-colors"
              >
                <span className="flex items-center gap-1.5 truncate">
                  {isOpen ? (
                    <ChevronDown className="w-3.5 h-3.5 shrink-0" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 shrink-0" />
                  )}
                  <span className="truncate">{group.label}</span>
                </span>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">
                  {group.items.length}
                </span>
              </button>

              {isOpen && (
                <div className="space-y-1 pl-1">
                  {group.items.map((item) => {
                    const Icon = resolvePlaybookIcon(item.icon);
                    return (
                      <button
                        key={item.node_type}
                        type="button"
                        draggable
                        onDragStart={(e) => handleDragStart(e, item)}
                        onClick={() => onAddNode(item)}
                        title={item.description}
                        className="w-full group flex items-center gap-2.5 bg-slate-700/75 hover:bg-slate-600/80 border border-slate-600/60 hover:border-amber-500/50 text-left px-2.5 py-2 rounded-lg transition-colors"
                      >
                        <span
                          className="w-6 h-6 rounded flex items-center justify-center shrink-0"
                          style={{
                            backgroundColor: `${item.color}22`,
                            color: item.color,
                          }}
                        >
                          <Icon className="w-3.5 h-3.5" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-xs font-medium text-slate-100 truncate">
                            {item.name}
                          </span>
                          <span className="block text-[10px] text-slate-400 truncate">
                            {item.subcategory || item.category}
                          </span>
                        </span>
                        <Plus className="w-3.5 h-3.5 text-slate-400 group-hover:text-amber-400 shrink-0" />
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}

        {/* Disabled / Unlicensed Modules Disclosure */}
        {disabledGroups.length > 0 && (
          <div className="pt-2 border-t border-slate-700">
            <button
              type="button"
              onClick={() => setShowDisabledModules((prev) => !prev)}
              className="w-full flex items-center justify-between text-xs font-medium text-slate-400 hover:text-slate-200 py-1.5"
            >
              <span className="flex items-center gap-1.5">
                <Lock className="w-3.5 h-3.5 text-slate-500" />
                Unavailable Modules ({disabledGroups.length})
              </span>
              {showDisabledModules || Boolean(query) ? (
                <ChevronDown className="w-3.5 h-3.5" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5" />
              )}
            </button>

            {(showDisabledModules || Boolean(query)) && (
              <div className="mt-2 space-y-3">
                {disabledGroups.map(([modName, group]) => (
                  <div
                    key={modName}
                    className="rounded-lg bg-slate-900/50 border border-slate-700/80 p-2 space-y-1.5"
                  >
                    <div className="flex items-center justify-between text-[11px] text-slate-300 font-semibold">
                      <span>{group.label}</span>
                      <span className="text-[10px] text-amber-400">Locked</span>
                    </div>
                    <p className="text-[10px] text-slate-400 leading-snug">
                      {group.reason}
                    </p>
                    <div className="space-y-1">
                      {group.items.map((item) => (
                        <div
                          key={item.node_type}
                          title={item.disabled_reason || group.reason}
                          className="flex items-center justify-between px-2 py-1 rounded bg-slate-800/60 text-slate-500 text-xs cursor-not-allowed"
                        >
                          <span className="truncate">{item.name}</span>
                          <Lock className="w-3 h-3 shrink-0" />
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
