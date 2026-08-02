# AI4SE Course References

这些文件是 SafeFix 项目的课程依据和背景资料，原则上保持原始内容，不直接在其中记录项目决策。

## 项目要求

### `course-requirements/AI4SE_Final_Project_Common.md`

所有期末项目共享的要求，包括：

- Superpowers 工作流
- SPEC、PLAN 与过程文档
- TDD 和代码评审
- Git worktree 与 subagent 开发
- API Key 安全管理
- 测试、CI 与分发
- AGENT_LOG 和最终交付物

### `course-requirements/AI4SE_Final_Project_A_Harness.md`

A 类 Coding Agent Harness 专属要求，包括：

- 自己实现 Agent 主循环
- 可注入 Mock LLM 的抽象层
- 工具、记忆、治理、反馈和配置
- 机制必须由代码实现
- 核心机制必须能够离线确定性测试
- 危险动作拦截、失败反馈和重点机制演示

SafeFix 的完整作业约束由以上两个文件共同组成。

## 课程资料

### `course-materials/AgenticEngineering-with-notes.pptx`

Agentic Engineering、软件可信性、监督强度、理解力负债以及工程责任。

### `course-materials/Prompt_Context_Harness_Engineering-with-notes.pptx`

Prompt Engineering、Context Engineering 与 Harness Engineering 的关系，以及工具、反馈、治理、记忆等系统机制。

### `course-materials/agent-loop.html`

Agent Loop 相关课程页面存档。

## 项目文档边界

- 课程原始要求：`docs/course-requirements/`
- 课程背景资料：`docs/course-materials/`
- 项目最终规约：根目录 `SPEC.md`
- 实现任务计划：根目录 `PLAN.md`
- 规约生成与冷启动过程：根目录 `SPEC_PROCESS.md`
- Agent 开发过程证据：根目录 `AGENT_LOG.md`

当项目设计与课程要求发生冲突时，以课程要求文件为准，并在 `SPEC_PROCESS.md` 中记录修订。
