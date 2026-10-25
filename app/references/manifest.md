# 动作清单与工具合同（version 1）

工具只装配已经检查过的完整姿态，不替代角色绘制、姿势设计或人工审图。依赖 Python 3.10+、Pillow；不依赖项目游戏框架、OpenCV 或 NumPy。

本文件的制作manifest和规则格runtime仍为version 1无损基线。需要透明trim、统一降尺寸、有损WebP或独立序列ZIP时，通过[共用工作台](studio-workbench.md)的`sprite_export.py`导出独立`package.json`，遵守[资产与分发合同](asset-layout-and-export.md)。压缩包保留来源、完整画布、trim偏移、稳定clip ID及时间轴；不覆盖此制作合同或把有损包当作原画。

```sh
python3 scripts/motion_tool.py inspect --manifest /path/motion.json --out /path/inspection.json
python3 scripts/motion_tool.py pack --manifest /path/motion.json --out /path/runtime --columns 4
python3 scripts/motion_tool.py verify --runtime /path/runtime/motion.runtime.json
python3 scripts/motion_tool.py preview --runtime /path/runtime/motion.runtime.json --out /path/review/index.html
python3 scripts/motion_tool.py animate --runtime /path/runtime/motion.runtime.json --variant default --action greet --out /path/review/greet.webp
python3 scripts/test_motion_tool.py -v
```

以上命令相对技能目录执行，也可以传脚本绝对路径。`preview` 不传 `--out` 时生成 runtime 旁的 `preview.html`；HTML 仍引用旁边或相对目录的图集，分享时保留其相对路径。工具的 WebP 必须经过 Pillow 的 lossless RGBA 回读比对，不能以“文件能打开”替代像素验证。

## 输入示例

下例假定两张来源确实为 512×512，像素密度为每逻辑单位 2 像素，地面投影原点经审图定为来源坐标 `[256,440]`。不要把示例锚点直接套到其他角色。

```json
{
  "version": 1,
  "character": {"id": "companion", "name": "新伙伴"},
  "canvas": [256, 256],
  "origin": [128, 220],
  "scale": 1,
  "background": "transparent",
  "sources": {
    "pose_a": {"path": "masters/greet-a.png", "pixel_ratio": 2},
    "pose_b": {"path": "masters/greet-b.png", "pixel_ratio": 2}
  },
  "variants": {
    "default": {
      "name": "日常衣装",
      "actions": {
        "greet": {
          "name": "站起来打招呼",
          "loop": false,
          "frames": [
            {"source": "pose_a", "anchor": [256,440], "markers": {"hand": [340,210]}},
            {"source": "pose_b", "rect": [0,0,512,512], "anchor": [256,440], "markers": {"hand": [350,160]}}
          ],
          "sequence": [0,1,0],
          "durations_ms": [160,320,180],
          "events": [{"step": 1, "name": "greeting_peak", "payload": {"kind": "visual"}}],
          "root_motion": [[0,0],[0,0],[0,0]]
        }
      }
    }
  }
}
```

## 字段约束

| 字段 | 约束与含义 |
| --- | --- |
| `version` | 必须为整数 `1`。输入可以包含未知扩展字段，但工具不承诺把它们带入 runtime，且不能替代本合同必填项。 |
| `character.id`、来源/衣装/动作/marker ID | 1–64 个 ASCII 字母、数字、`_`、`-`，首字符必须为字母或数字。不可包含路径、斜杠、点、汉字。显示名放在 `character.name`。若 `--` 组合导致图集重名，工具拒绝并要求改 ID。 |
| `variant.name`、`action.name` | 可选非空字符串，可中文，分别用于衣装与动作的显示名；缺省由消费端显示 ID。runtime 保留显示名，机器 ID、图集命名、索引与时序不受影响。 |
| `canvas` | 所有衣装、所有动作共同使用的输出 `[width,height]`，正整数；单张来源、单帧和单图集各有 6400 万像素保护上限。 |
| `origin` | 输出画布中统一的角色地面/身体参考点，两个有限数；允许在图外。不是 alpha 包围盒中心。 |
| `scale` | 唯一的角色逻辑缩放，有限正数。不能按衣装、动作、姿态的 alpha 包围盒分别缩放。 |
| `background` | 缺省 `transparent`，要求来源带 alpha 或透明色信息，且至少一像素 alpha 小于 255。`opaque` 允许无 alpha 来源，但不添加或猜测底色。输出统一为 RGBA。 |
| `sources` | ID 到来源记录。`path` 相对输入 manifest 所在目录或为绝对路径。`pixel_ratio` 缺省 1，有限正数，表示每逻辑源单位有几个像素，必须来自来源的真实制作规格。 |
| `frames` | 非空的已绘制姿态数组。`source` 引用来源 ID；`rect` 为源像素坐标 `[x0,y0,x1,y1]`（左上包含、右下不包含），缺省整图，必须为源图内的非空整数矩形，支持非均分图集。 |
| `frame.anchor` | 必填；相对 `rect` 左上角的源像素参考点。跳跃、飞行允许锚点在图外，表示地面投影。不能自动改为扫帚、尾巴或道具的最低点。 |
| `frame.markers` | 可选的 ID → `[x,y]`，相对 `rect` 的源像素坐标。例如手持点、脚点、视线点。只记录和转换坐标，不拆层、不自动移动肢体。 |
| `loop` | 必填布尔值。一次性动作停止行为由消费端处理；清单不创建业务计时器。 |
| `sequence` | 缺省按 `frames` 全部顺序。非空的有效帧索引；可以往返、重复，重复播放不等于增加绘制帧。 |
| `durations_ms` | 必填的正整数数组，与 `sequence` 等长。每项对应一段播放时间，不能传 FPS 或总时长代替。 |
| `events` | 可选数组，每项 `step` 为 `sequence` 的段索引，`name` 为非空字符串，`payload` 可为有限 JSON。允许同段多个 cue。工具和预览不执行游戏奖励、位移、伤害或存档写入；战斗权威仍属于游戏规则。 |
| `root_motion` | 可选，与 `sequence` 等长的 `[x,y]` 数组，单位为输出画布像素；每项是相对动作起点的绝对位移，首项必须为 `[0,0]`。仅输出元数据，不烘入像素。循环累计、碰撞和移动授权交给消费端。 |

每个数都必须有限；NaN/Infinity、空序列、非法事件段、越界来源 rect、空 alpha 帧均拒绝。当前不支持透明空帧/fade 合同，不要用一张空图绕过错误。使用来源图自带的透明边距；如果需要抠图，应另走美术流程并人工确认，不在本工具里执行。

## 装配与诊断

```text
effective_scale = scale / source.pixel_ratio
resized_size = round(source_rect_size * effective_scale)
placement = round(origin - anchor * effective_scale)
```

工具统一缩放整个来源矩形，再整体平移到共同画布。缩放使用 Pillow LANCZOS；同尺寸时不重采样。`markers` 随相同缩放和平移变换。允许裁掉完全透明的外边距，任何 alpha 非零像素越界均报错，包括独立漂浮道具、尾尖和扫穗；不会自动取最大连通块、抹边、调色、修脸、贴脚、旋转前肢或按单帧 bbox 缩放。

`inspect` 为每帧输出来源文件 hash、来源 rect、输出 `alphaBounds`、`clippedPixels`、整体平移、有效缩放及转换后的 markers。若裁切，仍写出报告，然后以退出码 2 报错。报告的 `recommendedCanvas` 和 `recommendedOrigin` 是根据所有帧完整 alpha 范围得到的共同扩边建议；由作者确认后修改 manifest，不自动放大角色或移动某一帧。

相邻播放段的 `maskedUnionMeanAbsRGBA` 是两张输出画布在 alpha 非零并集内的 RGBA 平均绝对差（0–255），循环动作包含末段至首段。它只能提示近似重复、跳变或可疑停顿，不能判定动作正确，也不会自动删帧或改时长。检查中位姿态重复、右侧帧密集、手掌离杆等问题时，仍须看逐帧姿态和实际小尺寸播放。

## 输出与核验

`pack` 生成 `motion.runtime.json` 以及 `<character>--<variant>--<action>.webp`。每个来源姿态按 `frames` 顺序装入格子（不去重、不造插值帧）；列数缺省 4，实际不超过帧数。未使用的末行格子透明。

runtime 保留 `version/character/canvas/origin/scale/background`，`sourceHashes` 记录每份来源的 SHA-256、字节数、尺寸、像素密度、绝对 `path` 与原 `inputPath`，便于追溯和阻止覆盖；在其他机器浏览或验证图集时不要求原来源存在。

每个 `variants.<variant>.actions.<action>` 含：

- 可选 `name`：动作显示名；衣装层也保留自己的可选 `name`。
- `atlas`：相对 runtime 文件的安全 ASCII 图集文件名。
- `atlasSize/grid/cellSize`：图集像素尺寸、列行数及共同画布大小。
- `frames`：每格 `rect`、相对格内的非空 `bounds`、共同 `origin`、变换后 `markers`、`source` 与 `sourceRect`。
- `sha256/bytes`：图集文件本身的 hash 与字节数。
- `sequence/durations_ms/loop/events/root_motion`：播放和元数据合同。

`verify` 检查合同、hash/字节数、WebP 尺寸、网格位置、实际 alpha bounds、来源坐标合法性、时间与 cue/root_motion 长度，不需要重新生图。它不能证明画面审美、骨骼连接、动作意图或真机性能已通过。

`pack` 在临时目录完成装配、RGBA 回读和 runtime 核验后替换对应派生文件，拒绝覆盖来源图或输入 manifest。`preview` 拒绝覆盖来源、runtime 或图集，将 atlas URL 改为相对 HTML 的 URL 编码路径，并把注入数据中的所有 `<` 编码成 `\u003c`；模板通过 JSON 数据节点读取，不把角色名、事件 payload 当可执行脚本或 HTML。

工具 fixture 测试覆盖几何合同与数据安全；临时几何图不代表成品角色、动作已验收或游戏已接入。

## 真正的动图导出

`animate` 读取核验后的 runtime 和指定衣装、动作的图集，按实际 `frame.rect` 取完整画布，再按 `sequence` 和每段 `durations_ms` 导出 lossless animated WebP。只支持 `.webp` 扩展名；生成例如 `greet.webp` 和 `greet.metadata.json`。不会重新绘制姿态、增加插值、抠图、修改来源或烘入 `root_motion`。

导出后立即解码完整文件，将每个解码段覆盖的时间区间逐一与原播放段比对：尺寸、alpha、alpha 非零处的 RGB、总时长和循环次数都必须一致。透明像素不可见的 RGB 不作为动图可见效果失败依据。编码器可以把相邻相同画面合并，因此应区分三个数字：

- `sourceFrames`：runtime 中已绘制的来源姿态数量。
- `playbackSteps`：`sequence` 中的播放段数量，重复段也计数。
- `encodedFrames`：实际 WebP 解码帧数，可能少于播放段数；不能把它报告成新绘制的帧数。

`loop:true` 写 WebP 循环次数 `0`（无限）；`loop:false` 写次数 `1`（播放一次）。一次性动图结束后是否保留终帧依赖阅读器，HTML 预览可用于明确的停止与后继动作检查。若编码器将全静止的时间轴优化为无时长静图，工具会用标准 `ANIM/ANMF` 容器保留同一 lossless 画面的时长，不伪造不同姿态。

metadata 记录输入 runtime/atlas hash、角色/衣装/动作 ID、原序列及段时长、总时长、实际编码帧数、loop、events、root_motion 和输出 hash/字节数。events 仅为 cue 数据，任何伤害、奖励、移动、碰撞或存档操作都不在导出中执行；root 位移仍留给引擎处理。

WebP 单边不得超过 16383 像素；图集太宽时应调整列数或共同画布，不能单独缩小某一姿态。动画每段时长上限为 16777215 ms，当前编码路径总时长上限为 2147483647 ms；全静止单幅容器的总时长上限为 16777215 ms。超限报错，不截短时长。输出动图及 metadata 均不能覆盖来源、图集或 runtime。
