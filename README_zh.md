# Forge

[English](README.md) | [简体中文](README_zh.md)

Forge 是面向 Dora 数据流的机器人基础框架，提供跨语言消息 schema、机器人和策略节点运行器，以及类型化 ToolEndpoint 契约。具体机器人驱动和硬件 SDK 集成由下游节点提供。

## 版本状态

本分支 `stable/1.x` 维护兼容 Dora 0.4 的 Forge 1.x；`master` 维护 Forge 2.x。
六个包家族独立发布：Common `1.0.1`、Msgs `1.3.1`、Robot `1.0.1`、Policy `1.0.1`、Kinematics `1.0.1`、Tool `1.0.1`。

Common、Msgs 和 Tool 独立包支持 Python 3.11；完整源码工作区仍要求 Python 3.12。
Tool `1.0.1` 通过兼容的类型声明支持 Python 3.11，线协议与执行生命周期保持不变。
运行时检查类型别名使用 `typing.get_args(alias)`，不再访问 `alias.__value__`。
Msgs `1.3.1` 的图像编解码要求 Pillow `>=13.0.0,<14`。

## Python 3.11 节点安装

在节点自己的 Python 3.11 环境中安装发布包：

```bash
python -m pip install "forge-tool[dora]==1.0.1"
```

`[dora]` 引入 Arrow 消息载体依赖 `forge-msgs>=1.2.0,<2`，不安装 Dora 运行时。
具体节点负责安装与部署匹配的 Dora 版本；本分支的节点运行器使用 `dora-rs==0.4.1`。
不需要 Arrow 绑定时，可以只安装无运行时依赖的基础包：

```bash
python -m pip install "forge-tool==1.0.1"
```

支持 Python 3.11 不代表已验证所有 Isaac Sim／RoboDojo 集成，仍需在目标节点环境验证仿真器、驱动和运行时依赖。

## 源码开发与测试

源码工作区使用 Python 3.12 或更新版本，以及 `uv`：

```bash
uv sync --locked --all-packages --all-extras --dev
uv run python scripts/check_release_versions.py
uv run ruff check .
uv run pytest packages/msgs/tests packages/policy/tests packages/robot/tests packages/kinematics/tests packages/tool/tests
uv run python scripts/build_python_distributions.py
```

Python 3.11 节点应使用独立虚拟环境安装发布包，不要直接同步包含 Python 3.12 包的完整工作区。
Rust 需要支持 edition 2024 的工具链；C++ 需要 C++20、CMake 3.20 及 Apache Arrow C++。

## 文档导航

| 内容 | 入口 |
| --- | --- |
| ToolEndpoint、协议与 Python 用法 | [Tool 文档](interfaces/forge_tool/README.md) · [Python 包指南](packages/tool/README.md) |
| 权威消息 schema | [forge_msgs 接口](interfaces/forge_msgs/) |
| 完整仓库布局、C++／Rust 构建与 API 示例 | [英文开发指南](README.md) |
| 各包更新记录 | [CHANGELOG](CHANGELOG.md) |
| 独立版本、打包与家族标签发布规则 | [RELEASING](RELEASING.md) |

发布使用不可变的家族标签，例如 `forge-tool-v1.0.1`。不同主版本的依赖约束与运行时应分别维护。
