import Cocoa
import WebKit
import Darwin

final class StudioApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
  var window: NSWindow!
  var web: WKWebView!
  var service: Process?
  var lock: Int32 = -1
  var library = ""
  var logHandle: FileHandle?
  var preparingExit = false
  var updateBusy = false
  var updateQuitting = false
  var updateTask: Process?
  var pythonPath = ""
  var scriptRoot = ""
  var bundled = false

  func applicationDidFinishLaunching(_ notification: Notification) {
    let info = Bundle.main.infoDictionary!
    if let icon = Bundle.main.url(forResource: "Studio", withExtension: "icns") {
      NSApp.applicationIconImage = NSImage(contentsOf: icon)
    }
    bundled = info["StudioBundled"] as? Bool ?? false
    #if arch(arm64)
    let architecture = "arm64"
    #else
    let architecture = "x86_64"
    #endif
    let resources = Bundle.main.resourceURL!
    pythonPath = bundled ? resources.appendingPathComponent("runtime/" + architecture + "/python/bin/python3").path : info["StudioPython"] as! String
    scriptRoot = bundled ? resources.appendingPathComponent("character-motion/scripts").path : URL(fileURLWithPath: info["StudioScript"] as! String).deletingLastPathComponent().path
    library = info["StudioLibrary"] as? String ?? NSHomeDirectory() + "/Desktop/MotionLab"
    try? FileManager.default.createDirectory(atPath: library + "/.studio", withIntermediateDirectories: true)
    lock = Darwin.open(library + "/.studio/window.lock", O_CREAT | O_RDWR, 0o600)
    if lock < 0 || flock(lock, LOCK_EX | LOCK_NB) != 0 {
      NSRunningApplication.runningApplications(withBundleIdentifier: "local.character-motion.studio").first(where: { $0.processIdentifier != getpid() })?.activate(options: [.activateAllWindows])
      NSApp.terminate(nil)
      return
    }
    let menu = NSMenu()
    let item = NSMenuItem(); menu.addItem(item)
    let appMenu = NSMenu(); item.submenu = appMenu
    appMenu.addItem(withTitle: "关于角色动作工坊", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
    appMenu.addItem(withTitle: "检查更新…", action: #selector(checkUpdates), keyEquivalent: "").target = self
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "设置…", action: #selector(runMenu(_:)), keyEquivalent: ",").representedObject = "settings"
    appMenu.items.last?.target = self
    appMenu.addItem(withTitle: "显示资源目录", action: #selector(reveal), keyEquivalent: "").target = self
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "退出角色动作工坊", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    let editItem = NSMenuItem(); menu.addItem(editItem)
    let edit = NSMenu(title: "编辑"); editItem.submenu = edit
    for (name, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
      edit.addItem(withTitle: name, action: Selector(action), keyEquivalent: key)
    }
    for (title, entries) in [("文件", [("打开资源工程…", "projects", "o"), ("导入素材…", "import", "i"), ("保存调整", "save", "s"), ("导出资源…", "export", "e")]), ("视图", [("动作编辑", "motion", "1"), ("骨骼参考", "sampling", "2"), ("逐帧对照", "comparison", "3"), ("工程文件", "files", "4"), ("显示／隐藏检查器", "inspector", ""), ("刷新素材", "refresh", "r")])] {
      let top = NSMenuItem(); if title == "文件" { menu.insertItem(top, at: 1) } else { menu.addItem(top) }
      let submenu = NSMenu(title: title); top.submenu = submenu
      for (label, command, key) in entries {
        let entry = submenu.addItem(withTitle: label, action: #selector(runMenu(_:)), keyEquivalent: key)
        entry.representedObject = command; entry.target = self
      }
      if title == "文件" {
        submenu.addItem(.separator())
        submenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
      }
    }
    NSApp.mainMenu = menu
    window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 920), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    window.title = "MotionLab"
    window.minSize = NSSize(width: 1080, height: 700)
    window.setFrameAutosaveName("CharacterMotionStudio")
    window.center(); window.delegate = self
    let configuration = WKWebViewConfiguration()
    configuration.userContentController.addUserScript(WKUserScript(source: "document.addEventListener('DOMContentLoaded',()=>document.documentElement.classList.add('desktop-app'))", injectionTime: .atDocumentStart, forMainFrameOnly: true))
    web = WKWebView(frame: window.contentView!.bounds, configuration: configuration)
    web.autoresizingMask = [.width, .height]
    web.uiDelegate = self; web.navigationDelegate = self
    window.contentView!.addSubview(web)
    web.loadHTMLString("<body style='background:#f4f2ec;font:18px -apple-system;padding:70px;color:#354b40'>正在打开角色动作工坊…</body>", baseURL: nil)
    window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    let process = Process(); service = process
    process.executableURL = URL(fileURLWithPath: pythonPath)
    process.arguments = ["-B", scriptRoot + (bundled ? "/release_bootstrap.py" : "/studio_desktop.py"), "--library", library, "--owner-pid", String(getpid())]
    process.environment = runtimeEnvironment()
    let output = Pipe(); process.standardOutput = output
    let logPath = library + "/.studio/service.log"
    FileManager.default.createFile(atPath: logPath, contents: nil)
    logHandle = FileHandle(forWritingAtPath: logPath); process.standardError = logHandle
    process.terminationHandler = { [weak self] p in
      guard p.terminationStatus != 0 else { return }
      DispatchQueue.main.async { self?.fail("本地服务启动或运行失败。详情保存在资源目录的 .studio/service.log。") }
    }
    do { try process.run() } catch { fail(error.localizedDescription); return }
    DispatchQueue.global().async { [weak self] in
      let data = output.fileHandleForReading.availableData
      guard let line = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), let url = URL(string: line), url.host == "127.0.0.1" else { return }
      DispatchQueue.main.async { self?.web.load(URLRequest(url: url)) }
    }
  }
  func fail(_ message: String) {
    let alert = NSAlert(); alert.messageText = "无法打开工作台"; alert.informativeText = message; alert.runModal()
  }
  @objc func runMenu(_ sender: NSMenuItem) {
    guard let command = sender.representedObject as? String else { return }
    web.evaluateJavaScript("window.motionStudioCommands?.run('" + command + "')", completionHandler: nil)
  }
  @objc func reveal() { NSWorkspace.shared.open(URL(fileURLWithPath: library)) }
  @objc func reload() { web.reload() }
  func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window?.makeKeyAndOrderFront(nil); return true }
  func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
  func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
    if web == nil || updateQuitting { return .terminateNow }
    if preparingExit { return .terminateCancel }
    preparingExit = true
    web.callAsyncJavaScript("await window.motionStudioCommands?.prepareClose()", arguments: [:], in: nil, in: .page) { [weak self] result in
      guard let self = self else { NSApp.reply(toApplicationShouldTerminate: true); return }
      if case .failure(let error) = result {
        let alert = NSAlert(); alert.messageText = "无法保存关闭前的调整"; alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "返回工作台"); alert.addButton(withTitle: "仍然退出")
        let quit = alert.runModal() == .alertSecondButtonReturn
        self.preparingExit = false
        NSApp.reply(toApplicationShouldTerminate: quit)
      } else { NSApp.reply(toApplicationShouldTerminate: true) }
    }
    return .terminateLater
  }
  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
  func applicationWillTerminate(_ notification: Notification) {
    if updateTask?.isRunning == true { updateTask?.terminate() }
    service?.terminationHandler = nil
    if service?.isRunning == true { service?.terminate(); service?.waitUntilExit() }
    if lock >= 0 { flock(lock, LOCK_UN); Darwin.close(lock) }
    try? logHandle?.close()
  }
  func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
    if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
    return nil
  }
  func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
    let panel = NSOpenPanel(); panel.allowsMultipleSelection = parameters.allowsMultipleSelection; panel.canChooseDirectories = false
    panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
  }
  func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
    if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme ?? ""), url.host != "127.0.0.1" {
      NSWorkspace.shared.open(url); decisionHandler(.cancel); return
    }
    if navigationAction.shouldPerformDownload { decisionHandler(.download) } else { decisionHandler(.allow) }
  }
  func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
    decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
  }
  func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
  func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
  func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
    let panel = NSSavePanel(); panel.nameFieldStringValue = suggestedFilename; panel.directoryURL = URL(fileURLWithPath: library)
    panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
  }
}

extension StudioApp {
  func runtimeEnvironment() -> [String: String] {
    var environment = ProcessInfo.processInfo.environment
    environment.removeValue(forKey: "PYTHONHOME")
    environment.removeValue(forKey: "PYTHONPATH")
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment
  }
  func updateMessage(_ title: String, _ text: String) {
    let alert = NSAlert(); alert.messageText = title; alert.informativeText = text; alert.runModal()
  }
  func updateCommand(_ arguments: [String], title: String, completion: @escaping ([String: Any]) -> Void) {
    updateBusy = true; window.title = "MotionLab · " + title
    let process = Process(); updateTask = process
    process.executableURL = URL(fileURLWithPath: pythonPath)
    process.arguments = ["-B", scriptRoot + "/studio_update.py"] + arguments
    process.environment = runtimeEnvironment()
    let output = Pipe()
    process.standardOutput = output; process.standardError = FileHandle.nullDevice; process.standardInput = FileHandle.nullDevice
    DispatchQueue.global().async { [weak self] in
      var result: [String: Any]
      do {
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? ["error": "更新程序未返回有效结果。"]
      } catch { result = ["error": "无法启动更新程序：" + error.localizedDescription] }
      DispatchQueue.main.async {
        self?.updateBusy = false; self?.updateTask = nil; self?.window.title = "MotionLab"
        completion(result)
      }
    }
  }
  @objc func checkUpdates() {
    guard !updateBusy else { return }
    guard bundled else {
      NSWorkspace.shared.open(URL(string: "https://github.com/rainlotus97/motionlab-releases/releases/latest")!)
      return
    }
    let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    updateCommand(["check", "--version", current], title: "正在检查更新…") { [weak self] result in
      guard let self = self else { return }
      if let error = result["error"] as? String {
        self.updateMessage("未能检查更新", error)
        return
      }
      guard result["available"] as? Bool == true, let latest = result["version"] as? String else {
        self.updateMessage("已是最新版本", "当前版本：" + current); return
      }
      let alert = NSAlert(); alert.messageText = "MotionLab " + latest + " 可用"
      alert.informativeText = "下载校验后将保存调整、退出并替换应用，然后重新打开。工程与素材保留，旧应用会另存为备份。"
      alert.addButton(withTitle: "下载并安装"); alert.addButton(withTitle: "稍后")
      guard alert.runModal() == .alertFirstButtonReturn else { return }
      self.updateCommand(["prepare", "--app", Bundle.main.bundleURL.path, "--version", latest, "--owner-pid", String(getpid())], title: "正在下载并校验更新…") { result in
        if let error = result["error"] as? String { self.updateMessage("更新未安装", error); return }
        guard let stage = result["stage"] as? String else { return }
        self.finishUpdate(stage)
      }
    }
  }
  func finishUpdate(_ stage: String) {
    web.callAsyncJavaScript("await window.motionStudioCommands?.prepareClose()", arguments: [:], in: nil, in: .page) { [weak self] result in
      guard let self = self else { return }
      if case .failure(let error) = result { self.updateMessage("更新尚未安装", "请先保存调整后重试。" + error.localizedDescription); return }
      var supportsARM: Int32 = 0
      var length = MemoryLayout<Int32>.size
      let query = sysctlbyname("hw.optional.arm64", &supportsARM, &length, nil, 0)
      let architecture = query == 0 && supportsARM == 1 ? "arm64" : "x86_64"
      let resources = stage + "/MotionLab.app/Contents/Resources"
      let helper = Process()
      helper.executableURL = URL(fileURLWithPath: resources + "/runtime/" + architecture + "/python/bin/python3")
      helper.arguments = ["-B", resources + "/character-motion/scripts/studio_update.py", "apply", "--stage", stage]
      helper.environment = self.runtimeEnvironment()
      let log = stage + "/apply.log"
      FileManager.default.createFile(atPath: log, contents: nil)
      let output = FileHandle(forWritingAtPath: log)
      helper.standardOutput = output; helper.standardError = output
      do {
        try helper.run()
        self.updateQuitting = true; NSApp.terminate(nil)
      } catch { self.updateMessage("无法安装更新", error.localizedDescription) }
    }
  }
}

let app = NSApplication.shared
let delegate = StudioApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
