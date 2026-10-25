# Release 构建与维护

默认交付分别为 `MotionLab-<版本>-arm64.zip`（M 系列）和 `MotionLab-<版本>-x86_64.zip`（Intel）。每个包只含对应架构的原生启动器和 Python/Pillow，工程在 app 外。版本号统一放 `VERSION`；运行时版本、下载 URL 和 SHA-256 固定在 `release-runtime.json`。

## GitHub Actions 构建

`.github/workflows/release.yml` 在 GitHub 的 `macos-14` ARM runner、`macos-15-intel` Intel runner 上独立构建与验证。推送 `v<版本>` 标签自动触发，也可在 Actions → Build Mac releases → Run workflow 手动触发。标签必须和 VERSION 一致，普通 main 提交不自动发版。

每个平台检查签名、原生二进制架构、唯一随包运行时、隔离资源库启动、重复启动保留工程、技能链接、9 段松团动作、43 个骨骼来源和导出。Intel 包必须在 Intel runner 原生运行；不以 Rosetta 测试替代。单元测试还覆盖芯片识别、更新兼容、损坏包及汇总校验。所有检查通过后产生 `release-ready` Actions artifact，含安装 ZIP、SHA256SUMS、update.json 和 verification.json；保留 14 天。正式 Release 文件长期保留。

开发仓库与历史保持私有；公开发行仓库 `app/` 保存用户已授权的构建快照，仅含随安装包公开的脚本、工具与两套示例。`.github/workflows/release.yml` 负责构建，`BUILD_SOURCE.json` 记录快照来源提交，不公开开发历史或个人资源库。

维护者修改 VERSION、完成定向检查并提交后运行：

```sh
python3 scripts/publish_release.py
```

该命令要求干净 main 和已登录 GitHub CLI，将随包文件同步为公开构建快照、复制工作流、提交并推送版本标签。**本机不编译、不下载或上传安装包，无需 Xcode。** GitHub Actions 自动完成两个 Mac 包的构建、测试、汇总、草稿上传和正式发布。发布任务只用该仓库自带的短期 GITHUB_TOKEN，不需要额外密钥；普通用户下载和更新均免登录。公开下载页 main 只更新快照，不发布未通过验证的包。

也可在公开仓库直接提交 app/VERSION 和相应改动、推送同名版本标签。Actions 手动运行仅生成测试 artifact；发版需标签触发。失败后先查看 Actions 日志，修复后重新构建；已公开版本不覆盖，必须提升版本号。不要移动已有版本标签。

## 兼容升级

0.1.2 只识别通用包，因此 0.1.3 额外构建一次 universal 桥接包，供旧版“检查更新”使用。新版 update.json 的 `latestVersion/platforms` 指向最新两个单芯片包；根 `version/asset/bytes/sha256` 保留桥接版，旧版仍能升级。后续版本只构建两个包，从上次公开清单继承桥接信息，不能删除 0.1.3 的桥接 ZIP。新客户端即使经 Rosetta 启动也根据硬件选择 arm64。

`assemble_release.py` 在发布前重算 ZIP hash、验证原生测试报告，并生成清单。`studio_update.py` 只读取固定公开仓库 Release 附件 update.json，避免 GitHub API 匿名限流；限制 GitHub 下载域，校验 hash、包路径、芯片、版本与 codesign，然后等待原进程退出再原子替换。保留旧 app，替换／启动命令失败恢复。数据目录不参与替换。新进程启动后的任意业务故障不保证自动回滚，可手动恢复备份。

## 手动诊断（非正常发布路径）

仅诊断构建器需要 macOS、Xcode Command Line Tools 和 Python 3.10+：

```sh
python3 scripts/build_release.py --architecture arm64
python3 scripts/verify_release.py output/release/MotionLab-<版本>-arm64.zip --require-native
```

Intel 传 `x86_64` 并在 Intel 主机验证。构建缓存和 ZIP 不进入 Git。脚本、许可与选定示例随 app 公开；密钥、本地环境、生成历史和其他个人素材不随包上传。

当前使用 ad-hoc 签名，尚无 Apple Developer ID 签名与公证，首次 Gatekeeper 提示应如实告知。不能自动降低系统安全设置。未来接入正式签名时，对嵌套 Mach-O 与 app 签名、完成 notarization 后再上传。
