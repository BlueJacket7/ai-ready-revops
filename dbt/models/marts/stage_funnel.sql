-- New-business stage funnel for closed deals created in the window.
-- A deal "reached" a stage if its history shows that stage or any later one, so a
-- deal that skipped a stage in the CRM still counts as having passed through it.
-- Open deals are excluded: their funnel is not finished.
with opps as (
    select opportunity_id, segment, is_won
    from {{ ref('stg_opportunities') }}
    where opportunity_type = 'New Business'
      and is_closed
      and created_at >= '{{ var("window_start") }}'::date
),

furthest as (
    select o.opportunity_id, o.segment, o.is_won,
           case when o.is_won then 6
                else max(h.stage_order) filter (where h.stage_order <= 5) end as furthest_order
    from opps o
    join {{ ref('stg_stage_history') }} h using (opportunity_id)
    group by 1, 2, 3
),

stages as (
    select * from (values
        (1, 'Discovery'), (2, 'Qualification'), (3, 'Solution'),
        (4, 'Proposal'), (5, 'Negotiation'), (6, 'Closed Won')
    ) as s(stage_order, stage)
),

counts as (
    select
        coalesce(f.segment, 'All') as segment,
        s.stage_order,
        s.stage,
        count(*) filter (where f.furthest_order >= s.stage_order) as reached
    from stages s
    cross join furthest f
    group by grouping sets ((f.segment, s.stage_order, s.stage), (s.stage_order, s.stage))
)

select
    segment,
    stage_order,
    stage,
    reached,
    round(reached / nullif(lag(reached) over (partition by segment order by stage_order), 0)::double, 4)
        as conversion_from_prior_stage,
    round(reached / nullif(first_value(reached) over (partition by segment order by stage_order), 0)::double, 4)
        as conversion_from_discovery
from counts
