export interface OrganizationOption {
  id: number;
  name: string;
}

export const SCHEDULE_PRESETS: Array<{ value: string; label: string }> = [
  { value: "0", label: "Manual only (no automatic runs)" },
  { value: "900", label: "Every 15 minutes" },
  { value: "3600", label: "Hourly" },
  { value: "21600", label: "Every 6 hours" },
  { value: "86400", label: "Daily" },
  { value: "custom", label: "Custom interval (seconds)" },
];

export const PRESET_INTERVAL_VALUES = new Set([
  "0",
  "900",
  "3600",
  "21600",
  "86400",
]);

export function extractOrganizations(rawData: unknown): OrganizationOption[] {
  const raw = rawData as
    | { organizations?: OrganizationOption[]; items?: OrganizationOption[] }
    | OrganizationOption[]
    | undefined;
  if (Array.isArray(raw)) return raw;
  if (Array.isArray(raw?.organizations)) return raw.organizations;
  if (Array.isArray(raw?.items)) return raw.items;
  return [];
}

export function formatScheduleInterval(seconds?: number | null): string {
  if (seconds == null || seconds === 0) return "Manual only";
  if (seconds === 900) return "Every 15 min";
  if (seconds === 3600) return "Hourly";
  if (seconds === 21600) return "Every 6 hours";
  if (seconds === 86400) return "Daily";
  return `Every ${seconds}s`;
}

export function getApiErrorMessage(err: unknown, fallback: string): string {
  const maybeErr = err as {
    response?: { data?: { error?: string; message?: string } };
    message?: string;
  };
  return (
    maybeErr?.response?.data?.error ||
    maybeErr?.response?.data?.message ||
    maybeErr?.message ||
    fallback
  );
}

export function isSecretConfigured(
  obj: Record<string, unknown> | null | undefined,
  keys: string[],
): boolean {
  if (!obj) return false;
  return keys.some((key) => {
    const val = obj[key];
    return typeof val === "string" ? val.trim().length > 0 : Boolean(val);
  });
}

export async function readUploadedTextFile(file: File): Promise<string> {
  if (typeof file.text === "function") {
    return file.text();
  }
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () =>
      reject(reader.error || new Error("Failed to read file"));
    reader.readAsText(file);
  });
}

export function resolveScheduleState(intervalSeconds?: number | null): {
  preset: string;
  customSeconds: string;
} {
  const numeric = intervalSeconds ?? 3600;
  const asStr = String(numeric);
  if (PRESET_INTERVAL_VALUES.has(asStr)) {
    return { preset: asStr, customSeconds: asStr };
  }
  return { preset: "custom", customSeconds: asStr };
}

export function resolveScheduleIntervalNumber(
  preset: string,
  customSeconds: string,
): number {
  if (preset !== "custom") {
    return Number(preset);
  }
  const parsed = Number(customSeconds);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 3600;
}
