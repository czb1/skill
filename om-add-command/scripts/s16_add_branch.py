"""
[Skill-16] 分支参数(条件参数)管理
通过 omres-cli command-branch upsert / list、mml-para list、command-para list、datatype enum-query-all 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, Dict, Any, List, Optional
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


def _run_cli(context: WorkflowContext, args: list, step_name: str, timeout: int = 60) -> dict:
    """
    执行omres-cli命令并解析JSON-RPC 2.0输出

    Args:
        context: 工作流上下文
        args: 命令参数列表（不含omres-cli本身）
        step_name: 步骤名称（用于错误信息）
        timeout: 超时秒数

    Returns:
        dict: JSON-RPC result字段

    Raises:
        StepExecutionError: 执行失败时抛出
    """
    cli_path = _find_omres_cli()
    cmd = [cli_path] + args

    if context.base_url:
        cmd.extend(["--server", context.base_url])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', timeout=timeout
        )
    except FileNotFoundError:
        raise StepExecutionError(
            step_name=step_name,
            message=f"omres-cli未找到: {cli_path}，请确认omres-cli已安装或在项目目录下",
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
            message=f"命令执行失败: {error_msg}",
            context_state=context.state
        )

    # 提取result
    result = rpc_output.get("result", {})

    # 检查业务状态码
    if isinstance(result, dict):
        code = result.get("code")
        if code is not None and code != 0:
            raise StepExecutionError(
                step_name=step_name,
                message=f"业务执行失败: {result.get('msg', result.get('message', '未知错误'))}",
                context_state=context.state
            )

        # 兼容旧格式：直接包含status字段
        if "status" in result and not result.get("status"):
            raise StepExecutionError(
                step_name=step_name,
                message=f"业务执行失败: {result.get('message', '未知错误')}",
                context_state=context.state
            )

    return result


def add_command_branch(
    context: WorkflowContext,
    switchCommandParaId: int,
    switchEnumItemId: int,
    childCommandParaDtos: List[Dict[str, Any]] = None,
    commandId: int = None
) -> dict:
    """
    添加命令分支(条件参数) — 通过omres-cli command-branch upsert

    Args:
        context: 工作流上下文
        switchCommandParaId: 切换参数ID (条件参数的mmlParaId)
        switchEnumItemId: 枚举项ID (触发显示的枚举值ID)
        childCommandParaDtos: 子参数列表 (条件满足时显示的参数)
        commandId: 命令ID

    Returns:
        dict: 添加结果
    """
    task_id = context.get_required_state("taskId")
    commandId = commandId or context.get_required_state("commandId")

    payload = {
        "taskId": task_id,
        "commandId": commandId,
        "commandBranchTableId": [],
        "switchCommandParaId": switchCommandParaId,
        "switchCommandParaExtendName": "",
        "switchEnumItemId": switchEnumItemId,
        "childCommandParaDtos": childCommandParaDtos or []
    }

    body = json.dumps(payload, ensure_ascii=False)

    print(f"  [DEBUG] add_command_branch: switchCommandParaId={switchCommandParaId}, switchEnumItemId={switchEnumItemId}")

    result = _run_cli(
        context,
        ["command-branch", "upsert", "--body", body],
        step_name="add_command_branch",
        timeout=60
    )

    print(f"  [DEBUG] add_command_branch成功")

    return result


def query_command_branches(
    context: WorkflowContext,
    commandId: int = None
) -> List[Dict[str, Any]]:
    """
    查询命令的分支参数列表 — 通过omres-cli command-branch list

    Args:
        context: 工作流上下文
        commandId: 命令ID

    Returns:
        List[Dict[str, Any]]: 分支参数列表
    """
    task_id = context.get_required_state("taskId")
    commandId = commandId or context.get_required_state("commandId")

    body = json.dumps({
        "taskId": task_id,
        "commandId": commandId
    }, ensure_ascii=False)

    print(f"  [DEBUG] query_command_branches: commandId={commandId}")

    result = _run_cli(
        context,
        ["command-branch", "list", "--body", body],
        step_name="query_command_branches",
        timeout=60
    )

    branches = result.get("data", [])
    print(f"  [DEBUG] query_command_branches返回 {len(branches)} 条记录")

    return branches


def get_enum_item_id(
    context: WorkflowContext,
    enumTypeId: int,
    enumItemValue: int
) -> int:
    """
    根据枚举类型ID和枚举值获取枚举项ID — 通过omres-cli datatype query-all

    Args:
        context: 工作流上下文
        enumTypeId: 枚举类型ID
        enumItemValue: 枚举值 (如0代表ON)

    Returns:
        int: 枚举项ID
    """
    task_id = context.get_required_state("taskId")
    module_id = context.moduleId

    body = json.dumps({
        "projectId": task_id,
        "type": "enum",
        "moduleId": module_id
    }, ensure_ascii=False)

    print(f"  [DEBUG] get_enum_item_id: enumTypeId={enumTypeId}, enumItemValue={enumItemValue}")

    result = _run_cli(
        context,
        ["datatype", "query-all", "--body", body],
        step_name="get_enum_item_id",
        timeout=60
    )

    enum_list = result.get("data", [])

    for enum_item in enum_list:
        if enum_item.get("id") == enumTypeId:
            extended_enum_items = enum_item.get("extendedEnumItem", "")
            if extended_enum_items:
                items = extended_enum_items.split(",")
                for item in items:
                    parts = item.split("（")
                    if len(parts) == 2:
                        item_name = parts[0]
                        item_value_part = parts[1].rstrip("）")
                        try:
                            item_value = int(item_value_part)
                            if item_value == enumItemValue:
                                print(f"  [DEBUG] 找到枚举项: name={item_name}, value={item_value}, id={enumTypeId}")
                                return item_value
                        except ValueError:
                            continue

    print(f"  [WARNING] 未找到枚举类型{enumTypeId}中值{enumItemValue}对应的枚举项")
    return enumItemValue


def get_mml_para_id_by_field_id(
    context: WorkflowContext,
    fieldId: int
) -> int:
    """
    根据字段ID获取mmlParaId — 通过omres-cli mml-para list

    Args:
        context: 工作流上下文
        fieldId: 字段ID

    Returns:
        int: mmlParaId
    """
    task_id = context.get_required_state("taskId")
    moc_id = context.get_required_state("mocId")

    body = json.dumps({
        "taskId": task_id,
        "mocId": moc_id
    }, ensure_ascii=False)

    print(f"  [DEBUG] get_mml_para_id_by_field_id: fieldId={fieldId}")

    result = _run_cli(
        context,
        ["mml-para", "list", "--body", body],
        step_name="get_mml_para_id_by_field_id",
        timeout=60
    )

    para_list = result.get("data", [])

    for para in para_list:
        if str(para.get("fieldId")) == str(fieldId):
            mml_para_id = para.get("id")
            print(f"  [DEBUG] 找到mmlParaId: {mml_para_id} for fieldId: {fieldId}")
            return mml_para_id

    raise StepExecutionError(
        step_name="get_mml_para_id_by_field_id",
        message=f"未找到字段{fieldId}对应的mmlParaId",
        context_state=context.state
    )


def add_conditional_branch(
    context: WorkflowContext,
    switchFieldName: str,
    triggerEnumValue: int,
    childFieldNames: List[str],
    commandId: int = None
) -> dict:
    """
    添加条件分支参数
    当switchFieldName的值为triggerEnumValue时，显示childFieldNames中的参数

    Args:
        context: 工作流上下文
        switchFieldName: 切换参数字段名 (如ALARMSW)
        triggerEnumValue: 触发值 (如0代表ON)
        childFieldNames: 子参数字段名列表 (如[HIGHTSTR, HIGHTEND, OVERLOADSTR, OVERLOADEND])
        commandId: 命令ID

    Returns:
        dict: 添加结果
    """
    task_id = context.get_required_state("taskId")
    commandId = commandId or context.get_required_state("commandId")

    # 获取switch参数的commandParaId (从commandPara/list获取)
    switch_command_para_id = get_command_para_id_by_name(context, switchFieldName, commandId)

    # 获取触发值的enumItemId
    enum_item_id = get_enum_item_id_by_value(context, switchFieldName, triggerEnumValue, commandId)

    # 构建所有子参数的childCommandParaDtos
    child_command_para_dtos = []
    for child_field_name in childFieldNames:
        child_command_para_id = get_command_para_id_by_name(context, child_field_name, commandId)
        child_command_para_dtos.append({
            "childCommandParaId": child_command_para_id,
            "isChildMustGive": 0,
            "childCommandParaName": child_field_name
        })

    print(f"  [DEBUG] add_command_branch请求:")
    print(f"    - switchCommandParaId: {switch_command_para_id} ({switchFieldName})")
    print(f"    - switchEnumItemId: {enum_item_id} (value={triggerEnumValue})")
    print(f"    - childParams: {[d['childCommandParaName'] for d in child_command_para_dtos]}")

    result = add_command_branch(
        context,
        switchCommandParaId=switch_command_para_id,
        switchEnumItemId=enum_item_id,
        childCommandParaDtos=child_command_para_dtos,
        commandId=commandId
    )

    print(f"  [DEBUG] 分支添加成功: 当{switchFieldName}={triggerEnumValue}时显示{childFieldNames}")

    return result


def get_command_para_id_by_name(
    context: WorkflowContext,
    paraName: str,
    commandId: int
) -> int:
    """
    根据参数名获取commandParaId — 通过omres-cli command-para list

    Args:
        context: 工作流上下文
        paraName: 参数名
        commandId: 命令ID

    Returns:
        int: commandParaId
    """
    task_id = context.get_required_state("taskId")

    body = json.dumps({
        "taskId": task_id,
        "commandId": commandId
    }, ensure_ascii=False)

    result = _run_cli(
        context,
        ["command-para", "list", "--body", body],
        step_name="get_command_para_id_by_name",
        timeout=60
    )

    para_list = result.get("data", [])

    for para in para_list:
        if para.get("mmlParaName") == paraName:
            command_para_id = para.get("id")
            print(f"  [DEBUG] 找到commandParaId: {command_para_id} for paraName: {paraName}")
            return command_para_id

    raise StepExecutionError(
        step_name="get_command_para_id_by_name",
        message=f"未找到参数{paraName}对应的commandParaId",
        context_state=context.state
    )


def get_enum_item_id_by_value(
    context: WorkflowContext,
    paraName: str,
    enumValue: int,
    commandId: int = None
) -> int:
    """
    根据参数名和枚举值获取enumItemId
    1. 先通过 mml-para list 获取字段的 customizeDataTypeId
    2. 再通过 datatype enum-query-all 获取枚举详情找到对应值的 id

    Args:
        context: 工作流上下文
        paraName: 参数名 (如ALARMSW)
        enumValue: 枚举值 (如0代表ON)
        commandId: 命令ID

    Returns:
        int: enumItemId
    """
    task_id = context.get_required_state("taskId")
    moc_id = context.get_required_state("mocId")

    # 第一步：通过 mml-para list 获取字段的 customizeDataTypeId
    para_body = json.dumps({
        "taskId": task_id,
        "mocId": moc_id
    }, ensure_ascii=False)

    print(f"  [DEBUG] get_enum_item_id_by_value第1步: paraName={paraName}")

    para_result = _run_cli(
        context,
        ["mml-para", "list", "--body", para_body],
        step_name="get_enum_item_id_by_value_step1",
        timeout=60
    )

    para_list = para_result.get("data", [])

    customize_data_type_id = None
    for para in para_list:
        if para.get("paraName") == paraName:
            customize_data_type_id = para.get("customizeDataTypeId")
            print(f"  [DEBUG] 找到customizeDataTypeId: {customize_data_type_id} for {paraName}")
            break

    if not customize_data_type_id:
        raise StepExecutionError(
            step_name="get_enum_item_id_by_value",
            message=f"未找到参数{paraName}的customizeDataTypeId",
            context_state=context.state
        )

    # 第二步：通过 datatype enum-query-all 获取枚举详情
    module_id = context.moduleId
    query_body = json.dumps({
        "projectId": task_id,
        "enumDataTypeNameId": customize_data_type_id,
        "moduleId": module_id
    }, ensure_ascii=False)

    print(f"  [DEBUG] get_enum_item_id_by_value第2步: enumDataTypeNameId={customize_data_type_id}")

    query_result = _run_cli(
        context,
        ["datatype", "enum-query-all", "--body", query_body],
        step_name="get_enum_item_id_by_value_step2",
        timeout=60
    )

    print(f"  [DEBUG] enum-query-all响应完成")

    data = query_result.get("data", [])

    for enum_item in data:
        if enum_item.get("enumItemValue") == enumValue:
            enum_item_id = enum_item.get("id")
            print(f"  [DEBUG] 找到enumItemId: {enum_item_id} for {paraName}={enumValue}")
            return enum_item_id

    raise StepExecutionError(
        step_name="get_enum_item_id_by_value",
        message=f"未找到参数{paraName}中值{enumValue}对应的enumItemId",
        context_state=context.state
    )


if __name__ == "__main__":
    from context import create_context
    ctx = create_context(taskName="TEST", userName="test", moduleId=5)
    print("分支参数模块测试需要完整流程")
