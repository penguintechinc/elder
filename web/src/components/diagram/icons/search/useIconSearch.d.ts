/**
 * Icon Search Hook
 * Provides search functionality with debouncing and keyboard navigation
 */
import type { IconDefinition } from '../types';
/**
 * Options for the useIconSearch hook
 */
export interface UseIconSearchOptions {
    debounceMs?: number;
    maxResults?: number;
}
/**
 * Return type for the useIconSearch hook
 */
export interface UseIconSearchReturn {
    query: string;
    setQuery: (query: string) => void;
    results: IconDefinition[];
    isLoading: boolean;
    selectedIndex: number;
    setSelectedIndex: (index: number) => void;
    handleKeyDown: (e: React.KeyboardEvent) => void;
    clearSearch: () => void;
}
/**
 * Hook for searching through icons with debouncing and keyboard navigation
 * Manages search state, results, and keyboard interactions
 */
export declare function useIconSearch(allIcons: IconDefinition[], options?: UseIconSearchOptions): UseIconSearchReturn;
