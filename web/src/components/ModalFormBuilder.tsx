import Card, { CardHeader, CardContent } from "@/components/Card";
import FormBuilder from "@/components/FormBuilder";
import { FormConfig, FormField } from "@/types/form";

export interface ModalFormBuilderProps {
  isOpen?: boolean;
  onClose: () => void;
  title: string;
  config?: FormConfig;
  fields?: FormField[];
  initialValues?: Record<string, unknown>;
  onSubmit: (data: Record<string, unknown>) => void;
  isLoading?: boolean;
  submitLabel?: string;
}

export default function ModalFormBuilder({
  isOpen = true,
  onClose,
  title,
  config,
  fields,
  initialValues,
  onSubmit,
  isLoading = false,
  submitLabel,
}: ModalFormBuilderProps) {
  if (!isOpen) return null;

  const baseConfig: FormConfig = config ?? { fields: fields || [] };
  // Merge submitLabel into config if provided as prop
  const finalConfig = submitLabel ? { ...baseConfig, submitLabel } : baseConfig;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">{title}</h2>
        </CardHeader>
        <CardContent>
          <FormBuilder
            config={finalConfig}
            initialValues={initialValues}
            onSubmit={onSubmit}
            onCancel={onClose}
            isLoading={isLoading}
          />
        </CardContent>
      </Card>
    </div>
  );
}
