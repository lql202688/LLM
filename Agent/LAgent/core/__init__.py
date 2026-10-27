"""业务核心层：LLM 客户端、会话编排、工具注册。

core 层不依赖任何 UI 框架，通过 core.events.AgentEvent 向外推送状态，
任何界面（CLI / TUI / Web / Desktop）只需消费事件即可渲染。
"""
