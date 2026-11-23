import { useState, useRef, useEffect, useCallback, useId } from "react";

interface Option {
  value: string | number;
  label: string;
}

interface SearchableSelectProps {
  options?: Option[];
  value?: string | number;
  onChange: (value: string | number, option: Option) => void;
  onSearch?: (query: string) => void;
  placeholder?: string;
  isLoading?: boolean;
  disabled?: boolean;
  allowCustom?: boolean;
  allowClear?: boolean;
  className?: string;
  ariaLabel?: string;
}

export default function SearchableSelect({
  options = [],
  value,
  onChange,
  onSearch,
  placeholder = "Search...",
  isLoading = false,
  disabled = false,
  allowCustom = false,
  allowClear = false,
  className = "",
  ariaLabel,
}: SearchableSelectProps) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  // Find selected option label
  const selectedOption = options.find((o) => String(o.value) === String(value));

  // Filter options by query (client-side filtering when onSearch is not provided)
  const filteredOptions = onSearch
    ? options
    : options.filter((o) =>
        o.label.toLowerCase().includes(query.toLowerCase()),
      );

  // Debounced search callback
  const handleQueryChange = useCallback(
    (newQuery: string) => {
      setQuery(newQuery);
      setHighlightedIndex(-1);
      if (onSearch) {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => onSearch(newQuery), 300);
      }
    },
    [onSearch],
  );

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const handleSelect = (option: Option) => {
    onChange(option.value, option);
    setIsOpen(false);
    setQuery("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) {
      if (e.key === "ArrowDown" || e.key === "Enter") {
        setIsOpen(true);
        e.preventDefault();
      }
      return;
    }

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlightedIndex((prev) =>
          prev < filteredOptions.length - 1 ? prev + 1 : 0,
        );
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlightedIndex((prev) =>
          prev > 0 ? prev - 1 : filteredOptions.length - 1,
        );
        break;
      case "Enter":
        e.preventDefault();
        if (
          highlightedIndex >= 0 &&
          highlightedIndex < filteredOptions.length
        ) {
          handleSelect(filteredOptions[highlightedIndex]);
        } else if (allowCustom && query.trim()) {
          const custom = query.trim();
          handleSelect({ value: custom, label: custom });
        }
        break;
      case "Escape":
        setIsOpen(false);
        setQuery("");
        break;
    }
  };

  const trimmedQuery = query.trim();
  const showCustomOption =
    allowCustom &&
    trimmedQuery.length > 0 &&
    !options.some(
      (o) => String(o.value).toLowerCase() === trimmedQuery.toLowerCase(),
    );
  const displayValue =
    selectedOption?.label ?? (allowCustom && value ? String(value) : "");
  const hasValue = value !== undefined && value !== "" && value !== null;

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={isOpen}
        aria-controls={listboxId}
        aria-autocomplete="list"
        value={isOpen ? query : displayValue}
        onChange={(e) => handleQueryChange(e.target.value)}
        onFocus={() => {
          setIsOpen(true);
          setQuery(allowCustom && value ? String(value) : "");
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        className={`w-full rounded-lg border border-slate-600 bg-slate-700 py-2 pl-3 text-sm text-white placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:opacity-50 ${
          allowClear && hasValue ? "pr-14" : "pr-8"
        }`}
      />
      {/* Clear button & Dropdown chevron */}
      <div className="absolute inset-y-0 right-0 flex items-center gap-1 pr-2.5">
        {allowClear && hasValue && !disabled && (
          <button
            type="button"
            aria-label="Clear selection"
            onClick={(e) => {
              e.stopPropagation();
              onChange("", { value: "", label: "" });
              setQuery("");
            }}
            className="rounded p-0.5 text-slate-400 hover:text-white"
          >
            <svg
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        )}
        <svg
          className="pointer-events-none h-4 w-4 text-slate-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </div>

      {isOpen && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-slate-600 bg-slate-700 py-1 shadow-lg"
        >
          {showCustomOption && (
            <button
              type="button"
              role="option"
              aria-selected={false}
              onClick={() =>
                handleSelect({ value: trimmedQuery, label: trimmedQuery })
              }
              className="w-full border-b border-slate-600 px-3 py-2 text-left text-sm text-primary-300 hover:bg-slate-600"
            >
              Use &ldquo;{trimmedQuery}&rdquo;
            </button>
          )}
          {isLoading ? (
            <div className="px-3 py-2 text-sm text-slate-400">Loading...</div>
          ) : filteredOptions.length === 0 && !showCustomOption ? (
            <div className="px-3 py-2 text-sm text-slate-400">
              {query
                ? "No results found"
                : onSearch
                  ? "Type to search..."
                  : "No options available"}
            </div>
          ) : (
            filteredOptions.map((option, index) => (
              <button
                key={`${option.value}`}
                type="button"
                role="option"
                aria-selected={String(option.value) === String(value)}
                onClick={() => handleSelect(option)}
                className={`w-full px-3 py-2 text-left text-sm transition-colors ${
                  index === highlightedIndex
                    ? "bg-primary-600 text-white"
                    : String(option.value) === String(value)
                      ? "bg-slate-600 text-white"
                      : "text-slate-200 hover:bg-slate-600"
                }`}
              >
                {option.label}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
