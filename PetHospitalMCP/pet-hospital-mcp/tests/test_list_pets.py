"""list_pets 工具本身的单元测试.

覆盖:
- 输入模型严格校验:枚举值、page>=1、1<=pageSize<=500、min/max 非负且
  min<=max、拒绝未知字段、拒绝 NaN/Infinity、拒绝类型错误;
- to_query_params 行为(丢弃 None/空串、camelCase alias);
- 输出模型兼容 Go 的 records/charges 可能为 null 或数组;
- 输出模型字段名 camelCase alias。
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from pydantic import ValidationError

from pet_hospital_mcp.tools.list_pets import (
    ListPetsInput,
    ListPetsOutput,
    PetItem,
)


# ---------------------------------------------------------------------------
# 输入校验:枚举
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("species", "狗"),  # 应为 "犬"
        ("species", "Cthululu"),
        ("status", "看病中"),  # 应为 "就诊中"
        ("status", "dead"),
        ("sortBy", "owner"),  # 应为 "ownerName"
        ("sortBy", "totalcost"),  # 大小写不匹配
        ("order", "ascending"),  # 应为 "asc"
        ("order", "DESC"),  # 大小写不匹配
    ],
)
def test_input_rejects_invalid_enum(field: str, value: str) -> None:
    kwargs: dict[str, Any] = {field: value}
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(**kwargs)
    # 错误信息里应出现字段名或 Literal 提示
    msg = str(exc_info.value)
    assert field in msg or "literal" in msg.lower() or "Input should be" in msg


def test_input_accepts_all_valid_species() -> None:
    for s in ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]:
        ListPetsInput(species=s)


def test_input_accepts_all_valid_status() -> None:
    for s in ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]:
        ListPetsInput(status=s)


def test_input_accepts_all_valid_sortby() -> None:
    for s in [
        "id", "name", "ownerName", "species", "doctor",
        "disease", "status", "totalCost", "visitCount",
        "createdAt", "updatedAt",
    ]:
        ListPetsInput(sortBy=s)


# ---------------------------------------------------------------------------
# 输入校验:数值边界
# ---------------------------------------------------------------------------


def test_input_rejects_page_lt_1() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput(page=0)


def test_input_rejects_page_zero() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput(page=0)


def test_input_accepts_page_one() -> None:
    ListPetsInput(page=1)


def test_input_rejects_pagesize_lt_1() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput(pageSize=0)


def test_input_rejects_pagesize_gt_500() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput(pageSize=501)


def test_input_accepts_pagesize_boundaries() -> None:
    ListPetsInput(pageSize=1)
    ListPetsInput(pageSize=500)


def test_input_rejects_negative_min() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(min=-0.01)
    assert "non-negative" in str(exc_info.value) or "greater" in str(exc_info.value).lower()


def test_input_rejects_negative_max() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(max=-1.0)
    assert "non-negative" in str(exc_info.value) or "greater" in str(exc_info.value).lower()


def test_input_rejects_min_gt_max() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(min=100.0, max=50.0)
    assert "min" in str(exc_info.value).lower() and "max" in str(exc_info.value).lower()


def test_input_accepts_min_eq_max() -> None:
    obj = ListPetsInput(min=100.0, max=100.0)
    assert obj.min == 100.0


# ---------------------------------------------------------------------------
# 输入校验:NaN / Infinity / 类型错误 / 未知字段
# ---------------------------------------------------------------------------


def test_input_rejects_nan_min() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"min": float("nan")})


def test_input_rejects_nan_max() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"max": float("nan")})


def test_input_rejects_infinity_min() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"min": float("inf")})


def test_input_rejects_negative_infinity_max() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"max": float("-inf")})


def test_input_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput.model_validate({"petName": "旺财"})
    assert "extra" in str(exc_info.value).lower() or "unexpected" in str(exc_info.value).lower()


def test_input_rejects_wrong_type_page() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"page": "abc"})


def test_input_rejects_wrong_type_pagesize() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"pageSize": [1, 2]})


def test_input_rejects_wrong_type_min() -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"min": "not-a-number"})


def test_input_rejects_wrong_type_species() -> None:
    # species 期望字符串枚举,传 list 应失败
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"species": ["犬"]})


# ---------------------------------------------------------------------------
# to_query_params 行为
# ---------------------------------------------------------------------------


def test_to_query_params_drops_none() -> None:
    obj = ListPetsInput(page=1, pageSize=10)
    params = obj.to_query_params()
    assert params == {"page": "1", "pageSize": "10"}


def test_to_query_params_drops_empty_string() -> None:
    obj = ListPetsInput(q="", name="旺财")
    params = obj.to_query_params()
    assert "q" not in params
    assert params["name"] == "旺财"


def test_to_query_params_uses_camel_aliases() -> None:
    obj = ListPetsInput(
        ownerName="张三",
        ownerPhone="13800001111",
        sortBy="totalCost",
        pageSize=10,
        species="犬",
    )
    params = obj.to_query_params()
    assert "ownerName" in params
    assert "ownerPhone" in params
    assert "sortBy" in params
    assert "pageSize" in params
    assert "owner_name" not in params
    assert "owner_phone" not in params


def test_to_query_params_float_integer_form() -> None:
    # min=100.0 应转为 "100" 而非 "100.0"
    obj = ListPetsInput(min=100.0, max=200.5)
    params = obj.to_query_params()
    assert params["min"] == "100"
    assert params["max"] == "200.5"


# ---------------------------------------------------------------------------
# 输出模型:兼容 Go 的 null / 数组
# ---------------------------------------------------------------------------


def test_output_model_handles_null_records_and_charges(sample_pet_dict: dict) -> None:
    payload = dict(sample_pet_dict)
    payload["records"] = None
    payload["charges"] = None
    item = PetItem.model_validate(payload)
    assert item.records is None
    assert item.charges is None


def test_output_model_handles_array_records_and_charges(sample_pet_dict: dict) -> None:
    item = PetItem.model_validate(sample_pet_dict)
    assert item.records is not None
    assert len(item.records) == 1
    assert item.records[0].diagnosis == "急性肠胃炎"
    assert item.charges is not None
    assert len(item.charges) == 1
    assert item.charges[0].item == "血常规检查"


def test_output_model_camelcase_aliases(sample_pet_dict: dict) -> None:
    item = PetItem.model_validate(sample_pet_dict)
    # Python 属性 snake_case
    assert item.owner_name == "张三"
    assert item.owner_phone == "13800001111"
    assert item.chip_no == "CHIP-90001"
    assert item.age_months == 36
    assert item.total_cost == 1040.0
    assert item.visit_count == 1


def test_output_model_dump_by_alias(sample_pet_dict: dict) -> None:
    item = PetItem.model_validate(sample_pet_dict)
    dumped = item.model_dump(by_alias=True)
    assert "ownerName" in dumped
    assert "ownerPhone" in dumped
    assert "chipNo" in dumped
    assert "ageMonths" in dumped
    assert "totalCost" in dumped
    assert "visitCount" in dumped
    assert "createdAt" in dumped


def test_list_pets_output_model_fields() -> None:
    fields = set(ListPetsOutput.model_fields.keys())
    assert fields == {"items", "total", "page", "page_size", "total_pages", "total_cost"}


def test_list_pets_output_dump_by_alias(sample_list_response: dict) -> None:
    output = ListPetsOutput.model_validate(sample_list_response["data"])
    dumped = output.model_dump(by_alias=True)
    assert set(dumped.keys()) == {
        "items", "total", "page", "pageSize", "totalPages", "totalCost"
    }


def test_output_model_ignores_extra_fields(sample_pet_dict: dict) -> None:
    payload = dict(sample_pet_dict)
    payload["unknownField"] = "should be ignored"
    # 不应抛错
    item = PetItem.model_validate(payload)
    assert item.id == "PET-000001"
