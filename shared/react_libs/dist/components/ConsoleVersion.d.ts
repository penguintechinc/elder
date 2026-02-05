import * as React from 'react';
/**
 * Version information structure
 */
export interface VersionInfo {
    /** Full version string (e.g., "1.0.0.1737727200") */
    full: string;
    /** Major version number */
    major: number;
    /** Minor version number */
    minor: number;
    /** Patch version number */
    patch: number;
    /** Build epoch timestamp (seconds since Unix epoch) */
    buildEpoch: number;
    /** Human-readable build date */
    buildDate: string;
    /** Semantic version without build (e.g., "1.0.0") */
    semver: string;
}
/**
 * Configuration for console output styling
 */
export interface ConsoleStyleConfig {
    /** Primary color for app name/banner (CSS color) */
    primaryColor?: string;
    /** Secondary color for version info (CSS color) */
    secondaryColor?: string;
    /** Accent color for highlights (CSS color) */
    accentColor?: string;
    /** Background color (CSS color) */
    backgroundColor?: string;
    /** Font family for console output */
    fontFamily?: string;
}
/**
 * Props for ConsoleVersion component
 */
export interface ConsoleVersionProps {
    /** Application name to display */
    appName: string;
    /** Version string in format "Major.Minor.Patch.Epoch" or "Major.Minor.Patch" */
    version: string;
    /** Optional build epoch override (if not included in version string) */
    buildEpoch?: number;
    /** Optional environment name (e.g., "development", "production") */
    environment?: string;
    /** Custom style configuration */
    styleConfig?: ConsoleStyleConfig;
    /** Whether to show banner/header (default: true) */
    showBanner?: boolean;
    /** Banner style: 'elder' for emoji+name (default), 'box' for ASCII art box */
    bannerStyle?: 'elder' | 'box';
    /** Emoji to display before app name (default: '🚀') */
    emoji?: string;
    /** Whether to output to console on mount (default: true) */
    logOnMount?: boolean;
    /** Additional metadata to display (e.g., API URL) */
    metadata?: Record<string, string | number>;
    /** Callback when version info is logged */
    onLog?: (info: VersionInfo) => void;
    /** Children to render (optional - component can be render-less) */
    children?: React.ReactNode;
}
/**
 * Parse version string into structured VersionInfo
 */
export declare function parseVersion(version: string, buildEpochOverride?: number): VersionInfo;
/**
 * Log version information to browser console with styled output
 * Matches Elder's App.tsx console output pattern
 */
export declare function logVersionToConsole(appName: string, versionInfo: VersionInfo, options?: {
    environment?: string;
    styleConfig?: ConsoleStyleConfig;
    showBanner?: boolean;
    bannerStyle?: 'elder' | 'box';
    emoji?: string;
    metadata?: Record<string, string | number>;
}): void;
/**
 * ConsoleVersion Component
 *
 * Logs build version and epoch information to the browser console on mount.
 * Inspired by Elder's console output pattern with gold/dark theme styling.
 *
 * @example
 * // Basic usage - just logs to console
 * <ConsoleVersion appName="MyApp" version="1.0.0.1737727200" />
 *
 * @example
 * // With environment and metadata
 * <ConsoleVersion
 *   appName="MyApp"
 *   version="1.0.0.1737727200"
 *   environment="development"
 *   metadata={{ 'API URL': 'http://localhost:5000' }}
 * />
 *
 * @example
 * // With custom styling
 * <ConsoleVersion
 *   appName="MyApp"
 *   version="1.0.0"
 *   buildEpoch={1737727200}
 *   styleConfig={{ primaryColor: '#00ff00' }}
 * />
 */
export declare function ConsoleVersion({ appName, version, buildEpoch, environment, styleConfig, showBanner, bannerStyle, emoji, logOnMount, metadata, onLog, children, }: ConsoleVersionProps): React.ReactNode;
/**
 * Hook to get parsed version info without console output
 *
 * @example
 * const versionInfo = useVersionInfo("1.0.0.1737727200");
 * console.log(versionInfo.buildDate); // "2025-01-24 12:00:00 UTC"
 */
export declare function useVersionInfo(version: string, buildEpoch?: number): VersionInfo;
/**
 * API status response structure
 */
export interface ApiStatusResponse {
    /** API version string */
    version?: string;
    /** Build epoch timestamp */
    build_epoch?: number;
    /** Any additional fields */
    [key: string]: unknown;
}
/**
 * Props for AppConsoleVersion component
 */
export interface AppConsoleVersionProps {
    /** Application name (used as prefix for WebUI/API labels) */
    appName: string;
    /** WebUI version string */
    webuiVersion: string;
    /** WebUI build epoch (optional if included in version string) */
    webuiBuildEpoch?: number;
    /** Environment name (e.g., "development", "production") */
    environment?: string;
    /** API status endpoint URL (default: "/api/v1/status") */
    apiStatusUrl?: string;
    /** Custom style configuration */
    styleConfig?: ConsoleStyleConfig;
    /** Banner style: 'elder' for emoji+name (default), 'box' for ASCII art box */
    bannerStyle?: 'elder' | 'box';
    /** Emoji for WebUI version (default: '🖥️') */
    webuiEmoji?: string;
    /** Emoji for API version (default: '⚙️') */
    apiEmoji?: string;
    /** Additional metadata to display with WebUI version */
    metadata?: Record<string, string | number>;
    /** Callback when WebUI version is logged */
    onWebuiLog?: (info: VersionInfo) => void;
    /** Callback when API version is logged */
    onApiLog?: (info: VersionInfo) => void;
    /** Callback on API fetch error */
    onApiError?: (error: Error) => void;
    /** Children to render (optional) */
    children?: React.ReactNode;
}
/**
 * AppConsoleVersion Component
 *
 * Logs both WebUI and API build version/epoch information to the browser console.
 * WebUI version logs immediately on mount. API version is fetched and logged after.
 *
 * @example
 * // Basic usage - logs both WebUI and API versions
 * <AppConsoleVersion
 *   appName="Elder"
 *   webuiVersion={import.meta.env.VITE_VERSION || '0.0.0'}
 *   webuiBuildEpoch={Number(import.meta.env.VITE_BUILD_TIME) || 0}
 *   environment={import.meta.env.MODE}
 * />
 *
 * @example
 * // With custom API endpoint and metadata
 * <AppConsoleVersion
 *   appName="MyApp"
 *   webuiVersion="1.0.0.1737727200"
 *   environment="production"
 *   apiStatusUrl="/api/v2/health"
 *   metadata={{ 'API URL': 'https://api.example.com' }}
 * />
 */
export declare function AppConsoleVersion({ appName, webuiVersion, webuiBuildEpoch, environment, apiStatusUrl, styleConfig, bannerStyle, webuiEmoji, apiEmoji, metadata, onWebuiLog, onApiLog, onApiError, children, }: AppConsoleVersionProps): React.ReactNode;
/**
 * Hook to fetch and parse API version info
 *
 * @example
 * const { apiVersion, loading, error } = useApiVersionInfo('/api/v1/status');
 */
export declare function useApiVersionInfo(apiStatusUrl?: string): {
    apiVersion: VersionInfo | null;
    loading: boolean;
    error: Error | null;
};
export default ConsoleVersion;
//# sourceMappingURL=ConsoleVersion.d.ts.map