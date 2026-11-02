-- NRR and GRR recomputed by a different route (a 12-month lag window over each
-- account's history, instead of the model's self-join) must agree.
with lagged as (
    select month, arr, lag(arr, 12) over (partition by account_id order by month) as arr_12
    from {{ ref('account_arr_monthly') }}
),
expected as (
    select month,
           sum(arr) / sum(arr_12)              as nrr,
           sum(least(arr, arr_12)) / sum(arr_12) as grr
    from lagged
    where arr_12 > 0
    group by 1
)
select r.month, r.nrr, e.nrr as expected_nrr, r.grr, e.grr as expected_grr
from {{ ref('retention_monthly') }} r
join expected e using (month)
where r.segment = 'All' and (abs(r.nrr - e.nrr) > 0.0001 or abs(r.grr - e.grr) > 0.0001)
