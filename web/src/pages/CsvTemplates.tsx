import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Upload, FileSpreadsheet, CheckCircle2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import CsvImportModal from "@/components/CsvImportModal";

interface CsvTemplateSpec {
  resource: string;
  label: string;
  max_rows: number;
  headers: string[];
  required: string[];
  rules: Record<string, string>;
  example: string[];
}

export default function CsvTemplates() {
  const [importResource, setImportResource] = useState<string | null>(null);

  const { data, isLoading } = useQuery<{
    max_rows: number;
    templates: CsvTemplateSpec[];
  }>({
    queryKey: ["csv-templates"],
    queryFn: () => api.getCsvTemplates(),
  });

  const handleDownload = async (resource: string) => {
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

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white flex items-center gap-3">
          <FileSpreadsheet className="w-8 h-8 text-primary-400" />
          CSV Import Templates
        </h1>
        <p className="mt-2 text-slate-400">
          Download exact CSV templates (up to {data?.max_rows ?? 1000} rows per upload). Headers and column order are strictly validated on import.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {data?.templates?.map((tpl) => (
            <Card key={tpl.resource}>
              <CardHeader className="flex flex-row items-center justify-between gap-2">
                <div>
                  <h2 className="text-xl font-semibold text-white">
                    {tpl.label}
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Max {tpl.max_rows} rows • {tpl.headers.length} columns
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleDownload(tpl.resource)}
                  >
                    <Download className="w-4 h-4 mr-1.5" />
                    Template
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setImportResource(tpl.resource)}
                  >
                    <Upload className="w-4 h-4 mr-1.5" />
                    Import
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div>
                  <div className="text-xs uppercase text-slate-500 mb-1">
                    Exact Header Row
                  </div>
                  <div className="text-xs font-mono text-primary-300 bg-slate-900 border border-slate-700 rounded p-2 overflow-x-auto">
                    {tpl.headers.join(",")}
                  </div>
                </div>
                <div className="space-y-1">
                  {tpl.headers.map((h) => (
                    <div
                      key={h}
                      className="text-xs text-slate-400 flex items-start gap-1.5"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5 text-green-400 mt-0.5 flex-shrink-0" />
                      <span>
                        <span className="font-mono text-slate-200">{h}</span>
                        {tpl.required.includes(h) && (
                          <span className="text-red-400 ml-1">*</span>
                        )}
                        : {tpl.rules[h]}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {importResource && (
        <CsvImportModal
          resource={importResource}
          isOpen={!!importResource}
          onClose={() => setImportResource(null)}
        />
      )}
    </div>
  );
}
