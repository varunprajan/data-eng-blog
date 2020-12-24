-- attempt_single_tuple_incremental.sql
{{
    config(
        materialization='incremental',
        unique_key='user_day_window_id'
    )
}}

WITH tmp AS (
SELECT
    user_id
    ,birth_date
    ,{{ var("day_window") }} AS day_window
    ,SUM(revenue) AS cumulative_revenue
FROM {{ ref('user_transactions') }}
WHERE
    DATEDIFF(DAY, birth_date, '{{ var("date_of_interest") }}' ) = {{ var("day_window") }}
    AND date <= '{{ var("date_of_interest") }}'
GROUP BY 1, 2
)

SELECT
    {{ dbt_utils.surrogate_key('user_id', 'day_window') }} AS user_day_window_id,
    *
FROM tmp
