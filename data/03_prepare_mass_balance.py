# Copyright (C) 2024 Andy Aschwanden
#
# This file is part of pism-ragis.
#
# PISM-RAGIS is free software; you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation; either version 3 of the License, or (at your option) any later
# version.
#
# PISM-RAGIS is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with PISM; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

# pylint: disable=consider-using-with
# import-untyped

"""
Prepare mass balance data from Mankoff et al. (2021).
"""
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from importlib.resources import files
from pathlib import Path

import cf_xarray.units  # pylint: disable=unused-import
import numpy as np
import pandas as pd
import pint_xarray  # pylint: disable=unused-import
import toml
import xarray as xr

from pism_ragis.datetools import decimal_year_to_datetime
from pism_ragis.download import download_earthaccess, download_netcdf, save_netcdf

xr.set_options(keep_attrs=True)


if __name__ == "__main__":
    __spec__ = None  # type: ignore

    # set up the option parser
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.description = "Prepare Mass Balance from Mankoff et al. (2021)."
    url = "https://dataverse.geus.dk/api/access/datafile/:persistentId?persistentId=doi:10.22008/FK2/OHI23Z/MRSBQR"
    options = parser.parse_args()
    p = Path("mass_balance")
    p.mkdir(parents=True, exist_ok=True)

    ragis_config_file = Path(
        str(files("pism_ragis.data").joinpath("ragis_config.toml"))
    )
    ragis_config = toml.load(ragis_config_file)

    basin_vars_dict = ragis_config["Mankoff"]["basin"]
    basin_vars = [v for v in basin_vars_dict.values() if not "uncertainty" in v]
    basin_uncertainty_vars = [v for v in basin_vars_dict.values() if "uncertainty" in v]

    gis_vars_dict = ragis_config["Mankoff"]["gis"]
    gis_vars = [v for v in gis_vars_dict.values() if not "uncertainty" in v]
    gis_uncertainty_vars = [v for v in gis_vars_dict.values() if "uncertainty" in v]

    ds = download_netcdf(url)
    for v in ["MB_err", "BMB_err", "MB_ROI", "MB_ROI_err", "BMB_ROI_err"]:
        ds[v].attrs["units"] = "Gt day-1"
    ds = ds.pint.quantify()

    comp = {"zlib": True, "complevel": 2}
    encoding = {var: comp for var in ds.data_vars}

    fn = "mankoff_greenland_mass_balance_clean.nc"
    p_fn = p / fn
    ds.pint.dequantify().to_netcdf(p_fn, encoding=encoding)

    gis = ds[list(gis_vars_dict.keys())]

    ds = ds.rename_vars(basin_vars_dict)[list(basin_vars_dict.values())]
    ds = ds.rename({"region": "basin"})

    gis = gis.rename_vars(gis_vars_dict)[list(gis_vars_dict.values())]
    gis = gis.expand_dims("basin")
    gis["basin"] = ["GIS"]

    ds = xr.concat([ds, gis], dim="basin")
    ds["basin"] = ds["basin"].astype("<U3")

    # The data is unevenly space in time. Until 1986, the interval is 1 year, and
    # from 1986 on, the interval is 1 day. We thus compute the amount of days in a given
    # interval.
    dt = (
        (ds.time.diff(dim="time", label="lower") / np.timedelta64(1, "s"))
        .pint.quantify("s")
        .pint.to("day")
    )
    for v in basin_vars + basin_uncertainty_vars:
        ds[f"cumulative_{v}"] = (ds[v].pint.to("Gt day^-1") * dt).cumsum(dim="time")
        ds[v] = ds[v].pint.to("Gt year^-1")
        ds[f"cumulative_{v}"] = ds[f"cumulative_{v}"].pint.to("Gt")

    # ds["cumulative_mass_balance_uncertainty"] = (((ds["grounding_line_flux_uncertainty"] ** 2 + ds["basal_mass_balance_uncertainty"] ** 2)**(1/2)).pint.to("Gt day^-1") * dt).cumsum(dim="time")

    # remove last time entry because it is NaN for cumulative uncertainties.
    ds = ds.isel(time=slice(0, -1))
    discharge_sign = xr.DataArray(-1).pint.quantify("1")
    ds["grounding_line_flux"] *= discharge_sign

    comp = {"zlib": True, "complevel": 2}
    encoding = {var: comp for var in ds.data_vars}

    fn = "mankoff_greenland_mass_balance.nc"
    p_fn = p / fn
    mankoff_ds = ds.pint.dequantify()
    save_netcdf(mankoff_ds, p_fn)

    short_name = "GREENLAND_MASS_TELLUS_MASCON_CRI_TIME_SERIES_RL06.1_V3"
    results = download_earthaccess(result_dir=p, short_name=short_name)
    grace_file = results[0]
    # Read the data into a pandas DataFrame
    df = pd.read_csv(
        grace_file,
        header=32,  # Skip the header lines
        sep="\s+",
        names=[
            "year",
            "cumulative_mass_balance",
            "mass_balance_uncertainty",
        ],
    )

    # Vectorize the function to apply it to the entire array
    vectorized_conversion = np.vectorize(decimal_year_to_datetime)

    # Print the result
    date = vectorized_conversion(df["year"])
    df["time"] = date

    ds = xr.Dataset.from_dataframe(df.set_index(df["time"]))
    ds["cumulative_mass_balance"].attrs.update({"units": "Gt"})
    ds["cumulative_mass_balance_uncertainty"] = np.sqrt(
        (ds["mass_balance_uncertainty"] ** 2).cumsum(dim="time")
    )
    ds["cumulative_mass_balance_uncertainty"].attrs.update({"units": "Gt"})

    ds = ds.pint.quantify()

    days_in_interval = (
        (ds.time.diff(dim="time") / np.timedelta64(1, "s"))
        .pint.quantify("s")
        .pint.to("year")
    )

    ds["mass_balance"] = (
        ds["cumulative_mass_balance"].diff(dim="time") / days_in_interval
    )
    ds = ds.expand_dims({"basin": ["GRACE"]})
    fn = "grace_greenland_mass_balance.nc"
    p_fn = p / fn
    grace_ds = ds.pint.dequantify()
    save_netcdf(grace_ds, p_fn)
