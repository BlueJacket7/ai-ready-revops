-- ARR per account per month, built from subscription events.
-- Grain: one row per (account, month) from the account's first event to the
-- as-of month. ARR at a month includes every event booked in or before it.
with events as (
    select * from {{ ref('stg_subscription_events') }}
),

months as (
    select unnest(generate_series(
        (select min(event_month) from events),
        '{{ var("as_of_month") }}'::date,
        interval 1 month
    ))::date as month
),

accounts as (
    select account_id, any_value(segment) as segment, min(event_month) as first_month
    from events
    group by 1
),

grid as (
    select a.account_id, a.segment, m.month
    from accounts a
    join months m on m.month >= a.first_month
),

deltas as (
    select account_id, event_month as month, sum(arr_delta) as delta
    from events
    group by 1, 2
)

select
    grid.account_id,
    grid.segment,
    grid.month,
    round(sum(coalesce(deltas.delta, 0)) over (
        partition by grid.account_id order by grid.month
        rows between unbounded preceding and current row
    ), 2) as arr
from grid
left join deltas using (account_id, month)
