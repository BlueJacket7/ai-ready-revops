select
    e.event_id,
    e.subscription_id,
    a.canonical_account_id      as account_id,
    a.segment,
    e.event_month::date         as event_month,
    e.event_type,
    e.arr_delta::double         as arr_delta,
    nullif(e.opportunity_id, '') as opportunity_id
from {{ source('clean', 'subscription_events') }} e
left join {{ ref('stg_accounts') }} a on a.account_id = e.account_id
