/**
 * Icon Search Hook
 * Provides search functionality with debouncing and keyboard navigation
 */

import { useState, useCallback, useMemo, useRef, useEffect } from 'react';
import type { IconDefinition } from '../types';
import { buildSearchIndex, searchIcons } from './searchIndex';

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
export function useIconSearch(
  allIcons: IconDefinition[],
  options?: UseIconSearchOptions
): UseIconSearchReturn {
  const debounceMs = options?.debounceMs || 150;
  const maxResults = options?.maxResults || 50;

  // State
  const [query, setQueryState] = useState('');
  const [results, setResults] = useState<IconDefinition[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);

  // Refs for debouncing
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Build search index once and memoize
  const searchIndex = useMemo(() => {
    return buildSearchIndex(allIcons);
  }, [allIcons]);

  // Perform search and update results
  const performSearch = useCallback(
    (searchQuery: string) => {
      setIsLoading(true);

      try {
        const searchResults = searchIcons(searchQuery, searchIndex, {
          maxResults,
        });
        setResults(searchResults);
        setSelectedIndex(-1); // Reset selection when results change
      } catch (error) {
        console.error('Search error:', error);
        setResults([]);
        setSelectedIndex(-1);
      } finally {
        setIsLoading(false);
      }
    },
    [searchIndex, maxResults]
  );

  // Handle query changes with debouncing
  const setQuery = useCallback(
    (newQuery: string) => {
      setQueryState(newQuery);
      setIsLoading(true);

      // Clear existing timer
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }

      // Set new timer
      debounceTimerRef.current = setTimeout(() => {
        performSearch(newQuery);
      }, debounceMs);
    },
    [debounceMs, performSearch]
  );

  // Clear search
  const clearSearch = useCallback(() => {
    setQuery('');
    setResults([]);
    setSelectedIndex(-1);
  }, [setQuery]);

  // Keyboard navigation handler
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowUp':
          e.preventDefault();
          setSelectedIndex((prevIndex) => {
            const newIndex = prevIndex <= 0 ? results.length - 1 : prevIndex - 1;
            return newIndex;
          });
          break;

        case 'ArrowDown':
          e.preventDefault();
          setSelectedIndex((prevIndex) => {
            const newIndex = prevIndex >= results.length - 1 ? 0 : prevIndex + 1;
            return newIndex;
          });
          break;

        case 'Enter':
          e.preventDefault();
          if (selectedIndex >= 0 && selectedIndex < results.length) {
            const selectedIcon = results[selectedIndex];
            // Dispatch custom event that can be handled by parent components
            const event = new CustomEvent('iconSelected', {
              detail: selectedIcon,
            });
            document.dispatchEvent(event);
          }
          break;

        case 'Escape':
          e.preventDefault();
          clearSearch();
          break;

        default:
          break;
      }
    },
    [results, selectedIndex, clearSearch]
  );

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);

  return {
    query,
    setQuery,
    results,
    isLoading,
    selectedIndex,
    setSelectedIndex,
    handleKeyDown,
    clearSearch,
  };
}
