"""
[Skill-04] 解析XML建模文件
通过 omres-cli upload parse-xml 命令实现，替代直接HTTP调用。
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


def _run_omres_cli(cmd: list, context: WorkflowContext, step_name: str, timeout: int = 120) -> dict:
    """
    执行omres-cli命令并解析JSON-RPC输出

    Args:
        cmd: 命令参数列表（不含omres-cli本身）
        context: 工作流上下文
        step_name: 步骤名（用于错误提示）
        timeout: 超时秒数

    Returns:
        dict: JSON-RPC result字段

    Raises:
        StepExecutionError: 执行失败时抛出
    """
    cli_path = _find_omres_cli()

    full_cmd = [cli_path] + cmd

    full_cmd.extend(_server_args(context))

    try:
        proc = subprocess.run(
            full_cmd,
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
            message=f"omres-cli命令执行超时: {' '.join(cmd)}",
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
            message=f"omres-cli执行失败: {error_msg}",
            context_state=context.state
        )

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

        if "status" in result and not result.get("status"):
            raise StepExecutionError(
                step_name=step_name,
                message=f"业务执行失败: {result.get('message', '未知错误')}",
                context_state=context.state
            )

    return result


def parse_xml(context: WorkflowContext, fileName: str = None, path: str = None, flag: bool = False) -> dict:
    """
    解析上传的XML建模文件（通过omres-cli）

    Args:
        context: 工作流上下文
        fileName: 文件名 (可选，从context读取)
        path: 上传后的路径 (可选，从context读取)
        flag: 解析标志 (必须为False)

    Returns:
        dict: 解析结果

    Raises:
        StepExecutionError: 解析失败时抛出
    """
    fileName = fileName or context.get_state("upload_fileName")
    path = path or context.get_state("upload_path")

    if not fileName or not path:
        raise StepExecutionError(
            step_name="parse_xml",
            message="文件名或上传路径不能为空",
            context_state=context.state
        )

    task_id = context.get_required_state("taskId")

    # 构造omres-cli upload parse-xml命令
    body = json.dumps({
        "fileName": fileName,
        "flag": flag,
        "path": path,
        "taskId": task_id
    }, ensure_ascii=False)

    cmd = [
        "upload", "parse-xml",
        "--body", body,
    ]

    result = _run_omres_cli(cmd, context, "parse_xml", timeout=120)

    # 更新上下文状态
    context.set_state("parse_xml_result", result)
    context.set_state("is_parsed", True)

    return result


if __name__ == "__main__":
    from context import create_context
    from s01_login import ensure_authenticated
    from s02_create_project import create_project
    from s03_upload_file import upload_file

    ctx = create_context(
        taskName="TEST_ZL0605",
        userName="z00847484",
        file_path="D:/git/26.0/ComConfig/om/ZL0605.zip"
    )
    ensure_authenticated(ctx)
    create_project(ctx)
    upload_file(ctx)
    result = parse_xml(ctx)
    print(f"解析结果: {result}")
