-- A churn event must take its subscription to exactly zero.
select subscription_id, sum(arr_delta) as arr_after_churn
from {{ ref('stg_subscription_events') }}
where subscription_id in (select subscription_id from {{ ref('stg_subscription_events') }} where event_type = 'churn')
group by 1
having abs(sum(arr_delta)) > 0.05
