import { useEffect, useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import {
  ChevronDown,
  Download,
  FileCode2,
  FileJson,
  Image as ImageIcon,
  Upload,
} from "lucide-react";

export interface ExportImportMenuProps {
  kind: "diagram" | "playbook";
  onExportPng: () => void | Promise<void>;
  onExportJpg: () => void | Promise<void>;
  onExportSvg: () => void;
  onExportJson: () => void;
  onImportJsonFile: (file: File) => void | Promise<void>;
  disabled?: boolean;
}

/**
 * Toolbar controls for exporting a Diagram or Playbook (PNG, JPG, SVG, JSON)
 * and importing a shared JSON backup file.
 */
export default function ExportImportMenu({
  kind,
  onExportPng,
  onExportJpg,
  onExportSvg,
  onExportJson,
  onImportJsonFile,
  disabled = false,
}: ExportImportMenuProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const menuId = useId();

  const entityLabel = kind === "diagram" ? "diagram" : "playbook";

  useEffect(() => {
    if (!menuOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setMenuOpen(false);
      }
    };

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen]);

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter(
      (el): el is HTMLButtonElement => el !== null,
    );
    if (items.length === 0) return;

    const currentIndex = items.findIndex(
      (el) => el === document.activeElement,
    );

    if (event.key === "ArrowDown") {
      event.preventDefault();
      const next = items[(currentIndex + 1) % items.length];
      next.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const prev = items[(currentIndex - 1 + items.length) % items.length];
      prev.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      items[0].focus();
    } else if (event.key === "End") {
      event.preventDefault();
      items[items.length - 1].focus();
    }
  };

  const runExportAction = async (action: () => void | Promise<void>) => {
    setMenuOpen(false);
    setIsExporting(true);
    try {
      await action();
    } finally {
      setIsExporting(false);
    }
  };

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await onImportJsonFile(file);
  };

  const exportItems = [
    {
      key: "png",
      label: "Export PNG",
      icon: ImageIcon,
      onSelect: () => runExportAction(onExportPng),
    },
    {
      key: "jpg",
      label: "Export JPG",
      icon: ImageIcon,
      onSelect: () => runExportAction(onExportJpg),
    },
    {
      key: "svg",
      label: "Export SVG",
      icon: FileCode2,
      onSelect: () => runExportAction(onExportSvg),
    },
    {
      key: "json",
      label: "Export JSON (Backup / Share)",
      icon: FileJson,
      onSelect: () => runExportAction(onExportJson),
    },
  ];

  return (
    <div className="flex items-center gap-2" ref={containerRef}>
      {/* Export dropdown */}
      <div className="relative">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setMenuOpen((prev) => !prev)}
          disabled={disabled || isExporting}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-controls={menuOpen ? menuId : undefined}
          aria-label={`Export ${entityLabel}`}
          className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-slate-100 border border-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors"
        >
          <Download className="w-4 h-4 text-amber-400" />
          <span>{isExporting ? "Exporting..." : "Export"}</span>
          <ChevronDown className="w-3.5 h-3.5 text-slate-300" />
        </button>

        {menuOpen && (
          <div
            id={menuId}
            role="menu"
            tabIndex={-1}
            aria-label={`Export ${entityLabel} formats`}
            onKeyDown={handleMenuKeyDown}
            className="absolute right-0 mt-1.5 w-60 rounded-lg bg-slate-800 border border-slate-600 shadow-xl py-1 z-50"
          >
            {exportItems.map((item, idx) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.key}
                  ref={(el) => {
                    itemRefs.current[idx] = el;
                  }}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    void item.onSelect();
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-sm text-slate-100 hover:bg-slate-700 focus:bg-slate-700 focus:outline-none transition-colors"
                >
                  <Icon className="w-4 h-4 text-amber-400 shrink-0" />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Import JSON button */}
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        disabled={disabled}
        aria-label={`Import ${entityLabel} JSON`}
        className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-slate-100 border border-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors"
      >
        <Upload className="w-4 h-4 text-sky-400" />
        <span>Import JSON</span>
      </button>

      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        aria-label={`Select ${entityLabel} JSON backup file`}
        onChange={(event) => {
          void handleFileChange(event);
        }}
        className="hidden"
      />
    </div>
  );
}
