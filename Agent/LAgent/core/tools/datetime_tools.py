"""日期时间工具：通过本地命令获取系统日期时间。"""
from __future__ import annotations

import subprocess


def get_date() -> str:
    """调用本地 date 命令获取日期和时间（Windows: date /t & time /t）"""
    try:
        out = subprocess.run(
            ["cmd", "/c", "date /t & time /t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = out.stdout.strip()
        if out.returncode != 0 or not result:
            return f"命令执行失败: {out.stderr.strip()}"
        return result
    except Exception as e:
        return f"调用异常: {e}"


def is_leap_year(year: int) -> str:
    """用 Python 判断闰年：
    能被 4 整除且不能被 100 整除，或者能被 400 整除 → 闰年"""
    year = int(year)
    leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    if leap:
        return f"{year} 年是闰年，2 月有 29 天。"
    return f"{year} 年不是闰年（{year} ÷ 4 = {year // 4} 余 {year % 4}），2 月有 28 天。"
