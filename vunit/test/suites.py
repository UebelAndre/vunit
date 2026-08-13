# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Contains different kinds of test suites
"""

from __future__ import annotations

from pathlib import Path
from time import time
from hashlib import blake2b
from threading import get_ident
from typing import TYPE_CHECKING, Any, Callable, Sequence
from .. import ostools
from .report import PASSED, SKIPPED, FAILED, TestStatus

if TYPE_CHECKING:
    from ..configuration import Configuration
    from ..sim_if import SimulatorInterface
    from .bench import Test


class IndependentSimTestCase(object):
    """
    A test case to be run in an independent simulation
    """

    @staticmethod
    def get_name(test: Test, config: Configuration) -> str:
        """Return full test case name based on library, testbench, configuration, and test names."""
        name = f"{config.library_name!s}.{config.design_unit_name!s}"

        if not config.is_default:
            if config.name is None:
                raise RuntimeError("Non-default configuration must have a name")
            name += "." + config.name

        if test.is_explicit:
            if test.name is None:
                raise RuntimeError("Explicit test must have a name")
            name += "." + test.name
        elif config.is_default:
            # JUnit XML test reports wants three dotted name hierarchies
            name += ".all"

        return name

    def __init__(
        self,
        test: Test,
        config: Configuration,
        simulator_if: SimulatorInterface | None,
        seed: str | None,
        *,
        elaborate_only: bool = False,
    ) -> None:
        self._name = self.get_name(test, config)

        self._configuration = config

        self._test = test

        self._run = TestRun(
            simulator_if=simulator_if,
            config=config,
            elaborate_only=elaborate_only,
            test_suite_name=self._name,
            test_cases=[test.name],
            seed=seed,
        )

    @property
    def file_name(self) -> str:
        return self._test.location.file_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def attribute_names(self) -> set[str]:
        return self._test.attribute_names

    @property
    def test_configuration(self) -> Configuration:
        return self._configuration

    @property
    def test_information(self) -> Test:
        """
        Returns the test information
        """
        return self._test

    def run(self, *args: Any, **kwargs: Any) -> bool:
        """
        Run the test case using the output_path
        """
        results = self._run.run(*args, **kwargs)

        return results[self._test.name] == PASSED

    def get_seed(self) -> str | None:
        return self._run.get_seed()


class SameSimTestSuite(object):
    """
    A test suite where multiple test cases are run within the same simulation
    """

    @staticmethod
    def get_name(config: Configuration) -> str:
        """Return full test suite name based on library, testbench, and configuration names."""
        name = f"{config.library_name!s}.{config.design_unit_name!s}"
        if not config.is_default:
            if config.name is None:
                raise RuntimeError("Non-default configuration must have a name")
            name += "." + config.name

        return name

    def __init__(
        self,
        tests: list[Test],
        config: Configuration,
        simulator_if: SimulatorInterface | None,
        seed: str | None,
        *,
        elaborate_only: bool = False,
    ) -> None:
        self._name = self.get_name(config)

        self._configuration = config

        self._tests = tests
        self._run = TestRun(
            simulator_if=simulator_if,
            config=config,
            elaborate_only=elaborate_only,
            test_suite_name=self._name,
            test_cases=[_require_test_name(test) for test in tests],
            seed=seed,
        )

    @property
    def file_name(self) -> str:
        return self._tests[0].location.file_name if self._tests else ""

    @property
    def test_names(self) -> list[str]:
        return [_full_name(self._name, _require_test_name(test)) for test in self._tests]

    @property
    def test_information(self) -> dict[str, Test]:
        """
        Returns a dictionary mapping full test name to test information object
        """
        return {_full_name(self._name, _require_test_name(test)): test for test in self._tests}

    @property
    def test_configuration(self) -> dict[str, Configuration]:
        """
        Returns a dictionary mapping full test name to the shared configuration used for the whole suite.
        """
        return {_full_name(self._name, _require_test_name(test)): self._configuration for test in self._tests}

    @property
    def name(self) -> str:
        return self._name

    def keep_matches(self, test_filter: Callable[..., bool]) -> bool:
        """
        Keep tests which pattern return False if no remaining tests
        """

        def _merge_attributes(attribute_names: set[str], attributes: dict[str, Any]) -> set[str]:
            merged_attributes = attribute_names.copy()
            merged_attributes.update(set(attributes.keys()))
            return merged_attributes

        self._tests = [
            test
            for test in self._tests
            if test_filter(
                name=_full_name(self.name, _require_test_name(test)),
                attribute_names=_merge_attributes(test.attribute_names, self._configuration.attributes),
            )
        ]
        self._run.set_test_cases([_require_test_name(test) for test in self._tests])
        return len(self._tests) > 0

    def run(self, *args: Any, **kwargs: Any) -> dict[str, TestStatus]:
        """
        Run the test suite using output_path
        """
        raw_results = self._run.run(*args, **kwargs)
        results: dict[str, TestStatus] = {}
        for test_name, result in raw_results.items():
            if test_name is None:
                raise RuntimeError("SameSimTestSuite should not contain implicit tests")
            results[_full_name(self._name, test_name)] = result

        return results

    def get_seed(self) -> str | None:
        return self._run.get_seed()


class TestRun(object):
    """
    A single simulation run yielding the results for one or several test cases
    """

    def __init__(
        self,
        *,
        simulator_if: SimulatorInterface | None,
        config: Configuration,
        elaborate_only: bool,
        test_suite_name: str,
        test_cases: Sequence[str | None],
        seed: str | None,
    ) -> None:  # pylint: disable=too-many-arguments
        self._simulator_if = simulator_if
        self._config = config
        self._elaborate_only = elaborate_only
        self._test_suite_name = test_suite_name
        self._test_cases: Sequence[str | None] = test_cases
        self._seed = seed

    def get_seed(self) -> str | None:
        """Return externally assigned seed or generate one from system time and thread identifier."""
        if self._seed:
            pass
        elif "seed" in self._config.sim_options:
            self._seed = self._config.sim_options["seed"]
        else:
            now_us = str(int(time() * 1e6)).encode()
            thread_id = get_ident().to_bytes(8, byteorder="little")
            self._seed = blake2b(now_us, digest_size=8, salt=thread_id).hexdigest()

        return self._seed

    def set_test_cases(self, test_cases: Sequence[str | None]) -> None:
        self._test_cases = test_cases

    def run(self, output_path: str, read_output: Callable[[], str]) -> dict[str | None, TestStatus]:
        """
        Run selected test cases within the test suite

        Returns a dictionary of test results
        """
        if self._simulator_if is None:
            raise RuntimeError("Cannot run test without a simulator interface")

        results: dict[str | None, TestStatus] = {}
        for name in self._test_cases:
            results[name] = FAILED

        seed = self.get_seed()
        if seed is None:
            raise RuntimeError("Failed to determine seed for test run")
        if not self._config.call_pre_config(output_path, self._simulator_if.output_path, seed):
            return results

        # Ensure result file exists
        ostools.write_file(get_result_file_name(output_path), "")

        sim_ok = self._simulate(output_path)

        if self._elaborate_only:
            status = PASSED if sim_ok else FAILED
            return dict((name, status) for name in self._test_cases)

        results = self._read_test_results(file_name=get_result_file_name(output_path))

        done, results = self._check_results(results, sim_ok)
        if done:
            return results

        if not self._config.call_post_check(output_path, read_output):
            for name in self._test_cases:
                results[name] = FAILED

        return results

    def _check_results(
        self, results: dict[str | None, TestStatus], sim_ok: bool
    ) -> tuple[bool, dict[str | None, TestStatus]]:
        """
        Test the results and the exit code; return True the status of any test is not PASSED
        """
        # If any test failed, return results
        for status in results.values():
            if status == FAILED:
                return True, results

        # Force fail if all tests pass in the presence of non-zero exit code
        if self._simulator_if is not None and self._simulator_if.has_valid_exit_code() and not sim_ok:
            return (
                True,
                dict((name, FAILED) if results[name] is PASSED else (name, results[name]) for name in results),
            )

        return False, results

    def _simulate(self, output_path: str) -> bool:
        """
        Add runner_cfg generic values and run simulation
        """

        if self._simulator_if is None:
            raise RuntimeError("Cannot simulate without a simulator interface")

        config = self._config.copy()
        seed = self.get_seed()

        print(f"Seed for {self._test_suite_name}: {seed}")

        if "output_path" in config.generic_names and "output_path" not in config.generics:
            config.generics["output_path"] = str(output_path.replace("\\", "/")) + "/"

        runner_cfg = {
            "enabled_test_cases": ",".join(
                encode_test_case(test_case) or "" for test_case in self._test_cases if test_case is not None
            ),
            "use_color": self._simulator_if.use_color,
            "output path": output_path.replace("\\", "/") + "/",
            "active python runner": True,
            "tb path": config.tb_path.replace("\\", "/") + "/",
            "seed": seed,
        }

        # @TODO Warn if runner cfg already set?
        config.generics["runner_cfg"] = encode_dict(runner_cfg)

        return bool(
            self._simulator_if.simulate(
                output_path=output_path,
                test_suite_name=self._test_suite_name,
                config=config,
                elaborate_only=self._elaborate_only,
            )
        )

    def _read_test_results(self, file_name: str) -> dict[str | None, TestStatus]:  # pylint: disable=too-many-branches
        """
        Read test results from vunit_results file
        """

        results: dict[str | None, TestStatus] = {}
        for name in self._test_cases:
            results[name] = FAILED

        if not ostools.file_exists(file_name):
            return results

        test_results = ostools.read_file(file_name)
        test_starts: list[str] = []
        test_suite_done = False

        for line in test_results.splitlines():
            if line.startswith("test_start:"):
                test_name = line[len("test_start:") :]
                if test_name not in test_starts:
                    test_starts.append(test_name)

            elif line.startswith("test_suite_done"):
                test_suite_done = True

        for idx, test_name in enumerate(test_starts):
            last_start = idx == len(test_starts) - 1

            if test_suite_done or not last_start:
                results[test_name] = PASSED

        for test_case_name in self._test_cases:
            # Anonymous test case
            if test_case_name is None:
                results[test_case_name] = PASSED if test_suite_done else FAILED
                continue

            if test_case_name not in test_starts:
                results[test_case_name] = SKIPPED

        for result_name in results:
            if result_name not in self._test_cases:
                raise RuntimeError(f"Got unknown test case {result_name!s}")

        return results


def encode_test_case(test_case: str | None) -> str | None:
    """
    Encode test case name to escape commas. Avoids that test case names including commas
    are interpreted as several test cases in the comma separated list of enabled test cases
    included in the runner_cfg string.
    """
    if test_case is not None:
        return test_case.replace(",", ",,")

    return None


def encode_dict(dictionary: dict[str, Any]) -> str:
    """
    Encode dictionary for custom VHDL dictionary parser
    """

    def escape(value: str) -> str:
        return value.replace(":", "::").replace(",", ",,")

    encoded = []
    for key in sorted(dictionary.keys()):
        value = dictionary[key]
        encoded.append(f"{escape(key)!s} : {escape(encode_dict_value(value))!s}")
    return ",".join(encoded)


def encode_dict_value(value: Any) -> str:  # pylint: disable=missing-docstring
    if isinstance(value, bool):
        return str(value).lower()

    return str(value)


def _full_name(test_suite_name: str, test_case_name: str) -> str:
    return test_suite_name + "." + test_case_name


def _require_test_name(test: Test) -> str:
    """Return the name of an explicit test or raise if it is missing."""
    if test.name is None:
        raise RuntimeError("Test must have a name")
    return test.name


def get_result_file_name(output_path: str) -> str:
    return str(Path(output_path) / "vunit_results")
