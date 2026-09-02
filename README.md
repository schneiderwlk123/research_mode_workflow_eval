# Full Trace Audit

面向 Agent Workflow 的全链路评测项目。目标是覆盖 Agent 一次运行中的输入、规划、模型调用、工具调用、工具结果利用、异常恢复、最终输出和评测报告。

当前仓库已建立评测器公共契约与开发工具链，具体指标将按确认后的顺序逐项引入。

## 项目结构

```text
configs/                 评测配置样例
data/raw/                本地原始 trace（不提交）
data/processed/          本地规范化数据（不提交）
docs/arch/               架构设计
docs/specs/              评测规范和指标定义
src/collectors/          数据采集与格式适配（待实现）
src/evaluators/          评测器公共契约与逐指标实现
src/reporters/           报告生成（待实现）
src/storage/             存储访问（待实现）
tests/                   与 src 结构对应的行为测试
artifacts/               本地运行产物（不提交）
reports/                 本地评测报告（不提交）
```

## 数据与安全

原始 trace 可能包含用户问题、模型输出、工具参数及外部文档内容。仓库默认忽略所有 JSONL 文件，仅允许显式提交 `tests/fixtures/` 下的脱敏数据。日志和报告中不得写入密钥、Cookie 或未脱敏的个人信息。

协作约定见 [AGENTS.md](AGENTS.md)。正式开发前应先在 `docs/arch/` 和 `docs/specs/` 中确认架构、输入 schema、指标口径和验收标准。

## 开发命令

```powershell
# 安装项目和开发依赖
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 测试
.\.venv\Scripts\python.exe -m pytest

# 静态检查与格式校验
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .

# 类型检查
.\.venv\Scripts\mypy.exe src tests
```

评测流水线入口尚未实现，因此当前没有运行命令。首个可运行管线切片会同时加入入口和对应命令，避免提供没有实际行为的占位 CLI。
