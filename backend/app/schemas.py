"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None
    missing_dependencies: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    receipt: dict[str, Any] | None = None
    duplicate: bool = False
    applied: bool = False


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None



class CatalogEntry(BaseModel):
    """数据目录明细结构。"""

    field_0: str | None = None  # 编目编号
    field_1: str | None = None  # 数据集名称
    field_2: str | None = None  # 存储路径
    field_3: str | None = None  # 文件格式
    field_4: str | None = None  # 所属分层
    field_5: str | None = None  # 负责人
    field_6: str | None = None  # 业务域标签
    field_7: str | None = None  # 编目版本

class IngestEntry(BaseModel):
    """接入通道明细结构。"""

    field_0: str | None = None  # 通道编号
    field_1: str | None = None  # 源端类型
    field_2: str | None = None  # 连接参数
    field_3: str | None = None  # 接入模式
    field_4: str | None = None  # 批次大小
    field_5: str | None = None  # 校验规则
    field_6: str | None = None  # 异常处理
    field_7: str | None = None  # 通道负责人

class BronzeEntry(BaseModel):
    """原始表明细结构。"""

    field_0: str | None = None  # 表编号
    field_1: str | None = None  # 来源通道
    field_2: str | None = None  # 目标路径
    field_3: str | None = None  # 保留策略
    field_4: str | None = None  # 分区规则
    field_5: str | None = None  # 文件格式
    field_6: str | None = None  # 压缩类型
    field_7: str | None = None  # 数据校验

class SilverEntry(BaseModel):
    """清洗表明细结构。"""

    field_0: str | None = None  # 表编号
    field_1: str | None = None  # 上游原始表
    field_2: str | None = None  # 清洗脚本
    field_3: str | None = None  # 格式校验
    field_4: str | None = None  # 异常过滤
    field_5: str | None = None  # 去重字段
    field_6: str | None = None  # 输出路径
    field_7: str | None = None  # 调度周期

class GoldEntry(BaseModel):
    """聚合视图明细结构。"""

    field_0: str | None = None  # 视图编号
    field_1: str | None = None  # 上游清洗表
    field_2: str | None = None  # 聚合维度
    field_3: str | None = None  # 度量字段
    field_4: str | None = None  # 聚合函数
    field_5: str | None = None  # 物化策略
    field_6: str | None = None  # 更新频率
    field_7: str | None = None  # 数据新鲜度

class LakezoneEntry(BaseModel):
    """分区方案明细结构。"""

    field_0: str | None = None  # 方案编号
    field_1: str | None = None  # 目标表名
    field_2: str | None = None  # 分区字段
    field_3: str | None = None  # 分区粒度
    field_4: str | None = None  # 保留天数
    field_5: str | None = None  # 冷热分层
    field_6: str | None = None  # 归档路径
    field_7: str | None = None  # 小文件合并

class StreamEntry(BaseModel):
    """流处理作业明细结构。"""

    field_0: str | None = None  # 作业编号
    field_1: str | None = None  # 输入Topic
    field_2: str | None = None  # 处理逻辑
    field_3: str | None = None  # 窗口类型
    field_4: str | None = None  # 窗口时长秒
    field_5: str | None = None  # 水位延迟
    field_6: str | None = None  # 检查点间隔
    field_7: str | None = None  # 输出Topic

class BatchEntry(BaseModel):
    """批处理任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 输入路径
    field_2: str | None = None  # 处理脚本
    field_3: str | None = None  # 输出路径
    field_4: str | None = None  # 分区覆盖
    field_5: str | None = None  # 依赖上游
    field_6: str | None = None  # 重试策略
    field_7: str | None = None  # 超时终止

class SnapshotEntry(BaseModel):
    """快照记录明细结构。"""

    field_0: str | None = None  # 快照编号
    field_1: str | None = None  # 目标表名
    field_2: str | None = None  # 快照时间
    field_3: str | None = None  # 快照大小
    field_4: str | None = None  # 存储介质
    field_5: str | None = None  # 过期策略
    field_6: str | None = None  # 恢复校验
    field_7: str | None = None  # 依赖快照

class GovernEntry(BaseModel):
    """治理规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 治理对象
    field_2: str | None = None  # 治理类型
    field_3: str | None = None  # 触发阈值
    field_4: str | None = None  # 执行动作
    field_5: str | None = None  # 执行频率
    field_6: str | None = None  # 影响范围
    field_7: str | None = None  # 回滚策略

class MetaEntry(BaseModel):
    """元数据条目明细结构。"""

    field_0: str | None = None  # 条目编号
    field_1: str | None = None  # 对象类型
    field_2: str | None = None  # 对象名称
    field_3: str | None = None  # 属性键
    field_4: str | None = None  # 属性值
    field_5: str | None = None  # 数据来源
    field_6: str | None = None  # 更新频率
    field_7: str | None = None  # 版本变更

class LineageEntry(BaseModel):
    """血缘节点明细结构。"""

    field_0: str | None = None  # 节点编号
    field_1: str | None = None  # 节点类型
    field_2: str | None = None  # 节点名称
    field_3: str | None = None  # 上游节点列表
    field_4: str | None = None  # 下游节点列表
    field_5: str | None = None  # 转换说明
    field_6: str | None = None  # 采集时间
    field_7: str | None = None  # 图谱版本

class QualityEntry(BaseModel):
    """质量指标明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 监控对象
    field_2: str | None = None  # 指标类型
    field_3: str | None = None  # 阈值范围
    field_4: str | None = None  # 检测周期
    field_5: str | None = None  # 告警级别
    field_6: str | None = None  # 通知渠道
    field_7: str | None = None  # 恢复策略

class ScheduleEntry(BaseModel):
    """编排计划明细结构。"""

    field_0: str | None = None  # 计划编号
    field_1: str | None = None  # 任务列表
    field_2: str | None = None  # 依赖图定义
    field_3: str | None = None  # 调度表达式
    field_4: str | None = None  # 并发上限
    field_5: str | None = None  # 失败处理
    field_6: str | None = None  # 超时秒数
    field_7: str | None = None  # 通知策略

class BackfillEntry(BaseModel):
    """回填作业明细结构。"""

    field_0: str | None = None  # 作业编号
    field_1: str | None = None  # 目标表名
    field_2: str | None = None  # 回填起始
    field_3: str | None = None  # 回填截止
    field_4: str | None = None  # 分区范围
    field_5: str | None = None  # 并行度
    field_6: str | None = None  # 失败续跑
    field_7: str | None = None  # 完成校验

class SearchEntry(BaseModel):
    """检索索引明细结构。"""

    field_0: str | None = None  # 索引编号
    field_1: str | None = None  # 索引名称
    field_2: str | None = None  # 覆盖表名
    field_3: str | None = None  # 索引字段
    field_4: str | None = None  # 分词策略
    field_5: str | None = None  # 排序权重
    field_6: str | None = None  # 刷新频率
    field_7: str | None = None  # 索引大小

class ResourceEntry(BaseModel):
    """计量记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 计算引擎
    field_2: str | None = None  # 作业名称
    field_3: str | None = None  # CPU核秒
    field_4: str | None = None  # 内存GB秒
    field_5: str | None = None  # 存储占用GB
    field_6: str | None = None  # 扫描数据量
    field_7: str | None = None  # 计费周期

class AuditEntry(BaseModel):
    """审计日志明细结构。"""

    field_0: str | None = None  # 日志编号
    field_1: str | None = None  # 操作类型
    field_2: str | None = None  # 操作对象
    field_3: str | None = None  # 操作详情
    field_4: str | None = None  # 操作来源
    field_5: str | None = None  # 执行时间
    field_6: str | None = None  # 影响行数
    field_7: str | None = None  # 回滚能力
