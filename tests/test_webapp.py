"""The web server, end to end, with an in-memory store and the stand-in model."""

import datetime as dt
import io
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fake_store import FakeStore
from u4cast.connections import Connection, checked
from u4cast.layout import Kind
from u4cast.model import StandInForecaster
from u4cast.serving import ModelChoice, is_deployed, served_name, workbench_choices
from u4cast.settings import Settings
from webapp.api import create_app
from webapp.workspace import Workspace

MONTHS = pd.date_range("2022-01-01", periods=40, freq="MS")
SKUS = ["GIN", "RUM", "VODKA"]
COUNTIES = ["LINN", "POLK"]


def sales() -> pd.DataFrame:
    """Two counties by three SKUs, 40 months. A SKU sells 100 * (s + 1) + month in each county."""
    return pd.DataFrame(
        [
            {"month": month.date(), "county": county, "sku": sku, "bottles": 100.0 * (s + 1) + i}
            for county in COUNTIES
            for s, sku in enumerate(SKUS)
            for i, month in enumerate(MONTHS)
        ]
    )


@pytest.fixture
def store() -> FakeStore:
    fake = FakeStore()
    fake.add_table("sales", sales())
    return fake


@pytest.fixture
def client(store, tmp_path) -> TestClient:
    workspace = Workspace(lambda: store, StandInForecaster())
    workspace.connect()
    return TestClient(create_app(workspace, front_end=tmp_path))


def add_dataset(client: TestClient, name: str = "Sales") -> dict:
    response = client.post(
        "/api/datasets",
        json={
            "name": name,
            "source_table": "fake.sales",
            "date_column": "month",
            "target_column": "bottles",
            "dimension_columns": ["county", "sku"],
            "frequency": "monthly",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def finish(client: TestClient, response) -> dict:
    """Wait for the job a request started and return it."""
    assert response.status_code == 200, response.text
    job = response.json()
    for _ in range(400):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"the job did not finish: {job}")


def new_forecast(client: TestClient, **changes) -> dict:
    body = {
        "forecast_name": "Monthly demand",
        "dimension_columns": ["county", "sku"],
        "horizon": 6,
        "cutoffs": 2,
        "cutoff_step": 3,
    } | changes
    job = finish(client, client.post("/api/datasets/Sales/forecasts", json=body))
    assert job["state"] == "done", job
    return job["result"]


def test_status_says_when_the_data_lake_is_connected(store, tmp_path):
    workspace = Workspace(lambda: store, StandInForecaster())
    client = TestClient(create_app(workspace, front_end=tmp_path))
    assert client.get("/api/status").json()["state"] == "starting"
    assert client.get("/api/datasets").status_code == 503
    workspace.connect()
    status = client.get("/api/status").json()
    assert (status["state"], status["model"], status["database"]) == ("ready", "stand-in", "fake")


def test_a_failed_connection_is_reported(tmp_path):
    def broken():
        raise RuntimeError("no data connection")

    workspace = Workspace(broken, StandInForecaster())
    workspace.connect()
    client = TestClient(create_app(workspace, front_end=tmp_path))
    status = client.get("/api/status").json()
    assert (status["state"], status["error"]) == ("failed", "no data connection")
    assert client.get("/api/datasets").json() == {"detail": "no data connection"}


def test_a_table_is_profiled_and_its_column_roles_suggested(client):
    assert client.get("/api/sources").json() == [{"table": "fake.sales", "dataset": None}]
    profile = client.get("/api/sources/profile", params={"table": "fake.sales"}).json()
    assert profile["rows"] == 240
    assert [column["name"] for column in profile["columns"]] == [
        "month",
        "county",
        "sku",
        "bottles",
    ]
    assert profile["suggested"] == {
        "date_column": "month",
        "target_column": "bottles",
        "dimension_columns": ["county", "sku"],
    }
    assert (profile["frequency"], profile["frequency_share"]) == ("monthly", 1.0)
    assert (profile["first_date"], profile["last_date"], profile["periods"]) == (
        "2022-01-01",
        "2025-04-01",
        40,
    )
    assert profile["preview"][0] == {
        "month": "2022-01-01",
        "county": "LINN",
        "sku": "GIN",
        "bottles": 100.0,
    }
    missing = client.get("/api/sources/profile", params={"table": "fake.nothing"})
    assert missing.status_code == 400


def test_a_dataset_is_saved_once_and_then_listed(client):
    saved = add_dataset(client)
    assert saved["fill"] == "missing"
    assert [d["name"] for d in client.get("/api/datasets").json()] == ["Sales"]
    again = client.post(
        "/api/datasets",
        json={
            "name": "Sales",
            "source_table": "fake.sales",
            "date_column": "month",
            "target_column": "bottles",
            "dimension_columns": ["sku"],
            "frequency": "monthly",
        },
    )
    assert again.status_code == 400
    assert "already saved" in again.json()["detail"]
    overview = client.get("/api/datasets/Sales").json()
    assert overview["dates"][0] == "2022-01-01"
    assert len(overview["dates"]) == 40
    assert overview["minimum_history"] == 24
    assert overview["forecast_groups"] == []
    assert overview["values_table"] == "fake.forecast_values_sales"
    assert client.get("/api/datasets/Sales/dimensions/sku").json() == SKUS
    assert client.get("/api/datasets/Other").status_code == 400


def test_a_dataset_must_name_columns_the_table_has(client):
    response = client.post(
        "/api/datasets",
        json={
            "name": "Broken",
            "source_table": "fake.sales",
            "date_column": "month",
            "target_column": "price",
            "dimension_columns": ["sku"],
            "frequency": "monthly",
        },
    )
    assert response.status_code == 400
    assert "no column named price" in response.json()["detail"]


def test_a_slice_is_described_before_it_is_forecast(client, store):
    add_dataset(client)
    body = {
        "dimension_columns": ["sku"],
        "slice_filter": {"county": ["POLK"]},
        "horizon": 6,
        "cutoffs": 2,
        "cutoff_step": 3,
    }
    summary = client.post("/api/datasets/Sales/slice", json=body).json()
    assert (summary["series"], summary["steps"], summary["left_out"]) == (3, 40, 0)
    assert (summary["history_start"], summary["history_end"]) == ("2022-01-01", "2025-04-01")
    assert summary["by_start"] == [
        {"start": "2022-01-01", "steps": 40, "series": 3},
        {"start": "2023-01-01", "steps": 28, "series": 3},
    ]
    assert summary["backtest_cutoffs"] == ["2024-07-01", "2024-10-01"]
    assert summary["default_horizon"] == 12
    assert summary["seconds"] > 0
    # Asking again does not read the data lake again.
    client.post("/api/datasets/Sales/slice", json=body)
    assert store.loads == 1
    too_short = client.post(
        "/api/datasets/Sales/slice", json=body | {"history_start": "2024-01-01"}
    ).json()
    assert (too_short["series"], too_short["left_out"]) == (0, 3)
    assert too_short["left_out_by_reason"] == {"too_short": 3}
    nothing = client.post(
        "/api/datasets/Sales/slice", json=body | {"slice_filter": {"county": ["NONE"]}}
    )
    assert nothing.status_code == 400


def test_a_forecast_is_made_stored_and_backtested(client, store):
    add_dataset(client)
    result = new_forecast(client)
    assert (result["series"], result["rows"], result["backtests"]) == (6, 36, 2)
    kinds = [record.kind for record in store.saved_forecasts]
    assert kinds == [Kind.FORECAST, Kind.BACKTEST, Kind.BACKTEST]
    overview = client.get("/api/datasets/Sales/forecasts/Monthly demand").json()
    assert len(overview["issues"]) == 1
    assert overview["issues"][0]["series"] == 6
    assert overview["issues"][0]["history_end_date"] == "2025-04-01"
    assert [b["history_end_date"] for b in overview["backtests"]] == ["2024-07-01", "2024-10-01"]
    assert overview["reissue_dates"][0] == "2023-12-01"
    assert "2025-04-01" not in overview["reissue_dates"]
    groups = client.get("/api/datasets/Sales").json()["forecast_groups"]
    assert [(g["forecast_name"], g["issues"], g["backtests"]) for g in groups] == [
        ("Monthly demand", 1, 2)
    ]


def test_a_forecast_name_is_used_once(client):
    add_dataset(client)
    new_forecast(client)
    again = client.post(
        "/api/datasets/Sales/forecasts",
        json={"forecast_name": "Monthly demand", "dimension_columns": ["sku"], "horizon": 6},
    )
    assert again.status_code == 400
    assert "Open it to reissue it" in again.json()["detail"]
    unnamed = client.post(
        "/api/datasets/Sales/forecasts",
        json={"forecast_name": " ", "dimension_columns": ["sku"], "horizon": 6},
    )
    assert unnamed.status_code == 400


def test_the_chart_of_one_series_and_of_the_total(client):
    add_dataset(client)
    forecast_id = new_forecast(client, cutoffs=0)["forecast_id"]
    one = client.get(
        f"/api/forecasts/{forecast_id}/chart", params={"ts_id": "county=POLK|sku=RUM"}
    ).json()
    assert one["label"] == "POLK · RUM"
    assert len(one["dates"]) == len(one["actual"]) == 40
    assert one["actual"][-1] == 239.0
    assert one["future_dates"] == [f"2025-{m:02d}-01" for m in range(5, 11)]
    # The stand-in repeats the last value, 20% below and above.
    assert one["forecast"] == [239.0] * 6
    assert one["low"] == pytest.approx([239.0 * 0.8] * 6)
    assert one["actual_after"] == [None] * 6

    total = client.get(f"/api/forecasts/{forecast_id}/chart").json()
    assert total["label"] == "Total of 6 series"
    last = 2 * (139 + 239 + 339)
    assert total["actual"][-1] == last
    assert total["forecast"] == [float(last)] * 6
    # The range of the total comes from forecasting the total, not from adding up ranges.
    assert total["low"] == pytest.approx([last * 0.8] * 6)
    assert total["high"] == pytest.approx([last * 1.2] * 6)
    unknown = client.get(f"/api/forecasts/{forecast_id}/chart", params={"ts_id": "sku=NONE"})
    assert unknown.status_code == 400


def test_series_are_searched_sorted_and_paged(client):
    add_dataset(client)
    forecast_id = new_forecast(client, cutoffs=0)["forecast_id"]
    everything = client.get(f"/api/forecasts/{forecast_id}/series").json()
    assert everything["total"] == 6
    assert everything["rows"][0]["label"] in ("LINN · VODKA", "POLK · VODKA")
    assert everything["rows"][0]["forecast_sum"] == 6 * 339.0
    # Against the same six months a year earlier: May to October 2024.
    earlier = sum(300.0 + i for i in range(28, 34))
    assert everything["rows"][0]["previous_sum"] == earlier
    assert everything["rows"][0]["change"] == pytest.approx(6 * 339.0 / earlier - 1)
    found = client.get(f"/api/forecasts/{forecast_id}/series", params={"q": "polk ru"}).json()
    assert [row["ts_id"] for row in found["rows"]] == ["county=POLK|sku=RUM"]
    by_name = client.get(
        f"/api/forecasts/{forecast_id}/series", params={"sort": "name", "limit": 2, "offset": 2}
    ).json()
    assert [row["label"] for row in by_name["rows"]] == ["LINN · VODKA", "POLK · GIN"]


def test_reissues_overlap_and_are_compared_with_what_happened(client, store):
    add_dataset(client)
    new_forecast(client, history_end="2024-10-01", cutoffs=0)
    address = "/api/datasets/Sales/forecasts/Monthly demand"
    second = finish(client, client.post(f"{address}/reissue", json={"as_of": "2024-12-01"}))
    assert second["state"] == "done", second
    third = finish(client, client.post(f"{address}/reissue", json={}))
    assert third["state"] == "done", third
    ends = [record.history_end_date.isoformat() for record in store.saved_forecasts]
    assert ends == ["2024-10-01", "2024-12-01", "2025-04-01"]
    assert len({record.forecast_id for record in store.saved_forecasts}) == 3

    repeated = finish(client, client.post(f"{address}/reissue", json={}))
    assert repeated["state"] == "failed"
    assert "already issued" in repeated["error"]

    compared = client.get(f"{address}/compare", params={"ts_id": "county=LINN|sku=GIN"}).json()
    assert [issue["history_end_date"] for issue in compared["issues"]] == ends
    assert compared["dates"][0] == "2024-11-01"
    assert compared["dates"][-1] == "2025-10-01"
    march = compared["dates"].index("2025-03-01")
    # March 2025 was predicted by the first two issues, and has since happened.
    assert [forecast[march] for forecast in compared["forecasts"]] == [133.0, 135.0, None]
    assert compared["actual"][march] == 138.0
    assert compared["actual"][-1] is None
    total = client.get(f"{address}/compare").json()
    assert total["label"] == "Total of 6 series"
    assert total["forecasts"][0][0] == 2 * (133.0 + 233.0 + 333.0)

    # The first issue's chart now shows what happened after it.
    first_id = compared["issues"][0]["forecast_id"]
    chart = client.get(f"/api/forecasts/{first_id}/chart").json()
    assert chart["dates"][-1] == "2024-10-01"
    assert chart["actual_after"] == [2 * (134.0 + 234.0 + 334.0) + 6 * i for i in range(6)]


def test_accuracy_comes_from_stored_backtests(client):
    add_dataset(client)
    address = "/api/datasets/Sales/forecasts/Monthly demand"
    new_forecast(client, cutoffs=0)
    assert client.get(f"{address}/accuracy").json() == {"available": False}
    checked = finish(
        client, client.post(f"{address}/accuracy", json={"cutoffs": 2, "cutoff_step": 3})
    )
    assert checked["state"] == "done", checked
    accuracy = client.get(f"{address}/accuracy").json()
    assert accuracy["available"]
    assert accuracy["cutoffs"] == ["2024-07-01", "2024-10-01"]
    # A flat forecast of series that rise by 1 a month is off by 1 to 6.
    assert accuracy["total"]["mae"] == pytest.approx(3.5)
    assert accuracy["total"]["n"] == 2 * 6 * 6
    assert [row["step"] for row in accuracy["by_step"]] == [1, 2, 3, 4, 5, 6]
    assert accuracy["by_step"][2]["mae"] == pytest.approx(3.0)
    assert accuracy["series"] == 6
    # Worst first: the same error weighs most on the smallest series.
    assert accuracy["by_series"][0]["label"].endswith("GIN")
    assert accuracy["by_series"][0]["n"] == 12
    searched = client.get(f"{address}/accuracy", params={"q": "vodka"}).json()
    assert searched["series"] == 2
    again = finish(
        client, client.post(f"{address}/accuracy", json={"cutoffs": 2, "cutoff_step": 3})
    )
    assert again["state"] == "failed"
    assert "already checked" in again["error"]


def test_stored_rows_and_the_csv_download(client):
    add_dataset(client)
    forecast_id = new_forecast(client, cutoffs=0)["forecast_id"]
    stored = client.get(f"/api/forecasts/{forecast_id}/rows", params={"limit": 3}).json()
    assert stored["table"] == "fake.forecast_values_sales"
    assert stored["rows"] == 36
    assert len(stored["preview"]) == 3
    assert stored["columns"] == [
        "forecast_id",
        "ts_id",
        "county",
        "sku",
        "date",
        "forecast",
        "low",
        "high",
        "step",
    ]
    assert forecast_id in stored["sql"]
    download = client.get(f"/api/forecasts/{forecast_id}/download.csv")
    assert download.headers["content-type"].startswith("text/csv")
    assert (
        'filename="monthly_demand_2025-04-01_forecast.csv"'
        in download.headers["content-disposition"]
    )
    table = pd.read_csv(io.StringIO(download.text))
    assert len(table) == 36
    assert table["date"].iloc[0] == "2025-05-01"
    assert np.isfinite(table[["forecast", "low", "high"]].to_numpy()).all()


def test_an_uploaded_csv_becomes_a_new_table(client, store):
    text = sales().rename(columns={"bottles": "Units Sold"}).to_csv(index=False)
    uploaded = client.post("/api/uploads", files={"file": ("My Sales.csv", text, "text/csv")})
    assert uploaded.json() == {"table": "fake.upload_my_sales", "rows": 240}
    profile = client.get("/api/sources/profile", params={"table": "fake.upload_my_sales"}).json()
    assert profile["suggested"]["date_column"] == "month"
    assert profile["suggested"]["target_column"] == "units_sold"
    assert profile["frequency"] == "monthly"
    # The same file again gets its own table: an upload never replaces one.
    again = client.post("/api/uploads", files={"file": ("My Sales.csv", text, "text/csv")})
    assert again.json()["table"] == "fake.upload_my_sales_2"
    broken = client.post(
        "/api/uploads", files={"file": ("x.csv", "just one column\n1\n", "text/csv")}
    )
    assert broken.status_code == 400


def test_unknown_addresses(client):
    assert client.get("/api/nothing").status_code == 404
    assert client.get("/api/jobs/unknown").status_code == 404
    page = client.get("/datasets/anything")
    assert page.status_code == 503
    assert "front end is not built" in page.text


def with_dollars() -> pd.DataFrame:
    """The sales table with a second number column: a bottle costs 10 dollars."""
    frame = sales()
    frame["dollars"] = frame["bottles"] * 10
    return frame


def test_a_forecast_chooses_which_number_to_forecast(tmp_path):
    store = FakeStore()
    store.add_table("sales", with_dollars())
    workspace = Workspace(lambda: store, StandInForecaster())
    workspace.connect()
    client = TestClient(create_app(workspace, front_end=tmp_path))
    add_dataset(client)
    # The dataset's usual number comes first; the others can be chosen per forecast.
    assert client.get("/api/datasets/Sales").json()["number_columns"] == ["bottles", "dollars"]

    new_forecast(client, cutoffs=0)
    new_forecast(client, forecast_name="Revenue", target_column="dollars", cutoffs=2)
    targets = {record.forecast_name: record.target_column for record in store.saved_forecasts}
    assert targets == {"Monthly demand": "bottles", "Revenue": "dollars"}

    def last_actual(name: str) -> float:
        overview = client.get(f"/api/datasets/Sales/forecasts/{name}").json()
        chart = client.get(f"/api/forecasts/{overview['issues'][0]['forecast_id']}/chart").json()
        assert chart["target"] == ("dollars" if name == "Revenue" else "bottles")
        return [value for value in chart["actual"] if value is not None][-1]

    assert last_actual("Revenue") == pytest.approx(10 * last_actual("Monthly demand"))

    # A reissue and the accuracy check keep the forecast's own number.
    again = client.post(
        "/api/datasets/Sales/forecasts/Revenue/reissue", json={"as_of": "2024-12-01"}
    )
    assert finish(client, again)["state"] == "done"
    assert store.saved_forecasts[-1].target_column == "dollars"
    accuracy = client.get("/api/datasets/Sales/forecasts/Revenue/accuracy").json()
    assert accuracy["target"] == "dollars"

    # The history of the whole dataset, for either number.
    bottles = client.get("/api/datasets/Sales/history").json()
    dollars = client.get("/api/datasets/Sales/history?target=dollars").json()
    assert (bottles["target"], dollars["target"]) == ("bottles", "dollars")
    assert len(bottles["dates"]) == 40
    assert bottles["dates"][0] == "2022-01-01"
    assert bottles["values"][0] == 2 * (100 + 200 + 300)
    assert dollars["values"] == [10 * value for value in bottles["values"]]

    refused = client.post(
        "/api/datasets/Sales/slice",
        json={"dimension_columns": ["sku"], "target_column": "county"},
    )
    assert refused.status_code == 400
    assert "not a number column" in refused.json()["detail"]


def test_new_rows_in_the_history_table_are_noticed(client, store, monkeypatch):
    monkeypatch.setattr("webapp.workspace.DATES_FRESH_SECONDS", 0)
    add_dataset(client)
    assert client.get("/api/datasets/Sales").json()["dates"][-1] == "2025-04-01"
    new_forecast(client, cutoffs=0)
    # A month of new sales lands in the table.
    more = sales()[lambda frame: frame["month"] == MONTHS[-1].date()].assign(
        month=dt.date(2025, 5, 1)
    )
    store.frames["fake.sales"] = pd.concat([store.frames["fake.sales"], more], ignore_index=True)
    assert client.get("/api/datasets/Sales").json()["dates"][-1] == "2025-05-01"
    overview = client.get("/api/datasets/Sales/forecasts/Monthly demand").json()
    assert overview["reissue_dates"][-1] == "2025-05-01"
    job = finish(
        client, client.post("/api/datasets/Sales/forecasts/Monthly demand/reissue", json={})
    )
    assert job["state"] == "done", job
    assert store.saved_forecasts[-1].history_end_date == dt.date(2025, 5, 1)


def test_a_forecast_chooses_its_model_and_a_reissue_keeps_it(store, tmp_path):
    small, large = StandInForecaster(), StandInForecaster()
    small.model_name, large.model_name = "mistralai/Timestral-28M", "mistralai/Timestral-209M"
    choices = [
        ModelChoice(key="timestral", forecaster=large, default=True),
        ModelChoice(key="timestral-small", forecaster=small),
    ]
    workspace = Workspace(lambda: store, large, lambda: choices)
    workspace.connect()
    client = TestClient(create_app(workspace, front_end=tmp_path))
    add_dataset(client)
    assert client.get("/api/models").json() == [
        {"key": "timestral", "model_name": "mistralai/Timestral-209M", "default": True},
        {"key": "timestral-small", "model_name": "mistralai/Timestral-28M", "default": False},
    ]
    new_forecast(client, cutoffs=0, history_end="2024-10-01")
    new_forecast(
        client, forecast_name="Small", model="timestral-small", cutoffs=1, history_end="2024-10-01"
    )
    used = {record.forecast_name: record.model_name for record in store.saved_forecasts}
    assert used == {
        "Monthly demand": "mistralai/Timestral-209M",
        "Small": "mistralai/Timestral-28M",
    }
    again = client.post("/api/datasets/Sales/forecasts/Small/reissue", json={})
    assert finish(client, again)["state"] == "done"
    assert store.saved_forecasts[-1].model_name == "mistralai/Timestral-28M"

    refused = client.post(
        "/api/datasets/Sales/forecasts",
        json={"forecast_name": "Other", "dimension_columns": ["sku"], "model": "prophet"},
    )
    assert refused.status_code == 400
    assert "no model named prophet" in refused.json()["detail"]


def test_workbench_models_become_choices():
    class Model:
        def __init__(self, name, access_key, description):
            self.name, self.access_key, self.description = name, access_key, description

    settings = Settings.from_environment(
        {"U4CAST_MODEL_ACCESS_KEY": "key-1", "CDSW_DOMAIN": "example.com", "CDSW_APIV2_KEY": "k"}
    )
    found = workbench_choices(
        settings,
        [
            Model(
                "timestral-small", "key-2", "Timestral forecasts for u4cast. Serves a/Small-28M."
            ),
            Model("churn", "key-3", "Something else entirely."),
            Model("timestral", "key-1", "An older description."),
        ],
    )
    assert [(c.key, c.model_name, c.default) for c in found] == [
        ("timestral", "mistralai/Timestral-209M", True),
        ("timestral-small", "a/Small-28M", False),
    ]
    assert served_name("nothing here") is None


def test_a_forecast_is_drilled_into_by_its_dimensions(client):
    add_dataset(client)
    new_forecast(client, cutoffs=0)
    overview = client.get("/api/datasets/Sales/forecasts/Monthly demand").json()
    address = f"/api/forecasts/{overview['issues'][0]['forecast_id']}"

    everything = client.get(f"{address}/drill").json()
    assert everything["dimensions"] == ["county", "sku"]
    assert everything["options"] == {"county": COUNTIES, "sku": SKUS}
    assert everything["series"] == 6

    # One county: its three SKUs added together.
    polk = client.get(f"{address}/chart", params={"drill": '{"county": "POLK"}'}).json()
    total = client.get(f"{address}/chart").json()
    assert polk["series"] == 3
    assert polk["label"].startswith("POLK")
    assert polk["actual"][0] == 100 + 200 + 300
    assert polk["forecast"][0] == pytest.approx(total["forecast"][0] / 2)

    # Every dimension chosen: one series, with its own range.
    one = client.get(
        f"{address}/chart", params={"drill": '{"county": "POLK", "sku": "RUM"}'}
    ).json()
    assert (one["series"], one["actual"][0]) == (1, 200.0)
    assert one["ts_id"] == "county=POLK|sku=RUM"

    nothing = client.get(f"{address}/chart", params={"drill": '{"county": "NOWHERE"}'})
    assert nothing.status_code == 400
    wrong = client.get(f"{address}/chart", params={"drill": '{"store": "1"}'})
    assert wrong.status_code == 400


class FakeCatalog:
    """Two connections that look at the same two databases."""

    def __init__(self) -> None:
        self.asked = 0

    def connections(self):
        return [Connection("lake", "SPARK"), Connection("warehouse", "IMPALA")]

    def databases(self, connection):
        self.asked += 1
        if connection not in ("lake", "warehouse"):
            raise ValueError(f"This project has no data connection named {connection}.")
        return ["fake", "retail"]

    def tables(self, connection, database):
        checked(database)
        return {"fake": ["datasets", "forecast_values_sales", "forecasts", "sales"]}.get(
            database, ["orders"]
        )


def test_a_table_is_browsed_to_through_a_data_connection(store, tmp_path):
    catalog = FakeCatalog()
    workspace = Workspace(lambda: store, StandInForecaster(), None, lambda _: catalog)
    workspace.connect()
    client = TestClient(create_app(workspace, front_end=tmp_path))
    add_dataset(client)

    start = client.get("/api/connections").json()
    assert start["database"] == "fake"
    assert start["connections"] == [
        {"name": "lake", "type": "SPARK", "kind": "Data lake, with Spark"},
        {"name": "warehouse", "type": "IMPALA", "kind": "Impala virtual warehouse"},
    ]
    assert client.get("/api/connections/warehouse/databases").json() == ["fake", "retail"]
    assert client.get("/api/connections/warehouse/databases").json() == ["fake", "retail"]
    assert catalog.asked == 1  # the second answer was kept

    # The application's own tables are left out, and a table in use says by which dataset.
    mine = client.get("/api/connections/lake/databases/fake/tables").json()
    assert mine == [{"table": "fake.sales", "name": "sales", "dataset": "Sales"}]
    other = client.get("/api/connections/lake/databases/retail/tables").json()
    assert other == [{"table": "retail.orders", "name": "orders", "dataset": None}]

    assert client.get("/api/connections/nowhere/databases").status_code == 400
    assert client.get("/api/connections/lake/databases/a;drop/tables").status_code == 400


def test_without_a_catalog_there_is_nothing_to_browse(client):
    assert client.get("/api/connections").json() == {"connections": [], "database": "fake"}


def test_only_a_deployed_workbench_model_counts():
    class Listing:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    class Client:
        def list_model_builds(self, project_id, model_id, page_size):
            return Listing(model_builds=[Listing(id="b1"), Listing(id="b2")])

        def list_model_deployments(self, project_id, model_id, build_id, page_size):
            states = {"running": {"b2": ["deployed"]}, "stopped": {"b1": ["stopped"]}}
            found = states[model_id].get(build_id, [])
            return Listing(model_deployments=[Listing(status=status) for status in found])

    assert is_deployed(Client(), "p", "running")
    assert not is_deployed(Client(), "p", "stopped")
