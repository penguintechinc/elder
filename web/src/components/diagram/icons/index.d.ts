/**
 * Main Icon Registry - Combines all icon sources
 */
import type { IconComponent, IconDefinition, IconCategory } from './types';
export * from './types';
export declare const iconMap: Record<string, IconComponent>;
export declare const iconCategories: Record<string, IconCategory>;
export declare function getAllIcons(): IconDefinition[];
export declare function getIcon(id: string): IconComponent | undefined;
export declare function getCategoryKeys(): string[];
export { useIconSearch } from './search/useIconSearch';
export { IconSearch } from './search/IconSearch';
