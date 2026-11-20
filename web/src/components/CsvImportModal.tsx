import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Upload, Download, X, AlertCircle, CheckCircle2, FileSpreadsheet } from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";

interface CsvTemplateSpec {
  resource: string;
  label: string;
  max_rows: number;
  headers: string[];
  required: string[];
  rules: Record<string, string>;
  example: string[];
}

interface CsvImportModalProps {
  resource: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export default function CsvImportModal({
  resource,
  isOpen,
  onClose,
  onSuccess,
}: CsvImportModalProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [csvText, setCsvText] = useState("");
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [headerMismatch, setHeaderMismatch] = useState<{
    expected?: string[];
    actual?: string[];
  } | null>(null);

  const { data: templatesData } = useQuery<{
    max_rows: number;
    templates: CsvTemplateSpec[];
  }>({
    queryKey: ["csv-templates"],
    queryFn: () => api.getCsvTemplates(),
    enabled: isOpen,
  });

  const spec = templatesData?.templates?.find((t) => t.resource === resource);

  const importMutation = useMutation({
    mutationFn: (payload: File | string) =>
      api.importCsvResource(resource, payload),
    onSuccess: (res: { imported: number }) => {
      toast.success(`Imported ${res.imported} row(s) successfully`);
      setValidationErrors([]);
      setHeaderMismatch(null);
      setSelectedFile(null);
      setCsvText("");
      onSuccess?.();
      onClose();
    },
    onError: (err: {
      response?: {
        data?: {
          error?: string;
          message?: string;
          details?: string[];
          expected_headers?: string[];
          actual_headers?: string[];
        };
      };
    }) => {
      const data = err?.response?.data;
      if (data?.details?.length) {
        setValidationErrors(data.details);
        setHeaderMismatch(null);
      } else if (data?.expected_headers) {
        setHeaderMismatch({
          expected: data.expected_headers,
          actual: data.actual_headers,
        });
        setValidationErrors([]);
      } else {
        setValidationErrors([
          data?.error || data?.message || "Failed to import CSV",
        ]);
      }
      toast.error(data?.error || "CSV validation failed");
    },
  });

  if (!isOpen) return null;

  const handleDownloadTemplate = async () => {
    try {
      const content = await api.downloadCsvTemplate(resource);
      const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${resource}_template.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to download template");
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setValidationErrors([]);
    setHeaderMismatch(null);
    if (selectedFile) {
      importMutation.mutate(selectedFile);
      return;
    }
    if (csvText.trim()) {
      importMutation.mutate(csvText);
      return;
    }
    toast.error("Please select a .csv file or paste CSV content");
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader className="flex flex-row items-center justify-between">
          <div className="flex items-center gap-2">
            <FileSpreadsheet className="w-5 h-5 text-primary-400" />
            <h2 className="text-xl font-semibold text-white">
              Import {spec?.label || resource} from CSV
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-white rounded"
            aria-label="Close CSV import modal"
          >
            <X className="w-5 h-5" />
          </button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="p-3 rounded-lg bg-slate-800/70 border border-slate-700 space-y-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-xs font-medium text-slate-300">
                Exact Template Required (max {spec?.max_rows ?? 1000} rows)
              </span>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={handleDownloadTemplate}
                >
                  <Download className="w-3.5 h-3.5 mr-1.5" />
                  Download Template
                </Button>
                <Link
                  to="/csv-templates"
                  className="text-xs text-primary-400 hover:text-primary-300 underline"
                >
                  All Templates
                </Link>
              </div>
            </div>
            {spec && (
              <div className="text-xs text-slate-400 font-mono bg-slate-900 p-2 rounded overflow-x-auto">
                {spec.headers.join(",")}
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="csv-upload-file"
                className="block text-sm font-medium text-slate-300 mb-1.5"
              >
                Upload CSV File (.csv, max 5 MB / 1,000 rows)
              </label>
              <input
                id="csv-upload-file"
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  const f = e.target.files?.[0] || null;
                  setSelectedFile(f);
                  if (f) setCsvText("");
                }}
                className="block w-full text-sm text-slate-300 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-600 file:text-white hover:file:bg-primary-500"
              />
            </div>

            <div>
              <label
                htmlFor="csv-paste-text"
                className="block text-sm font-medium text-slate-300 mb-1.5"
              >
                Or Paste CSV Content
              </label>
              <textarea
                id="csv-paste-text"
                value={csvText}
                onChange={(e) => {
                  setCsvText(e.target.value);
                  if (e.target.value) setSelectedFile(null);
                }}
                rows={5}
                placeholder={
                  spec
                    ? `${spec.headers.join(",")}\n${spec.example.join(",")}`
                    : "Paste CSV rows matching the exact header template..."
                }
                className="block w-full px-3 py-2 font-mono text-xs bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>

            {headerMismatch && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-300 space-y-1">
                <div className="flex items-center gap-1.5 font-semibold text-red-400">
                  <AlertCircle className="w-4 h-4" />
                  Exact header mismatch
                </div>
                <p>Expected: {headerMismatch.expected?.join(", ")}</p>
                <p>Actual: {headerMismatch.actual?.join(", ")}</p>
              </div>
            )}

            {validationErrors.length > 0 && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-300 max-h-48 overflow-y-auto space-y-1">
                <div className="flex items-center gap-1.5 font-semibold text-red-400">
                  <AlertCircle className="w-4 h-4" />
                  Validation Errors
                </div>
                {validationErrors.map((err, i) => (
                  <div key={i}>{err}</div>
                ))}
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={importMutation.isPending}>
                <Upload className="w-4 h-4 mr-2" />
                {importMutation.isPending ? "Validating & Importing..." : "Validate & Import"}
              </Button>
            </div>
          </form>

          {spec && (
            <div className="pt-2 border-t border-slate-700">
              <p className="text-xs font-medium text-slate-300 mb-2 flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
                Column Validation Rules
              </p>
              <div className="grid grid-cols-1 gap-1 text-xs text-slate-400">
                {spec.headers.map((h) => (
                  <div key={h}>
                    <span className="font-mono text-slate-200">{h}</span>:{" "}
                    {spec.rules[h]}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
