-- Opportunities, cleaned.
--   close_date: when the CRM field is blank on a closed deal, take the date the
--     deal entered Closed Won or Closed Lost from stage history (flagged).
--   amount_arr: blank stays null and is flagged; it is excluded from amount
--     averages, never treated as zero.
--   account_id: remapped to the canonical account.
with opp as (
    select * from {{ source('clean', 'opportunities') }}
),

closed_on as (
    select opportunity_id, max(entered_at::date) as closed_at
    from {{ source('clean', 'opportunity_stage_history') }}
    where stage in ('Closed Won', 'Closed Lost')
    group by 1
),

accts as (
    select account_id, canonical_account_id, segment from {{ ref('stg_accounts') }}
)

select
    opp.opportunity_id,
    accts.canonical_account_id                          as account_id,
    opp.account_id                                      as crm_account_id,
    accts.segment,
    opp.owner_rep_id,
    nullif(opp.primary_contact_id, '')                  as primary_contact_id,
    opp.opportunity_type,
    opp.lead_source,
    opp.created_at::date                                as created_at,
    coalesce(try_cast(nullif(opp.close_date, '') as date), closed_on.closed_at)
                                                        as close_date,
    (nullif(opp.close_date, '') is null and opp.is_closed::boolean)
                                                        as close_date_imputed,
    opp.stage,
    opp.is_closed::boolean                              as is_closed,
    opp.is_won::boolean                                 as is_won,
    try_cast(nullif(opp.amount_arr, '') as double)      as amount_arr,
    nullif(opp.amount_arr, '') is null                  as amount_missing
from opp
left join closed_on using (opportunity_id)
left join accts on accts.account_id = opp.account_id
