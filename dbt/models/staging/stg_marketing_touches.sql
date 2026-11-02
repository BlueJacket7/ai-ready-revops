select
    t.touch_id,
    t.contact_id,
    a.canonical_account_id as account_id,
    t.channel,
    t.campaign,
    t.touched_at::date     as touched_at
from {{ source('clean', 'marketing_touches') }} t
left join {{ ref('stg_accounts') }} a on a.account_id = t.account_id
