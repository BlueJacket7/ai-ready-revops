-- Every reported quarter carries a row for every segment, so a quarter where one
-- segment closed nothing shows as zero rather than silently disappearing.
select close_quarter, count(*) as segments
from {{ ref('pipeline_velocity_quarterly') }}
group by 1
having count(*) <> (select count(distinct segment) from {{ ref('stg_accounts') }})
