# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Contains Configuration class which contains configuration of a test run
"""

from __future__ import annotations

import logging
import inspect
from pathlib import Path
from copy import copy
from typing import Any, Callable, Protocol

from vunit.sim_if.factory import SIMULATOR_FACTORY

from vunit.source_file import SourceFile


LOGGER = logging.getLogger(__name__)

# Name of default configuration
DEFAULT_NAME: str | None = None


class ConfigurableDesignUnit(Protocol):
    """
    Structural protocol for a design unit that can carry a configuration.

    Any VHDL entity or Verilog module — plus test doubles used in the unit
    tests — is expected to expose this surface.
    """

    name: str
    generic_names: list[str]
    # Only present on entity-like design units; accessed under an ``is_entity`` guard.
    architecture_names: dict[str, str]

    @property
    def library_name(self) -> str: ...

    @property
    def file_name(self) -> str: ...

    @property
    def original_file_name(self) -> str: ...

    @property
    def is_entity(self) -> bool: ...

    @property
    def source_file(self) -> SourceFile: ...

    def set_add_architecture_callback(self, callback: Callable[[], None]) -> None: ...


# Pre/post hook callables can accept arbitrary keyword arguments; we introspect
# their signatures at runtime to decide what to pass. The return value is
# checked against ``is True`` so any object may be returned.
PreConfig = Callable[..., Any]
PostCheck = Callable[..., Any]


class AttributeException(Exception):
    pass


class Configuration(object):  # pylint: disable=too-many-instance-attributes
    """
    Represents a configuration of a test bench
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        name: str | None,
        design_unit: ConfigurableDesignUnit,
        *,
        generics: dict[str, Any] | None = None,
        sim_options: dict[str, Any] | None = None,
        pre_config: PreConfig | None = None,
        post_check: PostCheck | None = None,
        attributes: dict[str, Any] | None = None,
        vhdl_configuration_name: str | None = None,
    ) -> None:
        self.name: str | None = name
        self._design_unit: ConfigurableDesignUnit = design_unit
        self.generics: dict[str, Any] = {} if generics is None else generics
        self.sim_options: dict[str, Any] = {} if sim_options is None else sim_options
        self.attributes: dict[str, Any] = {} if attributes is None else attributes
        self.vhdl_configuration_name: str | None = vhdl_configuration_name

        self.tb_path: str = str(Path(design_unit.original_file_name).parent)

        # Fill in tb_path generic with location of test bench
        if "tb_path" in design_unit.generic_names:
            self.generics["tb_path"] = str(self.tb_path.replace("\\", "/")) + "/"

        self.pre_config: PreConfig | None = pre_config
        self.post_check: PostCheck | None = post_check

    def copy(self) -> Configuration:
        return Configuration(
            name=self.name,
            design_unit=self._design_unit,
            generics=self.generics.copy(),
            sim_options=self.sim_options.copy(),
            pre_config=self.pre_config,
            post_check=self.post_check,
            attributes=self.attributes.copy(),
            vhdl_configuration_name=self.vhdl_configuration_name,
        )

    @property
    def is_default(self) -> bool:
        return self.name is DEFAULT_NAME

    @property
    def generic_names(self) -> list[str]:
        return self._design_unit.generic_names

    @property
    def entity_name(self) -> str:
        return self._design_unit.name

    @property
    def design_unit_name(self) -> str:
        return self._design_unit.name

    @property
    def library_name(self) -> str:
        return self._design_unit.library_name

    @property
    def architecture_name(self) -> str | None:  # pylint: disable=missing-docstring
        if self._design_unit.is_entity:
            return next(iter(self._design_unit.architecture_names))

        return None

    def set_attribute(self, name: str, value: Any) -> None:
        """
        Set attribute
        """
        if name.startswith("."):
            self.attributes[name] = value
        else:
            raise AttributeException

    def set_vhdl_configuration_name(self, name: str) -> None:
        """
        Set VHDL configuration name
        """
        self.vhdl_configuration_name = name

    def set_generic(self, name: str, value: Any) -> None:
        """
        Set generic
        """
        if name not in self._design_unit.generic_names:
            LOGGER.warning(
                "Generic '%s' set to value '%s' not found in %s '%s.%s'. Possible values are [%s]",
                name,
                value,
                "entity" if self._design_unit.is_entity else "module",
                self._design_unit.library_name,
                self._design_unit.name,
                ", ".join(str(gname) for gname in self._design_unit.generic_names),
            )
        else:
            self.generics[name] = value

    def set_sim_option(self, name: str, value: Any) -> None:
        """
        Set sim option
        """
        SIMULATOR_FACTORY.check_sim_option(name, value)
        self.sim_options[name] = copy(value)

    @property
    def vhdl_assert_stop_level(self) -> str:
        """
        Return the VHDL assert stop level to use with the simulator
        """
        if "vhdl_assert_stop_level" in self.sim_options:
            level = self.sim_options["vhdl_assert_stop_level"]
        else:
            level = "error"

        if not isinstance(level, str):
            raise RuntimeError(
                f"Expected vhdl_assert_stop_level to be a string, got {type(level).__name__}"
            )
        return level

    def call_pre_config(
        self,
        output_path: str,
        simulator_output_path: str,
        seed: str,
    ) -> bool:
        """
        Call pre_config if available. Setting optional output_path
        """
        if self.pre_config is None:
            return True

        args = inspect.getfullargspec(self.pre_config).args

        kwargs: dict[str, Any] = {
            "output_path": output_path,
            "simulator_output_path": simulator_output_path,
            "seed": seed,
        }

        for argname in list(kwargs.keys()):
            if argname not in args:
                del kwargs[argname]

        return self.pre_config(**kwargs) is True

    def call_post_check(
        self,
        output_path: str,
        read_output: Callable[[], str],
    ) -> bool:
        """
        Call post_check if available. Setting optional output_path
        """
        if self.post_check is None:
            return True

        args = inspect.getfullargspec(self.post_check).args

        kwargs: dict[str, Callable[[], Any]] = {
            "output_path": lambda: output_path,
            "output": read_output,
        }

        resolved_kwargs: dict[str, Any] = {}
        for argname, provider in list(kwargs.items()):
            if argname in args:
                resolved_kwargs[argname] = provider()

        return self.post_check(**resolved_kwargs) is True


class ConfigurationVisitor(object):
    """
    An interface to visit simulation run configurations
    """

    def __init__(self, design_unit: ConfigurableDesignUnit) -> None:
        self.design_unit: ConfigurableDesignUnit = design_unit

    def _check_enabled(self) -> None:
        pass

    def get_configuration_dicts(self) -> list[dict[str | None, Configuration]]:
        raise NotImplementedError

    def set_attribute(self, name: str, value: Any) -> None:
        """
        Set attribute
        """
        self._check_enabled()
        for configs in self.get_configuration_dicts():
            for config in configs.values():
                config.set_attribute(name, value)

    def set_generic(self, name: str, value: Any) -> None:
        """
        Set generic
        """
        self._check_enabled()
        for configs in self.get_configuration_dicts():
            for config in configs.values():
                config.set_generic(name, value)

    def set_vhdl_configuration_name(self, value: str) -> None:
        """
        Set VHDL configuration name
        """
        self._check_enabled()
        for configs in self.get_configuration_dicts():
            for config in configs.values():
                config.set_vhdl_configuration_name(value)

    def set_sim_option(self, name: str, value: Any, overwrite: bool = True) -> None:
        """
        Set sim option

        :param overwrite: To overwrite the option or append to the existing value
        """
        self._check_enabled()
        for configs in self.get_configuration_dicts():
            for config in configs.values():
                if not overwrite:
                    config.set_sim_option(name, config.sim_options.get(name, []) + value)
                    continue
                config.set_sim_option(name, value)

    def set_pre_config(self, value: PreConfig | None) -> None:
        """
        Set pre_config function
        """
        self._check_enabled()
        for configs in self.get_configuration_dicts():
            for config in configs.values():
                config.pre_config = value

    def set_post_check(self, value: PostCheck | None) -> None:
        """
        Set post_check function
        """
        self._check_enabled()
        for configs in self.get_configuration_dicts():
            for config in configs.values():
                config.post_check = value

    @staticmethod
    def _check_architectures(design_unit: ConfigurableDesignUnit) -> None:
        """
        Check that an entity which has been classified as a VUnit test bench
        has exactly one architecture. Raise RuntimeError otherwise.
        """
        if design_unit.is_entity:
            if not design_unit.architecture_names:
                raise RuntimeError(f"Test bench '{design_unit.name!s}' has no architecture.")

            if len(design_unit.architecture_names) > 1:
                archs = ", ".join(
                    f"{name!s}:{Path(fname).name!s}" for name, fname in sorted(design_unit.architecture_names.items())
                )
                raise RuntimeError(
                    "Test bench not allowed to have multiple architectures. " f"Entity {design_unit.name!s} has {archs}"
                )

    def add_config(  # pylint: disable=too-many-arguments
        self,
        name: str,
        *,
        generics: dict[str, Any] | None = None,
        pre_config: PreConfig | None = None,
        post_check: PostCheck | None = None,
        sim_options: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        vhdl_configuration_name: str | None = None,
    ) -> None:
        """
        Add a configuration copying unset fields from the default configuration:
        """
        self._check_enabled()

        if name in (DEFAULT_NAME, ""):
            raise ValueError(f"Illegal configuration name {name!r}. Must be non-empty string")

        self._check_architectures(self.design_unit)

        for configs in self.get_configuration_dicts():
            if name in configs:
                raise RuntimeError(f"Configuration name {name!s} already defined")

            # Copy default configuration
            config = configs[DEFAULT_NAME].copy()
            config.name = name

            if pre_config is not None:
                config.pre_config = pre_config

            if post_check is not None:
                config.post_check = post_check

            if generics is not None:
                config.generics.update(generics)

            if sim_options is not None:
                config.sim_options.update(sim_options)

            if attributes is not None:
                for attribute in attributes:
                    if not attribute.startswith("."):
                        raise AttributeException
                config.attributes.update(attributes)

            if vhdl_configuration_name is not None:
                config.vhdl_configuration_name = vhdl_configuration_name

            configs[config.name] = config
