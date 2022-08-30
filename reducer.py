#!/usr/bin/env python3

import json
import logging
import os
import random
import shutil
import subprocess
import tarfile
import tempfile
import time
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Optional

from ccbuilder import Builder, BuildException, PatchDB, Repo

import preprocessing
import utils


# ==================== Reducer ====================
class TempDirEnv:
    def __init__(self) -> None:
        self.td: tempfile.TemporaryDirectory[str]

    def __enter__(self) -> Path:
        self.td = tempfile.TemporaryDirectory()
        tempfile.tempdir = self.td.name
        return Path(self.td.name)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_traceback: Optional[TracebackType],
    ) -> None:
        tempfile.tempdir = None


@dataclass
class Reducer:
    config: utils.NestedNamespace
    bldr: Builder

    def reduce_file(self, file: Path, force: bool = False) -> bool:
        """Reduce a case given in the .tar format.
        Interface for `reduced_code`.

        Args:
            file (Path): Path to .tar case.
            force (bool): Force a reduction (even if the case is already reduced).
        Returns:
            bool: If the reduction was successful.
        """
        case = utils.Case.from_file(self.config, file)

        if self.reduce_case(case, force=force):
            case.to_file(file)
            return True
        return False

    def reduce_case(self, case: utils.Case, force: bool = False) -> bool:
        """Reduce a case.

        Args:
            case (utils.Case): Case to reduce.
            force (bool): Force a reduction (even if the case is already reduced).

        Returns:
            bool: If the reduction was successful.
        """
        if not force and case.reduced_code:

            return True

        case.reduced_code = self.reduce_code(
            case.code, case.marker, case.bad_setting, case.good_settings, case.bisection
        )
        return bool(case.reduced_code)

    def reduce_code(
        self,
        code: str,
        marker: str,
        bad_setting: utils.CompilerSetting,
        good_settings: list[utils.CompilerSetting],
        bisection: Optional[str] = None,
        preprocess: bool = True,
    ) -> Optional[str]:
        """Reduce given code w.r.t. `marker`

        Args:
            code (str):
            marker (str): Marker which exhibits the interesting behaviour.
            bad_setting (utils.CompilerSetting): Setting which can not eliminate the marker.
            good_settings (list[utils.CompilerSetting]): Settings which can eliminate the marker.
            bisection (Optional[str]): if present the reducer will also check for the bisection
            preprocess (bool): Whether or not to run the code through preprocessing.

        Returns:
            Optional[str]: Reduced code, if successful.
        """

        bad_settings = [bad_setting]
        if bisection:
            bad_settings.append(copy(bad_setting))
            bad_settings[-1].rev = bisection
            repo = bad_setting.repo
            good_settings = good_settings + [copy(bad_setting)]
            good_settings[-1].rev = repo.rev_to_commit(f"{bisection}~")

        # creduce likes to kill unfinished processes with SIGKILL
        # so they can't clean up after themselves.
        # Setting a temporary temporary directory for creduce to be able to clean
        # up everything
        with TempDirEnv() as tmpdir:

            # preprocess file
            if preprocess:
                tmp = preprocessing.preprocess_csmith_code(
                    code,
                    utils.get_marker_prefix(marker),
                    bad_setting,
                    self.bldr,
                )
                # Preprocesssing may fail
                pp_code = tmp if tmp else code

            else:
                pp_code = code

            pp_code_path = tmpdir / "code_pp.c"
            with open(pp_code_path, "w") as f:
                f.write(pp_code)

            # save interesting_settings
            settings_path = tmpdir / "interesting_settings.json"

            int_settings: dict[str, Any] = {}
            int_settings["bad_settings"] = [
                bs.to_jsonable_dict() for bs in bad_settings
            ]
            int_settings["good_settings"] = [
                gs.to_jsonable_dict() for gs in good_settings
            ]
            with open(settings_path, "w") as f:
                json.dump(int_settings, f)

            # create script for creduce
            script_path = tmpdir / "check.sh"
            with open(script_path, "w") as f:
                print("#/bin/sh", file=f)
                print("TMPD=$(mktemp -d)", file=f)
                print("trap '{ rm -rf \"$TMPD\"; }' INT TERM EXIT", file=f)
                print(
                    "timeout 15 "
                    f"{Path(__file__).parent.resolve()}/checker.py"
                    f" --dont-preprocess"
                    f" --config {self.config.config_path}"
                    f" --marker {marker}"
                    f" --interesting-settings {str(settings_path)}"
                    f" --file code_pp.c",
                    # f' --file {str(pp_code_path)}',
                    file=f,
                )

            os.chmod(script_path, 0o777)
            # run creduce
            creduce_cmd = [
                self.config.creduce,
                "--n",
                f"{self.bldr.jobs}",
                str(script_path.name),
                str(pp_code_path.name),
            ]

            try:
                current_time = time.strftime("%Y%m%d-%H%M%S")
                build_log_path = (
                    Path(self.config.logdir)
                    / f"{current_time}-creduce-{random.randint(0,1000)}.log"
                )
                build_log_path.touch()
                # Set permissions of logfile
                shutil.chown(build_log_path, group=self.config.cache_group)
                os.chmod(build_log_path, 0o660)
                logging.info(f"creduce logfile at {build_log_path}")
                with open(build_log_path, "a") as build_log:
                    utils.run_cmd_to_logfile(
                        creduce_cmd, log_file=build_log, working_dir=Path(tmpdir)
                    )
            except subprocess.CalledProcessError as e:
                logging.info(f"Failed to process code. Exception: {e}")
                return None

            # save result in tar
            with open(pp_code_path, "r") as f:
                reduced_code = f.read()

            return reduced_code
