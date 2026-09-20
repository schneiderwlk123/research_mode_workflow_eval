"""评测器统一抽象契约。"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias

MetricValue: TypeAlias = int | float | str


class EvaluationStatus(StrEnum):
    """单项评测的计算状态。"""

    COMPUTED = "computed"
    NOT_COMPUTABLE = "not_computable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """统一的单项评测结果。

    `NOT_COMPUTABLE` 用于输入字段或外部基准不足的情况，避免把缺失值误记为零分；
    `ERROR` 用于评测器自身无法完成计算的隔离结果。元数据会复制为只读映射，防止
    下游聚合过程中意外改写已经产生的评测证据。
    """

    metric_name: str
    evaluator_version: str
    value: MetricValue | None
    status: EvaluationStatus
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric_name.strip():
            raise ValueError("metric_name 不能为空")
        if not self.evaluator_version.strip():
            raise ValueError("evaluator_version 不能为空")

        if self.status is EvaluationStatus.COMPUTED:
            if isinstance(self.value, str):
                if not self.value.strip():
                    raise ValueError("computed 状态的字符串指标值不能为空")
            elif (
                self.value is None
                or isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
                or not isfinite(float(self.value))
            ):
                raise ValueError("computed 状态必须携带有限数值或非空字符串")
        elif self.value is not None:
            raise ValueError("非 computed 状态不得携带数值，避免误参与聚合")

        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class BaseEvaluator(ABC):
    """所有指标评测器必须实现的最小接口。

    输入是规范化 Trace 的只读视图。具体评测器不得修改该映射，并应在字段不足时返回
    `NOT_COMPUTABLE`，而不是猜测、补零或静默丢弃样本。
    """

    @property
    @abstractmethod
    def metric_name(self) -> str:
        """返回跨运行保持稳定的指标标识。"""

    @property
    @abstractmethod
    def evaluator_version(self) -> str:
        """返回能够区分计算口径的评测器版本。"""

    @abstractmethod
    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        """对一个规范化 Trace 计算单项结果，且不得修改输入。"""
