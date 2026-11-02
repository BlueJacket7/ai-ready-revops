{# True when the quarter starting at `col` lies wholly inside the generated window.
   Partial first and last quarters would distort per-quarter rates (a one-month
   quarter makes CAC look four times worse), so quarterly marts keep only these. #}
{% macro complete_quarter(col) -%}
    ({{ col }} >= '{{ var("window_start") }}'::date
     and ({{ col }} + interval 3 month)::date <= ('{{ var("as_of_month") }}'::date + interval 1 month)::date)
{%- endmacro %}
