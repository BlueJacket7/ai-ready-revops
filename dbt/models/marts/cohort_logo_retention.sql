-- Logo retention by start-quarter cohort.
-- A customer joins the cohort of the quarter its ARR first went above zero, and is
-- retained at month N if its ARR is still above zero N months after its start.
-- Only cohorts whose start quarter lies wholly inside the window are included.
-- Late cells are observed for only part of a cohort (later starters have not yet
-- reached month N); fully_observed marks the cells that cover the whole cohort.
with a as (
    select * from {{ ref('account_arr_monthly') }}
),

starts as (
    select account_id, min(month) as start_month
    from a
    where arr > 0
    group by 1
),

cohorts as (
    select account_id, start_month, date_trunc('quarter', start_month)::date as cohort_quarter
    from starts
    where {{ complete_quarter("date_trunc('quarter', start_month)::date") }}
),

sizes as (
    select cohort_quarter, count(*) as cohort_size from cohorts group by 1
),

tracked as (
    select
        c.cohort_quarter,
        date_diff('month', c.start_month, a.month) as months_since_start,
        a.arr > 0                                   as active
    from cohorts c
    join a on a.account_id = c.account_id and a.month >= c.start_month
)

select
    t.cohort_quarter,
    t.months_since_start,
    s.cohort_size,
    count(*)                                                     as cohort_accounts_observed,
    count(*) filter (where t.active)                             as active_logos,
    round(count(*) filter (where t.active) / count(*)::double, 4) as logo_retention,
    count(*) = s.cohort_size                                     as fully_observed
from tracked t
join sizes s using (cohort_quarter)
group by t.cohort_quarter, t.months_since_start, s.cohort_size
