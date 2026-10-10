"use client";

import { ArrowLeft, Ban, ShieldCheck, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import type { EndUser } from "@/app/(dashboard)/hooks/customers/useCustomers";
import ObjectPermissionsView from "@/components/object_permissions_view";
import { PageHeader, PageHeaderControls, PageHeaderTitle } from "@/components/shared/PageHeader";
import { ModelsCell, MoneyCell, StatusBadge } from "@/components/shared/table_cells";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { toast } from "@/lib/toast";
import { formatNumberWithCommas } from "@/utils/dataUtils";

import { CustomerForm } from "./CustomerForm";
import { CUSTOMER_BLOCKING_NOTICE } from "./CustomersTableColumns";
import type { CustomerFormValues } from "./customerPayload";
import { useSaveCustomer } from "./useSaveCustomer";

type CustomerTab = "overview" | "settings";

type CustomerInfoViewProps = {
  customer: EndUser;
  accessToken: string | null;
  canModify: boolean;
  isEdit: boolean;
  tab: CustomerTab;
  isBlocking: boolean;
  onTabChange: (tab: CustomerTab) => void;
  onBack: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onDelete: (customer: EndUser) => void;
  onSetBlocked: (customer: EndUser) => void;
};

function OverviewField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{children}</dd>
    </div>
  );
}

function CustomerModelFields({ customer }: { customer: EndUser }) {
  return (
    <>
      <OverviewField label="Models">
        <ModelsCell models={customer.models} />
      </OverviewField>
      <OverviewField label="Allowed Model Region">
        {customer.allowed_model_region?.toUpperCase() ?? "Any"}
      </OverviewField>
      <OverviewField label="Default Model">{customer.default_model ?? "None"}</OverviewField>
    </>
  );
}

function CustomerOverview({ customer, accessToken }: Pick<CustomerInfoViewProps, "customer" | "accessToken">) {
  const budget = customer.litellm_budget_table;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Budget Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-xl font-semibold">${formatNumberWithCommas(customer.spend, 4)}</p>
            <p>of {budget?.max_budget == null ? "Unlimited" : `$${formatNumberWithCommas(budget.max_budget, 4)}`}</p>
            <p className="text-sm text-muted-foreground">Reset: {budget?.budget_duration ?? "No reset"}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Rate Limits</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              <OverviewField label="TPM">{budget?.tpm_limit ?? "Unlimited"}</OverviewField>
              <OverviewField label="RPM">{budget?.rpm_limit ?? "Unlimited"}</OverviewField>
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Models</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              <CustomerModelFields customer={customer} />
            </dl>
          </CardContent>
        </Card>
      </div>
      <ObjectPermissionsView objectPermission={customer.object_permission} accessToken={accessToken} />
    </div>
  );
}

function CustomerSettings({ customer, accessToken }: Pick<CustomerInfoViewProps, "customer" | "accessToken">) {
  const budget = customer.litellm_budget_table;
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        <OverviewField label="Alias">{customer.alias || "Not set"}</OverviewField>
        <OverviewField label="Budget ID">{customer.budget_id ?? "No budget"}</OverviewField>
        <OverviewField label="Max Budget">
          <MoneyCell value={budget?.max_budget} emptyText="Unlimited" showZero />
        </OverviewField>
        <OverviewField label="Reset Period">{budget?.budget_duration ?? "No reset"}</OverviewField>
        <OverviewField label="TPM">{budget?.tpm_limit ?? "Unlimited"}</OverviewField>
        <OverviewField label="RPM">{budget?.rpm_limit ?? "Unlimited"}</OverviewField>
        <CustomerModelFields customer={customer} />
      </dl>
      <ObjectPermissionsView
        objectPermission={customer.object_permission}
        variant="inline"
        className="border-t pt-4"
        accessToken={accessToken}
      />
    </div>
  );
}

function CustomerActions({
  customer,
  isBlocking,
  onDelete,
  onSetBlocked,
}: Pick<CustomerInfoViewProps, "customer" | "isBlocking" | "onDelete" | "onSetBlocked">) {
  return (
    <PageHeaderControls>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger
            render={<Button variant="outline" disabled={isBlocking} onClick={() => onSetBlocked(customer)} />}
          >
            {customer.blocked ? <ShieldCheck /> : <Ban />}
            {customer.blocked ? "Unblock" : "Block"}
          </TooltipTrigger>
          <TooltipContent role="tooltip">{CUSTOMER_BLOCKING_NOTICE}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
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
  tab,
  onTabChange,
  onBack,
  onEdit,
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
          <StatusBadge tone={customer.blocked ? "error" : "success"} label={customer.blocked ? "Blocked" : "Active"} />
        </div>
        <p className="mt-2 break-all font-mono text-sm text-muted-foreground">{customer.user_id}</p>
        {canModify && !showEdit && <CustomerActions {...actions} customer={customer} />}
      </PageHeader>
      <Tabs
        value={showEdit ? "settings" : tab}
        onValueChange={(value) => {
          if (value === "overview" || value === "settings") onTabChange(value);
        }}
      >
        <TabsList variant="line" className="h-auto w-full justify-start rounded-none border-b p-0">
          <TabsTrigger value="overview" className="flex-none rounded-none px-4 py-2">
            Overview
          </TabsTrigger>
          <TabsTrigger value="settings" className="flex-none rounded-none px-4 py-2">
            Settings
          </TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="pt-4">
          <CustomerOverview customer={customer} accessToken={accessToken} />
        </TabsContent>
        <TabsContent value="settings" className="pt-4">
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Customer Settings</CardTitle>
              {canModify && !showEdit && <Button onClick={onEdit}>Edit Settings</Button>}
            </CardHeader>
            <CardContent>
              {showEdit ? (
                <CustomerForm
                  customer={customer}
                  isEdit
                  isSaving={isSaving}
                  onSubmit={handleSave}
                  onCancel={onCancelEdit}
                />
              ) : (
                <CustomerSettings customer={customer} accessToken={accessToken} />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </>
  );
}
