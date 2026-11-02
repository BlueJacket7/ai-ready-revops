-- Every closed new-business deal created in the window must enter the funnel at
-- Discovery: the top row equals an independent count of those deals.
select f.reached, e.n
from {{ ref('stage_funnel') }} f
cross join (
    select count(*) as n from {{ ref('stg_opportunities') }}
    where opportunity_type = 'New Business' and is_closed
      and created_at >= '{{ var("window_start") }}'::date
) e
where f.segment = 'All' and f.stage = 'Discovery' and f.reached <> e.n
