-- Win rate and quota attainment by rep and close year.
-- Attainment counts won new-business and expansion ARR against annual quota.
-- Years are calendar years; the first and last years of the window are partial.
-- Won deals with no amount in the CRM add nothing to won_arr and are counted in
-- won_missing_amount, so understated attainment is visible rather than hidden.
-- This is per-employee performance data: warehouse only, never the llm export.
with closed as (
    select *
    from {{ ref('stg_opportunities') }}
    where is_closed
      and opportunity_type in ('New Business', 'Expansion')
      and close_date >= '{{ var("window_start") }}'::date
)

select
    r.rep_id,
    r.segment,
    year(c.close_date)                                                    as close_year,
    count(*) filter (where c.opportunity_type = 'New Business')           as nb_closed,
    count(*) filter (where c.opportunity_type = 'New Business' and c.is_won) as nb_won,
    round(count(*) filter (where c.opportunity_type = 'New Business' and c.is_won)
          / nullif(count(*) filter (where c.opportunity_type = 'New Business'), 0)::double, 4)
                                                                          as nb_win_rate,
    round(coalesce(sum(c.amount_arr) filter (where c.is_won), 0), 2)      as won_arr,
    count(*) filter (where c.is_won and c.amount_arr is null)             as won_missing_amount,
    r.annual_quota,
    round(coalesce(sum(c.amount_arr) filter (where c.is_won), 0) / r.annual_quota, 4)
                                                                          as quota_attainment,
    year(c.close_date) in (year('{{ var("window_start") }}'::date), year('{{ var("as_of_month") }}'::date))
        and not (month('{{ var("window_start") }}'::date) = 1 and year(c.close_date) = year('{{ var("window_start") }}'::date))
        and not (month('{{ var("as_of_month") }}'::date) = 12 and year(c.close_date) = year('{{ var("as_of_month") }}'::date))
                                                                          as is_partial_year
from closed c
join {{ ref('stg_reps') }} r on r.rep_id = c.owner_rep_id
group by r.rep_id, r.segment, year(c.close_date), r.annual_quota
