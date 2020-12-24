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
GROUP BY 1, 2
