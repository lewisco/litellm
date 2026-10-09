"use client";

import { z } from "zod";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useBudgetOptions } from "@/app/(dashboard)/hooks/budgets/useBudgetOptions";
import type { EndUser } from "@/app/(dashboard)/hooks/customers/useCustomers";
import { useUserModels } from "@/app/(dashboard)/hooks/models/useModels";
import MCPServerSelector from "@/components/mcp_server_management/MCPServerSelector";
import MCPToolPermissions from "@/components/mcp_server_management/MCPToolPermissions";
import { FormField } from "@/components/shared/form/FormField";
import { MultiSelect } from "@/components/shared/MultiSelect";
import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useZodForm } from "@/lib/forms/useZodForm";
import { toCustomerFormValues, type CustomerFormValues } from "./customerPayload";

const mcpSelectionShape = {
  servers: z.array(z.string()),
  accessGroups: z.array(z.string()),
  toolsets: z.array(z.string()),
};

const customerShape = {
  user_id: z.string().trim().min(1, "Customer ID is required"),
  alias: z.string(),
  budget_id: z.string(),
  max_budget: z
    .string()
    .refine(
      (value) => !value.trim() || (Number.isFinite(Number(value)) && Number(value) >= 0),
      "Enter a non-negative budget",
    ),
  models: z.array(z.string()),
  allowed_model_region: z.enum(["", "eu", "us"]),
  default_model: z.string(),
  mcp_servers_and_groups: z.object(mcpSelectionShape),
  mcp_tool_permissions: z.record(z.string(), z.array(z.string())),
};

const customerSchema = z.object(customerShape).refine((values) => !values.budget_id || !values.max_budget.trim(), {
  message: "Select a budget or enter a max budget, not both",
  path: ["max_budget"],
});

const editCustomerSchema = customerSchema.refine(
  (values) => !values.max_budget.trim() || Number(values.max_budget) > 0,
  { message: "Enter a budget greater than zero when editing", path: ["max_budget"] },
);

const regionOptions = [
  { value: "", label: "Any" },
  { value: "eu", label: "EU" },
  { value: "us", label: "US" },
];

type CustomerFormProps = {
  customer?: EndUser | null;
  isEdit?: boolean;
  isSaving?: boolean;
  onSubmit: (values: CustomerFormValues) => void | Promise<void>;
  onCancel: () => void;
};

export function CustomerForm({
  customer = null,
  isEdit = false,
  isSaving = false,
  onSubmit,
  onCancel,
}: CustomerFormProps) {
  const { accessToken } = useAuthorized();
  const { data: budgets = [] } = useBudgetOptions(accessToken);
  const { data: models = [] } = useUserModels();
  const form = useZodForm(isEdit ? editCustomerSchema : customerSchema, {
    defaultValues: toCustomerFormValues(customer),
  });
  const selectedBudgetId = form.watch("budget_id");
  const budgetOptions = [
    { value: "", label: "No budget" },
    ...(selectedBudgetId && !budgets.some((budget) => budget.budget_id === selectedBudgetId)
      ? [{ value: selectedBudgetId, label: selectedBudgetId }]
      : []),
    ...budgets.map((budget) => ({ value: budget.budget_id, label: budget.budget_id })),
  ];
  const modelOptions = models.map((model) => ({ label: model, value: model }));
  const selection = form.watch("mcp_servers_and_groups");

  return (
    <form onSubmit={form.handleSubmit(onSubmit)}>
      <FieldGroup>
        <FormField
          control={form.control}
          name="user_id"
          label="Customer ID"
          description="The user field or a mapped customer header on API requests identifies this customer"
        >
          {({ ref, ...control }) => <Input {...control} ref={ref} disabled={isEdit} />}
        </FormField>

        <FormField
          control={form.control}
          name="alias"
          label="Alias"
          description={isEdit ? "Leave empty to keep the current alias" : undefined}
        >
          {({ ref, ...control }) => <Input {...control} ref={ref} />}
        </FormField>

        <FormField
          control={form.control}
          name="budget_id"
          label="Budget"
          description={
            isEdit
              ? "Leaving both budget fields empty keeps the current budget"
              : "Choose a budget or enter a maximum below"
          }
        >
          {({ id, value, onChange }) => (
            <Select items={budgetOptions} value={value} onValueChange={(selected) => onChange(selected ?? "")}>
              <SelectTrigger id={id} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {budgetOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </FormField>

        <FormField
          control={form.control}
          name="max_budget"
          label="Max Budget (USD)"
          description={
            isEdit ? "Leave empty to keep the current maximum; the update API does not apply zero budgets" : undefined
          }
        >
          {({ ref, ...control }) => (
            <Input
              {...control}
              ref={ref}
              type="number"
              min={0}
              step="any"
              placeholder={isEdit ? "Keep current maximum" : "Unlimited"}
            />
          )}
        </FormField>

        <FormField
          control={form.control}
          name="models"
          label="Allowed Models"
          description="An empty selection allows all models the key can use"
        >
          {({ id, value, onChange }) => (
            <MultiSelect
              id={id}
              options={modelOptions}
              value={value}
              onValueChange={onChange}
              placeholder="Select models"
            />
          )}
        </FormField>

        <FormField
          control={form.control}
          name="allowed_model_region"
          label="Allowed Model Region"
          description={isEdit ? "Any keeps the current region when editing" : undefined}
        >
          {({ id, value, onChange }) => (
            <Select items={regionOptions} value={value} onValueChange={(selected) => onChange(selected ?? "")}>
              <SelectTrigger id={id} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {regionOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </FormField>

        <FormField
          control={form.control}
          name="default_model"
          label="Default Model"
          description={isEdit ? "Leave empty to keep the current default model" : undefined}
        >
          {({ ref, ...control }) => <Input {...control} ref={ref} />}
        </FormField>

        <FormField control={form.control} name="mcp_servers_and_groups" label="MCP Servers / Access Groups / Toolsets">
          {({ id, value, onChange }) => (
            <MCPServerSelector
              id={id}
              accessToken={accessToken ?? ""}
              value={value}
              onChange={onChange}
              placeholder="Select MCP servers, access groups or toolsets"
            />
          )}
        </FormField>
        <MCPToolPermissions
          initializeDefaultPermissions={!isEdit}
          accessToken={accessToken ?? ""}
          selectedServers={selection.servers}
          selectedAccessGroups={selection.accessGroups}
          selectedToolsets={selection.toolsets}
          toolPermissions={form.watch("mcp_tool_permissions")}
          onChange={(permissions) => form.setValue("mcp_tool_permissions", permissions)}
        />
      </FieldGroup>
      <div className="mt-6 flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={isSaving}>
          {isEdit ? "Save Changes" : "Create Customer"}
        </Button>
      </div>
    </form>
  );
}
