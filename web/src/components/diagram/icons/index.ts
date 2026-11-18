/**
 * Main Icon Registry - Combines all icon sources
 */

import type { IconComponent, IconDefinition, IconCategory } from './types';

// Internal icons
import { internalIconMap, internalCategories } from './internal';

// External adapters (import from .tsx files)
import { awsIconMap } from './aws/index';
import { awsCategories } from './aws/categories';
import { azureIconMap } from './azure/index';
import { azureCategories } from './azure/categories';
import { ibmIconMap } from './ibm/index';
import { ibmCategories } from './ibm/categories';
import { iconoirIconMap } from './iconoir/index';
import { iconoirCategories } from './iconoir/categories';

// Re-export types
export * from './types';

// Combined icon map (all sources)
export const iconMap: Record<string, IconComponent> = {
  ...internalIconMap,
  ...awsIconMap,
  ...azureIconMap,
  ...ibmIconMap,
  ...iconoirIconMap,
};

// Combined categories
export const iconCategories: Record<string, IconCategory> = {
  ...internalCategories,
  ...awsCategories,
  ...azureCategories,
  ...ibmCategories,
  ...iconoirCategories,
};

// Get all icons as flat array
export function getAllIcons(): IconDefinition[] {
  return Object.values(iconCategories).flatMap(cat => cat.icons);
}

// Get icon component by ID
export function getIcon(id: string): IconComponent | undefined {
  return iconMap[id];
}

// Get category keys
export function getCategoryKeys(): string[] {
  return Object.keys(iconCategories);
}

// Search export
export { useIconSearch } from './search/useIconSearch';
export { IconSearch } from './search/IconSearch';
