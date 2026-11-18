/**
 * Icon Search Module Exports
 * Provides search functionality, indexing, and React hooks for icon discovery
 */

// Search index functions and types
export { buildSearchIndex, searchIcons } from './searchIndex';
export type { SearchIndex } from './searchIndex';

// Search hook and types
export { useIconSearch } from './useIconSearch';
export type { UseIconSearchOptions, UseIconSearchReturn } from './useIconSearch';

// Icon Search Component (if exists)
export { default as IconSearch } from './IconSearch';
