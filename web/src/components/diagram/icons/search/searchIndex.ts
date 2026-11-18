/**
 * Icon Search Index
 * Provides fast searching and indexing for icon definitions
 * Supports tokenization, prefix matching, and relevance scoring
 */

import type { IconDefinition, SearchOptions, SearchResult } from '../types';

/**
 * Internal token index structure for fast lookups
 */
interface TokenIndex {
  [token: string]: Set<string>; // token -> set of icon IDs
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
 * Tokenize a string into searchable tokens
 * Handles camelCase, kebab-case, snake_case, and spaces
 */
function tokenize(text: string): string[] {
  if (!text) return [];

  return (
    text
      // Convert to lowercase
      .toLowerCase()
      // Split on transitions (camelCase, PascalCase)
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      // Split on non-alphanumeric characters
      .replace(/[^a-z0-9]+/g, ' ')
      // Split into individual tokens
      .split(/\s+/)
      // Remove empty tokens
      .filter((token) => token.length > 0)
  );
}

/**
 * Build a searchable index from icon definitions
 * Creates token mappings for fast lookups
 */
export function buildSearchIndex(icons: IconDefinition[]): SearchIndex {
  const tokenIndex: TokenIndex = {};
  const idIndex = new Map<string, IconDefinition>();

  for (const icon of icons) {
    // Index the icon by ID
    idIndex.set(icon.id, icon);

    // Tokenize and index label
    const labelTokens = tokenize(icon.label);
    for (const token of labelTokens) {
      if (!tokenIndex[token]) {
        tokenIndex[token] = new Set();
      }
      tokenIndex[token].add(icon.id);
    }

    // Tokenize and index ID
    const idTokens = tokenize(icon.id);
    for (const token of idTokens) {
      if (!tokenIndex[token]) {
        tokenIndex[token] = new Set();
      }
      tokenIndex[token].add(icon.id);
    }

    // Tokenize and index tags
    if (icon.tags && Array.isArray(icon.tags)) {
      for (const tag of icon.tags) {
        const tagTokens = tokenize(tag);
        for (const token of tagTokens) {
          if (!tokenIndex[token]) {
            tokenIndex[token] = new Set();
          }
          tokenIndex[token].add(icon.id);
        }
      }
    }
  }

  return {
    icons,
    tokenIndex,
    idIndex,
  };
}

/**
 * Calculate relevance score for an icon based on query match
 * Considers:
 * - Exact label match (highest score)
 * - Label prefix match
 * - Tag matches
 * - ID matches
 */
function calculateRelevanceScore(
  icon: IconDefinition,
  queryTokens: string[],
  _index: SearchIndex
): number {
  let score = 0;
  const labelLower = icon.label.toLowerCase();
  const idLower = icon.id.toLowerCase();
  const tagsLower = (icon.tags || []).map((t) => t.toLowerCase());

  for (const queryToken of queryTokens) {
    // Exact label match (highest priority)
    if (labelLower === queryToken) {
      score += 100;
    }
    // Label contains token as prefix
    else if (labelLower.startsWith(queryToken)) {
      score += 50;
    }
    // Label contains token anywhere
    else if (labelLower.includes(queryToken)) {
      score += 30;
    }

    // Exact ID match
    if (idLower === queryToken) {
      score += 80;
    }
    // ID contains token as prefix
    else if (idLower.startsWith(queryToken)) {
      score += 40;
    }
    // ID contains token anywhere
    else if (idLower.includes(queryToken)) {
      score += 20;
    }

    // Tag matches
    for (const tag of tagsLower) {
      if (tag === queryToken) {
        score += 60;
      } else if (tag.startsWith(queryToken)) {
        score += 25;
      } else if (tag.includes(queryToken)) {
        score += 10;
      }
    }
  }

  return score;
}

/**
 * Search for icons matching a query string
 * Supports prefix matching and relevance-based sorting
 */
export function searchIcons(
  query: string,
  index: SearchIndex,
  options?: SearchOptions
): IconDefinition[] {
  const maxResults = options?.maxResults || 50;
  const allowedSources = new Set(options?.sources);

  // Early return for empty query
  if (!query || query.trim().length === 0) {
    return [];
  }

  // Tokenize the query
  const queryTokens = tokenize(query);

  if (queryTokens.length === 0) {
    return [];
  }

  // Find all icons that match any token (using union of all matches)
  const matchedIconIds = new Set<string>();

  for (const queryToken of queryTokens) {
    // Check for prefix matches in the index
    for (const [token, iconIds] of Object.entries(index.tokenIndex)) {
      if (token.startsWith(queryToken)) {
        Array.from(iconIds).forEach((iconId) => {
          matchedIconIds.add(iconId);
        });
      }
    }
  }

  // Convert to icon definitions with scores
  const results: SearchResult[] = [];

  Array.from(matchedIconIds).forEach((iconId) => {
    const icon = index.idIndex.get(iconId);
    if (!icon) return;

    // Filter by source if specified
    if (allowedSources.size > 0 && !allowedSources.has(icon.source)) {
      return;
    }

    // Calculate relevance score
    const score = calculateRelevanceScore(icon, queryTokens, index);

    // Only include results with positive scores
    if (score > 0) {
      results.push({
        ...icon,
        score,
      });
    }
  });

  // Sort by relevance score (descending), then by label (ascending)
  results.sort((a, b) => {
    if (b.score !== a.score) {
      return b.score - a.score;
    }
    return a.label.localeCompare(b.label);
  });

  // Return top results
  return results.slice(0, maxResults);
}
