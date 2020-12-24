-- attempt_2.sql
{{
    config(
        materialized='incremental',
        unique_key='user_id'
    )
}}

{% set day_windows = [3, 7, 60, 365] %}

SELECT
    user_id
    ,birth_date
    {% for day_window in day_windows -%}
    ,(
        CASE
        WHEN DATEDIFF(DAY, birth_date, CURRENT_DATE) >= {{ day_window }}
        THEN
            SUM(
                CASE
                WHEN DATEDIFF(DAY, birth_date, date) < {{ day_window }}
                THEN
                    revenue
                ELSE
                    0
                END
            )
        ELSE
            NULL
        END
    ) AS d{{ day_window }}_revenue
    {% endfor -%}
FROM {{ ref('user_transactions') }}
{% if is_incremental() %}
WHERE
    DATEDIFF(DAY, birth_date, CURRENT_DATE) < {{ day_windows|max }}
    AND DATEDIFF(DAY, date, CURRENT_DATE) < {{ day_windows|max }}
{% endif %}
GROUP BY 1, 2
