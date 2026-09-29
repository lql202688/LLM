"""MCP 工具包.

每个工具一个模块,后续阶段新增工具只需在此处增加模块并复用
:mod:`pet_hospital_mcp.rest_client`、:mod:`pet_hospital_mcp.errors`
和 :mod:`pet_hospital_mcp.logging_config` 约定。
"""

from __future__ import annotations

from .list_pets import ListPetsInput, ListPetsOutput, register_list_pets_tool

__all__ = [
    "ListPetsInput",
    "ListPetsOutput",
    "register_list_pets_tool",
]
