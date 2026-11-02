-- One row per CRM account record, with duplicates mapped to a canonical account.
-- Duplicates are the same company entered twice; they share a domain. The
-- earliest-created record wins, ties broken by id.
with src as (
    select
        account_id,
        account_name,
        lower(trim(domain))            as domain,
        industry,
        segment,
        employee_count::integer         as employee_count,
        billing_country,
        billing_state,
        created_at::date                as created_at,
        owner_rep_id
    from {{ source('clean', 'accounts') }}
),

ranked as (
    select
        *,
        first_value(account_id) over (
            partition by domain order by created_at, account_id
        ) as canonical_account_id
    from src
)

select
    *,
    account_id <> canonical_account_id as is_duplicate
from ranked
