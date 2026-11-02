-- The retention base for each month must equal an independent count of accounts
-- paying twelve months earlier, and the rates must be within sane bounds.
with expected as (
    select (month + interval 12 month)::date as month, count(*) as n
    from {{ ref('account_arr_monthly') }}
    where arr > 0
    group by 1
)
select r.month, r.base_accounts, e.n, r.nrr, r.grr
from {{ ref('retention_monthly') }} r
join expected e using (month)
where r.segment = 'All'
  and (r.base_accounts <> e.n or r.grr < 0 or r.grr > 1 or r.nrr < r.grr or r.nrr > 3)
