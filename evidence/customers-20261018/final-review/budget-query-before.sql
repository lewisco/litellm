
SELECT "budget_id" FROM "LiteLLM_BudgetTable" WHERE "budget_id" = $1 AND EXISTS (
    SELECT 1 FROM "LiteLLM_EndUserTable" WHERE "budget_id" = $1 AND "user_id" <> $2
    UNION ALL SELECT 1 FROM "LiteLLM_VerificationToken" WHERE "budget_id" = $1
        OR "metadata"->>'end_user_budget_id' = $1
    UNION ALL SELECT 1 FROM "LiteLLM_OrganizationTable" WHERE "budget_id" = $1
    UNION ALL SELECT 1 FROM "LiteLLM_ProjectTable" WHERE "budget_id" = $1
    UNION ALL SELECT 1 FROM "LiteLLM_TagTable" WHERE "budget_id" = $1
    UNION ALL SELECT 1 FROM "LiteLLM_ModelAccessGroupBudgetTable" WHERE "budget_id" = $1
    UNION ALL SELECT 1 FROM "LiteLLM_TeamMembership" WHERE "budget_id" = $1
    UNION ALL SELECT 1 FROM "LiteLLM_OrganizationMembership" WHERE "budget_id" = $1
    UNION ALL SELECT 1 FROM "LiteLLM_TeamTable" WHERE "metadata"->>'team_member_budget_id' = $1
)
