# Evaluator Foundation Plan

## Scope

建立首批可执行项目骨架与统一评测器契约，不实现任何具体评测指标，也不解析真实 Trace。

## Design decisions

- 保持仓库约定的扁平 `src/` 布局，运行时从 `src` 导入 `evaluators` 等包。
- `BaseEvaluator` 仅依赖规范化后的只读映射，避免评测阶段修改原始 Trace。
- 所有评测结果使用不可变 `EvaluationResult`，以 `value=None` 明确表示不可计算。
- 指标名和评测器版本由具体实现以只读属性声明，保证聚合结果可追溯、可复现。
- 本批次只引入开发依赖 `pytest`、`ruff` 和 `mypy`，不引入运行时第三方依赖。

## Files and tasks

### Task 1: Configure the Python toolchain

Files:

- `pyproject.toml`
- `README.md`

RED:

- 运行尚未配置的测试命令，确认项目缺少测试收集和导入约定。

GREEN:

- 配置 Python 3.11+、pytest、Ruff 和 mypy。
- 在 README 中记录安装、测试、lint、format 和 type-check 命令。

### Task 2: Specify and implement the evaluator contract

Files:

- `tests/evaluators/test_base_evaluator.py`
- `src/evaluators/__init__.py`
- `src/evaluators/base.py`

RED:

- 先编写抽象类不可直接实例化、具体实现必须提供元数据和 `evaluate`、结果不可变且分数受约束的行为测试。
- 运行目标测试并观察因实现缺失而失败。

GREEN:

- 实现满足测试的最小 `BaseEvaluator`、`EvaluationResult` 和 `EvaluationStatus`。
- 运行目标测试直至通过。

### Task 3: Complete package skeleton and navigation docs

Files:

- `src/collectors/__init__.py`
- `src/reporters/__init__.py`
- `src/storage/__init__.py`
- `tests/collectors/__init__.py`
- `tests/evaluators/__init__.py`
- `tests/reporters/__init__.py`
- `tests/storage/__init__.py`
- `docs/arch/code-map.md`
- `docs/specs/metric-development-order.md`

RED:

- 使用包导入和文档自检确认各层入口及指标开发顺序尚未建立。

GREEN:

- 创建最小包入口。
- 记录当前代码地图和逐指标开发顺序。

## Verification

- `python -m pytest`
- `ruff check .`
- `ruff format --check .`
- `mypy src tests`

## Risks and exclusions

- 尚未定义聊天模式与研究模式的规范化 Trace schema；具体字段映射将在对应指标设计阶段确认。
- 尚未定义聚合器、报告器或配置加载器；本批次只保证评测器契约可扩展。
- 不读取、不复制、不提交仓库根目录中的真实 Trace 数据。
