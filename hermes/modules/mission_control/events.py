"""Mission Control event vocabulary.

Namespaced `mission_control.*`. These fire whenever a view is
materialized (e.g. `view.mission_summary_viewed`), and when the
live event stream is subscribed to / unsubscribed from.
"""

MISSION_SUMMARY_VIEWED = "mission_control.view.mission_summary_viewed"
MISSION_TIMELINE_VIEWED = "mission_control.view.mission_timeline_viewed"
MISSION_STATISTICS_VIEWED = "mission_control.view.mission_statistics_viewed"
MISSION_AGGREGATE_REFRESHED = "mission_control.view.aggregate_refreshed"
MISSION_STREAM_SUBSCRIBED = "mission_control.view.stream_subscribed"
MISSION_STREAM_UNSUBSCRIBED = "mission_control.view.stream_unsubscribed"

__all__ = [
    "MISSION_SUMMARY_VIEWED",
    "MISSION_TIMELINE_VIEWED",
    "MISSION_STATISTICS_VIEWED",
    "MISSION_AGGREGATE_REFRESHED",
    "MISSION_STREAM_SUBSCRIBED",
    "MISSION_STREAM_UNSUBSCRIBED",
]