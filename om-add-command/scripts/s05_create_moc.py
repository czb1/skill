"""
[Skill-05] 创建原子对象
通过 omres-cli moc add-name 和 omres-cli moc select-name 命令实现，替代直接HTTP调用。

关于「原子对象已存在」：
上传的建模文件里如果已经有同名对象（例如仓库里已存在
`.../modules/PCFDLB/DLBSCTPBUFFALM.xml`），解析阶段就会把它导入工程，
此时再 `moc add-name` 会被后端判为重复而失败。这是**正常情况**，不是错误：
需求本来就要求用这个名字建模，改名或换工程都不对。
因此本步骤是幂等的——先查是否已存在，存在就直接复用它的 mocId 继续后续步骤
（字段/枚举/命令都会在这个对象上增量修改），只有确实不存在时才创建。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING
from context import WorkflowContext, StepExecutionError
from omres_cli import (
    find_omres_cli as _find_omres_cli,
    run_cli as _run_cli,
    DEFAULT_TIMEOUT as _DEFAULT_TIMEOUT,
    is_duplicate_error,
    iter_data_records as _iter_data_records,
    pick_value as _pick_value,
)

if TYPE_CHECKING:
    from typing import Optional


def find_moc_by_name(context: WorkflowContext, mocName: str, moduleId: int = None) -> dict:
    """
    在当前工程里查找同名原子对象

    建模文件解析后，文件里已有的对象就在这个列表里，可以直接复用。

    Args:
        context: 工作流上下文
        mocName: 原子对象名称
        moduleId: 模块ID

    Returns:
        dict: 命中的 moc 条目；没有则返回空 dict
    """
    if not mocName:
        return {}

    try:
        result = query_moc_list(context, moduleId, match_name=mocName)
    except StepExecutionError as e:
        # 查询失败不应该掩盖真正的创建流程，交给调用方按原逻辑继续
        print(f"  [WARNING] 查询原子对象列表失败，无法判断 {mocName} 是否已存在: {e.message}")
        return {}

    for moc in _iter_data_records(result):
        if _pick_value(moc, ("mocName", "name")) == mocName:
            return moc
    return {}


def _adopt_existing_moc(context: WorkflowContext, moc: dict, mocTypeId: int) -> dict:
    """把已存在的原子对象登记到上下文，后续步骤按增量修改处理"""
    moc_name = _pick_value(moc, ("mocName", "name"))
    moc_id = _pick_value(moc, ("mocId", "id"))

    context.set_state("mocName", moc_name)
    context.set_state("mocTypeId", moc.get("mocTypeId", mocTypeId))
    context.set_state("mocId", moc_id)
    context.set_state("moc_created", False)
    context.set_state("moc_reused", True)

    print(
        f"  [INFO] 原子对象 {moc_name} 已存在于工程中（建模文件解析导入），"
        f"直接复用 mocId={moc_id} 继续增量建模，不重复创建"
    )

    return {"status": True, "reused": True, "data": {"mocId": moc_id}, "message": "复用已存在的原子对象"}


def create_moc(
    context: WorkflowContext,
    mocName: str = None,
    mocDescCh: str = None,
    mocDescEn: str = None,
    mocTypeId: int = 2,
    moduleId: int = None,
    w3Num: str = None,
    reuse_if_exists: bool = True
) -> dict:
    """
    创建原子对象(MOC) — 通过omres-cli moc add-name；同名对象已存在时自动复用

    Args:
        context: 工作流上下文
        mocName: 原子对象名称
        mocDescCh: 中文描述
        mocDescEn: 英文描述
        mocTypeId: 对象类型ID (2=配置, 3=状态, 4=操作)
        moduleId: 模块ID
        w3Num: 工号
        reuse_if_exists: 同名对象已存在时复用而不是报错（默认True）。
            置为False可恢复「必须是新对象」的严格行为

    Returns:
        dict: 创建结果；复用已存在对象时额外带 reused=True

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

    # 建模文件里已有同名对象时，解析阶段已经把它导入工程，直接复用
    if reuse_if_exists:
        existing = find_moc_by_name(context, mocName, moduleId)
        if existing:
            return _adopt_existing_moc(context, existing, mocTypeId)

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

    try:
        result = _run_cli(
            context,
            ["moc", "add-name", "--body", body],
            step_name="create_moc",
            timeout=_DEFAULT_TIMEOUT
        )
    except StepExecutionError as e:
        # 预查之后仍报重复：可能是预查时列表还没刷新，或并发导入。再查一次并复用
        if not (reuse_if_exists and is_duplicate_error(e.message)):
            raise
        existing = find_moc_by_name(context, mocName, moduleId)
        if not existing:
            raise
        print(f"  [INFO] 创建 {mocName} 时后端报「已存在」: {e.message}")
        return _adopt_existing_moc(context, existing, mocTypeId)

    # 更新上下文状态
    context.set_state("mocName", mocName)
    context.set_state("mocTypeId", mocTypeId)
    context.set_state("moc_created", True)
    context.set_state("moc_reused", False)

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


def query_moc_list(context: WorkflowContext, moduleId: int = None, match_name: str = None) -> dict:
    """
    查询原子对象列表 — 通过omres-cli moc select-name

    Args:
        context: 工作流上下文
        moduleId: 模块ID
        match_name: 只用于回填mocId的名称；默认用上下文里的mocName。
            传入时不会覆盖 context 的 mocName/mocId（供存在性预查使用）

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
        timeout=_DEFAULT_TIMEOUT
    )

    moc_list = _iter_data_records(result)
    context.set_state("moc_list", moc_list)

    # 预查模式（显式传 match_name）只返回数据，不改写上下文
    if match_name:
        return result

    # 查找当前创建的mocId
    moc_name = context.get_state("mocName")
    if moc_name:
        for moc in moc_list:
            if _pick_value(moc, ("mocName", "name")) == moc_name:
                context.set_state("mocId", _pick_value(moc, ("mocId", "id")))
                break

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
