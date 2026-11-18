/**
 * Iconoir Icon Categories
 * Organizes icons by functional categories with metadata
 */
import type { IconCategory } from '../types';
export declare const iconoirCategories: Record<string, IconCategory>;
/**
 * Get all icons from all categories
 */
export declare const getAllIconoirIcons: () => import("../types").IconDefinition[];
