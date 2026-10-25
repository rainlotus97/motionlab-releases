# 完整母版公共配准工具

用于已有完整帧的中心漂移、非意图高度差和侧面角度一致性排查。`scripts/frame_register.py`和两个同名HTML模板独立于游戏仓库；Python3.10+、Pillow即可。先实际修正错误母版，工具不能替代生图。

## 能做什么

- 从人工确认的身体轴与当前承重接触点配准整张图；摆动足不压到地面。
- 用户要求归一母版高度时，以明确的头部基准—支撑接触距离求**单一等比倍率**，再平移。没有按alpha bbox缩放，也没有拆身体、拉腿、拼头。
- 每相位显式给目标高度；同类左右半周期检查高度对称，保留合理起伏，不能强行把所有动作头顶锁死。
- 检查源hash、比例标记跨度、2D面部向量角度、相机／近远侧标签、左右支撑关系与完整alpha边界；超限阻止导出，并将预览置为blocked。旧图片留存，不冒充新通过结果。
- 生成PNG、透明动图、并排图、逐帧／正常速／半速预览、机器可读report；提供鼠标点击原图的标记页，导出JSON后可重建。

## 不能自动解决

相机绕人物的yaw、身体扭转、隐藏腿归属、衣裙遮挡需要实际审图／重绘。2D ear→nose角差仅是检测提示，不能证明3D视角正确。camera字段是作者确认的事实，不是工具检测结果。工具不旋转完整图来伪造另一方向。首尾自然度与步态不由数值通过判定。

头部／衣饰有主动运动时不要用变动的冠尖或发梢。选一致、可见、可复核的解剖或刚性标记；不确定点记录估计。头—脚距离因真实蹲伏／跳跃变化时，不应归一为站姿高度，本工具仅适用已明确目标轨迹的站立／步态母版。比例跨度超限要回到原图，不放宽阈值掩盖错误。

制作前冻结可靠的比例校验基准与源hash，优先使用未受本次重画影响的可信姿态。若默认基准本身被重画导致比较失真，须说明原因、保留旧结果并重新审图后更换；不逐次挑能过检的参考，不挪标记或放宽阈值求通过。人工点精度按实际可见性记录，不把小数输出当成精确解剖测量。校验基准变化也不能证明动作连续性改善。

## 输入合同

manifest所有路径相对manifest；`sha256`由当前PNG字节计算，换图后重新标记，不能只换hash沿用旧坐标。`frames`顺序就是播放顺序，durationMs保留既定节奏。`canvas`、`origin`及可选的顶层`targetBodyAxisX`为全组共享。目标高度按完整周期规划，而不是逐帧凭眼调整。

```json
{
  "version": 1,
  "title": "角色侧面行走",
  "canvas": [576,768],
  "origin": [288,710],
  "targetBodyAxisX": 288,
  "reference": "passing_a",
  "headMarker": "headTop",
  "camera": {"view":"E","facing":"right","nearSide":"Right"},
  "proportionPairs": [["headTop","neck"],["neck","waist"],["ear","nose"]],
  "anglePair": ["ear","nose"],
  "phasePairs": [],
  "tolerances": {"scaleFraction":0.06,"proportionFraction":0.075,"angleDegrees":5,"pairedHeadPixels":0.5},
  "frames": [{
    "id":"passing_a",
    "source":"masters/passing-a.png",
    "sha256":"REPLACE_WITH_ACTUAL_SHA256",
    "pixelRatio":1,
    "camera":{"view":"E","facing":"right","nearSide":"Right"},
    "support":"Left","swing":"Right","targetHeadY":90,"durationMs":234,
    "markers":{
      "headTop":[570,15],"neck":[600,282],"waist":[600,520],
      "bodyAxis":[600,520],"ear":[578,178],"nose":[675,190],
      "LeftContact":[662,1493],"RightContact":[695,1414]
    }
  }]
}
```

示例数值只解释合同，换角色必须重新测量。`headMarker`和比例／角度对可换成角色适用部位。`support/swing`可为Left、Right或ForeLeft等合法标识，必须存在`<support>Contact`；四足可选主要承重足对地并额外标记其他接触点，但当前工具不自动求解多足同时接地。`bodyAxis`必须由身体估计，不能用头发／裙摆bbox中心。

`origin[0]`是运动根／导出原点约定，不自动等于投影中的解剖骨盆。顶层`targetBodyAxisX`可显式指定源图人工`bodyAxis`应落到的规范画布X坐标，必须为画布内的有限数；省略时默认`origin[0]`，兼容已有清单。例如一组运动rootX为352，实际投影骨盆约333.6，就应分别记录，不能为了让人体点撞上root十字而错误平移。该字段只改变同一整图变换的水平目标，脚线仍取`origin[1]`，不改变身体比例或自动定位骨盆。

裙下髋点估计不稳而可见躯干标记清楚时，可设置顶层`"horizontalAnchor":{"marker":"neck","targetX":352}`；它优先选择整图水平配准的标记，不会锁定或重命名骨盆。该值必须为对象，marker必须为非空字符串且存在于每帧markers，targetX必须为画布范围内的有限数。省略时仍使用bodyAxis及旧targetBodyAxisX／origin约定；纵向倍率与支撑接触算法不变。report顶层和每帧记录实际horizontalAnchor，每帧bodyAxisX记录真实变换后的bodyAxis；非bodyAxis锚点的targetBodyAxisX为null，旧模式保留原数值。可见标记也必须是同一部位；该选项不能修复躯干实际扭转或比例错误。

`pixelRatio`是可选的正有限数，默认1，含义与`motion_tool`一致：每个规范源单位包含多少源像素。混用不同名义像素密度的母版时，先按已知制作尺寸声明它；例如同一母版及其2倍分辨率版本分别为1和2。不能根据待通过的校准倍率倒推它来绕过检查，也不能把人物实际比例漂移解释成分辨率差异。

`scaleFraction`限制各帧规范源单位倍率`scale * pixelRatio`相对reference的偏差；默认pixelRatio都为1时行为保持原样。pixelRatio只影响这项比较，不改变根据人工标记计算的scale、平移和输出点。`proportionFraction`仍限制变换后比例跨度偏差；`angleDegrees`检查与参考向量夹角；`phasePairs`列同类左右阶段，如01/03及02/04。阈值应来自任务容差，不固定套用示例。比例校准是整图允许的小幅归一；若一帧胸短腿长，即使总高度一致也应被比例检查拦下。

计算：

```text
scale = (groundY - targetHeadY) / (supportSourceY - headSourceY)
effectiveScaleCanonical = scale * pixelRatio
relativeScaleCorrection = effectiveScaleCanonical / reference.effectiveScaleCanonical - 1
targetBodyAxisX = manifest.targetBodyAxisX if supplied else originX
horizontalAnchor = manifest.horizontalAnchor if supplied else {marker:bodyAxis, targetX:targetBodyAxisX}
dx = horizontalAnchor.targetX - sourceMarkers[horizontalAnchor.marker].x * scale
bodyAxisX = bodyAxisSourceX * scale + dx
dy = groundY - supportSourceY * scale
```

所有点随同一个变换，采用浮点仿射避免整数平移舍入造成跳高。摆动鞋高度、前后脚距离、衣服与手指都不单独处理。

## 使用

```sh
python3 scripts/frame_register.py annotate --manifest /project/registration.json --out /project/annotate.html
python3 scripts/frame_register.py register --manifest /project/registration.json --out /project/aligned
python3 scripts/test_frame_register.py -v
```

标记页相对引用原图，不重复编码图像；保留相对目录结构，可直接本地打开。选帧／标记，点击位置，支持撤销并下载JSON。将JSON保存回原manifest位置，避免相对路径改变。标记变化不修改源图，下载本身也不自动写回项目。

`register`返回0只表示配准检查通过；2表示错误。输入文件不会被输出覆盖。输出`report.json`保存原图hash、源坐标、浮点倍率／位移、pixelRatio、effectiveScaleCanonical、各帧targetBodyAxisX、变换后所有点、比例和角差。导出原点十字仍显示origin；人体目标点与它不同可以是正确约定，不应假称错误漂移。输出PNG是可供已有motion_tool装配的完整规范画布，不再在下游重复归一或重复烘入根位移。

16项工具测试验证几何、人体目标轴与运动根分离、可见水平锚点在混合分辨率／横漂中的一致性及实际bodyAxis记录、无效或缺失锚点拒绝、混合源像素密度及真实比例错误、来源变更、非有限数、左右冲突、视角／比例／对称性异常、裁切、输入覆盖和标记页转义；测试fixture不作为角色美术通过证据。
