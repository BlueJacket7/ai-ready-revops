-- Trailing-twelve-month net and gross revenue retention.
-- Base: every account with ARR > 0 twelve months before the measured month.
--   NRR = their ARR now / their ARR then   (expansion counts, new logos do not)
--   GRR = sum(min(ARR now, ARR then)) / their ARR then   (expansion capped, so GRR <= 100%)
-- Reported from the first month whose twelve-month base lies inside the simulated
-- window; earlier bases never had a chance to churn and would flatter retention.
-- Measured at account level: one subscription's growth can offset another's
-- shrinkage within the same account.
with a as (
    select * from {{ ref('account_arr_monthly') }}
),

pairs as (
    select
        cur.month,
        prev.segment,
        prev.arr as base_arr,
        cur.arr  as current_arr
    from a prev
    join a cur
      on cur.account_id = prev.account_id
     and cur.month = (prev.month + interval 12 month)::date
    where prev.arr > 0
)

select
    month,
    coalesce(segment, 'All')                                  as segment,
    count(*)                                                  as base_accounts,
    round(sum(base_arr), 2)                                   as base_arr,
    round(sum(current_arr) / sum(base_arr), 4)                as nrr,
    round(sum(least(current_arr, base_arr)) / sum(base_arr), 4) as grr
from pairs
where month >= '{{ var("retention_start") }}'::date
group by grouping sets ((month, segment), (month))
