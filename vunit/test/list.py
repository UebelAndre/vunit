# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Functionality to handle lists of test suites and filtering of them
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol
from .report import PASSED, FAILED, TestStatus


class _TestCaseLike(Protocol):
    """Protocol describing the test case objects wrapped by :class:`TestSuiteWrapper`."""

    @property
    def name(self) -> str: ...

    @property
    def file_name(self) -> str: ...

    @property
    def attribute_names(self) -> set[str]: ...

    @property
    def test_configuration(self) -> Any: ...

    @property
    def test_information(self) -> Any: ...

    def run(self, *args: Any, **kwargs: Any) -> bool: ...

    def get_seed(self) -> str | None: ...


class TestSuiteLike(Protocol):
    """
    Protocol describing the test suite objects stored in :class:`TestList`.

    Both :class:`TestSuiteWrapper` and the various suite classes in
    :mod:`vunit.test.suites` implement this interface.
    """

    @property
    def name(self) -> str: ...

    @property
    def file_name(self) -> str: ...

    @property
    def test_names(self) -> list[str]: ...

    @property
    def test_information(self) -> dict[str, Any]: ...

    @property
    def test_configuration(self) -> dict[str, Any]: ...

    def keep_matches(self, test_filter: Callable[..., bool]) -> bool: ...

    def run(
        self, output_path: str, read_output: Callable[[], str]
    ) -> dict[str, TestStatus]: ...

    def get_seed(self) -> str | None: ...


class TestList(object):
    """
    A list of test suites
    """

    def __init__(self) -> None:
        self._test_suites: list[TestSuiteLike] = []

    def add_suite(self, test_suite: TestSuiteLike) -> None:
        self._test_suites.append(test_suite)

    def add_test(self, test_case: _TestCaseLike) -> None:
        """
        Add a single test that is automatically wrapped into a test suite
        """
        test_suite = TestSuiteWrapper(test_case)
        self._test_suites.append(test_suite)

    def keep_matches(self, test_filter: Callable[..., bool]) -> None:
        """
        Keep only testcases matching any pattern
        """
        self._test_suites = [test for test in self._test_suites if test.keep_matches(test_filter)]

    @property
    def num_tests(self) -> int:
        """
        Return the number of tests within
        """
        num_tests = 0
        for test_suite in self:
            num_tests += len(test_suite.test_names)
        return num_tests

    @property
    def test_names(self) -> list[str]:
        """
        Return the names of all tests
        """
        names: list[str] = []
        for test_suite in self:
            names += test_suite.test_names
        return names

    def __iter__(self) -> Iterator[TestSuiteLike]:
        return iter(self._test_suites)

    def __len__(self) -> int:
        return len(self._test_suites)

    def __getitem__(self, idx: int) -> TestSuiteLike:
        return self._test_suites[idx]


class TestSuiteWrapper(object):
    """
    Wrapper which creates a test suite from a single test case
    """

    def __init__(self, test_case: _TestCaseLike) -> None:
        self._test_case = test_case

    @property
    def file_name(self) -> str:
        return self._test_case.file_name

    @property
    def test_names(self) -> list[str]:
        return [self._test_case.name]

    @property
    def name(self) -> str:
        return self._test_case.name

    @property
    def test_configuration(self) -> dict[str, Any]:
        return {self.name: self._test_case.test_configuration}

    @property
    def test_information(self) -> dict[str, Any]:
        return {self.name: self._test_case.test_information}

    def keep_matches(self, test_filter: Callable[..., bool]) -> bool:
        attributes = self._test_case.attribute_names.copy()
        attributes.update(set(self._test_case.test_configuration.attributes.keys()))
        return test_filter(name=self._test_case.name, attribute_names=attributes)

    def run(self, *args: Any, **kwargs: Any) -> dict[str, TestStatus]:
        """
        Run the test suite and return the test results for all test cases
        """
        test_ok = self._test_case.run(*args, **kwargs)
        return {self._test_case.name: PASSED if test_ok else FAILED}

    def get_seed(self) -> str | None:
        return self._test_case.get_seed()
