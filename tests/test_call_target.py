"""Tests for _call_target_system: network errors, timeouts, invalid URLs."""

import socket
from unittest.mock import MagicMock, patch

import pytest
from app.tasks.celery_app import _call_target_system
from app.services.runner import count_tokens


class FakeCase:
    def __init__(self, input="test input", case_type="qa", expected_tool=None,
                 reference_context_ids=None, reference_answer="ref answer"):
        self.input = input
        self.case_type = case_type
        self.expected_tool = expected_tool
        self.reference_context_ids = reference_context_ids
        self.reference_answer = reference_answer


def test_falls_back_to_simulate_when_no_target_url():
    """Without target_url, returns simulated output with zero latency."""
    case = FakeCase(input="Hello", reference_answer="Hello world")
    output, tool, args, latency, tokens = _call_target_system(case, {})
    assert output == "Hello world"
    assert tool is None
    assert args is None
    assert latency == 0


def test_falls_back_when_target_url_empty_string():
    """Empty target_url also triggers fallback."""
    case = FakeCase(input="Hello")
    output, tool, args, latency, tokens = _call_target_system(case, {"target_url": ""})
    assert latency == 0


@patch("app.tasks.celery_app.requests.Session")
def test_raises_on_connection_error(mock_session_class):
    """Connection errors to target system raise an exception."""
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionError("Connection refused")
    mock_session_class.return_value = mock_session

    case = FakeCase(input="Hello")
    with pytest.raises(ConnectionError):
        _call_target_system(case, {
            "target_url": "http://example.com/api",
            "target_timeout": 5,
        })


@patch("app.tasks.celery_app.requests.Session")
def test_raises_on_timeout(mock_session_class):
    """Request timeout raises an exception."""
    mock_session = MagicMock()
    from requests.exceptions import Timeout
    mock_session.post.side_effect = Timeout("Request timed out")
    mock_session_class.return_value = mock_session

    case = FakeCase(input="Hello")
    with pytest.raises(Timeout):
        _call_target_system(case, {
            "target_url": "http://example.com/api",
            "target_timeout": 0.001,
        })


@patch("app.tasks.celery_app.requests.Session")
def test_raises_on_http_error(mock_session_class):
    """HTTP error status codes raise an exception."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("HTTP 500")
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    case = FakeCase(input="Hello")
    with pytest.raises(Exception, match="HTTP 500"):
        _call_target_system(case, {
            "target_url": "http://example.com/api",
        })


@patch("app.tasks.celery_app.requests.Session")
def test_parses_response_fields(mock_session_class):
    """Response is parsed correctly with various field names."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "answer": "The answer is 42",
        "tool_called": "calculator",
        "tool_args": {"expr": "6*7"},
    }
    mock_response.raise_for_status = MagicMock()
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    case = FakeCase(input="What is 6*7?", expected_tool="calculator")
    output, tool, args, latency, tokens = _call_target_system(case, {
        "target_url": "http://example.com/api",
    })

    assert output == "The answer is 42"
    assert tool == "calculator"
    assert args == {"expr": "6*7"}
    assert latency >= 0


@patch("app.tasks.celery_app.requests.Session")
def test_tries_multiple_response_field_names(mock_session_class):
    """Falls back through output/response when 'answer' is not present."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"output": "fallback output"}
    mock_response.raise_for_status = MagicMock()
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    case = FakeCase(input="Hi")
    output, tool, args, latency, tokens = _call_target_system(case, {
        "target_url": "http://example.com/api",
    })

    assert output == "fallback output"


@patch("app.tasks.celery_app.requests.Session")
def test_returns_empty_string_on_empty_response(mock_session_class):
    """When response has none of the expected fields, returns empty string."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"unexpected": "data"}
    mock_response.raise_for_status = MagicMock()
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    case = FakeCase(input="Hi")
    output, tool, args, latency, tokens = _call_target_system(case, {
        "target_url": "http://example.com/api",
    })

    assert output == ""


@patch("app.services.url_safety.socket.getaddrinfo")
def test_rejects_domain_resolving_to_private_ip(mock_getaddrinfo):
    """DNS-resolved private IPs are rejected before the request is sent."""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80)),
    ]

    case = FakeCase(input="Hi")
    with pytest.raises(ValueError, match="private|resolved"):
        _call_target_system(case, {
            "target_url": "http://internal.example/api",
        })


@patch("app.tasks.celery_app.requests.Session")
def test_disables_redirects_for_target_requests(mock_session_class):
    """Target requests must not follow redirects that could bypass SSRF checks."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"answer": "safe response"}
    mock_response.raise_for_status = MagicMock()
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    case = FakeCase(input="Hi")
    _call_target_system(case, {
        "target_url": "http://example.com/api",
    })

    assert mock_session.post.call_args.kwargs["allow_redirects"] is False
