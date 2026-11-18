/**
 * Icon Search Index
 * Provides fast searching and indexing for icon definitions
 * Supports tokenization, prefix matching, and relevance scoring
 */
import type { IconDefinition, SearchOptions } from '../types';
/**
 * Internal token index structure for fast lookups
 */
interface TokenIndex {
    [token: string]: Set<string>;
}
/**
 * Search index containing tokenized icon data
 */
export interface SearchIndex {
    icons: IconDefinition[];
    tokenIndex: TokenIndex;
    idIndex: Map<string, IconDefinition>;
}
/**
 * Build a searchable index from icon definitions
 * Creates token mappings for fast lookups
 */
export declare function buildSearchIndex(icons: IconDefinition[]): SearchIndex;
/**
 * Search for icons matching a query string
 * Supports prefix matching and relevance-based sorting
 */
export declare function searchIcons(query: string, index: SearchIndex, options?: SearchOptions): IconDefinition[];
export {};
