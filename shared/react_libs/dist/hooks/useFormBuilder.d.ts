/**
 * useFormBuilder Hook
 *
 * Custom React hook for form state management with validation.
 * Handles form values, errors, touched fields, and submission.
 */
import { FieldConfig } from '../components/FormBuilder/types';
export interface UseFormBuilderOptions {
    fields: FieldConfig[];
    initialData?: Record<string, any>;
    onSubmit: (data: Record<string, any>) => void | Promise<void>;
    validateOnChange?: boolean;
    validateOnBlur?: boolean;
}
export interface UseFormBuilderReturn {
    values: Record<string, any>;
    errors: Record<string, string>;
    touched: Record<string, boolean>;
    isSubmitting: boolean;
    isDirty: boolean;
    isValid: boolean;
    handleChange: (name: string, value: any) => void;
    handleBlur: (name: string) => void;
    handleSubmit: (e?: React.FormEvent) => Promise<void>;
    resetForm: () => void;
    setFieldValue: (name: string, value: any) => void;
    setFieldError: (name: string, error: string) => void;
    setValues: (values: Record<string, any>) => void;
}
export declare const useFormBuilder: ({ fields, initialData, onSubmit, validateOnChange, validateOnBlur, }: UseFormBuilderOptions) => UseFormBuilderReturn;
//# sourceMappingURL=useFormBuilder.d.ts.map