select
    opportunity_id,
    stage,
    entered_at::date as entered_at,
    case stage
        when 'Discovery'     then 1
        when 'Qualification' then 2
        when 'Solution'      then 3
        when 'Proposal'      then 4
        when 'Negotiation'   then 5
        when 'Closed Won'    then 6
        when 'Closed Lost'   then 6
    end as stage_order
from {{ source('clean', 'opportunity_stage_history') }}
