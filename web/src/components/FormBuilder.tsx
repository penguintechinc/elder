import { useState, useEffect, useMemo } from "react";
import { Key } from "lucide-react";
import toast from "react-hot-toast";
import Input from "@/components/Input";
import SearchableSelect from "@/components/SearchableSelect";
import Select from "@/components/Select";
import {
  FormField,
  FormConfig,
  processFormData,
  getDefaultValues,
  validateForm,
} from "@/types/form";

const PRESET_COLORS = [
  "#3b82f6", // Blue
  "#ef4444", // Red
  "#f97316", // Orange
  "#f59e0b", // Amber
  "#10b981", // Emerald
  "#14b8a6", // Teal
  "#06b6d4", // Cyan
  "#6366f1", // Indigo
  "#a855f7", // Purple
  "#ec4899", // Pink
  "#64748b", // Slate
  "#84cc16", // Lime
];

// Password generator helper
const generatePassword = () => {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let password = "";
  for (let i = 0; i < 14; i++) {
    password += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return password;
};

interface FormBuilderProps {
  config: FormConfig;
  initialValues?: Record<string, unknown>;
  onSubmit: (data: Record<string, unknown>) => void;
  onCancel?: () => void;
  isLoading?: boolean;
  className?: string;
}

export default function FormBuilder({
  config,
  initialValues,
  onSubmit,
  onCancel,
  isLoading = false,
  className = "",
}: FormBuilderProps) {
  const defaultValues = useMemo(
    () => getDefaultValues(config.fields),
    [config.fields],
  );
  const [values, setValues] = useState<Record<string, unknown>>(() => ({
    ...defaultValues,
    ...initialValues,
  }));
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Reset values when initialValues change
  useEffect(() => {
    setValues({
      ...defaultValues,
      ...initialValues,
    });
    setErrors({});
  }, [initialValues, defaultValues]);

  const handleChange = (name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }));
    // Clear error when user starts typing
    if (errors[name]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    console.log("[FormBuilder] Submit triggered, current values:", values);

    // Validate all fields
    const validationErrors = validateForm(values, config.fields);
    if (Object.keys(validationErrors).length > 0) {
      console.warn("[FormBuilder] Validation errors:", validationErrors);
      setErrors(validationErrors);
      return;
    }

    const processedData = processFormData(values, config.fields);
    console.log("[FormBuilder] Validation passed, submitting:", processedData);
    onSubmit(processedData);
  };

  const renderField = (field: FormField) => {
    // Check conditional visibility
    if (field.triggerField && !values[field.triggerField]) {
      return null;
    }
    if (field.showWhen && !field.showWhen(values)) {
      return null;
    }

    if (field.hidden) {
      return null;
    }

    const value = values[field.name];
    const error = errors[field.name];
    // Type assertion for HTML input values (unknown -> string when used in form elements)
    const stringValue =
      typeof value === "string" || typeof value === "number"
        ? String(value)
        : "";

    // Helper to wrap field with error message
    const withError = (element: React.ReactNode) => (
      <div key={field.name}>
        {element}
        {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
      </div>
    );

    switch (field.type) {
      case "select":
        if (field.searchable) {
          return withError(
            <div className="space-y-1">
              <span className="block text-sm font-medium text-yellow-500">
                {field.label}
                {field.required && <span className="text-red-400 ml-1">*</span>}
              </span>
              <SearchableSelect
                options={field.options || []}
                value={(value ?? "") as string | number}
                onChange={(val) => handleChange(field.name, val)}
                placeholder={
                  field.placeholder || `Search ${field.label.toLowerCase()}...`
                }
                disabled={field.disabled}
                isLoading={field.isLoading}
                allowCustom={field.allowCustom}
                allowClear={!field.required}
                ariaLabel={field.label}
              />
            </div>,
          );
        }
        return withError(
          <Select
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={(value ?? "") as string | number}
            onChange={(e) => handleChange(field.name, e.target.value)}
          >
            {!field.required && <option value="">Select...</option>}
            {field.options?.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>,
        );

      case "color":
        return withError(
          <div className="space-y-2">
            <span className="block text-sm font-medium text-yellow-500">
              {field.label}
              {field.required && <span className="text-red-400 ml-1">*</span>}
            </span>
            <div className="flex flex-wrap gap-2">
              {PRESET_COLORS.map((hex) => {
                const selected =
                  stringValue.toLowerCase() === hex.toLowerCase();
                return (
                  <button
                    key={hex}
                    type="button"
                    aria-label={`Select color ${hex}`}
                    onClick={() => handleChange(field.name, hex)}
                    disabled={field.disabled}
                    className={`h-7 w-7 rounded-full border-2 transition-transform ${
                      selected
                        ? "scale-110 border-white ring-2 ring-primary-400"
                        : "border-slate-600 hover:scale-105"
                    }`}
                    style={{ backgroundColor: hex }}
                  />
                );
              })}
            </div>
            <div className="flex items-center gap-2">
              <input
                type="color"
                aria-label={`${field.label} picker`}
                value={
                  /^#[0-9a-f]{6}$/i.test(stringValue) ? stringValue : "#3b82f6"
                }
                onChange={(e) => handleChange(field.name, e.target.value)}
                disabled={field.disabled}
                className="h-9 w-12 cursor-pointer rounded border border-slate-600 bg-slate-800 p-0.5"
              />
              <Input
                type="text"
                value={stringValue}
                onChange={(e) => handleChange(field.name, e.target.value)}
                placeholder={field.placeholder || "#3b82f6"}
                disabled={field.disabled}
              />
            </div>
          </div>,
        );

      case "radio":
        return withError(
          <div className="space-y-2">
            <span className="block text-sm font-medium text-yellow-500">
              {field.label}
              {field.required && <span className="text-red-400 ml-1">*</span>}
            </span>
            <div className="flex flex-wrap gap-4">
              {field.options?.map((opt) => (
                <label
                  key={opt.value}
                  className="flex items-center gap-2 text-sm text-slate-200 cursor-pointer"
                >
                  <input
                    type="radio"
                    name={field.name}
                    value={String(opt.value)}
                    checked={String(value ?? "") === String(opt.value)}
                    onChange={(e) => handleChange(field.name, e.target.value)}
                    disabled={field.disabled}
                    className="border-slate-600 bg-slate-700 text-primary-500 focus:ring-primary-500"
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          </div>,
        );

      case "checkbox":
        return (
          <div key={field.name} className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-yellow-500 cursor-pointer">
              <input
                type="checkbox"
                checked={!!value}
                onChange={(e) => handleChange(field.name, e.target.checked)}
                disabled={field.disabled}
                className="rounded border-slate-600 bg-slate-700 text-primary-500 focus:ring-primary-500"
              />
              {field.label}
            </label>
            {field.helpText && (
              <p className="text-xs text-slate-500 ml-6">{field.helpText}</p>
            )}
          </div>
        );

      case "textarea":
      case "multiline":
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-sm font-medium text-yellow-500">
              {field.label}
            </label>
            <textarea
              required={field.required}
              disabled={field.disabled}
              value={stringValue}
              onChange={(e) => handleChange(field.name, e.target.value)}
              placeholder={field.placeholder}
              rows={field.rows || 4}
              className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
            />
            {field.helpText && (
              <p className="text-xs text-slate-500">{field.helpText}</p>
            )}
          </div>
        );

      case "number":
        return withError(
          <Input
            type="number"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={stringValue}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />,
        );

      case "password":
        return (
          <Input
            key={field.name}
            type="password"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={stringValue}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        );

      case "password_generate":
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-sm font-medium text-yellow-500">
              {field.label}
            </label>
            <div className="flex gap-2">
              <Input
                type="text"
                required={field.required}
                disabled={field.disabled}
                value={stringValue}
                onChange={(e) => handleChange(field.name, e.target.value)}
                placeholder={field.placeholder}
                className="flex-1 font-mono"
              />
              <button
                type="button"
                onClick={() => {
                  const newPassword = generatePassword();
                  handleChange(field.name, newPassword);
                  toast.success("Password generated");
                }}
                className="px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition-colors"
                title="Generate random password"
              >
                <Key className="w-4 h-4" />
              </button>
            </div>
          </div>
        );

      case "email":
        return withError(
          <Input
            type="email"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={stringValue}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />,
        );

      case "date":
        return (
          <Input
            key={field.name}
            type="date"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={stringValue}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        );

      // Default: text, username, url, domain, ip, path, slug
      default:
        if (field.searchable) {
          return withError(
            <div className="space-y-1">
              <span className="block text-sm font-medium text-yellow-500">
                {field.label}
                {field.required && <span className="text-red-400 ml-1">*</span>}
              </span>
              <SearchableSelect
                options={field.options || []}
                value={stringValue}
                onChange={(val) => handleChange(field.name, val)}
                placeholder={
                  field.placeholder ||
                  `Search or enter ${field.label.toLowerCase()}...`
                }
                disabled={field.disabled}
                isLoading={field.isLoading}
                allowCustom={field.allowCustom ?? true}
                allowClear={!field.required}
                ariaLabel={field.label}
              />
            </div>,
          );
        }
        return withError(
          <Input
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={stringValue}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />,
        );
    }
  };

  return (
    <form onSubmit={handleSubmit} className={`space-y-4 ${className}`}>
      {config.fields.map(renderField)}

      <div className="flex gap-3 pt-4">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="flex-1 px-4 py-2 text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
          >
            {config.cancelLabel || "Cancel"}
          </button>
        )}
        <button
          type="submit"
          disabled={isLoading}
          className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          {isLoading ? "Saving..." : config.submitLabel || "Submit"}
        </button>
      </div>
    </form>
  );
}

// Re-export types for convenience
export type { FormField, FormConfig, SelectOption } from "@/types/form";
/* eslint-disable react-refresh/only-export-components -- Utility functions exported for convenience alongside FormBuilder component */
export {
  processFieldValue,
  processFormData,
  getDefaultValues,
} from "@/types/form";
