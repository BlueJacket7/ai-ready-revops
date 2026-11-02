-- A 'new' event means a new logo. It must never land on an account that was already
-- paying the month before; that revenue would be expansion, booked as new.
select e.event_id, e.account_id, e.event_month, a.arr as arr_prior_month
from {{ ref('stg_subscription_events') }} e
join {{ ref('account_arr_monthly') }} a
  on a.account_id = e.account_id
 and a.month = (e.event_month - interval 1 month)::date
where e.event_type = 'new' and a.arr > 0
