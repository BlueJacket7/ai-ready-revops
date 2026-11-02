-- New-business sales velocity by quarter and segment, in the standard form:
--
--   velocity_per_day = open qualified deals at quarter start
--                      x win rate of qualified deals closed in the quarter
--                      x average won ARR / average won sales cycle (days)
--
-- "Qualified" means the deal had entered Qualification or any later stage. Won
-- amounts missing in the CRM are excluded from the average, not zeroed.
-- Reported for complete quarters from velocity_start: the first quarter of the
-- window opens with only two months of pipeline and would understate velocity.
-- Every quarter has a row for every segment, even one with no closed deals.
with opps as (
    select * from {{ ref('stg_opportunities') }}
    where opportunity_type = 'New Business'
),

qualified_on as (
    select opportunity_id, min(entered_at) as qualified_at
    from {{ ref('stg_stage_history') }}
    where stage_order between 2 and 6
      and stage <> 'Closed Lost'
    group by 1
),

quarters as (
    select unnest(generate_series(
        '{{ var("velocity_start") }}'::date,
        ('{{ var("as_of_month") }}'::date - interval 2 month)::date,
        interval 3 month
    ))::date as quarter
),

segments as (
    select distinct segment from opps
),

grid as (
    select q.quarter, s.segment
    from quarters q cross join segments s
    where {{ complete_quarter('q.quarter') }}
),

open_at_start as (
    select q.quarter, o.segment, count(*) as open_qualified
    from grid q
    join opps o on o.created_at < q.quarter and o.segment = q.segment
    join qualified_on qo on qo.opportunity_id = o.opportunity_id and qo.qualified_at < q.quarter
    where not o.is_closed or o.close_date >= q.quarter
    group by 1, 2
),

closed_in_quarter as (
    select
        date_trunc('quarter', o.close_date)::date as quarter,
        o.segment,
        count(*)                                           as closed_qualified,
        count(*) filter (where o.is_won)                   as won,
        avg(o.amount_arr) filter (where o.is_won)          as avg_won_arr,
        avg(date_diff('day', o.created_at, o.close_date)) filter (where o.is_won) as avg_won_cycle_days
    from opps o
    join qualified_on qo using (opportunity_id)
    where o.is_closed
    group by 1, 2
)

select
    g.quarter                                          as close_quarter,
    g.segment,
    coalesce(s.open_qualified, 0)                      as open_qualified_at_start,
    coalesce(c.closed_qualified, 0)                    as closed_qualified,
    coalesce(c.won, 0)                                 as won_opps,
    round(c.won / nullif(c.closed_qualified, 0)::double, 4) as win_rate,
    round(c.avg_won_arr, 2)                            as avg_won_arr,
    round(c.avg_won_cycle_days, 1)                     as avg_won_cycle_days,
    round(coalesce(s.open_qualified, 0) * (c.won / nullif(c.closed_qualified, 0)::double) * c.avg_won_arr
          / nullif(c.avg_won_cycle_days, 0), 2)        as velocity_per_day
from grid g
left join closed_in_quarter c on c.quarter = g.quarter and c.segment = g.segment
left join open_at_start s on s.quarter = g.quarter and s.segment = g.segment
