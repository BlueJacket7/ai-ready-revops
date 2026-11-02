-- A churned or contracted account can reach zero, never below it.
select * from {{ ref('account_arr_monthly') }} where arr < -0.01
