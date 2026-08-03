"""
[Skill-12/13/14] MML命令方法管理
通过 omres-cli method add-name / update-name / delete-name / select-info 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, List
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, run_cli as _run_cli, DEFAULT_TIMEOUT as _DEFAULT_TIMEOUT

if TYPE_CHECKING:
    from typing import Optional


def add_method(
    context: WorkflowContext,
    commandType: str,
    mocTypeId: int = 2,
    mocId: int = None
) -> dict:
    """
    注册MML命令方法 — 通过omres-cli method add-name

    Args:
        context: 工作流上下文
        commandType: 命令类型 (create/createorset/get-config/get)
        mocTypeId: 对象类型ID (2=配置, 3=状态, 4=操作)
        mocId: 原子对象ID

    Returns:
        dict: 包含methodId的响应
    """
    mocId = mocId or context.get_required_state("mocId")
    task_id = context.get_required_state("taskId")

    if not commandType:
        raise StepExecutionError(
            step_name="add_method",
            message="commandType不能为空",
            context_state=context.state
        )

    body = json.dumps({
        "taskId": str(task_id),
        "mocId": mocId,
        "commandType": commandType,
        "mocTypeId": mocTypeId
    }, ensure_ascii=False)

    print(f"  [DEBUG] add_method: commandType={commandType}, mocId={mocId}, taskId={task_id}")

    result = _run_cli(
        context,
        ["method", "add-name", "--body", body],
        step_name="add_method",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state(f"method_added_{commandType}", True)
    context.set_state("last_commandType", commandType)

    # 方法创建后，查询获取methodId
    query_result = query_method_info(context, mocId)
    if isinstance(query_result, dict) and query_result.get("data"):
        for method in query_result.get("data", []):
            if method.get("commandType") == commandType:
                context.set_state("methodId", method.get("methodId"))
                break

    return result


def update_method_name(
    context: WorkflowContext,
    commandType: str,
    mmlCommandName: str,
    methodId: int = None,
    moduleName: str = None,
    w3Num: str = None
) -> dict:
    """
    更新方法名(绑定命令名) — 通过omres-cli method update-name

    Args:
        context: 工作流上下文
        commandType: 命令类型
        mmlCommandName: MML命令名称 (如 "SET HLBAAA")
        methodId: 方法ID
        moduleName: 模块名称
        w3Num: 工号

    Returns:
        dict: 更新结果
    """
    methodId = methodId or context.get_required_state("methodId")
    task_id = context.get_required_state("taskId")
    moduleName = moduleName or context.moduleName or "PCFNCS"
    w3Num = w3Num or context.w3Num or context.userName

    body = json.dumps({
        "commandType": commandType,
        "mmlCommandName": mmlCommandName,
        "methodId": methodId,
        "taskId": str(task_id),
        "w3Num": w3Num,
        "moduleName": moduleName
    }, ensure_ascii=False)

    print(f"  [DEBUG] update_method_name: commandType={commandType}, mmlCommandName={mmlCommandName}, methodId={methodId}")
    print(f"  [DEBUG] 注意: commandType应该用原始值如'createorset', 'get-config'")

    result = _run_cli(
        context,
        ["method", "update-name", "--body", body],
        step_name="update_method_name",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state("mmlCommandName", mmlCommandName)
    context.set_state("method_updated", True)

    return result


def delete_methods(context: WorkflowContext, methodIds: List[int], moduleName: str = None) -> dict:
    """
    删除多余方法 — 通过omres-cli method delete-name

    Args:
        context: 工作流上下文
        methodIds: 要删除的methodId列表
        moduleName: 模块名称

    Returns:
        dict: 删除结果
    """
    task_id = context.get_required_state("taskId")
    moduleName = moduleName or context.moduleName or "PCFNCS"

    if not methodIds:
        return {"status": True, "message": "无需要删除的方法"}

    body = json.dumps({
        "taskId": str(task_id),
        "moduleName": moduleName,
        "methodIds": ",".join(str(mid) for mid in methodIds)
    }, ensure_ascii=False)

    print(f"  [DEBUG] delete_methods: methodIds={methodIds}, taskId={task_id}")

    result = _run_cli(
        context,
        ["method", "delete-name", "--body", body],
        step_name="delete_methods",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state("methods_deleted", True)

    return result


def query_method_info(context: WorkflowContext, mocId: int = None) -> dict:
    """
    查询方法信息 — 通过omres-cli method select-info

    Args:
        context: 工作流上下文
        mocId: 原子对象ID

    Returns:
        dict: 包含方法列表的响应
    """
    mocId = mocId or context.get_required_state("mocId")
    task_id = context.get_required_state("taskId")

    body = json.dumps({
        "taskId": str(task_id),
        "mocId": mocId
    }, ensure_ascii=False)

    print(f"  [DEBUG] query_method_info: taskId={task_id}, mocId={mocId}")

    result = _run_cli(
        context,
        ["method", "select-info", "--body", body],
        step_name="query_method_info",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state("method_list", result.get("data", []))

    return result


if __name__ == "__main__":
    from context import create_context
    ctx = create_context(taskName="TEST", userName="test", moduleId=5)
    print("方法管理模块测试需要完整流程")
