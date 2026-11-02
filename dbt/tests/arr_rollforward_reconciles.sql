-- Every month: starting + new + expansion + contraction + churn = ending, to the cent.
select month
from {{ ref('arr_rollforward') }}
where abs(starting_arr + new_arr + expansion_arr + contraction_arr + churn_arr - ending_arr) > 0.011
