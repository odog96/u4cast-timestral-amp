"""The workbench model endpoint: the caller, the served handler, and the settings."""

import json

import numpy as np
import pytest

from u4cast import endpoint as endpoint_module
from u4cast.endpoint import ModelEndpointError, WorkbenchModelForecaster, handle_request
from u4cast.model import InProcessForecaster, StandInForecaster
from u4cast.serving import forecaster_from_settings
from u4cast.settings import Settings

URL = "https://modelservice.example.test/model"


class FakeEndpoint:
    """Stands in for Cloudera AI: passes each request to the served handler."""

    def __init__(self, failures=()):
        self.model = StandInForecaster()
        self.requests = []
        self.headers = []
        self._failures = list(failures)

    def __call__(self, url, payload, headers, timeout):
        assert url == URL
        self.requests.append(json.loads(json.dumps(payload)))  # must survive JSON
        self.headers.append(headers)
        if self._failures:
            raise self._failures.pop(0)
        return {"success": True, "response": handle_request(self.model, payload["request"])}


def histories(n_series, n_steps=40):
    return np.arange(n_series * n_steps, dtype=np.float64).reshape(n_series, n_steps) / 7.0


def caller(fake, **options):
    return WorkbenchModelForecaster(URL, "access-key", post=fake, **options)


def test_a_forecast_through_the_endpoint_matches_the_model_behind_it():
    data = histories(5)
    data[2, 10] = np.nan
    data[4, -1] = np.nan
    fake = FakeEndpoint()
    result = caller(fake).forecast(data, horizon=3)
    direct = StandInForecaster().forecast(data, horizon=3)
    for name in ("forecast", "low", "high"):
        assert np.allclose(getattr(result, name), getattr(direct, name), rtol=1e-6)
    assert (result.low_level, result.high_level) == (0.1, 0.9)


def test_the_request_carries_the_access_key_and_missing_values_as_null():
    data = np.array([[1.0, np.nan, 3.0]])
    fake = FakeEndpoint()
    caller(fake).forecast(data, horizon=2)
    assert fake.requests == [
        {"accessKey": "access-key", "request": {"histories": [[1.0, None, 3.0]], "horizon": 2}}
    ]


def test_the_api_key_is_sent_only_when_there_is_one():
    with_key, without_key = FakeEndpoint(), FakeEndpoint()
    caller(with_key, api_key="secret").forecast(histories(1), horizon=1)
    caller(without_key).forecast(histories(1), horizon=1)
    assert with_key.headers == [{"Authorization": "Bearer secret"}]
    assert without_key.headers == [{}]


def test_the_answer_is_plain_json_without_nan():
    answer = handle_request(StandInForecaster(), {"histories": [[1.0, None, 3.5]], "horizon": 2})
    text = json.dumps(answer, allow_nan=False)
    assert json.loads(text)["forecast"] == [[3.5, 3.5]]
    assert answer["model_name"] == "stand-in"
    assert answer["seconds"] >= 0


@pytest.mark.parametrize(
    ("n_steps", "horizon", "expected_batches"),
    [
        (32, 12, [500, 100]),  # 2 blocks a series: the batch size of 500 is the limit
        (200, 12, [250, 250, 100]),  # 8 blocks a series: 2,000 / 8
        (730, 30, [83] * 7 + [19]),  # 24 blocks a series: 2,000 / 24
        (200, 365, [105] * 5 + [75]),  # 7 + 12 blocks a series
    ],
)
def test_batches_are_sized_by_the_work_a_request_costs(n_steps, horizon, expected_batches):
    fake = FakeEndpoint()
    result = caller(fake).forecast(histories(600, n_steps), horizon)
    assert [len(request["request"]["histories"]) for request in fake.requests] == expected_batches
    assert result.forecast.shape == (600, horizon)


def test_the_model_name_is_the_one_the_endpoint_reports():
    model = caller(FakeEndpoint(), model_name="mistralai/Timestral-209M")
    model.forecast(histories(1), horizon=1)
    assert model.model_name == "stand-in"


def test_a_busy_endpoint_is_tried_again(monkeypatch):
    waits = []
    monkeypatch.setattr(endpoint_module.time, "sleep", waits.append)
    fake = FakeEndpoint(failures=[ModelEndpointError("busy", 429), ModelEndpointError("busy", 503)])
    result = caller(fake).forecast(histories(2), horizon=1)
    assert result.forecast.shape == (2, 1)
    assert len(fake.requests) == 3
    assert waits == [2.0, 4.0]


def test_a_replica_busy_with_another_request_is_tried_again(monkeypatch):
    monkeypatch.setattr(endpoint_module.time, "sleep", lambda seconds: None)
    busy = ModelEndpointError(
        'status 500: {"errors":{"error while calling model":'
        '"context deadline exceeded --- model busy"}}',
        500,
    )
    fake = FakeEndpoint(failures=[busy])
    assert caller(fake).forecast(histories(2), horizon=1).forecast.shape == (2, 1)
    assert len(fake.requests) == 2


def test_a_request_that_took_too_long_is_not_sent_again(monkeypatch):
    monkeypatch.setattr(endpoint_module.time, "sleep", lambda seconds: None)
    too_long = ModelEndpointError(
        'status 500: {"errors":{"error while calling model":"context deadline exceeded"}}', 500
    )
    fake = FakeEndpoint(failures=[too_long])
    with pytest.raises(ModelEndpointError, match="deadline exceeded"):
        caller(fake).forecast(histories(2), horizon=1)
    assert len(fake.requests) == 1


def test_an_endpoint_that_stays_busy_or_fails_is_reported(monkeypatch):
    monkeypatch.setattr(endpoint_module.time, "sleep", lambda seconds: None)
    busy = FakeEndpoint(failures=[ModelEndpointError("busy", 429)] * 3)
    with pytest.raises(ModelEndpointError, match="busy"):
        caller(busy).forecast(histories(1), horizon=1)
    assert len(busy.requests) == 3
    timed_out = FakeEndpoint(failures=[ModelEndpointError("status 504", 504)])
    with pytest.raises(ModelEndpointError, match="504"):
        caller(timed_out).forecast(histories(1), horizon=1)
    assert len(timed_out.requests) == 1


def test_an_answer_that_is_not_a_forecast_is_reported():
    def failed(url, payload, headers, timeout):
        return {"success": False, "errors": {"message": "function raised"}}

    with pytest.raises(ModelEndpointError, match="function raised"):
        caller(failed).forecast(histories(1), horizon=1)

    def wrong_shape(url, payload, headers, timeout):
        answer = handle_request(StandInForecaster(), payload["request"])
        answer["low"] = answer["low"][:1]
        return {"success": True, "response": answer}

    with pytest.raises(ModelEndpointError, match="low of shape"):
        caller(wrong_shape).forecast(histories(3), horizon=2)


def test_requests_the_handler_cannot_read_are_refused():
    model = StandInForecaster()
    with pytest.raises(ValueError, match="needs histories and horizon"):
        handle_request(model, {"histories": [[1.0]]})
    with pytest.raises(ValueError, match="rows of numbers"):
        handle_request(model, {"histories": [[1.0, 2.0], [1.0]], "horizon": 1})
    with pytest.raises(ValueError, match="rows of numbers"):
        handle_request(model, {"histories": [1.0, 2.0], "horizon": 1})


def test_serving_is_chosen_from_the_environment():
    local = Settings.from_environment({})
    assert local.serving == "in_process"
    assert isinstance(forecaster_from_settings(local), InProcessForecaster)

    in_cloudera = Settings.from_environment(
        {
            "U4CAST_MODEL_ACCESS_KEY": "access-key",
            "CDSW_DOMAIN": "ml.example.test",
            "CDSW_APIV2_KEY": "secret",
        }
    )
    assert in_cloudera.serving == "workbench"
    assert in_cloudera.endpoint == "https://modelservice.ml.example.test/model"
    assert "access-key" not in repr(in_cloudera)
    assert "secret" not in repr(in_cloudera)
    model = forecaster_from_settings(in_cloudera)
    assert isinstance(model, WorkbenchModelForecaster)
    assert model.endpoint == "https://modelservice.ml.example.test/model"

    forced = Settings.from_environment(
        {"U4CAST_MODEL_ACCESS_KEY": "access-key", "U4CAST_SERVING": "in_process"}
    )
    assert isinstance(forecaster_from_settings(forced), InProcessForecaster)


def test_settings_that_cannot_work_are_refused():
    with pytest.raises(ValueError, match="U4CAST_SERVING must be"):
        Settings.from_environment({"U4CAST_SERVING": "somewhere"})
    with pytest.raises(ValueError, match="U4CAST_MODEL_ACCESS_KEY"):
        forecaster_from_settings(Settings.from_environment({"U4CAST_SERVING": "workbench"}))
