-- attempt_pivot_incremental.sql
{{
    config(
        materialization='incremental',
        unique_key='user_day_window_id'
    )
}}

{% set day_windows = [1, 3, 7, 60, 365] %}

WITH tmp AS (
SELECT
    user_id
    ,birth_date
    ,DATEDIFF(DAY, birth_date, CURRENT_DATE) - 1 AS day_window
    ,SUM(revenue) AS cumulative_revenue
FROM {{ ref('user_transactions') }}
WHERE
    -- select birth_dates that correspond to cohort maturation on CURRENT_DATE - 1
    (
        {% for day_window in day_windows -%}
        DATEDIFF(DAY, birth_date, CURRENT_DATE) = {{ day_window }}
        {% if not loop.last -%}
        OR
        {% endif %}
        {% endfor -%}
    )
    -- superfluous, but might help query optimizer
    AND date >= birth_date
    AND DATEDIFF(DAY, date, CURRENT_DATE) < {{ day_windows|max }}
GROUP BY 1, 2
)

SELECT
    {{ dbt_utils.surrogate_key('user_id', 'day_window') }} AS user_day_window_id,
    *
FROM tmp
