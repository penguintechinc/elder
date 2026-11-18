import React, { useState, useCallback, useEffect, useRef } from 'react';
import type { IconDefinition, IconMap } from '../types';
import { useIconSearch } from './useIconSearch';

interface IconSearchProps {
  onSelect: (icon: IconDefinition) => void;
  allIcons: IconDefinition[];
  placeholder?: string;
  iconMap: IconMap;
}

/**
 * IconSearch UI Component
 *
 * Features:
 * - Search input with magnifying glass icon
 * - Dropdown results with icon preview, label, and source badge
 * - Keyboard navigation (Up/Down to select, Enter to confirm, Escape to close)
 * - Loading spinner while searching
 * - "No results" message when empty
 * - Max 30 results shown
 */
function IconSearch({
  onSelect,
  allIcons,
  placeholder = 'Search icons...',
  iconMap,
}: IconSearchProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const selectedItemRef = useRef<HTMLLIElement>(null);

  const { results, isLoading, setQuery } = useIconSearch(allIcons, { maxResults: 30 });

  // Handle search query changes
  useEffect(() => {
    setQuery(searchQuery);
    if (searchQuery.trim()) {
      setIsOpen(true);
      setSelectedIndex(-1);
    } else {
      setIsOpen(false);
    }
  }, [searchQuery, setQuery]);

  // Auto-scroll selected item into view
  useEffect(() => {
    if (selectedItemRef.current && dropdownRef.current) {
      selectedItemRef.current.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    }
  }, [selectedIndex]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (!isOpen && e.key !== 'ArrowUp' && e.key !== 'ArrowDown') {
        return;
      }

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setSelectedIndex(prev =>
            prev < results.length - 1 ? prev + 1 : prev
          );
          setIsOpen(true);
          break;

        case 'ArrowUp':
          e.preventDefault();
          setSelectedIndex(prev => (prev > 0 ? prev - 1 : -1));
          break;

        case 'Enter':
          e.preventDefault();
          if (selectedIndex >= 0 && results[selectedIndex]) {
            handleSelectIcon(results[selectedIndex]);
          }
          break;

        case 'Escape':
          e.preventDefault();
          setIsOpen(false);
          setSelectedIndex(-1);
          break;

        default:
          break;
      }
    },
    [isOpen, results, selectedIndex]
  );

  const handleSelectIcon = useCallback(
    (icon: IconDefinition) => {
      onSelect(icon);
      setSearchQuery('');
      setIsOpen(false);
      setSelectedIndex(-1);
      inputRef.current?.blur();
    },
    [onSelect]
  );

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
        setSelectedIndex(-1);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  /**
   * Get badge color based on icon source
   */
  const getSourceBadgeColor = (source: string): string => {
    switch (source) {
      case 'aws':
        return 'bg-orange-500/20 text-orange-300 border-orange-500/30';
      case 'azure':
        return 'bg-blue-500/20 text-blue-300 border-blue-500/30';
      case 'ibm':
        return 'bg-blue-500/20 text-blue-300 border-blue-500/30';
      case 'iconoir':
        return 'bg-purple-500/20 text-purple-300 border-purple-500/30';
      case 'gcp':
        return 'bg-red-500/20 text-red-300 border-red-500/30';
      case 'internal':
      default:
        return 'bg-gray-500/20 text-gray-300 border-gray-500/30';
    }
  };

  /**
   * Get badge label for source
   */
  const getSourceLabel = (source: string): string => {
    return source.charAt(0).toUpperCase() + source.slice(1);
  };

  return (
    <div className="relative w-full">
      {/* Search Input */}
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          {isLoading ? (
            <svg
              className="w-4 h-4 text-dark-500 animate-spin"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
          ) : (
            <svg
              className="w-4 h-4 text-dark-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
          )}
        </div>

        <input
          ref={inputRef}
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => searchQuery.trim() && setIsOpen(true)}
          placeholder={placeholder}
          className="w-full pl-10 pr-4 py-2 bg-dark-800 border border-dark-600 text-white rounded-lg
            placeholder-dark-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500
            transition-colors"
        />
      </div>

      {/* Dropdown Results */}
      {isOpen && (
        <div
          ref={dropdownRef}
          className="absolute z-50 w-full mt-1 bg-dark-900 border border-dark-700 rounded-lg shadow-xl
            max-h-96 overflow-y-auto"
        >
          {isLoading && (
            <div className="px-4 py-8 flex justify-center">
              <div className="flex flex-col items-center gap-2">
                <svg
                  className="w-6 h-6 text-blue-500 animate-spin"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                <span className="text-sm text-dark-400">Searching...</span>
              </div>
            </div>
          )}

          {!isLoading && results.length === 0 && searchQuery.trim() && (
            <div className="px-4 py-8 text-center">
              <p className="text-dark-400">No icons found for "{searchQuery}"</p>
            </div>
          )}

          {!isLoading && results.length > 0 && (
            <ul className="divide-y divide-dark-700">
              {results.map((icon, index) => {
                const IconComponent = iconMap[icon.id];
                const isSelected = index === selectedIndex;

                return (
                  <li
                    key={icon.id}
                    ref={isSelected ? selectedItemRef : null}
                    onClick={() => handleSelectIcon(icon)}
                    className={`px-3 py-3 cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-dark-700'
                        : 'hover:bg-dark-800'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      {/* Icon Preview */}
                      <div className="flex-shrink-0">
                        {IconComponent ? (
                          <IconComponent className="w-6 h-6 text-gray-300" />
                        ) : (
                          <div className="w-6 h-6 bg-dark-600 rounded flex items-center justify-center">
                            <span className="text-xs text-dark-400">?</span>
                          </div>
                        )}
                      </div>

                      {/* Icon Label */}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-white truncate">
                          {icon.label}
                        </p>
                      </div>

                      {/* Source Badge */}
                      <div className="flex-shrink-0">
                        <span
                          className={`inline-block px-2 py-1 text-xs font-medium rounded border
                            ${getSourceBadgeColor(icon.source)}`}
                        >
                          {getSourceLabel(icon.source)}
                        </span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {!isLoading && results.length > 0 && (
            <div className="px-4 py-2 bg-dark-900/50 border-t border-dark-700 text-xs text-dark-500 text-center">
              {results.length} result{results.length !== 1 ? 's' : ''} • Use ↑↓ to navigate, Enter to select
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default IconSearch;

export { IconSearch };
