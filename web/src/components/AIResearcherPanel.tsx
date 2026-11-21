import { Fragment, useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  RotateCcw,
  Send,
  Sparkles,
  Wrench,
} from "lucide-react";
import api, {
  type AIResearchCitation,
  type AIResearchHistoryMessage,
  type AIResearchResponse,
  type AIResearchStep,
} from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent } from "@/components/Card";

const SUGGESTED_PROMPTS: string[] = [
  "Which critical vulnerabilities affect our services and which entities depend on them?",
  "List expiring TLS certificates and pending access review requests.",
  "Analyze single points of failure and network topology risks across organizations.",
  "Summarize open helpdesk tickets, high-priority issues, and who is currently on call.",
];

interface ResearchTurn {
  id: string;
  query: string;
  model: string;
  response?: AIResearchResponse;
  error?: string;
}

function renderInlineSegments(text: string): ReactNode[] {
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return tokens
    .filter((token) => token.length > 0)
    .map((token, idx) => {
      if (token.startsWith("`") && token.endsWith("`") && token.length >= 2) {
        return (
          <code
            key={idx}
            className="rounded bg-slate-900 px-1.5 py-0.5 font-mono text-xs text-primary-300"
          >
            {token.slice(1, -1)}
          </code>
        );
      }
      if (token.startsWith("**") && token.endsWith("**") && token.length >= 4) {
        return (
          <strong key={idx} className="font-semibold text-white">
            {token.slice(2, -2)}
          </strong>
        );
      }
      return <Fragment key={idx}>{token}</Fragment>;
    });
}

function StructuredAnswer({ content }: { content: string }) {
  const lines = content.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push(
        <pre
          key={`code-${i}`}
          className="my-2 overflow-x-auto rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs text-slate-200"
        >
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    if (/^#{1,3}\s+/.test(trimmed)) {
      const headingText = trimmed.replace(/^#{1,3}\s+/, "");
      const isH1OrH2 = trimmed.startsWith("# ") || trimmed.startsWith("## ");
      blocks.push(
        isH1OrH2 ? (
          <h4
            key={`h-${i}`}
            className="mt-3 mb-1 text-base font-semibold text-white"
          >
            {renderInlineSegments(headingText)}
          </h4>
        ) : (
          <h5
            key={`h-${i}`}
            className="mt-2.5 mb-1 text-sm font-semibold text-slate-100"
          >
            {renderInlineSegments(headingText)}
          </h5>
        ),
      );
      i += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul
          key={`ul-${i}`}
          className="my-2 list-disc space-y-1 pl-5 text-sm text-slate-200"
        >
          {items.map((item, idx) => (
            <li key={idx}>{renderInlineSegments(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ol
          key={`ol-${i}`}
          className="my-2 list-decimal space-y-1 pl-5 text-sm text-slate-200"
        >
          {items.map((item, idx) => (
            <li key={idx}>{renderInlineSegments(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const paraLines: string[] = [trimmed];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].trim().startsWith("```") &&
      !/^#{1,3}\s+/.test(lines[i].trim()) &&
      !/^[-*]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i].trim());
      i += 1;
    }
    blocks.push(
      <p
        key={`p-${i}`}
        className="my-1.5 text-sm leading-relaxed text-slate-200"
      >
        {renderInlineSegments(paraLines.join(" "))}
      </p>,
    );
  }

  return <div className="space-y-1">{blocks}</div>;
}

function extractApiErrorMessage(err: unknown): string {
  if (err && typeof err === "object") {
    const maybeAxios = err as {
      response?: {
        status?: number;
        data?: { message?: string; error?: string };
      };
      message?: string;
    };
    const dataMsg =
      maybeAxios.response?.data?.message || maybeAxios.response?.data?.error;
    if (dataMsg) {
      return dataMsg;
    }
    if (maybeAxios.message) {
      return maybeAxios.message;
    }
  }
  return "AI research request failed";
}

function ToolTraceAccordion({ steps }: { steps: AIResearchStep[] }) {
  const [expanded, setExpanded] = useState(false);
  if (steps.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 border-t border-slate-700 pt-3">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-300 hover:text-white"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        <Wrench className="h-3.5 w-3.5 text-primary-400" />
        <span>
          MCP tool trace ({steps.length} call{steps.length === 1 ? "" : "s"})
        </span>
      </button>

      {expanded && (
        <div className="mt-2.5 space-y-2">
          {steps.map((step, idx) => (
            <div
              key={`${step.tool}-${idx}`}
              className="rounded-md border border-slate-700 bg-slate-900/70 p-3 text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-slate-300">
                    Step {step.step}
                  </span>
                  <span className="font-mono font-semibold text-primary-300">
                    {step.tool}
                  </span>
                  {step.endpoint && (
                    <span className="font-mono text-slate-400">
                      {step.endpoint}
                    </span>
                  )}
                </div>
                <span
                  className={
                    step.status === "ok"
                      ? "rounded-full bg-emerald-500/20 px-2 py-0.5 font-medium text-emerald-300"
                      : "rounded-full bg-red-500/20 px-2 py-0.5 font-medium text-red-300"
                  }
                >
                  {step.status}
                </span>
              </div>
              <pre className="mt-2 overflow-x-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-300">
                {JSON.stringify(step.arguments, null, 2)}
              </pre>
              {step.error ? (
                <p className="mt-1.5 font-mono text-[11px] text-red-300">
                  {step.error}
                </p>
              ) : (
                step.result_preview && (
                  <pre className="mt-1.5 max-h-36 overflow-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-400">
                    {step.result_preview}
                  </pre>
                )
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CitationList({
  citations,
  onNavigate,
}: {
  citations: AIResearchCitation[];
  onNavigate: (url: string) => void;
}) {
  if (citations.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 border-t border-slate-700 pt-3">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
        Sources &amp; Elder Citations ({citations.length})
      </h4>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {citations.map((citation) => (
          <button
            key={`${citation.resource_type}-${citation.id}`}
            type="button"
            onClick={() => onNavigate(citation.url)}
            className="flex items-center justify-between gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-left transition-colors hover:border-primary-500 hover:bg-slate-800"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-white">
                {citation.name}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-400">
                <span className="rounded bg-slate-800 px-1.5 py-0.5 uppercase">
                  {citation.resource_type.replace(/_/g, " ")}
                </span>
                <span className="truncate font-mono">{citation.url}</span>
              </div>
            </div>
            <ExternalLink className="h-3.5 w-3.5 shrink-0 text-slate-400" />
          </button>
        ))}
      </div>
    </div>
  );
}

export default function AIResearcherPanel() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selectedModelOverride, setSelectedModelOverride] = useState<
    string | null
  >(null);
  const [turns, setTurns] = useState<ResearchTurn[]>([]);

  const {
    data: config,
    isLoading: configLoading,
    isError: configFetchError,
  } = useQuery({
    queryKey: ["ai-research-config"],
    queryFn: () => api.getAIResearcherConfig(),
  });

  const defaultModel = config?.default_model || "gemma4:e4b";
  const selectedModel = selectedModelOverride ?? defaultModel;
  const modelOptions = Array.from(
    new Set([defaultModel, ...(config?.available_models ?? [])]),
  );

  const researchMutation = useMutation({
    mutationFn: (payload: {
      query: string;
      model: string;
      history: AIResearchHistoryMessage[];
    }) => api.runAIResearch(payload),
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || researchMutation.isPending) {
      return;
    }

    const history: AIResearchHistoryMessage[] = [];
    for (const turn of turns) {
      if (turn.response) {
        history.push({ role: "user", content: turn.query });
        history.push({ role: "assistant", content: turn.response.answer });
      }
    }

    const turnId = `turn-${Date.now()}-${turns.length}`;
    setQuery("");

    try {
      const response = await researchMutation.mutateAsync({
        query: trimmed,
        model: selectedModel,
        history,
      });
      setTurns((prev) => [
        ...prev,
        { id: turnId, query: trimmed, model: response.model, response },
      ]);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        {
          id: turnId,
          query: trimmed,
          model: selectedModel,
          error: extractApiErrorMessage(err),
        },
      ]);
    }
  };

  const ollamaUnavailable =
    !configLoading && (configFetchError || config?.ollama_reachable === false);

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {configLoading ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-700 px-3 py-1 text-xs font-medium text-slate-200">
                  Checking Ollama...
                </span>
              ) : ollamaUnavailable ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/20 px-3 py-1 text-xs font-medium text-red-300">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  Ollama unreachable
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-300">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Ollama connected
                </span>
              )}

              <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-500/20 px-3 py-1 text-xs font-medium text-primary-300">
                <Wrench className="h-3.5 w-3.5" />
                {config
                  ? `${config.mcp_tool_count} MCP tools enabled`
                  : "Loading MCP tools..."}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <label
                htmlFor="ai-research-model"
                className="text-xs font-medium text-slate-300"
              >
                Model
              </label>
              <select
                id="ai-research-model"
                value={selectedModel}
                onChange={(e) => setSelectedModelOverride(e.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-white focus:border-primary-500 focus:outline-none"
              >
                {modelOptions.map((modelName) => (
                  <option key={modelName} value={modelName}>
                    {modelName}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {ollamaUnavailable && (
            <div
              role="alert"
              className="flex items-start gap-2.5 rounded-lg border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
              <div>
                <p className="font-medium">Ollama service is unreachable</p>
                <p className="mt-0.5 text-xs text-red-300">
                  {config?.ollama_error ||
                    "Verify OLLAMA_URL is reachable from Elder API."}
                </p>
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label
                htmlFor="ai-research-query"
                className="mb-1.5 block text-sm font-medium text-slate-200"
              >
                Ask Elder AI Researcher (read-only across enabled MCP modules)
              </label>
              <textarea
                id="ai-research-query"
                rows={3}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Ask a cross-module IT, security, dependency, SBOM, or operational question..."
                className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3.5 py-2.5 text-sm text-white placeholder-slate-400 focus:border-primary-500 focus:outline-none"
              />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={researchMutation.isPending}
                  disabled={!query.trim() || researchMutation.isPending}
                >
                  <Send className="mr-1.5 h-4 w-4" />
                  {turns.length > 0 ? "Ask Follow-up" : "Run Research"}
                </Button>
                {turns.length > 0 && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setTurns([])}
                    disabled={researchMutation.isPending}
                  >
                    <RotateCcw className="mr-1.5 h-4 w-4" />
                    New Session
                  </Button>
                )}
              </div>

              <span className="text-xs text-slate-400">
                Read-only analysis using signed short-lived service JWT
              </span>
            </div>
          </form>

          {turns.length === 0 && (
            <div>
              <p className="mb-2 text-xs font-medium text-slate-400">
                Suggested IT &amp; Security prompts:
              </p>
              <div className="flex flex-wrap gap-2">
                {SUGGESTED_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => setQuery(prompt)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-1.5 text-left text-xs text-slate-200 transition-colors hover:border-primary-500 hover:text-white"
                  >
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary-400" />
                    <span>{prompt}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {turns.map((turn) => (
        <Card key={turn.id}>
          <CardContent className="space-y-4">
            <div className="flex items-start justify-between gap-3 border-b border-slate-700 pb-3">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wide text-primary-400">
                  Question
                </span>
                <p className="mt-1 text-sm font-medium text-white">
                  {turn.query}
                </p>
              </div>
              <span className="rounded bg-slate-900 px-2 py-0.5 font-mono text-xs text-slate-300">
                {turn.model}
              </span>
            </div>

            {turn.error ? (
              <div
                role="alert"
                className="flex items-start gap-2.5 rounded-lg border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                <div>
                  <p className="font-medium">Research failed</p>
                  <p className="mt-0.5 text-xs text-red-300">{turn.error}</p>
                </div>
              </div>
            ) : (
              turn.response && (
                <div>
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-emerald-400">
                    <Bot className="h-4 w-4" />
                    <span>Research Answer</span>
                  </div>
                  <StructuredAnswer content={turn.response.answer} />
                  <CitationList
                    citations={turn.response.citations}
                    onNavigate={(url) => navigate(url)}
                  />
                  <ToolTraceAccordion steps={turn.response.steps} />
                </div>
              )
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
