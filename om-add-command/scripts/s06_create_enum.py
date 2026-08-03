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

if TYPE_CHECKING:
    from typing import Optional


def _find_omres_cli() -> str:
    """查找omres-cli可执行文件路径"""
    cli_path = shutil.which("omres-cli") or shutil.which("omres-cli.exe")
    if cli_path:
        return cli_path

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(project_root, "omres-cli", "omres-cli", "omres-cli.exe"),
        os.path.join(project_root, "omres-cli", "omres-cli"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    cwd = os.getcwd()
    for _ in range(3):
        candidate = os.path.join(cwd, "omres-cli", "omres-cli", "omres-cli.exe")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    return "omres-cli"


def _run_cli(context: WorkflowContext, cmd: list, step_name: str, timeout: int = 60) -> dict:
    """
    执行omres-cli命令并解析JSON-RPC 2.0输出

    Args:
        context: 工作流上下文
        cmd: 命令列表
        step_name: 步骤名称（用于错误提示）
        timeout: 超时秒数

    Returns:
        dict: JSON-RPC result字段内容

    Raises:
        StepExecutionError: 执行失败时抛出
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', timeout=timeout
        )
    except FileNotFoundError:
        raise StepExecutionError(
            step_name=step_name,
            message=f"omres-cli未找到: {cmd[0]}，请确认omres-cli已安装或在项目目录下",
            context_state=context.state
        )
    except subprocess.TimeoutExpired:
        raise StepExecutionError(
            step_name=step_name,
            message=f"omres-cli命令执行超时",
            context_state=context.state
        )

    output = proc.stdout.strip() if proc.stdout else ""

    if not output:
        stderr_msg = proc.stderr.strip() if proc.stderr else ""
        raise StepExecutionError(
            step_name=step_name,
            message=f"命令返回为空, stderr: {stderr_msg[:200]}",
            context_state=context.state
        )

    try:
        rpc_output = json.loads(output)
    except json.JSONDecodeError as e:
        raise StepExecutionError(
            step_name=step_name,
            message=f"响应JSON解析失败: {e}, 原始响应: {output[:200]}",
            context_state=context.state
        )

    # 检查JSON-RPC错误
    if "error" in rpc_output:
        error = rpc_output["error"]
        error_msg = error.get("message", "未知错误")
        if "data" in error:
            data = error["data"]
            if isinstance(data, dict):
                error_msg = data.get("msg", data.get("message", error_msg))
            elif isinstance(data, str):
                error_msg = data
        raise StepExecutionError(
            step_name=step_name,
            message=f"执行失败: {error_msg}",
            context_state=context.state
        )

    return rpc_output.get("result", {})


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

    cmd = [cli_path, "datatype", "add", "--body", json.dumps(payload, ensure_ascii=False)]
    if context.base_url:
        cmd.extend(["--server", context.base_url])

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
        if context.base_url:
            query_cmd.extend(["--server", context.base_url])

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
    if context.base_url:
        cmd.extend(["--server", context.base_url])

    return _run_cli(context, cmd, "add_enum_item")


if __name__ == "__main__":
    from context import create_context, get_windows_credential
    from s01_login import login
    from s02_create_project import create_project

    ctx = create_context(
        taskName="TEST_ZL0605",
        userName="z00847484",
        passwd=get_windows_credential("omtool.rnd.huawei.com", "z00847484"),
        moduleId=5
    )
    login(ctx)
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
