"""AMP step 4: ingest the demo data into Iceberg tables with Spark.

Downloads Iowa liquor sales for 2022 through 2026, about 2.8 GB, and writes
two clean tables. See `ingest/ingest_iowa.py`.

Source: Iowa Department of Revenue, Alcoholic Beverages Division, published
on the Iowa Data Hub under CC BY 4.0.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "amp"))

from steps import run_job_with_spark

run_job_with_spark("u4cast: ingest demo data", "ingest/run_as_job.py")
