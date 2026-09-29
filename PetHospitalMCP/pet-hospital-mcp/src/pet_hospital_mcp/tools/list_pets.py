"""``list_pets`` 工具.

严格适配上游 Go REST API 的 ``GET /api/v1/pets``:
- 支持且仅支持 13 个查询参数(q/name/ownerName/ownerPhone/species/doctor/
  disease/status/min/max/sortBy/order/page/pageSize);
- 输入模型用 Pydantic 严格校验:枚举值与后端一致、page>=1、
  1<=pageSize<=500、min/max 非负且 min<=max、拒绝未知字段与 NaN/Infinity;
- 成功输出对应 Go 响应 ``data`` 的字段(items/total/page/pageSize/
  totalPages/totalCost),并兼容 records/charges 可能为 null;
- 工具执行失败统一返回 ``is_error=True`` 的 ``CallToolResult``,content
  内是统一错误结构 JSON(见 :mod:`pet_hospital_mcp.errors`)。
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..errors import (
    BACKEND_INVALID_RESPONSE,
    INTERNAL_ERROR,
    VALIDATION_ERROR,
    ToolError,
    translate_unknown_error,
)
from ..logging_config import get_logger, redact
from ..rest_client import PetHospitalClient


# ---------------------------------------------------------------------------
# 枚举(与上游 Go REST API 严格一致,见 internal/model/model.go)
# ---------------------------------------------------------------------------

SpeciesLiteral = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
StatusLiteral = Literal[
    "待就诊", "就诊中", "住院中", "已康复", "慢性病随访"
]
SortByLiteral = Literal[
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
]
OrderLiteral = Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class ListPetsInput(BaseModel):
    """``list_pets`` 工具的输入参数。

    所有字段名使用 snake_case,通过 alias 暴露为 Go API 期望的 camelCase
    查询参数名。``extra="forbid"`` 拒绝未知字段。
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=False,
    )

    q: str | None = None
    name: str | None = None
    owner_name: str | None = Field(default=None, alias="ownerName")
    owner_phone: str | None = Field(default=None, alias="ownerPhone")
    species: SpeciesLiteral | None = None
    doctor: str | None = None
    disease: str | None = None
    status: StatusLiteral | None = None
    min: float | None = None
    max: float | None = None
    sort_by: SortByLiteral | None = Field(default=None, alias="sortBy")
    order: OrderLiteral | None = None
    page: int | None = Field(default=None, ge=1)
    page_size: int | None = Field(default=None, alias="pageSize", ge=1, le=500)

    @field_validator("min", "max")
    @classmethod
    def _check_finite_and_non_negative(cls, v: float | None) -> float | None:
        if v is None:
            return None
        # 拒绝 NaN / Infinity(类型已限定为 float,但 JSON 可能塞进 NaN 字面量)
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError("must be a finite number")
        if v < 0:
            raise ValueError("must be non-negative")
        return v

    @model_validator(mode="after")
    def _check_min_le_max(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self

    def to_query_params(self) -> dict[str, str]:
        """转换为 Go API 期望的 camelCase 查询参数 dict。

        剔除 ``None`` 与空字符串;数值转为字符串。``page`` / ``page_size``
        为 ``None`` 时不传,由上游使用默认值。
        """
        dumped = self.model_dump(exclude_none=True, by_alias=True)
        out: dict[str, str] = {}
        for k, v in dumped.items():
            if isinstance(v, bool):
                # 防止 True/False 误传,本工具无布尔字段
                continue
            if isinstance(v, (int, float)):
                # 整数去掉小数尾
                if isinstance(v, float) and v.is_integer():
                    out[k] = str(int(v))
                else:
                    out[k] = repr(v)
            else:
                s = str(v)
                if s == "":
                    continue
                out[k] = s
        return out


# ---------------------------------------------------------------------------
# 输出模型(对应 Go API 响应 data)
# ---------------------------------------------------------------------------


class _CommonModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MedicalRecord(_CommonModel):
    id: str | None = None
    visit_date: str | None = Field(default=None, alias="visitDate")
    doctor: str | None = None
    diagnosis: str | None = None
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] | None = None
    weight_kg: float | None = Field(default=None, alias="weightKg")
    temperature: float | None = None
    follow_up: str | None = Field(default=None, alias="followUp")
    charge: float | None = None
    created_at: str | None = Field(default=None, alias="createdAt")


class Treatment(_CommonModel):
    id: str | None = None
    item: str | None = None
    category: str | None = None
    amount: float | None = None
    doctor: str | None = None
    date: str | None = None
    note: str | None = None


class PetItem(_CommonModel):
    id: str
    name: str
    species: str
    breed: str | None = None
    gender: str | None = None
    age_months: int | None = Field(default=None, alias="ageMonths")
    color: str | None = None
    chip_no: str | None = Field(default=None, alias="chipNo")
    owner_name: str = Field(alias="ownerName")
    owner_phone: str = Field(alias="ownerPhone")
    owner_addr: str | None = Field(default=None, alias="ownerAddr")
    doctor: str
    disease: str
    status: str
    allergy: str | None = None
    note: str | None = None
    # 兼容 Go 对 nil slice 序列化为 null 的情况
    records: list[MedicalRecord] | None = None
    charges: list[Treatment] | None = None
    total_cost: float = Field(alias="totalCost")
    visit_count: int = Field(alias="visitCount")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ListPetsOutput(_CommonModel):
    """``list_pets`` 成功输出,严格对应 Go API 响应 ``data`` 字段。"""

    items: list[PetItem]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")
    total_pages: int = Field(alias="totalPages")
    total_cost: float = Field(alias="totalCost")


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

TOOL_NAME = "list_pets"

TOOL_DESCRIPTION = (
    "查询宠物医院档案列表,严格对应上游 Go REST API 的 GET /api/v1/pets。\n"
    "\n"
    "用途:分页浏览 / 多维度筛选 / 排序宠物档案,返回每只宠物的核心信息、"
    "历史病历(records)与消费明细(charges),以及结果集的总花费。\n"
    "\n"
    "参数(全部可选;未知字段会被拒绝):\n"
    "- q: 全文检索关键词,跨字段 AND 匹配(覆盖病历与收费项目)\n"
    "- name: 宠物姓名(模糊)\n"
    "- ownerName: 主人姓名(模糊)\n"
    "- ownerPhone: 主人电话(模糊)\n"
    "- species: 种类枚举(犬/猫/兔/鸟/仓鼠/爬宠/其他)\n"
    "- doctor: 医生姓名(模糊)\n"
    "- disease: 疾病(模糊)\n"
    "- status: 状态枚举(待就诊/就诊中/住院中/已康复/慢性病随访)\n"
    "- min / max: 总花费区间(非负,min<=max)\n"
    "- sortBy: 排序字段(id/name/ownerName/species/doctor/disease/status/"
    "totalCost/visitCount/createdAt/updatedAt)\n"
    "- order: 排序方向(asc/desc)\n"
    "- page: 页码,>=1,默认 1\n"
    "- pageSize: 每页条数,1-500,默认 20\n"
    "\n"
    "返回值(成功): {items, total, page, pageSize, totalPages, totalCost};"
    "items 中 records / charges 可能为 null 或数组。\n"
    "返回值(失败): is_error=true,content 为统一错误结构 JSON:"
    "{error:{code,message,details}}。"
)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


def register_list_pets_tool(mcp: MCPServer, client: PetHospitalClient) -> None:
    """把 ``list_pets`` 工具注册到 MCPServer 上。

    工具处理函数捕获所有异常并翻译成统一错误结构,绝不把 Python/HTTPX/
    Pydantic 堆栈透传给 MCP 客户端。
    """
    log = get_logger(TOOL_NAME)

    @mcp.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)
    async def list_pets(input: ListPetsInput) -> CallToolResult:  # noqa: ANN401
        start = time.perf_counter()
        params = input.to_query_params()
        # 上下文绑定:tool_name + 脱敏后的 params
        run_log = log.bind(params=redact(params))

        try:
            # 1. 转发请求给上游 Go REST API(已带超时与重试)
            data: Any = client.list_pets(params)

            # 2. 校验上游响应结构是否符合 ListPetsOutput
            try:
                output = ListPetsOutput.model_validate(data)
            except Exception as exc:
                run_log.warning(
                    "backend_invalid_response",
                    status="error",
                    error=str(exc)[:200],
                    duration_ms=_elapsed_ms(start),
                )
                raise ToolError(
                    BACKEND_INVALID_RESPONSE,
                    "上游返回的数据不符合 list_pets 输出模型",
                    {"error_kind": type(exc).__name__},
                ) from exc

            # 3. 成功:返回结构化 content + structured_content
            payload = output.model_dump(by_alias=True, exclude_none=False)
            run_log.info(
                "ok",
                status="ok",
                total=output.total,
                page=output.page,
                page_size=output.page_size,
                duration_ms=_elapsed_ms(start),
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(payload, ensure_ascii=False),
                    )
                ],
                structured_content=payload,
                is_error=False,
            )

        except ToolError as exc:
            # 业务/上游错误:统一 is_error=True + 错误结构 JSON
            run_log.warning(
                "tool_error",
                status="error",
                error_code=exc.code,
                duration_ms=_elapsed_ms(start),
            )
            return CallToolResult(
                content=[TextContent(type="text", text=exc.to_json())],
                is_error=True,
            )

        except Exception as exc:  # noqa: BLE001
            # 兜底:任何未分类异常都翻译成 INTERNAL_ERROR
            err = translate_unknown_error(exc)
            run_log.error(
                "internal_error",
                status="error",
                error_kind=type(exc).__name__,
                duration_ms=_elapsed_ms(start),
            )
            return CallToolResult(
                content=[TextContent(type="text", text=err.to_json())],
                is_error=True,
            )

    # 让工具函数可被外部引用(便于测试 monkeypatch)
    list_pets.__name__ = TOOL_NAME  # type: ignore[attr-defined]


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


__all__ = [
    "ListPetsInput",
    "ListPetsOutput",
    "PetItem",
    "MedicalRecord",
    "Treatment",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "register_list_pets_tool",
]
