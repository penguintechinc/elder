import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export const FormField = ({ field, value, error, onChange, onBlur, }) => {
    const handleChange = (e) => {
        const newValue = field.type === 'checkbox'
            ? e.target.checked
            : field.type === 'number'
                ? e.target.value === '' ? '' : Number(e.target.value)
                : e.target.value;
        onChange(field.name, newValue);
        if (field.onChange) {
            field.onChange(newValue);
        }
    };
    const handleBlur = () => {
        if (onBlur) {
            onBlur(field.name);
        }
    };
    const renderInput = () => {
        const commonProps = {
            id: field.name,
            name: field.name,
            value: field.type === 'checkbox' ? undefined : (value ?? ''),
            checked: field.type === 'checkbox' ? Boolean(value) : undefined,
            onChange: handleChange,
            onBlur: handleBlur,
            required: field.required,
            disabled: field.disabled,
            autoFocus: field.autoFocus,
            placeholder: field.placeholder,
            min: field.min,
            max: field.max,
            minLength: field.minLength,
            maxLength: field.maxLength,
            pattern: field.pattern,
            step: field.step,
            className: field.type === 'checkbox'
                ? 'h-4 w-4 text-gold-400 focus:ring-gold-400 border-gray-600 rounded bg-dark-800'
                : 'input',
        };
        switch (field.type) {
            case 'textarea':
                return (_jsx("textarea", { ...commonProps, rows: field.rows || 3, className: "input" }));
            case 'select':
                return (_jsxs("select", { ...commonProps, className: "input", children: [_jsx("option", { value: "", children: "Select..." }), field.options?.map((option) => (_jsx("option", { value: option.value, disabled: option.disabled, children: option.label }, option.value)))] }));
            case 'radio':
                return (_jsx("div", { className: "space-y-2", children: field.options?.map((option) => (_jsxs("label", { className: "flex items-center space-x-2 cursor-pointer", children: [_jsx("input", { type: "radio", name: field.name, value: option.value, checked: value === option.value, onChange: handleChange, onBlur: handleBlur, disabled: option.disabled || field.disabled, required: field.required, className: "h-4 w-4 text-gold-400 focus:ring-gold-400 border-gray-600 bg-dark-800" }), _jsx("span", { className: "text-gray-300", children: option.label })] }, option.value))) }));
            case 'checkbox':
                return (_jsxs("label", { className: "flex items-center space-x-2 cursor-pointer", children: [_jsx("input", { type: "checkbox", ...commonProps }), _jsx("span", { className: "text-gray-300", children: field.label })] }));
            default:
                return _jsx("input", { type: field.type, ...commonProps });
        }
    };
    if (field.type === 'checkbox') {
        return (_jsxs("div", { className: field.className, children: [renderInput(), field.helperText && (_jsx("p", { className: "mt-1 text-sm text-gray-400", children: field.helperText })), error && (_jsx("p", { className: "mt-1 text-sm text-red-500", children: error }))] }));
    }
    return (_jsxs("div", { className: field.className, children: [_jsxs("label", { htmlFor: field.name, className: "block text-sm font-medium text-gray-300 mb-1", children: [field.label, field.required && _jsx("span", { className: "text-red-500 ml-1", children: "*" })] }), renderInput(), field.helperText && !error && (_jsx("p", { className: "mt-1 text-sm text-gray-400", children: field.helperText })), error && (_jsx("p", { className: "mt-1 text-sm text-red-500", children: error }))] }));
};
//# sourceMappingURL=FormField.js.map