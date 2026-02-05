import React from 'react';
import type { ZodType } from 'zod';
export { z } from 'zod';
/**
 * Supported field types for FormModalBuilder:
 *
 * Text inputs:
 * - `text` - Standard text input
 * - `email` - Email with validation
 * - `password` - Password input (hidden characters)
 * - `password_generate` - Password with generate button
 * - `tel` - Phone number
 * - `url` - URL with validation
 *
 * Multi-line text:
 * - `textarea` - Multi-line text input, returns trimmed string
 * - `multiline` - Multi-line input that splits by newline, returns string[] array
 *                 (useful for lists like domains, paths, tags)
 *
 * Selection:
 * - `select` - Dropdown select
 * - `checkbox` - Boolean checkbox
 * - `checkbox_multi` - Multiple checkbox selection (returns array of selected values)
 * - `radio` - Radio button group
 *
 * Date/Time:
 * - `date` - Date picker
 * - `time` - Time picker
 * - `datetime-local` - Date and time picker
 *
 * Files:
 * - `file` - Single file upload with drag & drop
 * - `file_multiple` - Multiple file upload with drag & drop
 *
 * Numeric:
 * - `number` - Numeric input with min/max support
 */
export interface FormField {
    name: string;
    type: 'text' | 'email' | 'password' | 'password_generate' | 'number' | 'tel' | 'url' | 'textarea' | 'multiline' | 'select' | 'checkbox' | 'checkbox_multi' | 'radio' | 'date' | 'time' | 'datetime-local' | 'file' | 'file_multiple';
    label: string;
    description?: string;
    /** Additional help text shown below the field */
    helpText?: string;
    defaultValue?: string | number | boolean;
    placeholder?: string;
    required?: boolean;
    disabled?: boolean;
    hidden?: boolean;
    options?: Array<{
        value: string | number;
        label: string;
    }>;
    min?: number;
    max?: number;
    pattern?: string;
    /** File type filter for file inputs (e.g., "image/*", ".pdf,.doc") */
    accept?: string;
    rows?: number;
    /** @deprecated Use `schema` instead for Zod-based validation */
    validation?: (value: any) => string | null;
    /** Custom Zod schema for field validation. Overrides automatic type-based validation. */
    schema?: ZodType;
    tab?: string;
    /** Field name that must be truthy for this field to be visible */
    triggerField?: string;
    /** Function that returns true if this field should be visible based on current form values */
    showWhen?: (values: Record<string, any>) => boolean;
    /** Callback when password is generated (for password_generate type) */
    onPasswordGenerated?: (password: string) => void;
    /** Maximum file size in bytes (for file inputs) */
    maxFileSize?: number;
    /** Maximum number of files (for file_multiple) */
    maxFiles?: number;
}
export interface FormTab {
    id: string;
    label: string;
    fields: FormField[];
}
export interface ColorConfig {
    modalBackground: string;
    headerBackground: string;
    footerBackground: string;
    overlayBackground: string;
    titleText: string;
    labelText: string;
    descriptionText: string;
    errorText: string;
    buttonText: string;
    fieldBackground: string;
    fieldBorder: string;
    fieldText: string;
    fieldPlaceholder: string;
    focusRing: string;
    focusBorder: string;
    primaryButton: string;
    primaryButtonHover: string;
    secondaryButton: string;
    secondaryButtonHover: string;
    secondaryButtonBorder: string;
    activeTab: string;
    activeTabBorder: string;
    inactiveTab: string;
    inactiveTabHover: string;
    tabBorder: string;
    errorTabText: string;
    errorTabBorder: string;
}
export interface FormModalBuilderProps {
    title: string;
    fields: FormField[];
    tabs?: FormTab[];
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (data: Record<string, any>) => Promise<void> | void;
    submitButtonText?: string;
    cancelButtonText?: string;
    width?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
    backgroundColor?: string;
    maxHeight?: string;
    zIndex?: number;
    autoTabThreshold?: number;
    fieldsPerTab?: number;
    colors?: ColorConfig;
}
/**
 * Generate a random password with mixed case letters and numbers.
 * @param length - Password length (default 14)
 */
export declare function generatePassword(length?: number): string;
export declare const FormModalBuilder: React.FC<FormModalBuilderProps>;
//# sourceMappingURL=FormModalBuilder.d.ts.map