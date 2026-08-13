# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Contains classes to represent a test bench and test cases
"""

from __future__ import annotations

import logging
import re
import bisect
import collections
from collections import OrderedDict
from typing import Any, Callable, Iterable
from ..ostools import file_exists
from ..cached import cached
from ..vhdl_parser import remove_comments as remove_vhdl_comments
from ..parsing.encodings import HDL_FILE_ENCODING
from ..source_file import file_type_of, VERILOG_FILE_TYPES
from ..configuration import Configuration, ConfigurationVisitor, DEFAULT_NAME, ConfigurableDesignUnit
from .list import TestList
from .suites import IndependentSimTestCase, SameSimTestSuite

from ..database import PickledDataBase
from ..sim_if import SimulatorInterface

LOGGER = logging.getLogger(__name__)


class TestBench(ConfigurationVisitor):
    """
    A VUnit test bench top level
    """

    def __init__(self, design_unit: ConfigurableDesignUnit, database: PickledDataBase | None = None) -> None:
        ConfigurationVisitor.__init__(self, design_unit)
        self._database = database

        self._individual_tests: bool = False
        self._configs: dict[str | None, Configuration] = {}
        self._test_cases: list[TestConfigurationVisitor] = []
        self._implicit_test: Test | None = None

        if design_unit.is_entity:
            design_unit.set_add_architecture_callback(self._add_architecture_callback)
            if design_unit.architecture_names:
                self._add_architecture_callback()
        else:
            self.scan_tests_from_file(design_unit.file_name)

    def _add_architecture_callback(self) -> None:
        """
        Called when architectures have been added
        """
        self._check_architectures(self.design_unit)
        file_name = list(self.design_unit.architecture_names.values())[0]
        self.scan_tests_from_file(file_name)

    @property
    def name(self) -> str:
        return self.design_unit.name

    @property
    def library_name(self) -> str:
        return self.design_unit.library_name

    @property
    def tests(self) -> list[TestConfigurationVisitor]:
        return self._test_cases

    def get_default_config(self) -> Configuration:
        """
        Get the default configuration of this test bench
        """
        if self._individual_tests:
            raise RuntimeError(f"Test bench {self.library_name!s}.{self.name!s} has individually configured tests")
        return self._configs[DEFAULT_NAME]

    def create_tests(
        self,
        simulator_if: SimulatorInterface | None,
        seed: str | None,
        elaborate_only: bool,
        test_list: TestList | None = None,
    ) -> TestList:
        """
        Create all test cases from this test bench
        """

        self._check_architectures(self.design_unit)

        if test_list is None:
            test_list = TestList()

        key = b"test_history"
        if not self._database or key not in self._database:
            test_history = {}
        else:
            test_history = self._database[key]

        if self._individual_tests:
            for test_case in self._test_cases:
                test_case.create_tests(simulator_if, seed, elaborate_only, test_list, test_history)
        elif self._implicit_test:
            for config in self._get_configurations_to_run():
                actual_seed = seed
                if seed == "repeat":
                    # Test suite name and test name are the same for implicit tests
                    test_suite_name = test_name = IndependentSimTestCase.get_name(self._implicit_test, config)
                    actual_seed = _get_historic_seed(test_history, test_suite_name, test_name)

                test_list.add_test(
                    IndependentSimTestCase(
                        test=self._implicit_test,
                        config=config,
                        simulator_if=simulator_if,
                        seed=actual_seed,
                        elaborate_only=elaborate_only,
                    )
                )
        else:
            for config in self._get_configurations_to_run():
                actual_seed = seed
                if seed == "repeat":
                    test_suite_name = SameSimTestSuite.get_name(config)
                    actual_seed = _get_historic_seed(test_history, test_suite_name)

                test_list.add_suite(
                    SameSimTestSuite(
                        tests=[test.test for test in self._test_cases],
                        config=config,
                        simulator_if=simulator_if,
                        seed=actual_seed,
                        elaborate_only=elaborate_only,
                    )
                )
        return test_list

    @property
    def test_case_names(self) -> list[str | None]:
        return [test.name for test in self._test_cases]

    def get_test_case(self, name: str) -> TestConfigurationVisitor:
        """
        Return the test case with name or raise KeyError
        """
        for test_case in self._test_cases:
            if test_case.name == name:
                return test_case
        raise KeyError(name)

    def get_configuration_dicts(self) -> list[dict[str | None, Configuration]]:  # pylint: disable=arguments-differ
        """
        Get all configurations within the test bench

        If running all tests in the same simulation there are no individual test configurations
        """
        if self._individual_tests:
            configs: list[dict[str | None, Configuration]] = []
            for test_case in self._test_cases:
                configs += test_case.get_configuration_dicts()
            return configs

        return [self._configs]

    def _get_configurations_to_run(self) -> Iterable[Configuration]:
        """
        Get all simulation runs for this test bench
        """
        configs = self._configs.copy()
        if len(configs) > 1:
            # Remove default configurations when there are more than one
            del configs[DEFAULT_NAME]
        return configs.values()

    def scan_tests_from_file(self, file_name: str) -> None:
        """
        Scan file for test cases and attributes
        """
        if not file_exists(file_name):
            raise ValueError(f"File {file_name!r} does not exist")

        def parse(content: str) -> tuple[list[Test], list[LegacyAttribute | Attribute]]:
            """
            Parse attributes and test case names
            """
            tests, attributes = _find_tests_and_attributes(content, file_name)
            return tests, attributes

        tests, attributes = cached(
            "test_bench.parse",
            parse,
            file_name,
            encoding=HDL_FILE_ENCODING,
            database=self._database,
            newline="",
        )

        for attr in attributes:
            if _is_user_attribute(attr.name):
                raise RuntimeError(
                    f"File global attributes are not yet supported: {attr.name!s} in "
                    f"{file_name!s} line {attr.location.lineno:d}"
                )

        for test in tests:
            for attr in test.attributes:
                if attr.name in _VALID_ATTRIBUTES:
                    raise RuntimeError(
                        f"Attribute {attr.name!s} is global and cannot be associated with test {test.name!s}: "
                        f"{file_name!s} line {attr.location.lineno:d}"
                    )

        default_config = Configuration(DEFAULT_NAME, self.design_unit)
        self._configs = OrderedDict({default_config.name: default_config})

        explicit_tests = [test for test in tests if test.is_explicit]
        if explicit_tests:
            # All tests shall be explicit when there are at least one explicit test
            if len(tests) != len(explicit_tests):
                raise RuntimeError("Cannot mix explicit and implicit tests in the same test bench")
            self._implicit_test = None
        else:
            # There can only be one implicit test
            if len(tests) != 1:
                raise RuntimeError("Expected exactly one implicit test when no explicit tests are found")
            self._implicit_test = tests[0]

        self._individual_tests = len(explicit_tests) > 0
        self._test_cases = [
            TestConfigurationVisitor(test, self.design_unit, self._individual_tests, default_config.copy())
            for test in explicit_tests
        ]

        # This must be done after self._test_cases have been created such that run_all_in_same_sim can disable them
        for attr in attributes:
            self.set_attribute(attr.name, attr.value)

    def set_attribute(self, name: str, value: Any) -> None:
        """
        Set attributes except fail_on_warning and run_all_in_same_sim which have special meanings
        """
        if name == "fail_on_warning":
            self.set_sim_option("vhdl_assert_stop_level", "warning" if value or value is None else "error")
            return

        if name == "run_all_in_same_sim":
            run_all_in_same_sim = value or value is None
            for test_case in self._test_cases:
                test_case.enable_configuration = not run_all_in_same_sim

            self._individual_tests = not run_all_in_same_sim and len(self._test_cases) > 0

            return

        super().set_attribute(name, value)

    def add_config(  # pylint: disable=too-many-arguments
        self,
        name: str,
        *,
        generics: dict[str, Any] | None = None,
        pre_config: Callable[..., Any] | None = None,
        post_check: Callable[..., Any] | None = None,
        sim_options: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        vhdl_configuration_name: str | None = None,
    ) -> None:
        """
        Add a configuration copying unset fields from the default configuration.

        fail_on_warning and run_all_in_same_sim have special meaning and are handled
        separately.
        """
        if attributes:
            if "fail_on_warning" in attributes:
                value = attributes["fail_on_warning"]
                self.set_sim_option("vhdl_assert_stop_level", "warning" if value or value is None else "error")
                del attributes["fail_on_warning"]

            if "run_all_in_same_sim" in attributes:
                value = attributes["run_all_in_same_sim"]
                run_all_in_same_sim = value or value is None
                for test_case in self._test_cases:
                    test_case.enable_configuration = not run_all_in_same_sim

                self._individual_tests = not run_all_in_same_sim and len(self._test_cases) > 0
                del attributes["run_all_in_same_sim"]

        super().add_config(
            name,
            generics=generics,
            pre_config=pre_config,
            post_check=post_check,
            sim_options=sim_options,
            attributes=attributes,
            vhdl_configuration_name=vhdl_configuration_name,
        )


class FileLocation(object):
    """
    The location of a token within a file

    - file name
    - offset and length in characters in the file
    """

    @staticmethod
    def from_match(file_name: str, match: re.Match[str], key: str | int, line_offsets: list[int]) -> FileLocation:
        """
        Create FileLocation from regex match key
        """
        offset = match.start(key)
        length = match.end(key) - match.start(key)
        return FileLocation.from_line_offsets(file_name, offset, length, line_offsets)

    @staticmethod
    def from_line_offsets(file_name: str, offset: int, length: int, line_offsets: list[int]) -> FileLocation:
        """
        Create FileLocation with lineno computed from line offsets
        """
        return FileLocation(file_name, offset, length, _lookup_lineno(offset, line_offsets))

    def __init__(self, file_name: str, offset: int, length: int, lineno: int) -> None:
        self.file_name = file_name
        self.offset = offset
        self.length = length
        self.lineno = lineno

    def _to_tuple(self) -> tuple[str, int, int, int]:
        return (self.file_name, self.offset, self.length, self.lineno)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FileLocation) and self._to_tuple() == other._to_tuple()

    def __repr__(self) -> str:
        return "FileLocation" + repr(self._to_tuple())

    def __hash__(self) -> int:
        return hash(self._to_tuple())


class Test(object):
    """
    Holds information about a test in the source code

    - name of test
    - location in file
    - if it was an explicit or implicit test [1]

    [1]: Explicit tests are those where the user has written run("test name").
         Implicit tests are those when there are no tests in the test bench, just the test suite
    """

    def __init__(self, name: str | None, location: FileLocation) -> None:
        self._name: str | None = name
        self._location = location
        self._attributes: list[Attribute] = []

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def location(self) -> FileLocation:
        return self._location

    @property
    def is_explicit(self) -> bool:
        return self._name is not None

    def add_attribute(self, attr: Attribute) -> None:
        self._attributes.append(attr)

    @property
    def attributes(self) -> list[Attribute]:
        return list(self._attributes)

    @property
    def attribute_names(self) -> set[str]:
        return set((attr.name for attr in self._attributes))

    def _to_tuple(self) -> tuple[str | None, FileLocation, tuple[Attribute, ...]]:
        return (self._name, self._location, tuple(self._attributes))

    def __repr__(self) -> str:
        return "Test" + repr(self._to_tuple())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Test) and self._to_tuple() == other._to_tuple()

    def __hash__(self) -> int:
        return hash(self._to_tuple())


class TestConfigurationVisitor(ConfigurationVisitor):
    """
    A means to creates configurations for single test
    """

    def __init__(
        self,
        test: Test,
        design_unit: ConfigurableDesignUnit,
        enable_configuration: bool,
        default_config: Configuration,
    ) -> None:
        ConfigurationVisitor.__init__(self, design_unit)
        self._test = test
        if not test.is_explicit:
            raise RuntimeError("TestConfigurationVisitor requires an explicit test")
        self.enable_configuration = enable_configuration
        self._configs: dict[str | None, Configuration] = OrderedDict(
            {default_config.name: default_config}
        )

    @property
    def name(self) -> str | None:
        return self._test.name

    @property
    def test(self) -> Test:
        return self._test

    def get_default_config(self) -> Configuration:
        """
        Get the default configuration of this test case
        """
        self._check_enabled()
        return self._configs[DEFAULT_NAME]

    def _check_enabled(self) -> None:
        if not self.enable_configuration:
            raise RuntimeError("Individual test configuration is not possible with run_all_in_same_sim")

    def get_configuration_dicts(self) -> list[dict[str | None, Configuration]]:  # pylint: disable=arguments-differ
        """
        Get all configurations of this test
        """
        return [self._configs]

    def _get_configurations_to_run(self) -> Iterable[Configuration]:
        """
        Get all simulation runs for this test bench
        """
        configs = self._configs.copy()
        if len(configs) > 1:
            # Remove default configurations when there are more than one
            del configs[DEFAULT_NAME]
        return configs.values()

    def create_tests(
        self,
        simulator_if: SimulatorInterface | None,
        seed: str | None,
        elaborate_only: bool,
        test_list: TestList | None = None,
        test_history: dict[str, Any] | None = None,
    ) -> None:  # pylint: disable=too-many-positional-arguments
        """
        Create all tests from this test case which may be several depending on the number of configurations
        """
        test_history = test_history if test_history else {}

        if test_list is None:
            raise RuntimeError("TestConfigurationVisitor.create_tests requires a test_list")

        for config in self._get_configurations_to_run():
            actual_seed = seed
            if seed == "repeat":
                # Test suite name and test name are the same for individual tests
                test_suite_name = test_name = IndependentSimTestCase.get_name(self._test, config)
                actual_seed = _get_historic_seed(test_history, test_suite_name, test_name)

            test_list.add_test(
                IndependentSimTestCase(
                    test=self._test,
                    config=config,
                    simulator_if=simulator_if,
                    seed=actual_seed,
                    elaborate_only=elaborate_only,
                )
            )


_RE_VHDL_TEST_CASE = re.compile(r'(\s|\()+run\s*\(\s*"(?P<name>.*?)"\s*\)', re.IGNORECASE)
_RE_VERILOG_TEST_CASE = re.compile(r'`TEST_CASE\s*\(\s*"(?P<name>.*?)"\s*\)')
_RE_VHDL_TEST_SUITE = re.compile(r"test_runner_setup\s*\(", re.IGNORECASE)
_RE_VERILOG_TEST_SUITE = re.compile(r"`TEST_SUITE\b")


def _get_line_offsets(code: str) -> list[int]:
    """
    Returns a list with one entry per line returning the offset in the
    code where it starts
    """

    offset = 0
    offsets: list[int] = []
    for line in code.splitlines():
        offsets.append(offset)
        offset += len(line) + 1

    return offsets


def _lookup_lineno(offset: int, offsets: list[int]) -> int:
    """
    Convert offset into line number
    """
    return bisect.bisect(offsets, offset)


def _check_duplicate_tests(tests: list[Test]) -> None:
    """
    Check for duplicate tests and raise RuntimeError
    """
    known_tests: dict[str | None, Test] = {}
    found_duplicates = False
    for test in tests:
        if test.name in known_tests:
            known_test = known_tests[test.name]
            LOGGER.error(
                'Duplicate test "%s" in %s line %i previously defined on line %i',
                test.name,
                test.location.file_name,
                test.location.lineno,
                known_test.location.lineno,
            )
            found_duplicates = True
        else:
            known_tests[test.name] = test

    if found_duplicates:
        raise RuntimeError("Duplicate tests where found")


def _find_tests(code: str, file_name: str, line_offsets: list[int] | None = None) -> list[Test]:
    """
    Finds all tests within a file including implicit tests where there
    is only a test suite

    returns a list to Test objects
    """

    if line_offsets is None:
        line_offsets = _get_line_offsets(code)

    is_verilog = file_type_of(file_name) in VERILOG_FILE_TYPES

    if is_verilog:
        code = _remove_verilog_comments(code)
        regexp = _RE_VERILOG_TEST_CASE
        suite_regexp = _RE_VERILOG_TEST_SUITE
    else:
        code = remove_vhdl_comments(code)
        regexp = _RE_VHDL_TEST_CASE
        suite_regexp = _RE_VHDL_TEST_SUITE

    tests = [
        Test(
            name=match.group("name"),
            location=FileLocation.from_match(file_name, match, "name", line_offsets),
        )
        for match in regexp.finditer(code)
    ]

    _check_duplicate_tests(tests)

    if not tests:
        # Implicit test, use the test suite start as lineno
        match = suite_regexp.search(code)

        if match:
            location = FileLocation.from_match(file_name, match, 0, line_offsets)
        else:
            LOGGER.warning("Found no tests or test suite within %s", file_name)
            location = FileLocation.from_line_offsets(file_name, 0, 0, line_offsets)

        tests = [Test(None, location=location)]

    return tests


def _check_duplicates(
    attrs: list[Attribute] | list[LegacyAttribute | Attribute],
    file_name: str,
    test_name: str | None = None,
) -> None:
    """
    Check for duplicate attributes, if test_name is None it is a file global attribute
    """
    previous: dict[str, Attribute | LegacyAttribute] = {}
    for attr in attrs:
        if attr.name in previous:
            loc = (
                "" if test_name is None else f"test {test_name!s} in "
            ) + f"{file_name!s} line {attr.location.lineno:d}"

            raise RuntimeError(
                f"Duplicate attribute {attr.name!s} of {loc!s}, "
                f"previously defined on line {previous[attr.name].location.lineno:d}"
            )

        previous[attr.name] = attr


def _find_tests_and_attributes(
    content: str, file_name: str
) -> tuple[list[Test], list[LegacyAttribute | Attribute]]:
    """
    Parse attributes and test case names

    Attributes are associated with a single test case by being located
    after the test case definition.

    Attributes are associated with file by being located above all
    test cases.

    NOTE: The legacy vunit_pragma is always associated with the entire file due to legacy reasons

    Returns the tests and global attributes. The tests have been annotated with attributes.
    """
    line_offsets = _get_line_offsets(content)
    attributes = _find_attributes(content, file_name, line_offsets)
    tests = _find_tests(content, file_name, line_offsets)

    tests = sorted(tests, key=lambda test: test.location.offset)
    offsets = [test.location.offset for test in tests]

    def associate(attr: Attribute | LegacyAttribute) -> Test | None:
        """
        Associate attribute with test case
        """
        idx = bisect.bisect_right(offsets, attr.location.offset)
        if idx == 0:
            return None
        return tests[idx - 1]

    global_attributes: list[LegacyAttribute | Attribute] = []
    for attr in attributes:
        if isinstance(attr, LegacyAttribute):
            global_attributes.append(attr)
        else:
            test = associate(attr)

            if test:
                test.add_attribute(attr)
            else:
                global_attributes.append(attr)

    for test in tests:
        _check_duplicates(test.attributes, file_name, test_name=test.name)

    _check_duplicates(global_attributes, file_name)

    return tests, global_attributes


_RE_ATTR_NAME = r"[a-zA-Z0-9_\-]+"
_RE_ATTRIBUTE = re.compile(r"vunit:\s*(?P<name>\.?" + _RE_ATTR_NAME + r")", re.IGNORECASE)
_RE_PRAGMA_LEGACY = re.compile(r"vunit_pragma\s+(?P<name>" + _RE_ATTR_NAME + ")", re.IGNORECASE)
_VALID_ATTRIBUTES = ["run_all_in_same_sim", "fail_on_warning"]


def _is_user_attribute(name: str) -> bool:
    return name.startswith(".")


def _find_attributes(
    code: str, file_name: str, line_offsets: list[int] | None = None
) -> list[LegacyAttribute | Attribute]:
    """
    Return a list of all vunit attributes parsed from the code

    Attributes are either built-in:
    // -- vunit: run_all_in_same_sim

    or user defined:
    // -- vunit: .foo

    @TODO only look inside comments
    """

    if line_offsets is None:
        line_offsets = _get_line_offsets(code)

    attributes: list[LegacyAttribute | Attribute] = []

    def _find(attr_class: Callable[..., LegacyAttribute | Attribute], regex: re.Pattern[str]) -> None:
        """
        Helper method to create attributes from regex
        """
        for match in regex.finditer(code):
            groups = match.groupdict(default=None)
            name = groups["name"]
            location = FileLocation.from_match(file_name, match, "name", line_offsets)

            if name is None:
                raise RuntimeError(
                    f"Failed to parse attribute name in {file_name!s} line {location.lineno:d}"
                )
            if not _is_user_attribute(name) and name not in _VALID_ATTRIBUTES:
                raise RuntimeError(f"Invalid attribute '{name!s}' in {file_name!s} line {location.lineno:d}")

            attributes.append(attr_class(name, value=None, location=location))

    _find(LegacyAttribute, _RE_PRAGMA_LEGACY)
    _find(Attribute, _RE_ATTRIBUTE)

    return attributes


# Add value field to be forwards compatible with having attribute values
Attribute = collections.namedtuple("Attribute", ["name", "value", "location"])
LegacyAttribute = collections.namedtuple("LegacyAttribute", ["name", "value", "location"])


VERILOG_REMOVE_COMMENT_RE = re.compile(r"(//[^\n]*)|(/\*.*?\*/)", re.DOTALL)


def _comment_repl(match: re.Match[str]) -> str:
    """
    Replace comment with equal amount of whitespace to make
    lexical position unaffected
    """
    text = match.group(0)
    return "".join(" " if c != "\n" else "\n" for c in text)


def _remove_verilog_comments(code: str) -> str:
    """
    Remove all verilog comments
    """
    return VERILOG_REMOVE_COMMENT_RE.sub(_comment_repl, code)


def _get_historic_seed(
    test_history: dict[str, Any],
    test_suite_name: str,
    test_name: str | None = None,
) -> str | None:
    """Return seed from test history or None if no history exists."""
    if test_suite_name not in test_history:
        return None

    # If there are multiple tests running in the same simulation, they all have the same seed so we take the
    # first test.
    if not test_name:
        if not test_history[test_suite_name]:  # This is a precaution. Should never happen.
            return None

        test_name = next(iter(test_history[test_suite_name]))

    if test_name not in test_history[test_suite_name]:
        return None

    seed_value = test_history[test_suite_name][test_name]["seed"]
    if seed_value is None:
        return None
    if not isinstance(seed_value, str):
        raise RuntimeError(
            f"Expected seed in test history to be a string, got {type(seed_value).__name__}"
        )
    return seed_value
