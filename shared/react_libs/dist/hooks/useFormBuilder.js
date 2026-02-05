/**
 * useFormBuilder Hook
 *
 * Custom React hook for form state management with validation.
 * Handles form values, errors, touched fields, and submission.
 */
import { useState, useCallback, useMemo } from 'react';
export const useFormBuilder = ({ fields, initialData = {}, onSubmit, validateOnChange = false, validateOnBlur = true, }) => {
    const initialValues = useMemo(() => {
        const defaults = {};
        fields.forEach((field) => {
            defaults[field.name] = initialData[field.name] ?? field.defaultValue ?? '';
        });
        return defaults;
    }, [fields, initialData]);
    const [values, setValuesState] = useState(initialValues);
    const [errors, setErrors] = useState({});
    const [touched, setTouched] = useState({});
    const [isSubmitting, setIsSubmitting] = useState(false);
    const validateField = useCallback((field, value) => {
        if (field.required && (value === '' || value === null || value === undefined)) {
            return `${field.label} is required`;
        }
        if (field.validate) {
            return field.validate(value);
        }
        if (field.type === 'email' && value) {
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (!emailRegex.test(value)) {
                return 'Invalid email address';
            }
        }
        if (field.type === 'url' && value) {
            try {
                new URL(value);
            }
            catch {
                return 'Invalid URL';
            }
        }
        if (field.minLength && value && value.length < field.minLength) {
            return `Must be at least ${field.minLength} characters`;
        }
        if (field.maxLength && value && value.length > field.maxLength) {
            return `Must be at most ${field.maxLength} characters`;
        }
        if (field.min !== undefined && value < field.min) {
            return `Must be at least ${field.min}`;
        }
        if (field.max !== undefined && value > field.max) {
            return `Must be at most ${field.max}`;
        }
        if (field.pattern && value) {
            const regex = new RegExp(field.pattern);
            if (!regex.test(value)) {
                return 'Invalid format';
            }
        }
        return null;
    }, []);
    const validateAllFields = useCallback(() => {
        const newErrors = {};
        fields.forEach((field) => {
            const error = validateField(field, values[field.name]);
            if (error) {
                newErrors[field.name] = error;
            }
        });
        return newErrors;
    }, [fields, values, validateField]);
    const handleChange = useCallback((name, value) => {
        setValuesState((prev) => ({ ...prev, [name]: value }));
        if (validateOnChange) {
            const field = fields.find((f) => f.name === name);
            if (field) {
                const error = validateField(field, value);
                setErrors((prev) => {
                    const newErrors = { ...prev };
                    if (error) {
                        newErrors[name] = error;
                    }
                    else {
                        delete newErrors[name];
                    }
                    return newErrors;
                });
            }
        }
    }, [fields, validateField, validateOnChange]);
    const handleBlur = useCallback((name) => {
        setTouched((prev) => ({ ...prev, [name]: true }));
        if (validateOnBlur) {
            const field = fields.find((f) => f.name === name);
            if (field) {
                const error = validateField(field, values[name]);
                setErrors((prev) => {
                    const newErrors = { ...prev };
                    if (error) {
                        newErrors[name] = error;
                    }
                    else {
                        delete newErrors[name];
                    }
                    return newErrors;
                });
            }
        }
    }, [fields, values, validateField, validateOnBlur]);
    const handleSubmit = useCallback(async (e) => {
        if (e) {
            e.preventDefault();
        }
        const newErrors = validateAllFields();
        setErrors(newErrors);
        const allTouched = {};
        fields.forEach((field) => {
            allTouched[field.name] = true;
        });
        setTouched(allTouched);
        if (Object.keys(newErrors).length > 0) {
            return;
        }
        setIsSubmitting(true);
        try {
            await onSubmit(values);
        }
        finally {
            setIsSubmitting(false);
        }
    }, [fields, values, validateAllFields, onSubmit]);
    const resetForm = useCallback(() => {
        setValuesState(initialValues);
        setErrors({});
        setTouched({});
        setIsSubmitting(false);
    }, [initialValues]);
    const setFieldValue = useCallback((name, value) => {
        setValuesState((prev) => ({ ...prev, [name]: value }));
    }, []);
    const setFieldError = useCallback((name, error) => {
        setErrors((prev) => ({ ...prev, [name]: error }));
    }, []);
    const setValues = useCallback((newValues) => {
        setValuesState(newValues);
    }, []);
    const isDirty = useMemo(() => {
        return JSON.stringify(values) !== JSON.stringify(initialValues);
    }, [values, initialValues]);
    const isValid = useMemo(() => {
        return Object.keys(errors).length === 0;
    }, [errors]);
    return {
        values,
        errors,
        touched,
        isSubmitting,
        isDirty,
        isValid,
        handleChange,
        handleBlur,
        handleSubmit,
        resetForm,
        setFieldValue,
        setFieldError,
        setValues,
    };
};
//# sourceMappingURL=useFormBuilder.js.map