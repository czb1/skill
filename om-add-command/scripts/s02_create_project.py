"""
[Skill-02] 创建工程
通过 omres-cli task create 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, server_args as _server_args

if TYPE_CHECKING:
    from typing import Optional


def create_project(context: WorkflowContext, taskName: str = None, neType: str = None, productType: str = None) -> dict:
    """
    创建新工程（通过omres-cli）

    Args:
        context: 工作流上下文
        taskName: 工程名称 (可选，从context读取)
        neType: 网元类型 (可选，默认UPCF)
        productType: 产品类型 (可选，默认0)

    Returns:
        dict: 包含taskId的响应

    Raises:
        StepExecutionError: 创建失败时抛出
    """
    taskName = taskName or context.taskName
    neType = neType or context.neType or "UPCF"
    productType = productType or context.productType or "0"

    if not taskName:
        raise StepExecutionError(
            step_name="create_project",
            message="工程名称不能为空",
            context_state=context.state
        )

    if not context.get_state("is_logged_in"):
        raise StepExecutionError(
            step_name="create_project",
            message="未登录或登录已失效",
            context_state=context.state
        )

    cli_path = _find_omres_cli()

    body = json.dumps({
        "taskName": taskName,
        "neType": neType,
        "productType": productType
    }, ensure_ascii=False)

    cmd = [
        cli_path, "task", "create",
        "--body", body,
    ]

    cmd.extend(_server_args(context))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', timeout=120
        )
    except FileNotFoundError:
        raise StepExecutionError(
            step_name="create_project",
            message=f"omres-cli未找到: {cli_path}，请确认omres-cli已安装或在项目目录下",
            context_state=context.state
        )
    except subprocess.TimeoutExpired:
        raise StepExecutionError(
            step_name="create_project",
            message="omres-cli task create 执行超时",
            context_state=context.state
        )

    output = proc.stdout.strip() if proc.stdout else ""

    if not output:
        stderr_msg = proc.stderr.strip() if proc.stderr else ""
        raise StepExecutionError(
            step_name="create_project",
            message=f"创建工程返回为空, stderr: {stderr_msg[:200]}",
            context_state=context.state
        )

    try:
        rpc_output = json.loads(output)
    except json.JSONDecodeError as e:
        raise StepExecutionError(
            step_name="create_project",
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
            step_name="create_project",
            message=f"创建工程失败: {error_msg}",
            context_state=context.state
        )

    # 提取result
    result = rpc_output.get("result", {})

    # 检查业务状态码
    if isinstance(result, dict):
        code = result.get("code")
        if code is not None and code != 0:
            raise StepExecutionError(
                step_name="create_project",
                message=f"创建工程失败: {result.get('msg', result.get('message', '未知错误'))}",
                context_state=context.state
            )

        # 兼容旧格式：直接包含status字段
        if "status" in result and not result.get("status"):
            raise StepExecutionError(
                step_name="create_project",
                message=f"创建工程失败: {result.get('message', '未知错误')}",
                context_state=context.state
            )

    # 提取taskId (通常在extendData字段)
    task_id = result.get("extendData") if isinstance(result, dict) else None
    if not task_id:
        raise StepExecutionError(
            step_name="create_project",
            message=f"未获取到taskId: {result}",
            context_state=context.state
        )

    # 更新上下文状态
    context.set_state("taskId", task_id)
    context.set_state("taskName", taskName)
    context.set_state("neType", neType)
    context.set_state("projectId", task_id)

    return result


if __name__ == "__main__":
    from context import create_context
    from s01_login import ensure_authenticated
    
    ctx = create_context(taskName="TEST_ZL0605", userName="z00847484")
    ensure_authenticated(ctx)
    result = create_project(ctx)
    print(f"创建工程结果: {result}")
    print(f"taskId: {ctx.get_state('taskId')}")
