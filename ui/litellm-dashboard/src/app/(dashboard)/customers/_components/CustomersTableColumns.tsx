"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { Ban, Copy, MoreHorizontal, Pencil, ShieldCheck, Trash2 } from "lucide-react";

import type { EndUser } from "@/app/(dashboard)/hooks/customers/useCustomers";
import { ALL_PROXY_MCP_SERVERS_SENTINEL, NO_MCP_SERVERS_SENTINEL } from "@/components/mcp_tools/constants";
import { DataTableSortHeader } from "@/components/shared/DataTable";
import { IdentityCell, ModelsCell, MoneyCell, StatusBadge } from "@/components/shared/table_cells";
import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cva.config";
import { copyToClipboard } from "@/utils/dataUtils";

export type CustomerTableActions = {
  canModify: boolean;
  onView: (customerId: string) => void;
  onEdit: (customerId: string) => void;
  onDelete: (customer: EndUser) => void;
  onSetBlocked: (customer: EndUser) => void;
};

const countLabel = (count: number, noun: string): string => (count ? `${count} ${noun}${count === 1 ? "" : "s"}` : "");

export function describeMcpGrant(permission: EndUser["object_permission"]): string {
  const servers = permission?.mcp_servers ?? [];
  if (servers.includes(NO_MCP_SERVERS_SENTINEL)) return "No MCP access";
  const serverLabel = servers.includes(ALL_PROXY_MCP_SERVERS_SENTINEL)
    ? "All MCP servers"
    : countLabel(servers.length, "server");
  const parts = [
    serverLabel,
    countLabel(permission?.mcp_access_groups?.length ?? 0, "group"),
    countLabel(permission?.mcp_toolsets?.length ?? 0, "toolset"),
    countLabel(Object.keys(permission?.mcp_tool_permissions ?? {}).length, "tool limit"),
  ].filter(Boolean);
  return parts.join(", ") || "No restriction";
}

function CustomerRowActions({
  customer,
  canModify,
  onEdit,
  onDelete,
  onSetBlocked,
}: CustomerTableActions & { customer: EndUser }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`Actions for ${customer.user_id}`}
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "text-muted-foreground")}
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {canModify && (
          <>
            <DropdownMenuItem onClick={() => onEdit(customer.user_id)}>
              <Pencil />
              Edit
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onSetBlocked(customer)}>
              {customer.blocked ? <ShieldCheck /> : <Ban />}
              {customer.blocked ? "Unblock" : "Block"}
            </DropdownMenuItem>
          </>
        )}
        <DropdownMenuItem onClick={() => void copyToClipboard(customer.user_id, "Customer ID copied")}>
          <Copy />
          Copy ID
        </DropdownMenuItem>
        {canModify && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => onDelete(customer)}>
              <Trash2 />
              Delete
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function getCustomerTableColumns(actions: CustomerTableActions): ColumnDef<EndUser>[] {
  return [
    {
      id: "user_id",
      accessorKey: "user_id",
      meta: { title: "Customer ID", skeleton: "twoLine" },
      header: ({ column }) => <DataTableSortHeader column={column} title="Customer ID" />,
      size: 260,
      cell: ({ row }) => (
        <IdentityCell
          title={row.original.user_id}
          subtitle={row.original.alias}
          titleClassName="font-mono text-xs"
          onClick={() => actions.onView(row.original.user_id)}
        />
      ),
    },
    {
      id: "spend",
      accessorKey: "spend",
      meta: { title: "Spend", className: "text-right", headerClassName: "text-right" },
      header: ({ column }) => <DataTableSortHeader column={column} title="Spend" className="justify-end" />,
      size: 130,
      cell: ({ row }) => <MoneyCell value={row.original.spend} showZero />,
    },
    {
      id: "max_budget",
      accessorFn: (customer) => customer.litellm_budget_table?.max_budget,
      meta: { title: "Max Budget", className: "text-right", headerClassName: "text-right" },
      header: ({ column }) => <DataTableSortHeader column={column} title="Max Budget" className="justify-end" />,
      size: 140,
      cell: ({ row }) => (
        <MoneyCell value={row.original.litellm_budget_table?.max_budget} emptyText="Unlimited" showZero />
      ),
    },
    {
      id: "models",
      meta: { title: "Models", skeleton: "chips" },
      header: "Models",
      size: 240,
      enableSorting: false,
      cell: ({ row }) => <ModelsCell models={row.original.models} />,
    },
    {
      id: "mcp_access",
      meta: { title: "MCP Access" },
      header: "MCP Access",
      size: 210,
      enableSorting: false,
      cell: ({ row }) => <span className="text-sm">{describeMcpGrant(row.original.object_permission)}</span>,
    },
    {
      id: "blocked",
      accessorKey: "blocked",
      meta: { title: "Status", skeleton: "badge" },
      header: ({ column }) => <DataTableSortHeader column={column} title="Status" />,
      size: 120,
      cell: ({ row }) => (
        <StatusBadge
          tone={row.original.blocked ? "error" : "success"}
          label={row.original.blocked ? "Blocked" : "Active"}
        />
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      size: 64,
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <CustomerRowActions {...actions} customer={row.original} />
        </div>
      ),
    },
  ];
}
