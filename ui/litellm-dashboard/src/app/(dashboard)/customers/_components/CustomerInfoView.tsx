"use client";

import { ArrowLeft, Ban, Pencil, ShieldCheck, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import type { EndUser } from "@/app/(dashboard)/hooks/customers/useCustomers";
import MCPServerPermissions from "@/components/permissions/MCPServerPermissions";
import { PageHeader, PageHeaderControls, PageHeaderTitle } from "@/components/shared/PageHeader";
import { ModelsCell, MoneyCell, StatusBadge } from "@/components/shared/table_cells";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/lib/toast";

import { CustomerForm } from "./CustomerForm";
import { describeMcpGrant } from "./CustomersTableColumns";
import type { CustomerFormValues } from "./customerPayload";
import { useSaveCustomer } from "./useSaveCustomer";

type CustomerInfoViewProps = {
  customer: EndUser;
  accessToken: string | null;
  canModify: boolean;
  isEdit: boolean;
  isBlocking: boolean;
  onBack: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onDelete: (customer: EndUser) => void;
  onSetBlocked: (customer: EndUser) => void;
};

function StatusLabel({ blocked }: { blocked: boolean }) {
  return <StatusBadge tone={blocked ? "error" : "success"} label={blocked ? "Blocked" : "Active"} />;
}

function OverviewField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{children}</dd>
    </div>
  );
}

function CustomerBudgetOverview({ customer }: { customer: EndUser }) {
  const budget = customer.litellm_budget_table;
  return (
    <>
      <OverviewField label="Spend">
        <MoneyCell value={customer.spend} showZero />
      </OverviewField>
      <OverviewField label="Max Budget">
        <MoneyCell value={budget?.max_budget} emptyText="Unlimited" showZero />
      </OverviewField>
      <OverviewField label="Budget ID">{customer.budget_id ?? "No budget"}</OverviewField>
      <OverviewField label="Reset Period">{budget?.budget_duration ?? "No reset"}</OverviewField>
      <OverviewField label="TPM">{budget?.tpm_limit ?? "Unlimited"}</OverviewField>
      <OverviewField label="RPM">{budget?.rpm_limit ?? "Unlimited"}</OverviewField>
    </>
  );
}

function CustomerModelOverview({ customer }: { customer: EndUser }) {
  return (
    <>
      <OverviewField label="Allowed Model Region">
        {customer.allowed_model_region?.toUpperCase() ?? "Any"}
      </OverviewField>
      <OverviewField label="Default Model">{customer.default_model ?? "None"}</OverviewField>
      <OverviewField label="Models">
        <ModelsCell models={customer.models} />
      </OverviewField>
    </>
  );
}

function CustomerOverview({ customer, accessToken }: Pick<CustomerInfoViewProps, "customer" | "accessToken">) {
  const permission = customer.object_permission;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Overview</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <dl className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          <CustomerBudgetOverview customer={customer} />
          <CustomerModelOverview customer={customer} />
        </dl>
        <div className="space-y-2 border-t pt-5">
          <p className="text-sm text-muted-foreground">{describeMcpGrant(permission)}</p>
          <MCPServerPermissions
            mcpServers={permission?.mcp_servers ?? []}
            mcpAccessGroups={permission?.mcp_access_groups ?? []}
            mcpToolsets={permission?.mcp_toolsets ?? []}
            mcpToolPermissions={permission?.mcp_tool_permissions ?? {}}
            accessToken={accessToken}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function CustomerActions({
  customer,
  isBlocking,
  onEdit,
  onDelete,
  onSetBlocked,
}: Pick<CustomerInfoViewProps, "customer" | "isBlocking" | "onEdit" | "onDelete" | "onSetBlocked">) {
  return (
    <PageHeaderControls>
      <Button variant="outline" onClick={onEdit}>
        <Pencil />
        Edit
      </Button>
      <Button variant="outline" disabled={isBlocking} onClick={() => onSetBlocked(customer)}>
        {customer.blocked ? <ShieldCheck /> : <Ban />}
        {customer.blocked ? "Unblock" : "Block"}
      </Button>
      <Button variant="destructive" onClick={() => onDelete(customer)}>
        <Trash2 />
        Delete
      </Button>
    </PageHeaderControls>
  );
}

export default function CustomerInfoView({
  customer,
  accessToken,
  canModify,
  isEdit,
  onBack,
  onCancelEdit,
  ...actions
}: CustomerInfoViewProps) {
  const { saveCustomer, isSaving } = useSaveCustomer(customer);
  const handleSave = async (values: CustomerFormValues) => {
    try {
      await saveCustomer(values);
      toast.success("Customer updated");
      onCancelEdit();
    } catch (error) {
      toast.fromError(error);
    }
  };
  const showEdit = canModify && isEdit;

  return (
    <>
      <PageHeader>
        <Button variant="ghost" className="mb-4" onClick={onBack}>
          <ArrowLeft />
          Back to Customers
        </Button>
        <div className="flex flex-wrap items-center gap-3">
          <PageHeaderTitle>{customer.alias || customer.user_id}</PageHeaderTitle>
          <StatusLabel blocked={customer.blocked} />
        </div>
        <p className="mt-2 break-all font-mono text-sm text-muted-foreground">{customer.user_id}</p>
        {canModify && !showEdit && <CustomerActions {...actions} customer={customer} />}
      </PageHeader>
      {showEdit ? (
        <Card>
          <CardHeader>
            <CardTitle>Edit Customer</CardTitle>
          </CardHeader>
          <CardContent>
            <CustomerForm
              customer={customer}
              isEdit
              isSaving={isSaving}
              onSubmit={handleSave}
              onCancel={onCancelEdit}
            />
          </CardContent>
        </Card>
      ) : (
        <CustomerOverview customer={customer} accessToken={accessToken} />
      )}
    </>
  );
}
