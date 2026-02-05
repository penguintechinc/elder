import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Modal } from './Modal';
import { FormField } from './FormField';
import { useFormBuilder } from '../../hooks/useFormBuilder';
export const FormBuilder = ({ mode = 'inline', isOpen = true, fields, title, submitLabel = 'Submit', cancelLabel = 'Cancel', onSubmit, onCancel, initialData, validateOnChange = false, validateOnBlur = true, loading = false, error = null, closeOnOverlayClick = true, showCloseButton = true, className = '', }) => {
    const { values, errors, touched, isSubmitting, handleChange, handleBlur, handleSubmit, } = useFormBuilder({
        fields,
        initialData,
        onSubmit,
        validateOnChange,
        validateOnBlur,
    });
    const renderForm = () => (_jsxs("form", { onSubmit: handleSubmit, className: `space-y-4 ${className}`, children: [error && (_jsx("div", { className: "p-3 bg-red-900/20 border border-red-500 rounded text-red-400 text-sm", children: error })), fields.map((field) => (_jsx(FormField, { field: field, value: values[field.name], error: touched[field.name] ? errors[field.name] : undefined, onChange: handleChange, onBlur: handleBlur }, field.name))), _jsxs("div", { className: "flex justify-end gap-3 pt-2", children: [onCancel && (_jsx("button", { type: "button", onClick: onCancel, disabled: isSubmitting || loading, className: "btn-secondary", children: cancelLabel })), _jsx("button", { type: "submit", disabled: isSubmitting || loading, className: "btn-primary", children: isSubmitting || loading ? (_jsxs("span", { className: "flex items-center gap-2", children: [_jsxs("svg", { className: "animate-spin h-4 w-4", xmlns: "http://www.w3.org/2000/svg", fill: "none", viewBox: "0 0 24 24", children: [_jsx("circle", { className: "opacity-25", cx: "12", cy: "12", r: "10", stroke: "currentColor", strokeWidth: "4" }), _jsx("path", { className: "opacity-75", fill: "currentColor", d: "M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" })] }), submitLabel, "..."] })) : (submitLabel) })] })] }));
    if (mode === 'modal') {
        return (_jsx(Modal, { isOpen: isOpen, onClose: onCancel || (() => { }), title: title, closeOnOverlayClick: closeOnOverlayClick, showCloseButton: showCloseButton, children: renderForm() }));
    }
    return (_jsxs("div", { className: className, children: [title && _jsx("h2", { className: "text-xl font-bold text-gold-400 mb-4", children: title }), renderForm()] }));
};
//# sourceMappingURL=FormBuilder.js.map