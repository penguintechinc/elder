import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useCallback, useMemo } from 'react';
import { z } from 'zod';
// Re-export z for convenience so consumers don't need to import zod separately
export { z } from 'zod';
/**
 * Build a Zod schema for a field based on its type and configuration.
 * Returns the appropriate validator with required/optional handling.
 */
function buildFieldSchema(field) {
    // If custom schema provided, use it (wrap with optional if not required)
    if (field.schema) {
        return field.required ? field.schema : field.schema.optional();
    }
    let schema;
    switch (field.type) {
        case 'email':
            schema = z.email(`${field.label} must be a valid email`);
            break;
        case 'url':
            schema = z.url(`${field.label} must be a valid URL`);
            break;
        case 'number': {
            let numSchema = z.coerce.number(`${field.label} must be a number`);
            if (field.min !== undefined) {
                numSchema = numSchema.min(field.min, `${field.label} must be at least ${field.min}`);
            }
            if (field.max !== undefined) {
                numSchema = numSchema.max(field.max, `${field.label} must be at most ${field.max}`);
            }
            schema = numSchema;
            break;
        }
        case 'tel':
            schema = z.string().regex(/^[\d\s\-+()]+$/, `${field.label} must be a valid phone number`);
            break;
        case 'date':
            schema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, `${field.label} must be a valid date`);
            break;
        case 'time':
            schema = z.string().regex(/^\d{2}:\d{2}(:\d{2})?$/, `${field.label} must be a valid time`);
            break;
        case 'datetime-local':
            schema = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/, `${field.label} must be a valid date and time`);
            break;
        case 'checkbox':
            schema = z.boolean();
            break;
        case 'checkbox_multi':
            // Multi-select checkboxes return an array of selected values
            if (field.options && field.options.length > 0) {
                const values = field.options.map((o) => String(o.value));
                schema = z.array(z.enum(values));
            }
            else {
                schema = z.array(z.string());
            }
            break;
        case 'select':
        case 'radio':
            if (field.options && field.options.length > 0) {
                const values = field.options.map((o) => String(o.value));
                schema = z.enum(values, `${field.label} must be one of the available options`);
            }
            else {
                schema = z.string();
            }
            break;
        case 'password':
            // Default password validation - at least 8 chars
            schema = z.string().min(8, `${field.label} must be at least 8 characters`);
            break;
        case 'password_generate':
            // Password with generate button - same validation as password
            schema = z.string().min(8, `${field.label} must be at least 8 characters`);
            break;
        case 'file':
            // Single file - validate it's a File or null/undefined
            schema = z.any().refine((val) => val === null || val === undefined || val instanceof File, `${field.label} must be a file`);
            break;
        case 'file_multiple':
            // Multiple files - validate it's an array of Files
            schema = z.any().refine((val) => val === null || val === undefined || (Array.isArray(val) && val.every((f) => f instanceof File)), `${field.label} must be files`);
            break;
        case 'multiline':
            // Multiline is stored as string during editing, converted to string[] on submit
            // Validation runs on the raw string value
            schema = z.string();
            break;
        case 'textarea':
        case 'text':
        default:
            schema = z.string();
            // Apply pattern if specified
            if (field.pattern) {
                schema = z.string().regex(new RegExp(field.pattern), `${field.label} format is invalid`);
            }
            break;
    }
    // Handle required vs optional
    if (field.required) {
        if (field.type === 'checkbox') {
            // For required checkboxes, must be true
            return z.literal(true, `${field.label} must be checked`);
        }
        if (field.type === 'checkbox_multi') {
            // For required multi-select, must have at least one selection
            return schema.refine((val) => Array.isArray(val) && val.length > 0, {
                message: `${field.label} requires at least one selection`,
            });
        }
        // For strings, add non-empty check
        if (field.type === 'text' || field.type === 'textarea' || field.type === 'multiline' ||
            field.type === 'email' || field.type === 'url' || field.type === 'password' ||
            field.type === 'password_generate' || field.type === 'tel' || field.type === 'select' || field.type === 'radio') {
            return schema.refine((val) => val !== undefined && val !== null && val !== '', {
                message: `${field.label} is required`,
            });
        }
        return schema;
    }
    // Optional field - allow empty string or undefined
    if (field.type === 'number') {
        // For optional numbers, allow empty string (coerce handles conversion)
        return schema.optional().or(z.literal(''));
    }
    return schema.optional().or(z.literal(''));
}
/**
 * Generate a random password with mixed case letters and numbers.
 * @param length - Password length (default 14)
 */
export function generatePassword(length = 14) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let password = '';
    for (let i = 0; i < length; i++) {
        password += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return password;
}
// Default dark mode theme with navy background and gold accents
const DEFAULT_COLORS = {
    // Background colors - Navy dark mode
    modalBackground: 'bg-slate-800',
    headerBackground: 'bg-slate-800',
    footerBackground: 'bg-slate-900',
    overlayBackground: 'bg-gray-900 bg-opacity-75',
    // Text colors - Gold and white
    titleText: 'text-amber-400',
    labelText: 'text-amber-300',
    descriptionText: 'text-slate-400',
    errorText: 'text-red-400',
    buttonText: 'text-slate-900',
    // Field colors - White backgrounds for contrast
    fieldBackground: 'bg-white',
    fieldBorder: 'border-slate-600',
    fieldText: 'text-slate-900',
    fieldPlaceholder: 'placeholder-slate-500',
    // Focus/Ring colors - Gold accents
    focusRing: 'focus:ring-amber-500',
    focusBorder: 'focus:border-amber-500',
    // Button colors - Gold primary
    primaryButton: 'bg-amber-500',
    primaryButtonHover: 'hover:bg-amber-600',
    secondaryButton: 'bg-slate-700',
    secondaryButtonHover: 'hover:bg-slate-600',
    secondaryButtonBorder: 'border-slate-600',
    // Tab colors - Gold active, slate inactive
    activeTab: 'text-amber-400',
    activeTabBorder: 'border-amber-500',
    inactiveTab: 'text-slate-400',
    inactiveTabHover: 'hover:text-slate-300 hover:border-slate-500',
    tabBorder: 'border-slate-700',
    errorTabText: 'text-red-400',
    errorTabBorder: 'border-red-500',
};
export const FormModalBuilder = ({ title, fields, tabs: manualTabs, isOpen, onClose, onSubmit, submitButtonText = 'Submit', cancelButtonText = 'Cancel', width = 'md', 
// backgroundColor kept for backwards compatibility but replaced by colors.modalBackground
backgroundColor: _backgroundColor = 'bg-white', maxHeight = 'max-h-[80vh]', zIndex = 9999, autoTabThreshold = 8, fieldsPerTab = 6, colors, }) => {
    void _backgroundColor; // Suppress unused variable warning
    const theme = colors || DEFAULT_COLORS;
    const [formData, setFormData] = useState(() => {
        const initial = {};
        fields.forEach((field) => {
            if (field.defaultValue !== undefined) {
                initial[field.name] = field.defaultValue;
            }
            else if (field.type === 'checkbox') {
                initial[field.name] = false;
            }
            else if (field.type === 'checkbox_multi') {
                initial[field.name] = [];
            }
            else {
                initial[field.name] = '';
            }
        });
        return initial;
    });
    const [errors, setErrors] = useState({});
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [activeTab, setActiveTab] = useState(0);
    const [dragOverField, setDragOverField] = useState(null);
    const widthClasses = {
        sm: 'max-w-sm',
        md: 'max-w-md',
        lg: 'max-w-lg',
        xl: 'max-w-xl',
        '2xl': 'max-w-2xl',
    };
    // Auto-generate tabs if field count exceeds threshold
    const tabs = useMemo(() => {
        if (manualTabs && manualTabs.length > 0) {
            return manualTabs;
        }
        // Check if fields have tab assignments
        const hasTabAssignments = fields.some((f) => f.tab);
        if (hasTabAssignments) {
            const tabMap = new Map();
            fields.forEach((field) => {
                const tabName = field.tab || 'General';
                if (!tabMap.has(tabName)) {
                    tabMap.set(tabName, []);
                }
                tabMap.get(tabName).push(field);
            });
            return Array.from(tabMap.entries()).map(([label, tabFields], index) => ({
                id: `tab-${index}`,
                label,
                fields: tabFields,
            }));
        }
        // Auto-generate tabs if field count exceeds threshold
        if (fields.length > autoTabThreshold) {
            const generatedTabs = [];
            const numTabs = Math.ceil(fields.length / fieldsPerTab);
            for (let i = 0; i < numTabs; i++) {
                const start = i * fieldsPerTab;
                const end = Math.min(start + fieldsPerTab, fields.length);
                generatedTabs.push({
                    id: `tab-${i}`,
                    label: i === 0 ? 'General' : `Step ${i + 1}`,
                    fields: fields.slice(start, end),
                });
            }
            return generatedTabs;
        }
        // No tabs needed
        return null;
    }, [fields, manualTabs, autoTabThreshold, fieldsPerTab]);
    // Filter fields based on conditional visibility (showWhen, triggerField, hidden)
    const isFieldVisible = useCallback((field) => {
        // Check if field is explicitly hidden
        if (field.hidden)
            return false;
        // Check triggerField - field is only visible if the trigger field is truthy
        if (field.triggerField && !formData[field.triggerField])
            return false;
        // Check showWhen function - field is only visible if showWhen returns true
        if (field.showWhen && !field.showWhen(formData))
            return false;
        return true;
    }, [formData]);
    const rawCurrentFields = tabs ? tabs[activeTab]?.fields || [] : fields;
    const currentFields = rawCurrentFields.filter(isFieldVisible);
    const primaryButtonClasses = `w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 ${theme.primaryButton} text-base font-medium ${theme.buttonText} ${theme.primaryButtonHover} focus:outline-none focus:ring-2 focus:ring-offset-2 ${theme.focusRing} sm:w-auto sm:text-sm disabled:opacity-50 disabled:cursor-not-allowed`;
    const secondaryButtonClasses = `mt-3 w-full inline-flex justify-center rounded-md border ${theme.secondaryButtonBorder} shadow-sm px-4 py-2 ${theme.secondaryButton} text-base font-medium ${theme.labelText} ${theme.secondaryButtonHover} focus:outline-none focus:ring-2 focus:ring-offset-2 ${theme.focusRing} sm:mt-0 sm:w-auto sm:text-sm disabled:opacity-50 disabled:cursor-not-allowed`;
    const handleChange = useCallback((name, value) => {
        setFormData((prev) => ({ ...prev, [name]: value }));
        setErrors((prev) => ({ ...prev, [name]: '' }));
    }, []);
    // File upload helpers
    const handleFileChange = useCallback((field, files) => {
        if (!files || files.length === 0)
            return;
        const fileArray = Array.from(files);
        // Validate file size if maxFileSize is set
        if (field.maxFileSize) {
            const oversizedFiles = fileArray.filter(f => f.size > field.maxFileSize);
            if (oversizedFiles.length > 0) {
                const maxSizeMB = (field.maxFileSize / (1024 * 1024)).toFixed(1);
                setErrors(prev => ({ ...prev, [field.name]: `File size must be less than ${maxSizeMB}MB` }));
                return;
            }
        }
        // Validate max files for multiple
        if (field.type === 'file_multiple' && field.maxFiles) {
            const currentFiles = formData[field.name] || [];
            if (currentFiles.length + fileArray.length > field.maxFiles) {
                setErrors(prev => ({ ...prev, [field.name]: `Maximum ${field.maxFiles} files allowed` }));
                return;
            }
        }
        if (field.type === 'file_multiple') {
            const currentFiles = formData[field.name] || [];
            handleChange(field.name, [...currentFiles, ...fileArray]);
        }
        else {
            handleChange(field.name, fileArray[0]);
        }
    }, [formData, handleChange]);
    const handleFileDrop = useCallback((field, e) => {
        e.preventDefault();
        setDragOverField(null);
        handleFileChange(field, e.dataTransfer.files);
    }, [handleFileChange]);
    const removeFile = useCallback((fieldName, index) => {
        if (index !== undefined) {
            // Remove from array (file_multiple)
            const currentFiles = formData[fieldName] || [];
            handleChange(fieldName, currentFiles.filter((_, i) => i !== index));
        }
        else {
            // Clear single file
            handleChange(fieldName, null);
        }
    }, [formData, handleChange]);
    const formatFileSize = (bytes) => {
        if (bytes < 1024)
            return `${bytes} B`;
        if (bytes < 1024 * 1024)
            return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };
    /**
     * Process form data before submission.
     * Converts multiline fields from string to string[] (split by newline).
     */
    const processFormData = useCallback((data) => {
        const processed = {};
        fields.forEach((field) => {
            const value = data[field.name];
            if (field.type === 'multiline' && typeof value === 'string') {
                // Split by newline, trim each line, filter empty lines
                processed[field.name] = value
                    .split('\n')
                    .map((line) => line.trim())
                    .filter(Boolean);
            }
            else {
                processed[field.name] = value;
            }
        });
        return processed;
    }, [fields]);
    const validate = useCallback(() => {
        const newErrors = {};
        // Filter to only validate visible fields
        const visibleFields = fields.filter((field) => {
            if (field.hidden)
                return false;
            if (field.triggerField && !formData[field.triggerField])
                return false;
            if (field.showWhen && !field.showWhen(formData))
                return false;
            return true;
        });
        // Build validation data only for visible fields
        const dataToValidate = {};
        visibleFields.forEach((field) => {
            dataToValidate[field.name] = formData[field.name];
        });
        // Build schema for visible fields only
        const visibleSchemaShape = {};
        visibleFields.forEach((field) => {
            visibleSchemaShape[field.name] = buildFieldSchema(field);
        });
        const visibleSchema = z.object(visibleSchemaShape);
        // Validate with Zod
        const result = visibleSchema.safeParse(dataToValidate);
        if (!result.success) {
            // Zod v4 uses issues array
            const issues = result.error.issues || [];
            issues.forEach((issue) => {
                const fieldName = String(issue.path[0]);
                if (fieldName && !newErrors[fieldName]) {
                    newErrors[fieldName] = issue.message;
                }
            });
        }
        // Also run legacy validation functions for backwards compatibility
        visibleFields.forEach((field) => {
            if (field.validation && !newErrors[field.name]) {
                const value = formData[field.name];
                if (value) {
                    const error = field.validation(value);
                    if (error) {
                        newErrors[field.name] = error;
                    }
                }
            }
        });
        setErrors(newErrors);
        return Object.keys(newErrors).length === 0;
    }, [fields, formData]);
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!validate()) {
            // Find first tab with errors
            if (tabs) {
                for (let i = 0; i < tabs.length; i++) {
                    const tabHasError = tabs[i].fields.some((field) => errors[field.name]);
                    if (tabHasError) {
                        setActiveTab(i);
                        break;
                    }
                }
            }
            return;
        }
        setIsSubmitting(true);
        try {
            // Process form data (e.g., convert multiline strings to arrays)
            const processedData = processFormData(formData);
            await onSubmit(processedData);
            onClose();
        }
        catch (error) {
            console.error('Form submission error:', error);
        }
        finally {
            setIsSubmitting(false);
        }
    };
    const handleNext = () => {
        if (tabs && activeTab < tabs.length - 1) {
            setActiveTab(activeTab + 1);
        }
    };
    const handlePrevious = () => {
        if (activeTab > 0) {
            setActiveTab(activeTab - 1);
        }
    };
    const renderField = (field) => {
        const commonClasses = `mt-1 block w-full rounded-md shadow-sm sm:text-sm ${theme.fieldBackground} ${theme.fieldBorder} ${theme.fieldText} ${theme.fieldPlaceholder} ${theme.focusBorder} ${theme.focusRing}`;
        const errorClasses = errors[field.name] ? `border-red-500 ${theme.errorText}` : '';
        // Helper to wrap field with helpText
        const withHelpText = (element) => (_jsxs(_Fragment, { children: [element, field.helpText && _jsx("p", { className: `text-xs ${theme.descriptionText} mt-1`, children: field.helpText })] }));
        switch (field.type) {
            case 'textarea':
                return withHelpText(_jsx("textarea", { id: field.name, name: field.name, rows: field.rows || 3, value: formData[field.name] || '', onChange: (e) => handleChange(field.name, e.target.value), placeholder: field.placeholder, required: field.required, disabled: field.disabled, className: `${commonClasses} ${errorClasses} ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }));
            case 'multiline':
                // Multiline: displays as textarea but stores/returns as string[] (split by newline)
                // The value in formData is stored as the raw string for editing,
                // but getProcessedFormData() converts it to an array
                return withHelpText(_jsx("textarea", { id: field.name, name: field.name, rows: field.rows || 3, value: formData[field.name] || '', onChange: (e) => handleChange(field.name, e.target.value), placeholder: field.placeholder, required: field.required, disabled: field.disabled, className: `${commonClasses} ${errorClasses} ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }));
            case 'password_generate':
                return withHelpText(_jsxs("div", { className: "flex gap-2 mt-1", children: [_jsx("input", { id: field.name, name: field.name, type: "text", value: formData[field.name] || '', onChange: (e) => handleChange(field.name, e.target.value), placeholder: field.placeholder, required: field.required, disabled: field.disabled, className: `flex-1 block w-full rounded-md shadow-sm sm:text-sm font-mono ${theme.fieldBackground} ${theme.fieldBorder} ${theme.fieldText} ${theme.fieldPlaceholder} ${theme.focusBorder} ${theme.focusRing} ${errorClasses} ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }), _jsx("button", { type: "button", onClick: () => {
                                const password = generatePassword();
                                handleChange(field.name, password);
                                field.onPasswordGenerated?.(password);
                            }, disabled: field.disabled, className: `px-3 py-2 rounded-md ${theme.secondaryButton} ${theme.secondaryButtonHover} ${theme.labelText} ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}`, title: "Generate random password", children: _jsx("svg", { xmlns: "http://www.w3.org/2000/svg", className: "h-4 w-4", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: _jsx("path", { d: "M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" }) }) })] }));
            case 'select':
                return (_jsxs("select", { id: field.name, name: field.name, value: formData[field.name] || '', onChange: (e) => handleChange(field.name, e.target.value), required: field.required, disabled: field.disabled, className: `${commonClasses} ${errorClasses} ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}`, children: [_jsx("option", { value: "", children: "Select..." }), field.options?.map((option) => (_jsx("option", { value: option.value, children: option.label }, option.value)))] }));
            case 'checkbox':
                return (_jsxs("div", { className: "flex items-center", children: [_jsx("input", { id: field.name, name: field.name, type: "checkbox", checked: formData[field.name] || false, onChange: (e) => handleChange(field.name, e.target.checked), disabled: field.disabled, className: `h-4 w-4 rounded ${theme.fieldBorder} ${theme.focusRing} text-amber-500 ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }), _jsx("label", { htmlFor: field.name, className: `ml-2 block text-sm ${theme.labelText} ${field.disabled ? 'opacity-50' : ''}`, children: field.label })] }));
            case 'radio':
                return (_jsx("div", { className: "space-y-2", children: field.options?.map((option) => (_jsxs("div", { className: "flex items-center", children: [_jsx("input", { id: `${field.name}-${option.value}`, name: field.name, type: "radio", value: option.value, checked: formData[field.name] === option.value, onChange: (e) => handleChange(field.name, e.target.value), disabled: field.disabled, className: `h-4 w-4 ${theme.fieldBorder} ${theme.focusRing} text-amber-500 ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }), _jsx("label", { htmlFor: `${field.name}-${option.value}`, className: `ml-2 block text-sm ${theme.labelText} ${field.disabled ? 'opacity-50' : ''}`, children: option.label })] }, option.value))) }));
            case 'checkbox_multi': {
                const selectedValues = formData[field.name] || [];
                return withHelpText(_jsx("div", { className: `space-y-2 max-h-48 overflow-y-auto border ${theme.fieldBorder} rounded-lg p-3 ${theme.fieldBackground}`, children: field.options && field.options.length > 0 ? (field.options.map((option) => (_jsxs("div", { className: "flex items-center", children: [_jsx("input", { id: `${field.name}-${option.value}`, name: field.name, type: "checkbox", value: option.value, checked: selectedValues.includes(String(option.value)), onChange: (e) => {
                                    const value = String(option.value);
                                    const newValues = e.target.checked
                                        ? [...selectedValues, value]
                                        : selectedValues.filter((v) => v !== value);
                                    handleChange(field.name, newValues);
                                }, disabled: field.disabled, className: `h-4 w-4 rounded ${theme.fieldBorder} ${theme.focusRing} text-amber-500 ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }), _jsx("label", { htmlFor: `${field.name}-${option.value}`, className: `ml-2 block text-sm ${theme.labelText} ${field.disabled ? 'opacity-50' : ''}`, children: option.label })] }, option.value)))) : (_jsx("p", { className: `text-sm ${theme.descriptionText}`, children: "No options available" })) }));
            }
            case 'file':
            case 'file_multiple': {
                const isMultiple = field.type === 'file_multiple';
                const currentFile = formData[field.name];
                const currentFiles = formData[field.name] || [];
                const isDragOver = dragOverField === field.name;
                return withHelpText(_jsxs("div", { className: "mt-1", children: [_jsxs("div", { onDragOver: (e) => {
                                e.preventDefault();
                                if (!field.disabled)
                                    setDragOverField(field.name);
                            }, onDragLeave: () => setDragOverField(null), onDrop: (e) => !field.disabled && handleFileDrop(field, e), className: `
                relative border-2 border-dashed rounded-lg p-4 text-center transition-colors
                ${isDragOver ? 'border-amber-500 bg-amber-500/10' : theme.fieldBorder}
                ${field.disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:border-amber-400'}
                ${errorClasses}
              `, children: [_jsx("input", { id: field.name, name: field.name, type: "file", multiple: isMultiple, accept: field.accept, disabled: field.disabled, onChange: (e) => handleFileChange(field, e.target.files), className: "absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed" }), _jsxs("div", { className: "space-y-2", children: [_jsx("svg", { xmlns: "http://www.w3.org/2000/svg", className: `mx-auto h-8 w-8 ${theme.descriptionText}`, fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 1.5, d: "M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" }) }), _jsxs("div", { className: `text-sm ${theme.descriptionText}`, children: [_jsx("span", { className: theme.labelText, children: "Click to upload" }), " or drag and drop"] }), field.accept && (_jsx("p", { className: `text-xs ${theme.descriptionText}`, children: field.accept })), field.maxFileSize && (_jsxs("p", { className: `text-xs ${theme.descriptionText}`, children: ["Max size: ", formatFileSize(field.maxFileSize)] }))] })] }), isMultiple && currentFiles.length > 0 && (_jsx("ul", { className: "mt-2 space-y-1", children: currentFiles.map((file, index) => (_jsxs("li", { className: `flex items-center justify-between text-sm ${theme.labelText} px-2 py-1 rounded ${theme.secondaryButton}`, children: [_jsxs("span", { className: "truncate flex-1 mr-2", children: [file.name, " (", formatFileSize(file.size), ")"] }), _jsx("button", { type: "button", onClick: () => removeFile(field.name, index), disabled: field.disabled, className: `${theme.errorText} hover:opacity-75 ${field.disabled ? 'cursor-not-allowed' : ''}`, children: _jsx("svg", { xmlns: "http://www.w3.org/2000/svg", className: "h-4 w-4", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", d: "M6 18L18 6M6 6l12 12" }) }) })] }, `${file.name}-${index}`))) })), !isMultiple && currentFile && (_jsxs("div", { className: `mt-2 flex items-center justify-between text-sm ${theme.labelText} px-2 py-1 rounded ${theme.secondaryButton}`, children: [_jsxs("span", { className: "truncate flex-1 mr-2", children: [currentFile.name, " (", formatFileSize(currentFile.size), ")"] }), _jsx("button", { type: "button", onClick: () => removeFile(field.name), disabled: field.disabled, className: `${theme.errorText} hover:opacity-75 ${field.disabled ? 'cursor-not-allowed' : ''}`, children: _jsx("svg", { xmlns: "http://www.w3.org/2000/svg", className: "h-4 w-4", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", d: "M6 18L18 6M6 6l12 12" }) }) })] }))] }));
            }
            default:
                return (_jsx("input", { id: field.name, name: field.name, type: field.type, value: formData[field.name] || '', onChange: (e) => handleChange(field.name, e.target.value), placeholder: field.placeholder, required: field.required, disabled: field.disabled, min: field.min, max: field.max, pattern: field.pattern, accept: field.accept, className: `${commonClasses} ${errorClasses} ${field.disabled ? 'opacity-50 cursor-not-allowed' : ''}` }));
        }
    };
    if (!isOpen)
        return null;
    return (_jsx("div", { className: "fixed inset-0 overflow-y-auto", style: { zIndex }, "aria-labelledby": "modal-title", role: "dialog", "aria-modal": "true", children: _jsxs("div", { className: "flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:block sm:p-0", children: [_jsx("div", { className: `fixed inset-0 ${theme.overlayBackground} transition-opacity`, "aria-hidden": "true", onClick: onClose }), _jsx("span", { className: "hidden sm:inline-block sm:align-middle sm:h-screen", "aria-hidden": "true", children: "\u200B" }), _jsxs("div", { className: `inline-block align-bottom ${theme.modalBackground} rounded-lg text-left shadow-xl transform transition-all sm:my-8 sm:align-middle ${widthClasses[width]} sm:w-full ${maxHeight} flex flex-col`, children: [_jsxs("div", { className: `px-4 pt-5 pb-4 sm:p-6 sm:pb-4 border-b ${theme.tabBorder} ${theme.headerBackground}`, children: [_jsx("h3", { className: `text-lg leading-6 font-medium ${theme.titleText}`, id: "modal-title", children: title }), tabs && tabs.length > 1 && (_jsx("div", { className: `mt-4 border-b ${theme.tabBorder}`, children: _jsx("nav", { className: "-mb-px flex space-x-4 overflow-x-auto", "aria-label": "Tabs", children: tabs.map((tab, index) => {
                                            const tabHasError = tab.fields.some((field) => errors[field.name]);
                                            return (_jsxs("button", { type: "button", onClick: () => setActiveTab(index), className: `
                          whitespace-nowrap py-2 px-1 border-b-2 font-medium text-sm flex items-center gap-1
                          ${activeTab === index
                                                    ? `${theme.activeTabBorder} ${theme.activeTab}`
                                                    : tabHasError
                                                        ? `${theme.errorTabBorder} ${theme.errorTabText} hover:border-red-400`
                                                        : `border-transparent ${theme.inactiveTab} ${theme.inactiveTabHover}`}
                        `, children: [tab.label, tabHasError && (_jsx("span", { className: `inline-flex items-center justify-center w-4 h-4 text-xs font-bold ${theme.buttonText} bg-red-500 rounded-full`, children: "!" }))] }, tab.id));
                                        }) }) }))] }), _jsx("div", { className: "flex-1 overflow-y-auto px-4 py-4 sm:px-6", children: _jsx("form", { onSubmit: handleSubmit, className: "space-y-4", children: currentFields.map((field) => (_jsxs("div", { children: [field.type !== 'checkbox' && (_jsxs("label", { htmlFor: field.name, className: `block text-sm font-medium ${theme.labelText}`, children: [field.label, field.required && _jsx("span", { className: `${theme.errorText} ml-1`, children: "*" })] })), field.description && _jsx("p", { className: `text-xs ${theme.descriptionText} mt-1`, children: field.description }), renderField(field), errors[field.name] && _jsx("p", { className: `mt-1 text-sm ${theme.errorText}`, children: errors[field.name] })] }, field.name))) }) }), _jsx("div", { className: `px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse gap-3 border-t ${theme.tabBorder} ${theme.footerBackground}`, children: tabs && tabs.length > 1 ? (_jsxs(_Fragment, { children: [activeTab === tabs.length - 1 ? (_jsx("button", { type: "submit", disabled: isSubmitting, onClick: handleSubmit, className: primaryButtonClasses, children: isSubmitting ? 'Submitting...' : submitButtonText })) : (_jsx("button", { type: "button", onClick: handleNext, className: primaryButtonClasses, children: "Next" })), activeTab > 0 && (_jsx("button", { type: "button", onClick: handlePrevious, className: secondaryButtonClasses, children: "Previous" })), _jsx("button", { type: "button", onClick: onClose, disabled: isSubmitting, className: secondaryButtonClasses, children: cancelButtonText })] })) : (_jsxs(_Fragment, { children: [_jsx("button", { type: "submit", disabled: isSubmitting, onClick: handleSubmit, className: primaryButtonClasses, children: isSubmitting ? 'Submitting...' : submitButtonText }), _jsx("button", { type: "button", onClick: onClose, disabled: isSubmitting, className: secondaryButtonClasses, children: cancelButtonText })] })) })] })] }) }));
};
//# sourceMappingURL=FormModalBuilder.js.map