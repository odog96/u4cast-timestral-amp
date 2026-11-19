"""The data connections of a Cloudera AI project, and what can be reached through them.

A project has a data connection to the data lake itself, used with Spark, and
one to each virtual warehouse of Cloudera Data Warehouse, Hive or Impala. All
of them look at the same catalog. This module lists the connections, and
through one of them the databases and the tables of a database, so a user
can browse to a table instead of typing its name.

The application reads the table it is given with Spark, whichever connection
it was found through.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

SPARK = "SPARK"
WAREHOUSES = ("HIVE", "IMPALA")
KINDS = {
    SPARK: "Data lake, with Spark",
    "HIVE": "Hive virtual warehouse",
    "IMPALA": "Impala virtual warehouse",
}
HIDDEN_DATABASES = ("_impala_builtins", "information_schema", "sys")

_NAME = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Connection:
    """One data connection of the project."""

    name: str
    type: str

    @property
    def kind(self) -> str:
        return KINDS.get(self.type, self.type)


class Catalog(Protocol):
    """What the application needs to browse to a table."""

    def connections(self) -> list[Connection]: ...

    def databases(self, connection: str) -> list[str]: ...

    def tables(self, connection: str, database: str) -> list[str]: ...


def checked(name: str) -> str:
    """Return a database name that is safe to put in a statement, or refuse it."""
    if not _NAME.match(name):
        raise ValueError(f"{name} is not a database name.")
    return name


class ClouderaCatalog:
    """Browses through the project's own data connections.

    `spark_sql` runs a statement on the application's Spark session and
    returns its rows; it serves the data lake connection. A virtual warehouse
    is asked directly, through the connection Cloudera AI hands out.
    """

    def __init__(self, spark_sql: Any) -> None:
        self._spark_sql = spark_sql

    def connections(self) -> list[Connection]:
        import cml.data_v1 as cmldata

        found = [
            Connection(name=row["name"], type=row["type"])
            for row in cmldata.get_project_dataconnections()
            if row.get("type") in (SPARK, *WAREHOUSES)
        ]
        return sorted(found, key=lambda c: (c.type != SPARK, c.name.lower()))

    def _connection(self, name: str) -> Connection:
        for connection in self.connections():
            if connection.name == name:
                return connection
        raise ValueError(f"This project has no data connection named {name}.")

    def _ask(self, connection: Connection, statement: str) -> list[str]:
        """Run a `show` statement and return the first column of its rows."""
        if connection.type == SPARK:
            return [str(row[0]) for row in self._spark_sql(statement)]
        import cml.data_v1 as cmldata

        link = cmldata.get_connection(connection.name)
        try:
            cursor = link.get_cursor()
            cursor.execute(statement)
            return [str(row[0]) for row in cursor.fetchall()]
        finally:
            link.close()

    def databases(self, connection: str) -> list[str]:
        found = self._ask(self._connection(connection), "show databases")
        return sorted(name for name in found if name not in HIDDEN_DATABASES)

    def tables(self, connection: str, database: str) -> list[str]:
        chosen = self._connection(connection)
        statement = f"show tables in {checked(database)}"
        if chosen.type == SPARK:
            # Spark answers with (database, table, is temporary).
            return sorted(str(row[1]) for row in self._spark_sql(statement))
        return sorted(self._ask(chosen, statement))
