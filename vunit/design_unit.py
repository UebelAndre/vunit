# Classes to model a HDL design hierarchy
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Classes representing Entites, Architectures, Packades, Modules etc
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from vunit.source_file import SourceFile


class DesignUnit(object):
    """
    Represents a generic design unit
    """

    # Declared on the base class so subclasses (Entity, Module) — as well as
    # duck-typed test doubles — all share the same structural surface. Only
    # Entity actually populates ``architecture_names``.
    generic_names: list[str] = []
    architecture_names: dict[str, str] = {}

    def __init__(self, name: str, source_file: SourceFile, unit_type: str) -> None:
        self.name = name
        self.source_file = source_file
        self.unit_type = unit_type

    @property
    def file_name(self) -> str:
        return self.source_file.name

    @property
    def original_file_name(self) -> str:
        return self.source_file.original_name

    @property
    def library_name(self) -> str:
        return self.source_file.library.name

    @property
    def is_entity(self) -> bool:
        return False

    @property
    def is_module(self) -> bool:
        return False

    def set_add_architecture_callback(self, callback: Callable[[], None]) -> None:
        """
        Only Entity-like design units support architectures; the default implementation
        raises so callers that reach this on a non-entity get a clear error.
        """
        raise RuntimeError(f"Design unit {self.name!r} is not an entity and has no architectures")


class VHDLDesignUnit(DesignUnit):
    """
    Represents a VHDL design unit
    """

    def __init__(
        self,  # pylint: disable=too-many-arguments
        name: str,
        source_file: SourceFile,
        unit_type: str,
        *,
        is_primary: bool = True,
        primary_design_unit: str | None = None,
    ) -> None:
        DesignUnit.__init__(self, name, source_file, unit_type)
        self.is_primary = is_primary
        self.primary_design_unit = primary_design_unit


class Entity(VHDLDesignUnit):
    """
    Represents a VHDL Entity
    """

    def __init__(
        self,
        name: str,
        source_file: SourceFile,
        generic_names: list[str] | None = None,
    ) -> None:
        VHDLDesignUnit.__init__(self, name, source_file, "entity", is_primary=True)
        self.generic_names = [] if generic_names is None else generic_names
        self._add_architecture_callback: Callable[[], None] | None = None
        self.architecture_names = {}

    def add_architecture(self, design_unit: VHDLDesignUnit) -> None:
        """
        Add architecture of this entity
        """
        self.architecture_names[design_unit.name] = design_unit.source_file.name

        if self._add_architecture_callback is not None:
            self._add_architecture_callback()

    def set_add_architecture_callback(self, callback: Callable[[], None]) -> None:
        """
        Set callback to be called when an architecture is added
        """
        if self._add_architecture_callback is not None:
            raise RuntimeError("add_architecture_callback is already set")
        self._add_architecture_callback = callback

    @property
    def is_entity(self) -> bool:
        return True


class Module(DesignUnit):
    """
    Represents a Verilog Module
    """

    def __init__(
        self,
        name: str,
        source_file: SourceFile,
        generic_names: list[str] | None = None,
    ) -> None:
        DesignUnit.__init__(self, name, source_file, "module")
        self.generic_names = [] if generic_names is None else generic_names

    @property
    def is_module(self) -> bool:
        return True
