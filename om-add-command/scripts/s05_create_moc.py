"""
[Skill-05] 创建原子对象
通过 omres-cli moc add-name 和 omres-cli moc select-name 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING
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


def create_moc(
    context: WorkflowContext,
    mocName: str = None,
    mocDescCh: str = None,
    mocDescEn: str = None,
    mocTypeId: int = 2,
    moduleId: int = None,
    w3Num: str = None
) -> dict:
    """
    创建原子对象(MOC) — 通过omres-cli moc add-name

    Args:
        context: 工作流上下文
        mocName: 原子对象名称
        mocDescCh: 中文描述
        mocDescEn: 英文描述
        mocTypeId: 对象类型ID (2=配置, 3=状态, 4=操作)
        moduleId: 模块ID
        w3Num: 工号

    Returns:
        dict: 创建结果

    Raises:
        StepExecutionError: 创建失败时抛出
    """
    mocName = mocName or context.moc_name
    mocDescCh = mocDescCh or context.moc_desc_ch
    mocDescEn = mocDescEn or context.moc_desc_en
    moduleId = moduleId or context.moduleId
    w3Num = w3Num or context.w3Num

    if not mocName:
        raise StepExecutionError(
            step_name="create_moc",
            message="原子对象名称不能为空",
            context_state=context.state
        )

    if not moduleId:
        raise StepExecutionError(
            step_name="create_moc",
            message="moduleId不能为空",
            context_state=context.state
        )

    if not context.get_state("is_logged_in"):
        raise StepExecutionError(
            step_name="create_moc",
            message="未登录或登录已失效",
            context_state=context.state
        )

    task_id = context.get_required_state("taskId")

    body = json.dumps({
        "mocName": mocName,
        "mocDescCh": mocDescCh,
        "mocDescEn": mocDescEn,
        "mocTypeId": str(mocTypeId),
        "isProcessReport": "",
        "m2k": "1",
        "maxRecordNum": "256",
        "minRecordNum": "0",
        "recUpgMode": "Auto",
        "taskId": str(task_id),
        "moduleId": str(moduleId),
        "w3Num": w3Num or context.userName
    }, ensure_ascii=False)

    print(f"  [DEBUG] create_moc: mocName={mocName}, taskId={task_id}, moduleId={moduleId}")

    result = _run_cli(
        context,
        ["moc", "add-name", "--body", body],
        step_name="create_moc",
        timeout=60
    )

    # 更新上下文状态
    context.set_state("mocName", mocName)
    context.set_state("mocTypeId", mocTypeId)
    context.set_state("moc_created", True)

    # 从响应中获取mocId
    moc_id = result.get("data", {}).get("mocId") if isinstance(result.get("data"), dict) else result.get("mocId")
    if moc_id:
        context.set_state("mocId", moc_id)
    else:
        # 查询获取mocId
        query_result = query_moc_list(context, moduleId)
        moc_list = query_result.get("data", [])
        for moc in moc_list:
            if moc.get("mocName") == mocName:
                context.set_state("mocId", moc.get("mocId"))
                break

    return result


def query_moc_list(context: WorkflowContext, moduleId: int = None) -> dict:
    """
    查询原子对象列表 — 通过omres-cli moc select-name

    Args:
        context: 工作流上下文
        moduleId: 模块ID

    Returns:
        dict: 包含moc列表的响应

    Raises:
        StepExecutionError: 查询失败时抛出
    """
    moduleId = moduleId or context.moduleId
    task_id = context.get_required_state("taskId")

    body = json.dumps({
        "taskId": str(task_id),
        "moduleId": str(moduleId)
    }, ensure_ascii=False)

    print(f"  [DEBUG] query_moc_list: taskId={task_id}, moduleId={moduleId}")

    result = _run_cli(
        context,
        ["moc", "select-name", "--body", body],
        step_name="query_moc_list",
        timeout=60
    )

    # 查找当前创建的mocId
    moc_name = context.get_state("mocName")
    if moc_name and result.get("data"):
        for moc in result.get("data", []):
            if moc.get("mocName") == moc_name:
                context.set_state("mocId", moc.get("mocId"))
                break

    context.set_state("moc_list", result.get("data", []))

    return result


if __name__ == "__main__":
    from context import create_context
    from s01_login import ensure_authenticated
    from s02_create_project import create_project

    ctx = create_context(
        taskName="TEST_ZL0605",
        userName="z00847484",
        moduleId=5,
        moc_name="HLBAAA",
        moc_desc_ch="HTTP负载均衡配置",
        moc_desc_en="HTTP Load Balance AAA Configuration"
    )
    ensure_authenticated(ctx)
    create_project(ctx)
    result = create_moc(ctx)
    print(f"创建原子对象结果: {result}")
