import type { IconCategory, IconDefinition } from '../types';
export declare const awsCategories: Record<string, IconCategory>;
/**
 * Get all AWS icon definitions across all categories
 */
export declare const getAllAwsIcons: () => IconDefinition[];
/**
 * Get AWS icon definition by ID
 */
export declare const getAwsIconDefinition: (id: string) => IconDefinition | undefined;
/**
 * Get all AWS icon IDs
 */
export declare const getAwsIconIds: () => string[];
/**
 * Get AWS category names
 */
export declare const getAwsCategoryNames: () => string[];
/**
 * Get AWS icons by category key
 */
export declare const getAwsIconsByCategory: (categoryKey: string) => IconDefinition[];
