-- Every closed deal has a close date after imputation, on or after its created date.
select opportunity_id from {{ ref('stg_opportunities') }}
where is_closed and (close_date is null or close_date < created_at)
