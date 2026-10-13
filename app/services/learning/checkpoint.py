"""Which parts of a lesson's checkpointData a client is allowed to write.

The rest (the open practice-exit marker and the exit attempt history) is server
state that decides whether a lesson is completed, so it can only be changed by
the practice-exit services.
"""

CLIENT_CHECKPOINT_KEYS = ("visited", "checks")


def sanitise_checkpoint(incoming: dict) -> dict:
    """Drops every key a client must not set (`exit`, `practice`, ...)."""
    return {key: incoming[key] for key in CLIENT_CHECKPOINT_KEYS if key in incoming}
