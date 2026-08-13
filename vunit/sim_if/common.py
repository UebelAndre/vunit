# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Common functions
"""

from __future__ import annotations

from typing import Callable

from . import SimulatorInterface
from .factory import SIMULATOR_FACTORY


def has_simulator() -> bool:
    return SIMULATOR_FACTORY.has_simulator


def simulator_is(*names: str) -> bool:
    """
    Check that current simulator is any of names
    """
    supported_names = [sim.name for sim in SIMULATOR_FACTORY.supported_simulators()]
    for name in names:
        assert name in supported_names
    selected = SIMULATOR_FACTORY.select_simulator()
    if selected is None:
        return False
    return selected.name in names


def simulator_check(func: Callable[[type[SimulatorInterface]], bool]) -> bool:
    """
    Check some method of the selected simulator
    """
    simif = SIMULATOR_FACTORY.select_simulator()
    if simif is None:
        return False
    return func(simif)
