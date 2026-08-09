# SafeFix

SafeFix 是一个本地运行的 Python 命令行修复工具。它会针对 pytest 失败用例，调用兼容 OpenAI API 的模型完成受控的“读取项目 - 选择工具 - 执行工具 - 获取反馈 - 继续决策”循环。它不是 Web 服务，也不提供 WebUI、云端托管或自动部署。

SafeFix 的重点是让 coding-agent 的关键机制由可测试的确定性代码控制：项目路径限制、工具分发、危险操作拦截、人工审批、冻结测试基线、反馈回灌、快照回滚、停止条件和可选的有界记忆。

## 适用场景与边界

适用场景：本地 Python 项目中已有 pytest 失败，需要在明确的写入范围内辅助定位和修复源代码。

不适用场景：执行任意 shell 命令、修改测试来“通过”测试、自动批准高风险修改、远程部署应用，或将 API Key 写入项目文件。

SafeFix 默认仅允许修改 `src/` 下的 Python 文件。测试文件、疑似凭据文件、绝对路径和逃离项目根目录的路径都会被拒绝。需要修改超过 3 个文件（`>3 files`）或超过 80 行（`>80 lines`）时，交互式会话会要求人工审批；非交互模式一律拒绝此类操作。

## 环境要求

- Python 3.11 或更高版本（Python 3.11+）。
- 能在 `PATH` 中找到 `curl`。SafeFix 通过它向兼容 OpenAI API 的模型端点发起请求。
- 正常运行修复时，本地进程需要能访问在项目配置中填写的模型 `base_url`。
- 交互式终端建议使用支持 ANSI 颜色和 Unicode 的终端；`TERM=dumb` 或设置 `NO_COLOR` 时会自动降级为无颜色输出。

SafeFix 只支持本地 CLI 工作流。在 Windows、macOS 或 Linux 上，只要安装了兼容版本的 Python、`curl` 和终端（compatible terminal），均可使用；README 中的环境变量示例以 POSIX shell 为主，并附带 Windows 激活虚拟环境的命令。它不是 cloud 服务。

## 获取与安装

### 从 GitHub 获取源码

```bash
git clone https://github.com/SelfLo06/SafeFix.git safefix
cd safefix
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

上面的激活命令适用于 macOS/Linux 的 POSIX shell。Windows 使用以下命令：

命令提示符（Command Prompt）：

```bat
.venv\Scripts\activate.bat
```

PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

### 从 GitHub Release 获取发行包

正式版本在 [v0.2.0 Release](https://github.com/SelfLo06/SafeFix/releases/tag/v0.2.0) 发布，包含：

- [wheel 包](https://github.com/SelfLo06/SafeFix/releases/download/v0.2.0/safefix-0.2.0-py3-none-any.whl)：适合直接安装。
- [源码包（sdist）](https://github.com/SelfLo06/SafeFix/releases/download/v0.2.0/safefix-0.2.0.tar.gz)：适合需要检查或从源码安装的场景。

下载后可安装任一包：

```bash
python -m pip install safefix-0.2.0-py3-none-any.whl
# 或者
python -m pip install safefix-0.2.0.tar.gz
```

若要从源码构建自己的发行包：

```bash
python -m pip install build
python -m build --wheel --sdist --outdir dist
```

构建完成后，`dist/` 内会包含 wheel 与源码包，可通过下列命令安装：

```bash
python -m pip install dist/*.whl
# 或在干净环境中安装源码包：
python -m pip install dist/*.tar.gz
```

## 配置 API Key 与模型

SafeFix 不把凭据写入配置文件、会话记录、命令行参数或 `.env` 文件；也就是说，它 does not store 凭据。它只从启动进程的环境变量（environment variable）读取三个角色独立的 API Key：

| 角色 | 环境变量 | 用途 |
| --- | --- | --- |
| 测试模型 | `SAFEFIX_TEST_API_KEY` | 生成候选测试时使用 |
| 修复模型 | `SAFEFIX_REPAIR_API_KEY` | 分析失败并提出工具动作时使用 |
| 检查模型 | `SAFEFIX_REVIEW_API_KEY` | 修复完成后的最终检查时使用 |

在当前 POSIX shell 中设置变量：

```bash
export SAFEFIX_TEST_API_KEY="..."
export SAFEFIX_REPAIR_API_KEY="..."
export SAFEFIX_REVIEW_API_KEY="..."
```

若只执行不需要某个模型角色的流程，缺失的变量会在实际需要该角色时以安全错误提示指出变量名，不会显示变量值。更新 Key 的方式是在启动 SafeFix 的 shell 中重新设置相应变量；从当前 POSIX shell 清除三个变量：

```bash
unset SAFEFIX_TEST_API_KEY SAFEFIX_REPAIR_API_KEY SAFEFIX_REVIEW_API_KEY
```

SafeFix 当前没有“保存、更新、清除持久凭据”的内置命令，也没有 raw-key CLI 参数。若需要跨终端持久保存秘密，请使用操作系统或组织规定的凭据管理方式；不要把真实 Key 提交到仓库。它不会读取 `.env`，也没有共享凭据或 provider fallback（回退）变量。

在待修复项目的根目录创建 `safefix.toml`。`base_url` 和 `model` 是 `safefix run` 的必填项：

```toml
base_url = "https://llm.example/v1"
model = "repair-model"
pytest_args = ["-q", "--tb=short"]
```

`pytest_args` 仅接受受限的报告类参数：`-q`、`-v`、`--tb=short`、`--tb=line`、`--tb=no`、`--disable-warnings` 和形如 `-rA` 的报告选项。会改变收集或执行范围的 `-k`、`-m`、`-x`、`--collect-only` 等参数会被拒绝，以确保冻结基线可比较。

## 运行 SafeFix

### 最简交互启动

进入待修复 Python 项目的根目录，设置修复模型凭据后直接运行：

```bash
cd /path/to/project
export SAFEFIX_REPAIR_API_KEY="..."
safefix
```

在 TTY 中，启动向导会使用当前目录，检测现有测试，默认选择 standard 模式和 TUI。该入口相当于运行 `safefix` without arguments。若缺少 `safefix.toml`，向导会询问修复模型 `base_url`（默认 `https://api.openai.com/v1`）与必填 `model`（required model name），并只写入当前修复角色所需的配置：

```toml
base_url = "..."
model = "..."
```

无参数启动必须在 TTY 中使用；重定向输入输出或非 TTY 环境会保持为安全的普通输出模式。它 does not create Test or Review Model settings，也不复用 Repair 设置；脚本、CI 或需要明确高级选项时使用 `safefix run PATH`。

### 脚本、CI 或显式参数模式

```bash
safefix run .
```

`run` 命令支持原有的 `--base-url`、`--model`，以及以下主要选项：

- `--generate-tests`：请求测试模型生成候选测试。
- `--baseline-source existing|generated|mixed`：指定冻结基线的测试来源。
- `--acceptance-mode review|standard|high-risk`：指定补丁接受策略。
- `--stability-runs`、`--max-auto-accepted-failures`：控制稳定性与自动接受阈值。
- `--test-base-url`、`--test-model`、`--review-base-url`、`--review-model`：分别设置测试与检查模型的端点和模型名。

仅当 SafeFix 确认没有收集到任何现有测试时，才允许使用仅生成测试的模式。测试、修复和检查三个角色不能共享凭据变量作为回退来源。

### 交互终端与控制命令

安装包会安装 `prompt_toolkit` 和 Rich。支持的终端中，`safefix run .` 会显示可保留滚动历史的 Guided Repair Console；它展示安全事件摘要，不显示凭据、完整 prompt、原始模型回复或超出既有摘要范围的源文件内容。

- `--tui`：请求交互终端界面。
- `--plain`：强制使用结构化普通事件输出。
- `--no-animation`：关闭短暂动画，只影响显示，不影响修复决策、事件顺序、产物、停止原因或退出码。
- `/pause`、`/resume`、`/stop`：在 TUI 中暂停、继续或停止会话。
- `/approve`、`/deny`：在出现高风险补丁审批提示时明确批准或拒绝。

`--tui` 与 `--plain` 互斥。非 TTY（non-TTY）输入或输出总会使用普通输出，即使传入 `--tui` 也不会启动交互提示，从而让日志和 CI 保持可预测。交互界面保留终端 scrollback，`TERM=dumb` 与 `NO_COLOR` 会关闭不兼容的视觉效果。

## 修复过程与安全机制

一次标准修复会经历以下步骤：

```text
收集或生成测试
→ 冻结 baseline
→ 模型提出工具动作
→ SafeFix 解析并校验动作
→ 执行受限工具
→ 运行测试并形成反馈
→ 将反馈回灌下一轮决策
→ 成功、停止或回滚
```

默认上限为 `max_steps = 30`、`max_rounds = 10`、`max_no_progress_rounds = 3`。测试变好时才会被视为严格进展；变差的补丁会回滚，连续无进展会停止。会话产物会保存冻结测试清单、生成测试准备摘要、安全的模型标识、评估和检查摘要、计数器以及最终停止原因；其中不包含秘密或原始模型输出。所有工具路径均为 project-relative（相对项目根目录）；绝对路径和越界路径会被拒绝。

项目记忆是可选且有界的。库调用者必须显式传入 `use_memory=True`；default context and `safefix run` do not load project memory。Memory stores summaries only，不保存凭据或源代码。

## 测试与可重复机制演示

本项目的测试不依赖真实模型、网络或 API Key。助教在完成安装后，可用下列一条命令运行 SafeFix 自身的完整离线测试集：

```bash
python -m pytest tests
```

预期结果：pytest 收集 `tests/` 中的正式测试并全部通过。请显式带上 `tests` 目录，不要直接在仓库根目录运行 `python -m pytest`：根目录下的 `tmp/` 是 SafeFix 用于压力/示例项目的工作区，其中的独立测试不属于 SafeFix 自身的测试套件，也没有作为仓库包安装。

### 单独运行三项机制演示

下列命令会只运行可重复的机制演示：

```bash
python -m pytest tests/mechanism -q
```

这些测试使用 `MockLLM` 或注入的 fake，回复序列、临时项目内容和断言均固定，因此每次运行的结果确定，不消耗 token、不发送网络请求、也不要求配置 API Key。三项课程要求的代表性场景如下：

| 验收目标 | 演示测试 | 稳定验证内容 |
| --- | --- | --- |
| 危险动作被 guardrail 拦截 | `tests/mechanism/test_demo_deny.py::test_demo_test_edit_is_permanently_denied` | MockLLM 尝试修改测试文件；测试断言文件保持不变、结果为 `GuardDecision.DENY`，且不会请求人工审批。 |
| 失败反馈改变下一步决策 | `tests/mechanism/test_demo_feedback_changes_action.py::test_feedback_changes_the_next_scripted_action` | 第一个脚本化补丁因修改测试而被拒绝；反馈回灌后第二个动作改为源文件补丁并成功，结果序列固定为 `denied`、`success`。 |
| 重点机制：严格进展、回滚与停止 | `tests/mechanism/test_demo_progress_rollback.py::test_better_same_worse_and_no_progress_are_deterministic` | 测试依次验证更好、相同、变差、无进展的判定；变差补丁会回滚，最终因无进展以 `NO_PROGRESS` 停止，源文件状态固定。 |

若只想逐项检查，可直接运行对应节点，例如：

```bash
python -m pytest tests/mechanism/test_demo_deny.py::test_demo_test_edit_is_permanently_denied -q
```

### MockLLM 单元测试

MockLLM 本身也有独立单元测试：

```bash
python -m pytest tests/unit/test_mock_llm.py -q
```

它验证 scripted response 会按既定顺序返回，脚本耗尽后稳定抛出 `ScriptExhaustedError`。这保证核心 harness 测试能够替换真实 LLM，在离线、无网络、无 token 消耗的条件下稳定运行。

## 项目结构

```text
src/safefix/       CLI、agent loop、工具、guardrail、feedback、memory 与模型客户端
tests/unit/        Harness 机制的确定性单元测试
tests/mechanism/   可重复的 A 类机制演示
docs/decision-records/
                   保留的产品设计决策记录
SPEC.md            产品与机制规约
PLAN.md            实施计划与提交追踪
SPEC_PROCESS.md    brainstorming 与冷启动过程记录
AGENT_LOG.md       实施和验证证据
```

## 持续集成与发布

仓库使用 GitHub Actions。每次推送都会安装依赖并运行正式测试集；最近一次工作流状态可在 GitHub Actions 页面查看。正式发行包通过 GitHub Release 提供，不要求也不包含服务器部署。

对于非交互自动化，SafeFix 的审批策略为 fail-closed：non-interactive mode SafeFix must deny 需要审批的动作，并且 never auto-approves。这里的 `deny` 是确定性的治理结果，不是模型建议。

## 已知限制

- SafeFix 仅面向 Python/pytest 的本地修复流程。
- 它只支持兼容 OpenAI API 的模型端点，并要求系统存在 `curl`。
- 它没有 WebUI、Docker 镜像、云服务、GitLab CI 或自动部署功能。
- 凭据只从当前进程环境读取，不提供内置持久化存储或 Key 管理命令。
- 模型输出虽会经过结构化解析、路径校验和 guardrail，但仍应由开发者审阅实际补丁与测试结果。
