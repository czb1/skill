"""
[Skill-06/07] 创建自定义枚举类型
通过 omres-cli datatype add / enum-add / query-all 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, List, Dict, Any
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, run_cli as _run_cli

if TYPE_CHECKING:
    from typing import Optional


def create_enum_type(
    context: WorkflowContext,
    dataType: str,
    moduleId: int = None,
    enumItems: List[Dict[str, Any]] = None,
    rangeStr: str = None
) -> dict:
    """
    创建自定义枚举类型并添加枚举值

    Args:
        context: 工作流上下文
        dataType: 枚举类型名称 (如 "HlbPeerFlowCtrlSwitch")
        moduleId: 模块ID
        enumItems: 枚举项列表 [{"enumItemName": "OFF", "enumItemValue": 0}, ...]
        rangeStr: 范围字符串（可自动解析为枚举项）

    Returns:
        dict: 包含cdtId的响应

    Raises:
        StepExecutionError: 创建失败时抛出
    """
    moduleId = moduleId or context.moduleId
    task_id = context.get_required_state("taskId")
    project_id = context.get_state("projectId") or task_id

    if not dataType:
        raise StepExecutionError(
            step_name="create_enum_type",
            message="枚举类型名称不能为空",
            context_state=context.state
        )

    if not moduleId:
        raise StepExecutionError(
            step_name="create_enum_type",
            message="moduleId不能为空",
            context_state=context.state
        )

    cli_path = _find_omres_cli()

    # 步骤1: 创建枚举类型 (omres-cli datatype add)
    payload = {
        "cdt": {
            "dataType": dataType,
            "dataTypeType": "ENUM_TYPE",
            "moduleId": moduleId,
            "isExtendedEnum": 0,
            "belongExtendedEnum": "",
            "extendedEnumItem": ""
        },
        "projectId": project_id
    }

    # server 由 _run_cli 统一决定（以 omres-cli 登录态里的 server 为准）
    cmd = [cli_path, "datatype", "add", "--body", json.dumps(payload, ensure_ascii=False)]

    result = _run_cli(context, cmd, "create_enum_type")

    # 检查业务状态码
    if isinstance(result, dict):
        if not result.get("status"):
            raise StepExecutionError(
                step_name="create_enum_type",
                message=f"创建枚举类型失败: {result.get('message', '未知错误')}",
                context_state=context.state
            )

    # 提取cdtId (枚举类型ID)
    cdt_id = result.get("data", {}).get("cdtId") if isinstance(result, dict) else None
    if not cdt_id:
        cdt_id = result.get("cdtId")

    if not cdt_id:
        # API创建成功后，通过queryAll查询获取cdtId
        query_payload = {"projectId": project_id, "type": "enum", "moduleId": moduleId}
        query_cmd = [cli_path, "datatype", "query-all", "--body", json.dumps(query_payload, ensure_ascii=False)]

        query_result = _run_cli(context, query_cmd, "create_enum_type")
        if isinstance(query_result, dict):
            for dt in query_result.get("data", []):
                if dt.get("dataType") == dataType:
                    cdt_id = dt.get("id")
                    break

    if not cdt_id:
        raise StepExecutionError(
            step_name="create_enum_type",
            message=f"未获取到cdtId: {result}",
            context_state=context.state
        )

    context.set_state("cdtId", cdt_id)
    context.set_state("enumTypeName", dataType)

    # 步骤2: 添加枚举值
    # 如果enumItems为空但rangeStr不为空，自动从rangeStr解析生成枚举项
    if not enumItems and rangeStr:
        import re
        enumItems = []
        # 首先尝试匹配 "名称 (值)" 格式
        pattern_with_value = r'([A-Z_]+)[:：]?\s*[（(]\s*(\d+)\s*[）)]'
        matches = re.findall(pattern_with_value, rangeStr)
        if matches:
            for idx, (name, value) in enumerate(matches):
                enumItems.append({
                    "enumItemName": name.strip(),
                    "enumItemValue": int(value.strip())
                })
        else:
            # 仅名称格式（如 "SUBID" 或 "PREFIX"），自动从0开始递增
            name_pattern = r'([A-Z_]+)'
            names = re.findall(name_pattern, rangeStr)
            for idx, name in enumerate(names):
                if name.strip():
                    enumItems.append({
                        "enumItemName": name.strip(),
                        "enumItemValue": idx
                    })

    if enumItems:
        for item in enumItems:
            enum_result = add_enum_item(
                context,
                enumItemName=item.get("enumItemName"),
                enumItemValue=item.get("enumItemValue"),
                enumDescCh=item.get("enumDescCh", item.get("enumItemName")),
                enumDescEn=item.get("enumDescEn", item.get("enumItemName")),
                cdtId=cdt_id
            )
            if not enum_result.get("status"):
                raise StepExecutionError(
                    step_name="create_enum_type",
                    message=f"添加枚举值{item.get('enumItemName')}失败: {enum_result.get('message')}",
                    context_state=context.state
                )

    context.set_state("enum_type_created", True)
    return result


def add_enum_item(
    context: WorkflowContext,
    enumItemName: str,
    enumItemValue: int,
    enumDescCh: str = None,
    enumDescEn: str = None,
    cdtId: int = None
) -> dict:
    """
    添加枚举值到已创建的枚举类型（通过omres-cli）

    Args:
        context: 工作流上下文
        enumItemName: 枚举项名称
        enumItemValue: 枚举项值
        enumDescCh: 中文描述
        enumDescEn: 英文描述
        cdtId: 枚举类型ID

    Returns:
        dict: 添加结果
    """
    cdtId = cdtId or context.get_required_state("cdtId")
    moduleId = context.moduleId
    task_id = context.get_required_state("taskId")
    project_id = context.get_state("projectId") or task_id

    cli_path = _find_omres_cli()

    payload = {
        "enumsTable": {
            "enumItemName": enumItemName,
            "enumItemValue": enumItemValue,
            "enumDescCh": enumDescCh or enumItemName,
            "enumDescChSel": False,
            "enumDescEn": enumDescEn or enumItemName,
            "meaningCh": "",
            "meaningChSel": False,
            "extendEnumIds": "",
            "id": "",
            "enumDatatypeNameId": cdtId
        },
        "projectId": project_id,
        "moduleId": moduleId
    }

    cmd = [cli_path, "datatype", "enum-add", "--body", json.dumps(payload, ensure_ascii=False)]

    return _run_cli(context, cmd, "add_enum_item")


if __name__ == "__main__":
    from context import create_context
    from s01_login import ensure_authenticated
    from s02_create_project import create_project

    ctx = create_context(
        taskName="TEST_ZL0605",
        userName="z00847484",
        moduleId=5
    )
    ensure_authenticated(ctx)
    create_project(ctx)

    result = create_enum_type(
        ctx,
        dataType="HlbPeerFlowCtrlSwitch",
        enumItems=[
            {"enumItemName": "OFF", "enumItemValue": 0},
            {"enumItemName": "ON", "enumItemValue": 1}
        ]
    )
    print(f"创建枚举类型结果: {result}")
    print(f"cdtId: {ctx.get_state('cdtId')}")
