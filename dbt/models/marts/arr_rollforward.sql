-- The ARR bridge: starting ARR + new + expansion + contraction + churn = ending ARR.
-- Contraction and churn are negative. Tested to reconcile month by month, and
-- tested against the sum of account-level ARR.
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

moves as (
    select
        m.month,
        coalesce(sum(case when e.event_type = 'new'         then e.arr_delta end), 0) as new_arr,
        coalesce(sum(case when e.event_type = 'expansion'   then e.arr_delta end), 0) as expansion_arr,
        coalesce(sum(case when e.event_type = 'contraction' then e.arr_delta end), 0) as contraction_arr,
        coalesce(sum(case when e.event_type = 'churn'       then e.arr_delta end), 0) as churn_arr
    from months m
    left join events e on e.event_month = m.month
    group by 1
),

bridge as (
    select
        *,
        new_arr + expansion_arr + contraction_arr + churn_arr as net_new_arr,
        sum(new_arr + expansion_arr + contraction_arr + churn_arr) over (
            order by month rows between unbounded preceding and current row
        ) as ending_arr
    from moves
)

select
    month,
    round(ending_arr - net_new_arr, 2) as starting_arr,
    round(new_arr, 2)                  as new_arr,
    round(expansion_arr, 2)            as expansion_arr,
    round(contraction_arr, 2)          as contraction_arr,
    round(churn_arr, 2)                as churn_arr,
    round(net_new_arr, 2)              as net_new_arr,
    round(ending_arr, 2)               as ending_arr,
    month >= '{{ var("window_start") }}'::date as in_window
from bridge
