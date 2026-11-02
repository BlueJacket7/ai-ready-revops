-- Every funnel stage must equal an independent count from stage history: a closed
-- new-business deal created in the window counts at stage N if any history row
-- (other than Closed Lost) is at stage N or later. This is what makes skipped
-- stages count as passed, checked by a different query than the model's.
with opps as (
    select opportunity_id, segment from {{ ref('stg_opportunities') }}
    where opportunity_type = 'New Business' and is_closed
      and created_at >= '{{ var("window_start") }}'::date
),
stages as (select unnest(range(1, 7)) as stage_order),
expected as (
    select coalesce(o.segment, 'All') as segment, s.stage_order, count(*) as n
    from stages s
    join opps o on exists (
        select 1 from {{ ref('stg_stage_history') }} h
        where h.opportunity_id = o.opportunity_id
          and h.stage <> 'Closed Lost'
          and h.stage_order >= s.stage_order
    )
    group by grouping sets ((o.segment, s.stage_order), (s.stage_order))
)
select e.segment, e.stage_order, e.n, f.reached
from expected e
left join {{ ref('stage_funnel') }} f using (segment, stage_order)
where f.reached is null or f.reached <> e.n
