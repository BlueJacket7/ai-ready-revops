-- The funnel's Closed Won row must equal an independent count of won, closed
-- new-business deals created in the window, per segment and overall.
with expected as (
    select coalesce(segment, 'All') as segment, count(*) as won
    from {{ ref('stg_opportunities') }}
    where opportunity_type = 'New Business' and is_closed and is_won
      and created_at >= '{{ var("window_start") }}'::date
    group by grouping sets ((segment), ())
)
select e.segment, e.won, f.reached
from expected e
left join {{ ref('stage_funnel') }} f on f.segment = e.segment and f.stage = 'Closed Won'
where f.reached is null or f.reached <> e.won
