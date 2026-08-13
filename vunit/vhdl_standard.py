# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Contains type defining VHDL standards and operations on them
"""

from __future__ import annotations


class VHDLStandard(object):
    """
    VHDL standard object which encapsulates knowledge about VHDL standards
    """

    _STANDARDS = {"1993", "2002", "2008", "2019"}

    def __init__(self, standard_name: str) -> None:
        if standard_name in self._STANDARDS:
            self._standard = standard_name
        else:
            for standard in self._STANDARDS:
                if standard.endswith(standard_name) and len(standard_name) == 2:
                    self._standard = standard
                    break
            else:
                raise ValueError(f"Unknown standard '{standard_name!s}'")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self._standard == other._standard  # pylint: disable=protected-access
        return False

    def __lt__(self, other: VHDLStandard) -> bool:
        return int(self._standard) < int(other._standard)  # pylint: disable=protected-access

    def __le__(self, other: VHDLStandard) -> bool:
        return int(self._standard) <= int(other._standard)  # pylint: disable=protected-access

    def __gt__(self, other: VHDLStandard) -> bool:
        return int(self._standard) > int(other._standard)  # pylint: disable=protected-access

    def __ge__(self, other: VHDLStandard) -> bool:
        return int(self._standard) >= int(other._standard)  # pylint: disable=protected-access

    def __str__(self) -> str:
        if self == VHDL.STD_1993:
            # For backwards compatibility due to legacy reasons
            return "93"
        return self._standard

    def __repr__(self) -> str:
        return f"VHDLStandard({self._standard!r})"

    def __hash__(self) -> int:
        return hash(self._standard)

    @property
    def supports_context(self) -> bool:
        return self >= VHDL.STD_2008

    @property
    def and_later(self) -> set[VHDLStandard]:
        """
        Return a set including this standard and all later standards
        """
        return {standard for standard in VHDL.STANDARDS if standard >= self}

    @property
    def and_earlier(self) -> set[VHDLStandard]:
        """
        Return a set including this standard and all earlier standards
        """
        return {standard for standard in VHDL.STANDARDS if standard <= self}


class VHDL(object):
    """
    Just a namespace for standards
    """

    STD_1993 = VHDLStandard("1993")
    STD_2002 = VHDLStandard("2002")
    STD_2008 = VHDLStandard("2008")
    STD_2019 = VHDLStandard("2019")
    STANDARDS = [STD_1993, STD_2002, STD_2008, STD_2019]

    @staticmethod
    def standard(name: str) -> VHDLStandard:
        return VHDLStandard(name)
