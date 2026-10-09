import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EndUser } from "@/app/(dashboard)/hooks/customers/useCustomers";
import { renderWithProviders, testQueryClient } from "../../../../../tests/test-utils";
import CustomersPanel from "./CustomersPanel";

const { post, get, useQuery, fetchServers, listTools, authorization } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  useQuery: vi.fn(),
  fetchServers: vi.fn(),
  listTools: vi.fn(),
  authorization: { accessToken: "sk-test", userRole: "Admin", userId: "admin", isViewOnly: false },
}));

vi.mock("@/lib/http/api", () => ({ $api: { useQuery } }));
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({ default: () => authorization }));
vi.mock("@/components/networking", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/networking")>()),
  apiClient: { post, get },
  fetchMCPServers: fetchServers,
  listMCPTools: listTools,
  fetchMCPToolsets: vi.fn().mockResolvedValue([]),
  fetchMCPAccessGroups: vi.fn().mockResolvedValue([]),
  modelAvailableCall: vi.fn().mockResolvedValue({ data: [] }),
}));

const activeCustomer: EndUser = {
  user_id: "alice@example.test",
  alias: "Alice",
  blocked: false,
  spend: 12.5,
  models: [],
  object_permission: {
    object_permission_id: "permission-alice",
    mcp_servers: [],
    mcp_access_groups: ["research"],
    mcp_toolsets: ["toolset-1"],
    mcp_tool_permissions: {},
    agents: [],
    agent_access_groups: [],
    models: [],
    search_tools: [],
    vector_stores: [],
    blocked_tools: [],
  },
};

const blockedCustomer: EndUser = {
  user_id: "bob@example.test",
  alias: "Bob",
  blocked: true,
  spend: 0,
  models: [],
};

const customers = [activeCustomer, blockedCustomer];

describe("Customers management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    testQueryClient.clear();
    authorization.userRole = "Admin";
    authorization.isViewOnly = false;
    get.mockResolvedValue([]);
    post.mockResolvedValue(activeCustomer);
    fetchServers.mockResolvedValue([]);
    listTools.mockResolvedValue({ tools: [], error: false });
    useQuery.mockImplementation(
      (_method: string, path: string, init: { params?: { query?: { end_user_id?: string } } }) => ({
        data:
          path === "/customer/list"
            ? customers
            : customers.find((customer) => customer.user_id === init.params?.query?.end_user_id),
        isLoading: false,
        isError: false,
      }),
    );
  });

  it("shows status and MCP access for each customer", () => {
    renderWithProviders(<CustomersPanel />);

    const alice = screen.getByRole("row", { name: /alice@example.test/ });
    expect(within(alice).getByText("Active")).toBeInTheDocument();
    expect(within(alice).getByText("1 group, 1 toolset")).toBeInTheDocument();
    const bob = screen.getByRole("row", { name: /bob@example.test/ });
    expect(within(bob).getByText("Blocked")).toBeInTheDocument();
    expect(within(bob).getByText("No restriction")).toBeInTheDocument();
  });

  it.each(["Alice", "alice@example.test"])("filters by ID or alias using %s", async (search) => {
    renderWithProviders(<CustomersPanel />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search customers" }), { target: { value: search } });

    await waitFor(() => expect(screen.queryByRole("row", { name: /bob@example.test/ })).not.toBeInTheDocument());
    expect(screen.getByRole("row", { name: /alice@example.test/ })).toBeInTheDocument();
  });

  it("creates a customer with the filled form values", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CustomersPanel />);
    await user.click(screen.getByRole("button", { name: "Create Customer" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Customer ID"), { target: { value: "  new@example.test  " } });
    fireEvent.change(within(dialog).getByLabelText("Alias"), { target: { value: "New Customer" } });
    fireEvent.change(within(dialog).getByLabelText("Max Budget (USD)"), { target: { value: "25" } });
    await user.click(within(dialog).getByRole("button", { name: "Create Customer" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledExactlyOnceWith("/customer/new", {
        accessToken: "sk-test",
        body: { user_id: "new@example.test", alias: "New Customer", max_budget: 25 },
      }),
    );
  });

  it("requires a customer ID before creating", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CustomersPanel />);
    await user.click(screen.getByRole("button", { name: "Create Customer" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Create Customer" }));

    expect(await within(dialog).findByText("Customer ID is required")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("edits a deep-linked customer without rewriting MCP permissions", async () => {
    const user = userEvent.setup();
    const onUrlUpdate = vi.fn();
    renderWithProviders(<CustomersPanel />, { searchParams: "customer=alice%40example.test", onUrlUpdate });
    expect(screen.getByRole("heading", { name: "Alice" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const alias = await screen.findByLabelText("Alias");
    expect(screen.getByLabelText("Customer ID")).toBeDisabled();
    fireEvent.change(alias, { target: { value: "Updated Alice" } });
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledExactlyOnceWith("/customer/update", {
        accessToken: "sk-test",
        body: { user_id: activeCustomer.user_id, alias: "Updated Alice", models: [] },
      }),
    );
    await waitFor(() =>
      expect(onUrlUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          searchParams: expect.any(URLSearchParams),
        }),
      ),
    );
    expect(onUrlUpdate.mock.calls.at(-1)?.[0].searchParams.get("edit")).toBeNull();
  });

  it("unblocks a customer from the row menu", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CustomersPanel />);
    await user.click(screen.getByRole("button", { name: "Actions for bob@example.test" }));
    await user.click(await screen.findByRole("menuitem", { name: "Unblock" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledExactlyOnceWith("/customer/unblock", {
        accessToken: "sk-test",
        body: { user_ids: [blockedCustomer.user_id] },
      }),
    );
  });

  it("preserves an unrestricted server when editing only the alias after tools load", async () => {
    const user = userEvent.setup();
    const directCustomer: EndUser = {
      ...activeCustomer,
      object_permission: {
        ...activeCustomer.object_permission!,
        mcp_servers: ["server-1"],
        mcp_access_groups: [],
        mcp_toolsets: [],
      },
    };
    useQuery.mockReturnValue({ data: directCustomer, isLoading: false, isError: false });
    fetchServers.mockResolvedValue([
      {
        server_id: "server-1",
        server_name: "Research Server",
        created_at: "",
        created_by: "admin",
        updated_at: "",
        updated_by: "admin",
      },
    ]);
    listTools.mockResolvedValue({
      error: false,
      tools: [
        { name: "read_document", description: "Read a document", inputSchema: {} },
        { name: "delete_document", description: "Delete a document", inputSchema: {} },
      ],
    });
    renderWithProviders(<CustomersPanel />, { searchParams: "customer=alice%40example.test&edit=true" });
    await screen.findByRole("radio", { name: "Flat List" });
    fireEvent.change(screen.getByLabelText("Alias"), { target: { value: "Updated Alice" } });
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledExactlyOnceWith("/customer/update", {
        accessToken: "sk-test",
        body: { user_id: activeCustomer.user_id, alias: "Updated Alice", models: [] },
      }),
    );
  });

  it("rejects an edit budget of zero because customer updates cannot apply it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CustomersPanel />, { searchParams: "customer=alice%40example.test&edit=true" });
    fireEvent.change(screen.getByLabelText("Max Budget (USD)"), { target: { value: "0" } });
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(await screen.findByText("Enter a budget greater than zero when editing")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("confirms deletion and clears the open customer from the URL", async () => {
    const user = userEvent.setup();
    const onUrlUpdate = vi.fn();
    renderWithProviders(<CustomersPanel />, { searchParams: "customer=alice%40example.test", onUrlUpdate });
    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Customer?" });
    expect(within(dialog).getByText(/recreates the customer with no restrictions/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Delete" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByPlaceholderText(activeCustomer.user_id), {
      target: { value: activeCustomer.user_id },
    });
    await user.click(confirm);

    await waitFor(() =>
      expect(post).toHaveBeenCalledExactlyOnceWith("/customer/delete", {
        accessToken: "sk-test",
        body: { user_ids: [activeCustomer.user_id] },
      }),
    );
    await waitFor(() => expect(onUrlUpdate).toHaveBeenCalledWith(expect.objectContaining({ queryString: "" })));
    expect(screen.getByRole("heading", { name: "Customers" })).toBeInTheDocument();
  });

  it("keeps Admin Viewer read-only, including an edit deep link", async () => {
    authorization.isViewOnly = true;
    const user = userEvent.setup();
    const list = renderWithProviders(<CustomersPanel />);
    expect(screen.queryByRole("button", { name: "Create Customer" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions for alice@example.test" }));
    expect(await screen.findByRole("menuitem", { name: "Copy ID" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Edit" })).not.toBeInTheDocument();
    list.unmount();
    renderWithProviders(<CustomersPanel />, { searchParams: "customer=alice%40example.test&edit=true" });
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save Changes" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
});
