select
    rep_id,
    segment,
    region,
    hire_date                       as hire_month,
    annual_quota::double            as annual_quota
from {{ source('clean', 'reps') }}
