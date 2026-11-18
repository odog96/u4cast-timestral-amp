"""How Spark is set up. These tests need no Spark."""

import pytest

from u4cast.storage import spark_settings


def test_spark_runs_inside_the_container_by_default():
    settings = spark_settings({"CDSW_CPU_MILLICORES": "3970"})
    assert settings == {
        "spark.driver.memory": "2g",
        "spark.master": "local[4]",
        "spark.sql.shuffle.partitions": "16",
    }
    assert spark_settings({})["spark.master"] == "local[2]"


def test_spark_on_the_cluster_is_capped():
    settings = spark_settings({"U4CAST_SPARK_MODE": "cluster"})
    assert settings == {
        "spark.driver.memory": "2g",
        "spark.executor.memory": "2g",
        "spark.dynamicAllocation.maxExecutors": "8",
    }
    changed = spark_settings(
        {
            "U4CAST_SPARK_MODE": "cluster",
            "U4CAST_SPARK_MAX_EXECUTORS": "3",
            "U4CAST_SPARK_DRIVER_MEMORY": "4g",
        }
    )
    assert changed["spark.dynamicAllocation.maxExecutors"] == "3"
    assert changed["spark.driver.memory"] == "4g"


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="U4CAST_SPARK_MODE must be local or cluster"):
        spark_settings({"U4CAST_SPARK_MODE": "yarn"})
