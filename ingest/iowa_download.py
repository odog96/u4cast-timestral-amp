"""Download the demo data: Iowa liquor sales, one file per year.

The State of Iowa publishes each year as a zip archive of CSV parts. This
module downloads the archives and unpacks the parts into a folder. It needs
nothing but Python, so it can run before Spark is started.

Source: Iowa Department of Revenue, Alcoholic Beverages Division, published
on the Iowa Data Hub under CC BY 4.0.
"""

from __future__ import annotations

import logging
import shutil
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

ADDRESS = "https://idh-be.iowa.gov/api/v1/datasets/{dataset_id}/rows.csv"
# The Iowa Data Hub's dataset id of each year.
DATASET_IDS: dict[int, int] = {2022: 1259, 2023: 1260, 2024: 1261, 2025: 1262, 2026: 1263}


def download_year(year: int, folder: Path) -> list[Path]:
    """Download one year and unpack its CSV parts into `folder`. Return the parts.

    A year that is already unpacked is not downloaded again.
    """
    folder.mkdir(parents=True, exist_ok=True)
    done = folder / f"{year}.done"
    if done.exists():
        parts = sorted(folder.glob(f"iowa_liquor_sales_{year}_*.csv"))
        logger.info("%d: already here, %d parts", year, len(parts))
        return parts

    archive = folder / f"{year}.zip"
    address = ADDRESS.format(dataset_id=DATASET_IDS[year])
    logger.info("%d: downloading %s", year, address)
    request = urllib.request.Request(address, headers={"User-Agent": "python-urllib"})
    with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out, length=8 * 1024 * 1024)

    parts = []
    with zipfile.ZipFile(archive) as unpacked:
        for name in unpacked.namelist():
            if name.endswith(".csv"):
                parts.append(Path(unpacked.extract(name, folder)))
    archive.unlink()
    done.touch()
    size = sum(part.stat().st_size for part in parts)
    logger.info("%d: %d parts, %.2f GB", year, len(parts), size / 1e9)
    return sorted(parts)


def download(years: list[int], folder: Path) -> list[Path]:
    """Download the given years into `folder` and return all CSV parts."""
    unknown = [year for year in years if year not in DATASET_IDS]
    if unknown:
        raise ValueError(
            f"No Iowa dataset is known for {unknown}; known years: {sorted(DATASET_IDS)}."
        )
    parts: list[Path] = []
    for year in years:
        parts.extend(download_year(year, folder))
    return parts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    download(sorted(DATASET_IDS), Path("data/iowa"))
