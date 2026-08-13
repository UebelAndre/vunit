# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Simulator interface(s)
"""

from __future__ import annotations

import argparse
import sys
import os
from abc import ABC, abstractmethod
from os import environ, listdir, pathsep
import locale
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from ..ostools import Process, simplify_path
from ..exceptions import CompileError
from ..color_printer import NO_COLOR_PRINTER, ColorPrinter


class Option(object):
    """
    A compile or sim option
    """

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def validate(self, value: Any) -> None:
        pass


def add_extension(executable_name: str) -> str:
    """
    Add .exe extension on Windows platforms if not already present
    """
    ext = Path(executable_name).suffix

    if (sys.platform == "win32" or os.name == "os2") and (ext != ".exe"):
        executable_name = executable_name + ".exe"

    return executable_name


class SimulatorInterface(ABC):  # pylint: disable=too-many-public-methods
    """
    Generic simulator interface.

    Concrete simulators must implement :meth:`from_args`, :meth:`simulate`, and
    :meth:`compile_source_file_command`; other methods provide sensible defaults.
    """

    name: str = "none"
    supports_gui_flag: bool = False
    package_users_depend_on_bodies: bool = False
    compile_options: list[Option] = []
    sim_options: list[Option] = []

    # True if simulator supports ANSI colors in GUI mode
    supports_colors_in_gui: bool = False

    def __init__(self, output_path: str, gui: bool) -> None:
        self._output_path = output_path
        self._gui = gui

    @property
    def output_path(self) -> str:
        return self._output_path

    @property
    def use_color(self) -> bool:
        return (not self._gui) or self.supports_colors_in_gui

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        """
        Add command line arguments
        """

    @staticmethod
    def supports_vhdl_contexts() -> bool:
        """
        Returns True when this simulator supports VHDL contexts
        """
        return True

    @classmethod
    def supports_vhdl_call_paths(cls) -> bool:
        """
        Returns True when this simulator supports VHDL-2019 call paths
        """
        return False

    @staticmethod
    def find_executable(executable: str) -> list[str]:
        """
        Return a list of all executables found in PATH
        """
        path = environ.get("PATH", None)
        if path is None:
            return []

        paths = path.split(pathsep)
        executable = add_extension(executable)

        result: list[str] = []
        if isfile(executable):
            result.append(executable)

        for prefix in paths:
            file_name = str(Path(prefix) / executable)
            if isfile(file_name):
                # the file exists, we have a shot at spawn working
                result.append(file_name)
        return result

    @classmethod
    def find_prefix(cls) -> str | None:
        """
        Find prefix by looking at VUNIT_<SIMULATOR_NAME>_PATH environment variable
        """
        prefix = os.environ.get("VUNIT_" + cls.name.upper() + "_PATH", None)
        if prefix is not None:
            return prefix
        return cls.find_prefix_from_path()

    @classmethod
    def find_prefix_from_path(cls) -> str | None:
        """
        Find simulator toolchain prefix from PATH environment variable
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """
        Returns True if simulator is available
        """
        return cls.find_prefix() is not None

    @classmethod
    def find_toolchain(
        cls,
        executables: Sequence[str],
        constraints: list[Callable[[str], bool]] | None = None,
    ) -> str | None:
        """
        Find the first path prefix containing all executables
        """
        constraints = [] if constraints is None else constraints

        if not executables:
            return None

        all_paths = [
            [str(Path(executables).parent.resolve()) for executables in cls.find_executable(name)]
            for name in executables
        ]

        for path0 in all_paths[0]:
            if all([path0 in paths for paths in all_paths] + [constraint(path0) for constraint in constraints]):
                return path0
        return None

    @classmethod
    def get_osvvm_coverage_api(cls) -> str | None:
        """
        Returns simulator name when OSVVM coverage API is supported, None otherwise.
        """
        return None

    @classmethod
    def supports_vhdl_package_generics(cls) -> bool:
        """
        Returns True when this simulator supports VHDL package generics
        """
        return False

    def has_valid_exit_code(self) -> bool:
        """
        Return if the simulation should fail with nonzero exit codes
        """
        return False

    @classmethod
    @abstractmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        output_path: str,
        **kwargs: Any,
    ) -> "SimulatorInterface":
        """
        Create a simulator instance from parsed CLI arguments.

        Concrete simulator subclasses override this to consume their own flags.
        """

    @staticmethod
    def supports_vhpi() -> bool:
        """
        Returns True when the simulator supports VHPI
        """
        return False

    @staticmethod
    def supports_coverage() -> bool:
        """
        Returns True when the simulator supports coverage
        """
        return False

    def merge_coverage(self, file_name: str, args: list[str] | None) -> None:
        """
        Hook for simulator interface to creating coverage reports
        """
        del file_name, args  # unused in base; overridden by simulators that support coverage
        raise RuntimeError("This simulator does not support merging coverage")

    def add_simulator_specific(self, project: Any) -> None:
        """
        Hook for the simulator interface to add simulator specific things to the project
        """

    def compile_project(
        self,
        project: Any,
        printer: ColorPrinter = NO_COLOR_PRINTER,
        continue_on_error: bool = False,
        target_files: list[Any] | None = None,
    ) -> None:
        """
        Compile the project
        param: target_files: Given a list of SourceFiles only these and dependent files are compiled
        """
        self.add_simulator_specific(project)
        self.setup_library_mapping(project)
        self.compile_source_files(project, printer, continue_on_error, target_files=target_files)

    @abstractmethod
    def simulate(
        self,
        output_path: str,
        test_suite_name: str,
        config: Any,
        elaborate_only: bool,
    ) -> bool:
        """
        Run one test suite. Concrete simulators override this.
        """

    def setup_library_mapping(self, project: Any) -> None:
        """
        Set up the simulator library mapping for the project.

        Default: no-op. Override in simulators that need per-project library setup.
        """
        del project

    def _compile_source_file(self, source_file: Any, printer: ColorPrinter) -> bool:
        """
        Compiles a single source file and prints status information
        """
        try:
            command = self.compile_source_file_command(source_file)
        except CompileError:
            printer.write("failed", fg="ri")
            printer.write("\n")
            printer.write(f"File type not supported by {self.name!s} simulator\n")

            return False

        try:
            output = check_output(command, env=self.get_env())
            printer.write("passed", fg="gi")
            printer.write("\n")
            printer.write(output)

        except subprocess.CalledProcessError as err:
            printer.write("failed", fg="ri")
            printer.write("\n")
            printer.write(f"=== Command used: ===\n{subprocess.list2cmdline(command)!s}\n")
            printer.write("\n")
            printer.write(f"=== Command output: ===\n{err.output!s}\n")

            return False

        return True

    def compile_source_files(
        self,
        project: Any,
        printer: ColorPrinter = NO_COLOR_PRINTER,
        continue_on_error: bool = False,
        target_files: list[Any] | None = None,
    ) -> None:
        """
        Use compile_source_file_command to compile all source_files
        param: target_files: Given a list of SourceFiles only these and dependent files are compiled
        """
        dependency_graph = project.create_dependency_graph()
        failures = []

        if target_files is None:
            source_files = project.get_files_in_compile_order(dependency_graph=dependency_graph)
        else:
            source_files = project.get_minimal_file_set_in_compile_order(target_files)

        source_files_to_skip = set()

        max_library_name = 0
        max_source_file_name = 0
        if source_files:
            max_library_name = max(len(source_file.library.name) for source_file in source_files)
            max_source_file_name = max(len(simplify_path(source_file.name)) for source_file in source_files)

        for source_file in source_files:
            printer.write(
                f"Compiling into {(source_file.library.name + ':').ljust(max_library_name + 1)!s} "
                f"{simplify_path(source_file.name).ljust(max_source_file_name)!s} "
            )
            sys.stdout.flush()

            if source_file in source_files_to_skip:
                printer.write("skipped", fg="rgi")
                printer.write("\n")
                continue

            if self._compile_source_file(source_file, printer):
                project.update(source_file)
            else:
                source_files_to_skip.update(dependency_graph.get_dependent([source_file]))
                failures.append(source_file)

                if not continue_on_error:
                    break

        if failures:
            printer.write("Compile failed\n", fg="ri")
            if continue_on_error:
                return
            raise CompileError

        if source_files:
            printer.write("Compile passed\n", fg="gi")
        else:
            printer.write("Re-compile not needed\n")

    @abstractmethod
    def compile_source_file_command(self, source_file: Any) -> list[str]:
        """Return the command line used to compile a single source file. Concrete simulators override this."""

    @staticmethod
    def get_env() -> Mapping[str, str] | None:
        """
        Allows inheriting classes to overload this to modify environment variables. Return None for default environment
        """
        return None


def isfile(file_name: str) -> bool:
    """
    Case insensitive Path.is_file()
    """
    fpath = Path(file_name)
    try:
        if not fpath.is_file():
            return False
    except PermissionError:
        return False

    return str(fpath.name) in listdir(str(fpath.parent))


def run_command(
    command: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """
    Run a command
    """
    try:
        proc = Process(command, cwd=cwd, env=env)
        proc.consume_output()
        return True
    except Process.NonZeroExitCode:
        pass
    return False


def check_output(command: Sequence[str], env: Mapping[str, str] | None = None) -> str:
    """
    Wrapper arround subprocess.check_output
    """
    def _decode(data: bytes) -> str:
        """Decode tool output robustly across platforms.

        Some simulators on Windows emit output in a legacy code page (e.g. cp1252),
        which can raise UnicodeDecodeError if decoded as strict UTF-8.
        """

        # Deduplicate via dict.fromkeys to avoid trying utf-8 twice
        # when locale.getpreferredencoding() returns "UTF-8".
        encodings_to_try = dict.fromkeys([
            "utf-8",
            locale.getpreferredencoding(False),
        ])

        for encoding in encodings_to_try:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue

        return data.decode("utf-8", errors="backslashreplace")

    try:
        output = subprocess.check_output(  # pylint: disable=unexpected-keyword-arg
            command, env=env, stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as err:
        err.output = _decode(err.output)
        raise err
    return _decode(output)


def check_executable(simulator_name: str, prefix: str | None, executable_name: str) -> None:
    """
    Check that the executable exists.

    This helps to identify the case where the environment variable named after the simulator is used for
    other purposes than naming the executable. Issue #1149.
    """
    env_name = environ.get(simulator_name)

    # If environment variable isn't used for executable naming and VUNIT_<SIMULATOR_NAME>_PATH isn't set, the prefix
    # will be invalid since the toolchain search didn't find anything.
    if prefix is None:
        error_message = f"{simulator_name} executable not found."
        if env_name is not None:
            error_message += (
                f" The {simulator_name} environment variable, if set, must be the executable name."
                f" Current value is {env_name}."
            )

        raise FileNotFoundError(error_message)

    prefix_path = Path(prefix)

    executable_name = add_extension(executable_name)

    executable_path = prefix_path / executable_name
    if not executable_path.is_file():
        error_message = f"{simulator_name} executable not found at {executable_path}."
        if env_name is not None:
            error_message += (
                f" The {simulator_name} environment variable, if set, must be the executable name."
                f" Current value is {env_name}."
            )

        raise FileNotFoundError(error_message)


class BooleanOption(Option):
    """
    Must be a boolean
    """

    def validate(self, value: Any) -> None:
        if value not in (True, False):
            raise ValueError(f"Option {self.name!r} must be a boolean. Got {value!r}")


class StringOption(Option):
    """
    Must be a string
    """

    def validate(self, value: Any) -> None:
        if not is_string_not_iterable(value):
            raise ValueError(f"Option {self.name!r} must be a string. Got {value!r}")


class ListOfStringOption(Option):
    """
    Must be a list of strings
    """

    def validate(self, value: Any) -> None:
        def fail() -> None:
            raise ValueError(f"Option {self.name!r} must be a list of strings. Got {value!r}")

        if is_string_not_iterable(value):
            fail()

        try:
            for elem in value:
                if not is_string_not_iterable(elem):
                    fail()
        except TypeError:
            fail()


class VHDLAssertLevelOption(Option):
    """
    VHDL assert level
    """

    _legal_values = ("warning", "error", "failure")

    def __init__(self) -> None:
        Option.__init__(self, "vhdl_assert_stop_level")

    def validate(self, value: Any) -> None:
        if value not in self._legal_values:
            raise ValueError(f"Option {self.name!r} must be one of {self._legal_values!s}. Got {value!r}")


def is_string_not_iterable(value: Any) -> bool:
    """
    Returns True if value is a string and not another iterable
    """
    return isinstance(value, str)
