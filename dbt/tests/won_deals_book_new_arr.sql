-- Every won new-business deal that closed before the as-of month must have booked
-- exactly one 'new' subscription event, and no 'new' event may lack its deal.
with won as (
    select opportunity_id from {{ ref('stg_opportunities') }}
    where opportunity_type = 'New Business' and is_won
      and close_date < '{{ var("as_of_month") }}'::date
),
booked as (
    select opportunity_id, count(*) as n from {{ ref('stg_subscription_events') }}
    where event_type = 'new' and opportunity_id is not null
    group by 1
)
select coalesce(w.opportunity_id, b.opportunity_id) as opportunity_id, b.n
from won w
full outer join booked b using (opportunity_id)
where b.n is null or b.n <> 1 or w.opportunity_id is null
