# Copyright (C) 2024 Andy Aschwanden, Constantine Khroulev
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

# pylint: disable=unused-import
"""
Analyze RAGIS ensemble.
"""

import time
import warnings
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Hashable, List, Mapping, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rioxarray as rxr
import seaborn as sns
import toml
import xarray as xr
import xskillscore
from dask.diagnostics import ProgressBar
from joblib import Parallel, delayed
from tqdm.auto import tqdm

from pism_ragis.filtering import importance_sampling
from pism_ragis.likelihood import log_normal, log_pseudo_huber
from pism_ragis.processing import load_ensemble, preprocess_nc

xr.set_options(keep_attrs=True)

plt.style.use("tableau-colorblind10")

sim_alpha = 0.5
sim_cmap = sns.color_palette("crest", n_colors=4).as_hex()[0:3:2]
sim_cmap = ["#a6cee3", "#1f78b4"]
sim_cmap = ["#CC6677", "#882255"]
obs_alpha = 1.0
obs_cmap = ["0.8", "0.7"]
# obs_cmap = ["#88CCEE", "#44AA99"]
hist_cmap = ["#a6cee3", "#1f78b4"]


if __name__ == "__main__":
    __spec__ = None  # type: ignore

    # set up the option parser
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    parser.description = "Compute ensemble statistics."
    parser.add_argument(
        "--result_dir",
        help="""Result directory.""",
        type=str,
        default="./results",
    )
    parser.add_argument(
        "--obs_url",
        help="""Path to "observed" mass balance.""",
        type=str,
        default="data/itslive/ITS_LIVE_GRE_G0240_2018.nc",
    )
    parser.add_argument(
        "--outlier_variable",
        help="""Quantity to filter outliers. Default="grounding_line_flux".""",
        type=str,
        default="grounding_line_flux",
    )
    parser.add_argument(
        "--fudge_factor",
        help="""Observational uncertainty multiplier. Default=3""",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--n_jobs", help="""Number of parallel jobs.""", type=int, default=4
    )
    parser.add_argument(
        "--notebook",
        help="""Use when running in a notebook to display a nicer progress bar. Default=False.""",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--parallel",
        help="""Open dataset in parallel. Default=False.""",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--resampling_frequency",
        help="""Resampling data to resampling_frequency for importance sampling. Default is "MS".""",
        type=str,
        default="MS",
    )
    parser.add_argument(
        "--reference_year",
        help="""Reference year.""",
        type=int,
        default=1986,
    )
    parser.add_argument(
        "--temporal_range",
        help="""Time slice to extract.""",
        type=str,
        nargs=2,
        default=None,
    )
    parser.add_argument(
        "FILES",
        help="""Ensemble netCDF files.""",
        nargs="*",
    )

    options = parser.parse_args()
    spatial_files = options.FILES
    fudge_factor = options.fudge_factor
    notebook = options.notebook
    parallel = options.parallel
    reference_year = options.reference_year
    resampling_frequency = options.resampling_frequency
    outlier_variable = options.outlier_variable
    ragis_config_file = Path(
        str(files("pism_ragis.data").joinpath("ragis_config.toml"))
    )
    ragis_config = toml.load(ragis_config_file)
    sampling_year = 2018

    ds = load_ensemble(spatial_files, preprocess=preprocess_nc, parallel=True)
    sim_ds = ds.sel({"time": str(sampling_year)}).mean(dim="time")

    observed = (
        xr.open_dataset(options.obs_url, chunks="auto")
        .sel({"time": str(sampling_year)})
        .mean(dim="time")
    )
    observed = observed.where(observed["ice"])

    obs_ds = observed.interp_like(sim_ds)

    sim_mask = sim_ds.isnull().sum(dim="exp_id") == 0
    sim_ds = sim_ds.where(sim_mask)
    # obs_ds = obs_ds.sel(
    #     {"x": slice(-225_000, -80_000), "y": slice(-2_350_000, -2_222_000)}
    # )
    # sim_ds = sim_ds.sel(
    #     {"x": slice(-225_000, -80_000), "y": slice(-2_350_000, -2_222_000)}
    # )
    x_min, y_min = -65517, -3317968
    x_max, y_max = 525929, -2528980
    obs_ds = obs_ds.sel({"x": slice(x_min, x_max), "y": slice(y_min, y_max)})
    sim_ds = sim_ds.sel({"x": slice(x_min, x_max), "y": slice(y_min, y_max)})

    print("Importance sampling using v")
    f = importance_sampling(
        observed=obs_ds,
        simulated=sim_ds,
        log_likelihood=log_normal,
        n_samples=len(ds.exp_id),
        fudge_factor=50,
        obs_mean_var="v",
        obs_std_var="v_err",
        sim_var="velsurf_mag",
        sum_dim=["x", "y"],
    )
    with ProgressBar():
        filtered_ids = f.compute()

    s = sim_ds["velsurf_mag"]
    o = obs_ds["v"]
    print(xskillscore.rmse(s, o, dim=["x", "y"], skipna=True).values)

    fig, axs = plt.subplots(1, 2, figsize=(12, 6))
    s.median(dim="exp_id").plot(ax=axs[0], vmin=0, vmax=500, label=False)
    o.plot(ax=axs[1], vmin=0, vmax=500)
    plt.show()

    observed = (
        xr.open_dataset(options.obs_url, chunks="auto")
        .sel({"time": str(sampling_year)})
        .mean(dim="time")
    )
