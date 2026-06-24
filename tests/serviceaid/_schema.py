"""Shared ACDC schema SAD for tests (saidified by callers)."""

BROKER_SCHEMA_SAD = {
    "$id": "",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "BrokerLicense",
    "type": "object",
    "properties": {
        "v": {"type": "string"},
        "d": {"type": "string"},
        "i": {"type": "string"},
        "ri": {"type": "string"},
        "s": {"type": "string"},
        "a": {
            "oneOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "d": {"type": "string"},
                        "i": {"type": "string"},
                        "dt": {"type": "string", "format": "date-time"},
                        "license": {"type": "string"},
                    },
                    "additionalProperties": False,
                    "required": ["d", "i", "dt", "license"],
                },
            ]
        },
    },
    "additionalProperties": False,
    "required": ["v", "d", "i", "ri", "s", "a"],
}

RATING_SCHEMA_SAD = {
    "$id": "",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "RatingResult",
    "type": "object",
    "properties": {
        "v": {"type": "string"},
        "d": {"type": "string"},
        "i": {"type": "string"},
        "ri": {"type": "string"},
        "s": {"type": "string"},
        "a": {
            "oneOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "d": {"type": "string"},
                        "i": {"type": "string"},
                        "dt": {"type": "string", "format": "date-time"},
                        "score": {"type": "number"},
                    },
                    "additionalProperties": False,
                    "required": ["d", "i", "dt", "score"],
                },
            ]
        },
    },
    "additionalProperties": False,
    "required": ["v", "d", "i", "ri", "s", "a"],
}
