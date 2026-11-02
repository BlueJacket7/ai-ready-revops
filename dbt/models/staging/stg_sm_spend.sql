select
    month::date                     as month,
    sales_spend::double             as sales_spend,
    marketing_spend::double         as marketing_spend,
    sales_spend::double + marketing_spend::double as total_spend
from {{ source('clean', 'sm_spend') }}
