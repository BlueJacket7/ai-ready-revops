-- Company ARR, built from monthly event totals, must equal the sum of account-level
-- ARR, built account by account on a separate month grid, every month (within a
-- dollar: account ARR is rounded to the cent before summing).
select r.month, r.ending_arr, a.total
from {{ ref('arr_rollforward') }} r
join (select month, sum(arr) as total from {{ ref('account_arr_monthly') }} group by 1) a using (month)
where abs(r.ending_arr - a.total) > 1.0
