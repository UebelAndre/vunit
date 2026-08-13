# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
A general tokenizer
"""

from __future__ import annotations

import logging
import re
from typing import Callable, NamedTuple, Sequence
from vunit.ostools import read_file, file_exists, simplify_path


LocationInfo = tuple[str | None, tuple[int, int]]
Location = tuple[LocationInfo, "Location | None"]


class TokenKind:
    """
    Base class for token kinds.  Instances act as singleton sentinels
    that can also be called as factories producing :class:`TokenType`
    instances of their own kind.
    """

    def __call__(self, value: str = "", location: Location | None = None) -> TokenType:
        return Token(self, value, location)


class TokenType(NamedTuple):
    kind: TokenKind
    value: str
    location: Location | None


def Token(kind: TokenKind, value: str = "", location: Location | None = None) -> TokenType:  # pylint: disable=invalid-name
    return TokenType(kind, value, location)


def new_token_kind(name: str) -> TokenKind:
    """
    Create a new token kind with nice __repr__
    """

    cls: type[TokenKind] = type(name, (TokenKind,), {"__repr__": lambda self: name})
    return cls()


TokenFunc = Callable[[TokenType], "TokenType | None"]


class Tokenizer(object):
    """
    Maintain a prioritized list of token regex
    """

    def __init__(self) -> None:
        self._regexs: list[tuple[str, str]] = []
        self._assoc: dict[str, tuple[TokenKind, TokenFunc | None]] = {}
        self._regex: re.Pattern[str] | None = None

    def add(self, kind: TokenKind, regex: str, func: TokenFunc | None = None) -> TokenKind:
        """
        Add token type
        """
        key = chr(ord("a") + len(self._regexs))
        self._regexs.append((key, regex))
        self._assoc[key] = (kind, func)
        return kind

    def finalize(self) -> None:
        self._regex = re.compile(
            "|".join(f"(?P<{spec[0]!s}>{spec[1]!s})" for spec in self._regexs),
            re.VERBOSE | re.MULTILINE,
        )

    def tokenize(  # pylint: disable=too-many-locals
        self,
        code: str,
        file_name: str | None = None,
        previous_location: Location | None = None,
        create_locations: bool = False,
    ) -> list[TokenType]:
        """
        Tokenize the code
        """
        if self._regex is None:
            raise RuntimeError("Tokenizer.finalize() must be called before tokenize()")
        tokens: list[TokenType] = []
        start = 0
        while True:
            match = self._regex.search(code, pos=start)
            if match is None:
                break
            lexpos = (start, match.end() - 1)
            start = match.end()
            group_name = match.lastgroup
            if group_name is None:
                # All registered patterns use named groups, so this should not happen.
                continue
            kind, func = self._assoc[group_name]
            value = match.group(group_name)

            location: Location | None
            if create_locations:
                location = ((file_name, lexpos), previous_location)
            else:
                location = None

            token = Token(kind, value, location)
            transformed: TokenType | None = token if func is None else func(token)

            if transformed is not None:
                tokens.append(transformed)
        return tokens


class TokenStream(object):
    """
    Helper class for traversing a stream of tokens
    """

    def __init__(self, tokens: Sequence[TokenType]) -> None:
        self._tokens: Sequence[TokenType] = tokens
        self._idx = 0

    def __len__(self) -> int:
        return len(self._tokens)

    def __getitem__(self, index: int) -> TokenType:
        return self._tokens[index]

    @property
    def eof(self) -> bool:
        return not self._idx < len(self._tokens)

    @property
    def idx(self) -> int:
        return self._idx

    @property
    def current(self) -> TokenType:
        return self._tokens[self._idx]

    def peek(self, offset: int = 0) -> TokenType:
        return self._tokens[self._idx + offset]

    def skip_while(self, *kinds: TokenKind) -> int:
        """
        Skip forward while token kind is present
        """
        while not self.eof:
            if not any(self._tokens[self._idx].kind == kind for kind in kinds):
                break
            self._idx += 1
        return self._idx

    def skip_until(self, *kinds: TokenKind) -> int:
        """
        Skip forward until token kind is present
        """
        while not self.eof:
            if any(self._tokens[self._idx].kind == kind for kind in kinds):
                break
            self._idx += 1
        return self._idx

    def pop(self) -> TokenType:
        """
        Return current token and advance stream
        """
        if self.eof:
            raise EOFException()

        self._idx += 1
        return self._tokens[self._idx - 1]

    def expect(self, *kinds: TokenKind) -> TokenType:
        """
        Expect to pop token with any of kinds
        """
        token = self.pop()
        if token.kind not in kinds:
            expected = str(kinds[0]) if len(kinds) == 1 else f"any of [{', '.join(str(kind) for kind in kinds)}]"
            raise LocationException.error(f"Expected {expected!s} got {token.kind!s}", token.location)
        return token

    def slice(self, start: int, end: int) -> list[TokenType]:
        return list(self._tokens[start:end])


def describe_location(location: Location | None, first: bool = True) -> str:
    """
    Describe the location as a string
    """
    if location is None:
        return "Unknown location"

    ((file_name, (start, end)), previous) = location

    retval = ""
    if previous is not None:
        retval += describe_location(previous, first=False) + "\n"

    if file_name is None:
        retval += "Unknown Python string"
        return retval

    if not file_exists(file_name):
        retval += f"Unknown location in {file_name!s}"
        return retval

    contents = read_file(file_name)

    if first:
        prefix = "at"
    else:
        prefix = "from"

    count = 0
    for lineno, line in enumerate(contents.splitlines()):
        lstart = count
        lend = lstart + len(line)
        if lstart <= start <= lend:
            retval += f"{prefix!s} {simplify_path(file_name)!s} line {lineno + 1:d}:\n"
            retval += line + "\n"
            retval += (" " * (start - lstart)) + ("~" * (min(lend - 1, end) - start + 1))
            return retval

        count = lend + 1
    return retval


class EOFException(Exception):
    """
    End of file
    """


class LocationException(Exception):
    """
    A an exception to be raised when there is a problem in a location
    """

    @classmethod
    def error(cls, message: str, location: Location | None) -> LocationException:
        return cls(message, location, "error")

    @classmethod
    def warning(cls, message: str, location: Location | None) -> LocationException:
        return cls(message, location, "warning")

    @classmethod
    def debug(cls, message: str, location: Location | None) -> LocationException:
        return cls(message, location, "debug")

    def __init__(self, message: str, location: Location | None, severity: str) -> None:
        Exception.__init__(self)
        assert severity in ("debug", "warning", "error")
        self._severtity = severity
        self._message = message
        self._location = location

    def log(self, logger: logging.Logger) -> None:
        """
        Log the exception
        """
        if self._severtity == "error":
            method = logger.error
        elif self._severtity == "warning":
            method = logger.warning
        else:
            method = logger.debug

        method(self._message + "\n%s", describe_location(self._location))


def add_previous(location: Location | None, previous: Location | None) -> Location | None:
    """
    Add previous location
    """
    if location is None:
        return previous

    current, old_previous = location
    return (current, add_previous(old_previous, previous))


def strip_previous(location: Location | None) -> LocationInfo | None:
    """
    Strip previous location
    """
    if location is None:
        return None
    return location[0]
