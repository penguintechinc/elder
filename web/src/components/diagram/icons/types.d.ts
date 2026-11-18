/**
 * Icon system TypeScript types
 */
import type { FC } from 'react';
/**
 * Standard props for all icon components
 */
export type IconProps = {
    className?: string;
    size?: number | string;
    color?: string;
};
/**
 * Icon component type
 */
export type IconComponent = FC<IconProps>;
/**
 * Icon sources for filtering and display
 */
export type IconSource = 'internal' | 'aws' | 'azure' | 'gcp' | 'ibm' | 'iconoir';
/**
 * Metadata for each icon (used in categories and search)
 */
export interface IconDefinition {
    id: string;
    label: string;
    color: string;
    source: IconSource;
    tags?: string[];
}
/**
 * Category containing icons
 */
export interface IconCategory {
    label: string;
    source: IconSource;
    icons: IconDefinition[];
}
/**
 * All categories map type
 */
export type IconCategoriesMap = Record<string, IconCategory>;
/**
 * Icon map type (id -> component)
 */
export type IconMap = Record<string, IconComponent>;
/**
 * Search options
 */
export interface SearchOptions {
    maxResults?: number;
    sources?: IconSource[];
}
/**
 * Search result with relevance score
 */
export interface SearchResult extends IconDefinition {
    score: number;
}
