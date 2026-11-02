-- Blended CAC and CAC payback by quarter.
--   CAC = prior-quarter sales and marketing spend / new logos this quarter
--   payback months = CAC / (average new-logo ARR x gross margin / 12)
-- Prior-quarter spend reflects the lag between spending and closing. Spend counts
-- only from complete quarters, so the first complete quarter returns null rather
-- than a CAC built on two months of spend.
-- Only complete quarters are reported.
with first_new as (
    select account_id, min(event_month) as start_month
    from {{ ref('stg_subscription_events') }}
    where event_type = 'new'
    group by 1
),

new_logos as (
    select
        date_trunc('quarter', f.start_month)::date as quarter,
        count(distinct f.account_id)               as new_logos,
        sum(e.arr_delta)                           as new_logo_arr
    from first_new f
    join {{ ref('stg_subscription_events') }} e
      on e.account_id = f.account_id and e.event_month = f.start_month and e.event_type = 'new'
    where f.start_month >= '{{ var("window_start") }}'::date
    group by 1
),

spend as (
    select date_trunc('quarter', month)::date as quarter, sum(total_spend) as sm_spend
    from {{ ref('stg_sm_spend') }}
    group by 1
    having {{ complete_quarter("date_trunc('quarter', month)::date") }}
),

joined as (
    select
        n.quarter,
        n.new_logos,
        n.new_logo_arr,
        s.sm_spend,
        lag(s.sm_spend) over (order by n.quarter) as prior_quarter_sm_spend
    from new_logos n
    left join spend s using (quarter)
)

select
    quarter,
    new_logos,
    round(new_logo_arr, 2)                                         as new_logo_arr,
    round(prior_quarter_sm_spend, 2)                               as prior_quarter_sm_spend,
    round(prior_quarter_sm_spend / new_logos, 2)                   as cac,
    round((prior_quarter_sm_spend / new_logos)
          / ((new_logo_arr / new_logos) * {{ var("gross_margin") }} / 12), 1) as cac_payback_months
from joined
where {{ complete_quarter('quarter') }}
