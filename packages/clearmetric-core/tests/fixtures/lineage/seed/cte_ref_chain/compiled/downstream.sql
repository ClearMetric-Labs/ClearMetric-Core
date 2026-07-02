with hop_a as (
    select event_id, event_type from upstream
),
hop_b as (
    select event_type from hop_a
)
select event_type from hop_b
