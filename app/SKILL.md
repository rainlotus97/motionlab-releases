---
name: character-motion
description: Create or extend coherent 2D character animations from text or references, inspect and edit them in a project-based workbench, and export portable game-ready sprite data. Use for animated characters, pets, NPCs, outfits, directions, motion repair, and existing motion references.
---

# 角色动作工坊

唯一技能入口是 `$character-motion`。生成、工作台、骨骼参考和游戏接入是本技能内部工作分支，共用同一套脚本与合同；无需并列安装其他角色动作技能。用户说旧名 `character-motion-simple/detailed/studio/skeleton` 时，按下表理解其意图并使用此处资源，不重新建立一套工具。

## 按当前任务选择分支

| 用户意图 | 读取的分支 |
| --- | --- |
| **生图：从零创建** | [从零设计](references/from-scratch.md)，再按角色复杂度选下列配方 |
| **生图：简洁角色／短腿萌宠** | [完整帧配方](references/simple-pet-motion.md)；优先完整角色多格母图 |
| **生图：长裙、宽袖、披发等复杂人物** | [复杂完整帧](references/complex-character-frames.md)；骨骼引导时再读[连续性合同](references/skeleton-to-spritesheet.md) |
| **工作台：打开、整理、调帧、导出** | 先读[桌面入口与资源库](references/desktop-workbench.md)，再读[工作台](references/studio-workbench.md)；修改共享工具前读[实现合同](references/studio-architecture.md) |
| **骨骼：查已有动作、相机、选帧** | [骨骼参考](references/skeleton-workbench.md)；有已采用方案时读[方案合同](references/sampling-plans.md) |
| **接入：游戏中移动、换装、切状态** | [运行边界](references/runtime-integration.md)，再读[交付格式](references/asset-layout-and-export.md) |

只读当前分支需要的资料。示例调用：`$character-motion 工作台：打开已有松团工程，检查日常衣装跑动，导出给游戏使用。` 分支是自然语言意图，不是新的技能名或必须输入的模式代码。

## 先继承项目，再决定本轮改变什么

先查项目 `studio.project.json`、`motion-plan.json`、已认可参考及直接消费者。按**角色 × 衣装 × 动作 × 朝向**确定本轮范围，区分新增动作、同身份换装、局部修复和游戏接入。已有完好资产继续有效；不要求用户重述已确认外观，不把过期草稿或被否定候选当成采用版。显示名与稳定ID分开；工程目录、命名和入口／出口遵守[资产合同](references/asset-layout-and-export.md)。

没有角色图但有文字要求时可以从零制作。仅缺失关键参考或要求实质冲突时澄清；其他实现选择沿用上下文。未请求生成时不因打开工作台而生图，未请求接入时不改宿主游戏。

## 已安装 Release 的工具调用

先识别安装形式。技能位于 `MotionLab.app/Contents/Resources/character-motion` 时，直接打开该 app；CLI 用此技能目录的 `scripts/run <脚本名> <参数>`，自动使用随包 Python。不要运行源码安装器、要求 Xcode 或重新编译应用。文档中的 `python3 scripts/foo.py` 在 Release 中改为 `scripts/run foo.py`。个人工程和配置放在 app 外；更新由原生菜单“检查更新…”完成，安装说明见[桌面入口](references/desktop-workbench.md)。

## 生成工具与可选依赖

先继承当前用户、项目及本次请求指定的生图服务；没有指定时，可使用当前环境可用的内置生图能力。此仓库不强制随想或任何外部服务，也不安装生成客户端或保存密钥。用户指定服务时，相应调整当前项目的工具选择、客户端配置与实际调用方式，不能只改说明仍调用原服务；配置仅保存在本机，检查能力与连通后再执行。服务不可用时说明具体障碍，不静默更换服务或模型。实际生成遵守所用工具的指令，记录真实服务、模型（未暴露记 unknown）、原图尺寸／alpha、参考分工与 hash。已生成且认可的图片直接复用，不为切换服务重新生成。工作台、骨骼参考与导出均可离线使用，不依赖生图服务。

2dcs 的绿幕和固定模式只属于明确采用它的任务；XSXB Frame Tuner／Godot／Unity 仅在目标宿主需要时使用。普通透明完整帧不因此增加成人骨架、绿幕、引擎或整世界SDK依赖。

## 制作和验收的关键边界

- 将动作意图拆成阶段、完整姿态、支撑／接触、时序和首尾；参考[动作设计](references/action-design.md)。先做代表动作审阅，再扩当前授权范围。多格生产读[母图与预算](references/sheet-authoring-and-budget.md)；增量任务读[扩展与保留](references/incremental-authoring.md)。
- 完整角色位图优先完整帧。不能靠拆臂绕单点旋转、局部贴头脚、CSS抖动或单图横移冒充新绘动作；已有经验证原生骨骼绑定可沿用。不同物种的接触和关节由其身体决定。
- 按真实返回图确定 source rect；统一尺度，按真实根点或接触点配准，不逐帧按bbox放大。导出原点、人工观察点、身体根点是不同概念；跳跃可有画布外地面投影。装配合同见[manifest](references/manifest.md)，配准问题读[配准工具](references/frame-registration.md)。
- 源姿态、播放段和FPS分开记录。相近姿态反复停留可能卡顿；优先检查时序与真实位移，不把重复播放称为新增绘画。一次性动作保留收势、终帧和中断语义。
- 实际看正常／慢速、最大伸展、末→首、动作／衣装切换和目标小尺寸。身份、肢体连通、道具接触、遮挡和自然度须视觉检查；hash、姿态数、配准指标及技术测试均不替代。修复读[质量与修复](references/quality-and-repair.md)。
- 非对称衣饰、惯用手、伤痕和文字不默认镜像。只有明确允许的对称资产才能复用方向；四方向素材不能标成八方向。
- 动画只解释宿主事实。位置、碰撞、命中、奖励、库存和存档由原模块拥有；释放RAF、订阅和异步任务，迟到结果不能写入新实例。

## 交付与维护

默认交付精简运行图包：`package.json + atlases/`，保留稳定身份、裁切／位置恢复、逐段毫秒时长、loop、root和events。其他会话从包内 `consumer` 读取绘制约定，按显式角色／衣装／动作／方向字段消费；不要重新凭图片猜格子。预览、独立PNG及制作证据按当前用途选择，不默认塞进游戏运行包。

制作源、生成记录、草稿、审阅结果和角色专有参数留在**资源工程**。仓库 `examples/` 仅保存用户明确授权的精选示例及许可；安装时复制到资源库，不覆盖已有工程。共享工作台只换清单和素材，不为每次生图复制HTML或播放器。遵循项目本地提交约定，交付时报告实际验证和未完成边界；不因本技能擅自上传、安装宠物或发布游戏。更多真实用法见[请求示例](references/example-requests.md)。
