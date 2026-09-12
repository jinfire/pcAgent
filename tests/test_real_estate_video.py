import pytest

from app.config import ModelSettings
from app.real_estate.video import (
    build_youtube_interaction_request,
    choose_video_processing_mode,
    validate_public_youtube_url,
)


def test_native_youtube_request_reuses_configured_model() -> None:
    configured = ModelSettings(provider="gemini", model="configured-multimodal-model", reasoning_effort="low")
    request = build_youtube_interaction_request(
        youtube_url="https://www.youtube.com/watch?v=public",
        model_settings=configured,
        duration_seconds=120,
    )
    assert request["model"] == "configured-multimodal-model"
    assert request["input"][1] == {
        "type": "video",
        "uri": "https://www.youtube.com/watch?v=public",
        "processing": "static",
    }
    assert request["metadata"]["result_is_unverified_claims"] is True


def test_long_or_targeted_video_uses_agentic_processing() -> None:
    assert choose_video_processing_mode(duration_seconds=301, targeted_query=False) == "agentic"
    assert choose_video_processing_mode(duration_seconds=60, targeted_query=True) == "agentic"
    assert choose_video_processing_mode(duration_seconds=60, targeted_query=False) == "static"


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=x",
        "https://youtube.example/watch?v=x",
        "https://user:password@youtube.com/watch?v=x",
        "file:///video.mp4",
    ],
)
def test_youtube_url_validation_rejects_non_public_targets(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_youtube_url(url)
