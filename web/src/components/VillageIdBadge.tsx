import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Copy, Link2, Share2 } from "lucide-react";

export interface VillageIdBadgeProps {
  villageId: string;
  className?: string;
  compact?: boolean;
  showLink?: boolean;
}

export default function VillageIdBadge({
  villageId,
  className = "",
  compact = false,
  showLink = true,
}: VillageIdBadgeProps) {
  const [copiedLink, setCopiedLink] = useState(false);
  const [copiedId, setCopiedId] = useState(false);
  const linkTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (linkTimerRef.current) clearTimeout(linkTimerRef.current);
      if (idTimerRef.current) clearTimeout(idTimerRef.current);
    };
  }, []);

  const origin =
    typeof window !== "undefined" && window.location?.origin
      ? window.location.origin
      : "";
  const permalink = `${origin}/id/${villageId}`;

  const displayId =
    compact && villageId.length > 14
      ? `${villageId.slice(0, 6)}...${villageId.slice(-4)}`
      : villageId;

  const handleCopyPermalink = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(permalink);
      setCopiedLink(true);
      if (linkTimerRef.current) clearTimeout(linkTimerRef.current);
      linkTimerRef.current = setTimeout(() => setCopiedLink(false), 2000);
    } catch (err) {
      console.error("Failed to copy Village ID link:", err);
    }
  };

  const handleCopyId = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(villageId);
      setCopiedId(true);
      if (idTimerRef.current) clearTimeout(idTimerRef.current);
      idTimerRef.current = setTimeout(() => setCopiedId(false), 2000);
    } catch (err) {
      console.error("Failed to copy Village ID:", err);
    }
  };

  const paddingClasses = compact
    ? "px-1.5 py-0.5 text-[11px] gap-1"
    : "px-2 py-1 text-xs gap-1.5";
  const iconClasses = compact ? "w-3 h-3" : "w-3.5 h-3.5";

  return (
    <span
      className={`inline-flex items-center max-w-full rounded-md border border-slate-700/80 bg-slate-900/90 text-slate-300 shadow-sm ${paddingClasses} ${className}`}
      data-testid="village-id-badge"
    >
      {showLink ? (
        <Link
          to={`/id/${villageId}`}
          onClick={(e) => e.stopPropagation()}
          title={`Open ${permalink}`}
          className="inline-flex items-center gap-1 font-mono text-amber-300/90 hover:text-amber-200 transition-colors truncate"
        >
          <Link2 className={`${iconClasses} text-amber-400/80 shrink-0`} />
          <span className="truncate">{displayId}</span>
        </Link>
      ) : (
        <span
          title={villageId}
          className="inline-flex items-center gap-1 font-mono text-slate-200 truncate"
        >
          <Link2 className={`${iconClasses} text-slate-400 shrink-0`} />
          <span className="truncate">{displayId}</span>
        </span>
      )}

      <span className="h-3 w-px bg-slate-700/80 shrink-0" aria-hidden="true" />

      <button
        type="button"
        onClick={handleCopyPermalink}
        aria-label="Copy Village ID link"
        title="Copy shareable link (/id/...)"
        className="inline-flex items-center justify-center p-0.5 rounded text-slate-400 hover:text-amber-300 hover:bg-slate-800 transition-colors shrink-0"
      >
        {copiedLink ? (
          <Check className={`${iconClasses} text-emerald-400`} />
        ) : (
          <Share2 className={iconClasses} />
        )}
      </button>

      <button
        type="button"
        onClick={handleCopyId}
        aria-label="Copy Village ID"
        title="Copy Village ID"
        className="inline-flex items-center justify-center p-0.5 rounded text-slate-400 hover:text-white hover:bg-slate-800 transition-colors shrink-0"
      >
        {copiedId ? (
          <Check className={`${iconClasses} text-emerald-400`} />
        ) : (
          <Copy className={iconClasses} />
        )}
      </button>
    </span>
  );
}
