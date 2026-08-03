"""
[Skill-03] 上传建模文件
通过 omres-cli upload file 命令实现，替代直接HTTP调用。
"""

import sys
import os
import zipfile
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import json
import shutil
from typing import TYPE_CHECKING, List
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, server_args as _server_args

if TYPE_CHECKING:
    from typing import Optional


def create_zip_package(source_dir: str, output_path: str = None) -> str:
    """
    将om目录下所有Pcf*服务文件夹压缩为ZIP文件

    Args:
        source_dir: om目录基础路径
        output_path: 输出zip路径（默认ZL0605.zip）

    Returns:
        str: 生成的zip包路径
    """
    if not os.path.exists(source_dir):
        raise StepExecutionError(
            step_name="create_zip_package",
            message=f"源目录不存在: {source_dir}"
        )

    if output_path is None:
        output_path = os.path.join(source_dir, "ZL0605.zip")

    if os.path.exists(output_path):
        os.remove(output_path)
        print(f"  已删除旧文件: {output_path}")

    folders_to_zip = []
    for item in os.listdir(source_dir):
        item_path = os.path.join(source_dir, item)
        if os.path.isdir(item_path) and item.startswith("Pcf"):
            folders_to_zip.append(item)

    folders_to_zip.sort()
    print(f"  待压缩文件夹 ({len(folders_to_zip)}个): {folders_to_zip}")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for folder in folders_to_zip:
            folder_path = os.path.join(source_dir, folder)
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.join(folder, os.path.relpath(file_path, folder_path))
                    zipf.write(file_path, arcname)
            print(f"  已添加: {folder}")

    print(f"  ✓ 压缩包创建成功: {output_path}")
    return output_path


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


def upload_file(context: WorkflowContext, source_dir: str = None, file_path: str = None) -> dict:
    """
    上传建模文件(ZL0605.zip)（通过omres-cli）

    Args:
        context: 工作流上下文
        file_path: 文件路径 (可选，从context读取)

    Returns:
        dict: 包含上传路径的响应

    Raises:
        StepExecutionError: 上传失败时抛出
    """
    file_path = file_path or context.get_state("file_path")

    if not file_path:
        raise StepExecutionError(
            step_name="upload_file",
            message="文件路径不能为空",
            context_state=context.state
        )

    if not os.path.exists(file_path):
        raise StepExecutionError(
            step_name="upload_file",
            message=f"文件不存在: {file_path}",
            context_state=context.state
        )

    task_id = context.get_required_state("taskId")

    # 构造omres-cli upload file命令
    cmd = [
        "upload", "file",
        "--taskId", str(task_id),
        "--file", file_path,
    ]

    result = _run_omres_cli(cmd, context, "upload_file", timeout=120)

    # 提取上传路径
    path = result.get("path") or result.get("data", {}).get("path")
    file_name = result.get("fileName") or result.get("data", {}).get("fileName")

    if not path:
        raise StepExecutionError(
            step_name="upload_file",
            message=f"未获取到上传路径: {result}",
            context_state=context.state
        )

    # 更新上下文状态
    context.set_state("upload_path", path)
    context.set_state("upload_fileName", file_name)

    return result


if __name__ == "__main__":
    from context import create_context
    from s01_login import ensure_authenticated
    from s02_create_project import create_project

    ctx = create_context(
        taskName="TEST_ZL0605",
        userName="z00847484",
        file_path="D:/git/26.0/ComConfig/om/ZL0605.zip"
    )
    ensure_authenticated(ctx)
    create_project(ctx)
    result = upload_file(ctx)
    print(f"上传结果: {result}")
    print(f"上传路径: {ctx.get_state('upload_path')}")
