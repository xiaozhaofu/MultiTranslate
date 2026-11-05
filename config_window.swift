// 原生配置窗口。凭据仅经匿名管道传递，保存与校验统一交给 Python 后端。
import AppKit
import Foundation

enum SettingValue: Codable, Equatable {
    case text(String), flag(Bool)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) { self = .flag(value) }
        else { self = .text(try container.decode(String.self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .text(let value): try container.encode(value)
        case .flag(let value): try container.encode(value)
        }
    }

    var text: String { if case .text(let value) = self { return value }; return "" }
    var flag: Bool { self == .flag(true) }
}

struct Field: Decodable {
    struct Options: Decodable {
        let text: String?
        let placeholder: String?
        let pairs: [[String]]?
    }
    let variable: String
    let label: String
    let description: String
    let type: String
    let config: Options
}

struct Group: Decodable {
    let title: String
    let variables: [String]
    let enable: String?
}

struct WindowInput: Decodable {
    let fields: [Field]
    let groups: [Group]
    let values: [String: SettingValue]
    let snapshot: String?
    let python: String
    let helper: String
    let note: String?
    let previewPath: String?
    let windowPreviewPath: String?
}

struct WorkerReply: Decodable {
    let ok: Bool
    let message: String?
}

struct SaveRequest: Encodable {
    let values: [String: SettingValue]
    let snapshot: String?

    // 首次保存也必须显式传 null，供后端核对原文件不存在。
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(values, forKey: .values)
        try container.encode(snapshot, forKey: .snapshot)
    }
    enum CodingKeys: String, CodingKey { case values, snapshot }
}

final class FormView: NSView {
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        dirtyRect.fill()
    }
}

final class FieldControl {
    let field: Field
    let view: NSControl
    var plain: NSTextField?

    init(_ field: Field, _ view: NSControl) { self.field = field; self.view = view }

    var value: SettingValue {
        if let popup = view as? NSPopUpButton, let pairs = field.config.pairs,
           pairs.indices.contains(popup.indexOfSelectedItem) {
            return .text(pairs[popup.indexOfSelectedItem][1])
        }
        if let button = view as? NSButton { return .flag(button.state == .on) }
        if let plain = plain, !plain.isHidden { return .text(plain.stringValue) }
        return .text((view as? NSTextField)?.stringValue ?? "")
    }
}

final class SettingsWindow: NSObject, NSWindowDelegate {
    let input: WindowInput
    let window: NSWindow
    let root = FormView()
    let document = FormView()
    let scroll = NSScrollView()
    let header = NSView()
    let footer = FormView()
    var controls: [String: FieldControl] = [:]
    var separators: [NSBox] = []
    var groupTitles: [NSTextField] = []
    var outcome = "cancelled"
    var showSecrets = false
    let status = NSTextField(labelWithString: "")
    let saveButton = NSButton()
    let cancelButton = NSButton()

    init(_ input: WindowInput) {
        self.input = input
        let screen = NSScreen.main?.visibleFrame.size ?? NSSize(width: 1024, height: 900)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: min(860, screen.width - 40),
                                             height: min(780, screen.height - 70)),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "MultiTranslate · 翻译设置"
        window.minSize = NSSize(width: 720, height: 560)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = root
        root.addSubview(header)
        root.addSubview(scroll)
        root.addSubview(footer)
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        scroll.documentView = document
        layoutWindow()
        buildHeader()
        buildFooter()
        buildForm()
        layoutWindow()
        updateStatus()
        window.center()
    }

    func label(_ text: String, _ frame: NSRect, size: CGFloat = 13,
               bold: Bool = false, help: Bool = false, parent: NSView) -> NSTextField {
        let view = NSTextField(labelWithString: text)
        view.frame = frame
        view.font = bold ? NSFont.systemFont(ofSize: size, weight: .semibold) : NSFont.systemFont(ofSize: size)
        view.textColor = help ? .secondaryLabelColor : .labelColor
        view.lineBreakMode = .byTruncatingTail
        parent.addSubview(view)
        return view
    }

    func button(_ text: String, _ action: Selector, _ frame: NSRect, parent: NSView) -> NSButton {
        let button = NSButton(title: text, target: self, action: action)
        button.frame = frame
        button.bezelStyle = .rounded
        parent.addSubview(button)
        return button
    }

    func buildHeader() {
        // header 使用普通坐标：从窗口顶部向下独立放置。
        _ = label("翻译设置", NSRect(x: 28, y: 50, width: 360, height: 27), size: 20, bold: true, parent: header)
        _ = label("选择服务商、填写密钥，并按需要设置代理。", NSRect(x: 28, y: 25, width: 560, height: 20),
                  size: 12, help: true, parent: header)
        let reveal = NSButton(checkboxWithTitle: "显示密钥和代理地址", target: self, action: #selector(toggleSecrets))
        reveal.frame = NSRect(x: header.bounds.width - 248, y: 50, width: 220, height: 25)
        reveal.autoresizingMask = [.minXMargin]
        header.addSubview(reveal)
    }

    func buildFooter() {
        let rule = NSBox(frame: NSRect(x: 24, y: 0, width: footer.bounds.width - 48, height: 1))
        rule.boxType = .separator
        rule.autoresizingMask = [.width]
        footer.addSubview(rule)
        status.frame = NSRect(x: 28, y: 10, width: footer.bounds.width - 56, height: 25)
        status.font = NSFont.systemFont(ofSize: 11)
        status.textColor = .secondaryLabelColor
        status.lineBreakMode = .byTruncatingTail
        status.autoresizingMask = [.width]
        footer.addSubview(status)
        _ = button("编辑 JSON…", #selector(openJSON), NSRect(x: 22, y: 40, width: 120, height: 32), parent: footer)
        _ = button("原生面板…", #selector(openNative), NSRect(x: 148, y: 40, width: 112, height: 32), parent: footer)
        cancelButton.title = "取消"
        cancelButton.target = self
        cancelButton.action = #selector(cancel)
        cancelButton.bezelStyle = .rounded
        cancelButton.keyEquivalent = "\u{1b}"
        footer.addSubview(cancelButton)
        saveButton.title = "保存"
        saveButton.target = self
        saveButton.action = #selector(save)
        saveButton.bezelStyle = .rounded
        saveButton.keyEquivalent = "\r"
        footer.addSubview(saveButton)
    }

    func layoutWindow() {
        let size = root.bounds.size
        header.frame = NSRect(x: 0, y: 0, width: size.width, height: 94)
        footer.frame = NSRect(x: 0, y: size.height - 88, width: size.width, height: 88)
        scroll.frame = NSRect(x: 0, y: 94, width: size.width, height: size.height - 182)
        document.setFrameSize(NSSize(width: scroll.contentSize.width, height: document.frame.height))
        cancelButton.frame = NSRect(x: size.width - 205, y: 40, width: 84, height: 32)
        saveButton.frame = NSRect(x: size.width - 116, y: 40, width: 92, height: 32)
    }

    func windowDidResize(_ notification: Notification) { layoutWindow() }

    func isSecret(_ name: String) -> Bool {
        return name.hasSuffix("_SK") || name.hasSuffix("_APPKEY") || name.hasSuffix("_APP_SECRET")
            || name.hasSuffix("_PROXY_URL") || name == "MT_AZURE_KEY"
    }

    func buildForm() {
        let fields = Dictionary(uniqueKeysWithValues: input.fields.map { ($0.variable, $0) })
        let width = document.frame.width
        var y: CGFloat = 8
        for (index, group) in input.groups.enumerated() {
            if index > 0 {
                let separator = NSBox(frame: NSRect(x: 24, y: y, width: width - 48, height: 1))
                separator.boxType = .separator
                separator.autoresizingMask = [.width]
                document.addSubview(separator)
                separators.append(separator)
                y += 27
            }
            let title = label(group.title, NSRect(x: 28, y: y, width: width - 250, height: 24),
                              size: 16, bold: true, parent: document)
            title.autoresizingMask = [.width]
            groupTitles.append(title)
            if let name = group.enable, let field = fields[name] {
                let control = NSButton(checkboxWithTitle: "启用此服务商", target: nil, action: nil)
                control.frame = NSRect(x: width - 190, y: y, width: 166, height: 24)
                control.autoresizingMask = [.minXMargin]
                control.state = input.values[name]?.flag == true ? .on : .off
                control.setAccessibilityLabel(group.title + " · 启用此服务商")
                document.addSubview(control)
                controls[name] = FieldControl(field, control)
            }
            y += 40
            for name in group.variables where name != group.enable {
                guard let field = fields[name] else { continue }
                addField(field, y: y, width: width)
                y += 72
            }
            y += 11
        }
        document.setFrameSize(NSSize(width: width, height: y + 4))
    }

    func addField(_ field: Field, y: CGFloat, width: CGFloat) {
        let title = label(field.label, NSRect(x: 28, y: y + 4, width: 184, height: 23), parent: document)
        title.setAccessibilityElement(false)
        let frame = NSRect(x: 226, y: y, width: width - 254, height: 28)
        let value = input.values[field.variable] ?? .text("")
        let control: NSControl
        if field.type == "checkbox" {
            let checkbox = NSButton(checkboxWithTitle: field.config.text ?? "启用", target: self, action: #selector(configChanged))
            checkbox.state = value.flag ? .on : .off
            control = checkbox
        } else if field.type == "popupbutton" {
            let popup = NSPopUpButton(frame: frame, pullsDown: false)
            for pair in field.config.pairs ?? [] { popup.addItem(withTitle: pair[0]) }
            let selected = field.config.pairs?.firstIndex(where: { $0[1] == value.text }) ?? -1
            popup.selectItem(at: selected)
            control = popup
        } else {
            let text: NSTextField = isSecret(field.variable) ? NSSecureTextField(frame: frame) : NSTextField(frame: frame)
            text.stringValue = value.text
            text.placeholderString = field.config.placeholder
            text.isEditable = true
            text.isSelectable = true
            control = text
        }
        control.frame = frame
        control.font = NSFont.systemFont(ofSize: 13)
        control.autoresizingMask = [.width]
        control.setAccessibilityLabel(field.label)
        control.toolTip = field.description
        document.addSubview(control)
        let record = FieldControl(field, control)
        if isSecret(field.variable) {
            let plain = NSTextField(frame: frame)
            plain.stringValue = value.text
            plain.placeholderString = field.config.placeholder
            plain.font = control.font
            plain.isHidden = true
            plain.autoresizingMask = [.width]
            plain.setAccessibilityLabel(field.label)
            document.addSubview(plain)
            record.plain = plain
        }
        controls[field.variable] = record
        let help = label(field.description, NSRect(x: 226, y: y + 33, width: width - 254, height: 31),
                         size: 11, help: true, parent: document)
        help.lineBreakMode = .byWordWrapping
        help.maximumNumberOfLines = 2
        help.autoresizingMask = [.width]
    }

    func values() -> [String: SettingValue] { controls.mapValues { $0.value } }

    @objc func configChanged() { updateStatus() }

    func updateStatus() {
        let source = controls["MT_USE_PANEL"]?.value.flag == true
            ? "保存后使用本窗口配置。" : "当前使用 JSON；要使用本窗口的设置，请勾选“使用窗口配置”。"
        status.stringValue = [input.note ?? "", source].filter { !$0.isEmpty }.joined(separator: " ")
        status.toolTip = status.stringValue
    }

    @objc func toggleSecrets(_ sender: NSButton) {
        window.makeFirstResponder(nil)
        showSecrets = sender.state == .on
        for record in controls.values {
            guard let plain = record.plain, let secure = record.view as? NSTextField else { continue }
            let source = showSecrets ? secure : plain
            let destination = showSecrets ? plain : secure
            destination.stringValue = source.stringValue
            source.isHidden = true
            destination.isHidden = false
        }
    }

    func callWorker(_ action: String) -> WorkerReply {
        do {
            let process = Process()
            let stdin = Pipe(), stdout = Pipe()
            process.executableURL = URL(fileURLWithPath: input.python)
            process.arguments = ["-B", input.helper, action]
            process.standardInput = stdin
            process.standardOutput = stdout
            process.standardError = FileHandle.nullDevice
            let content = (action == "save" || action == "validate")
                ? try JSONEncoder().encode(SaveRequest(values: values(), snapshot: input.snapshot)) : Data()
            try process.run()
            // 总输入由 34 项设置组成，stdin 关闭后 Python 才处理，不产生双向管道等待。
            try stdin.fileHandleForWriting.write(contentsOf: content)
            try stdin.fileHandleForWriting.close()
            let result = stdout.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else { throw CocoaError(.executableRuntimeMismatch) }
            return try JSONDecoder().decode(WorkerReply.self, from: result)
        } catch {
            return WorkerReply(ok: false, message: "配置操作未完成，请使用原生面板或 JSON 入口检查。")
        }
    }

    func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "设置尚未保存"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "继续编辑")
        alert.runModal()
    }

    @objc func save() {
        window.makeFirstResponder(nil)
        saveButton.isEnabled = false
        let reply = callWorker("save")
        saveButton.isEnabled = true
        if reply.ok { finish("saved") }
        else { showError(reply.message ?? "保存失败，请检查填写内容。") }
    }

    @objc func openJSON() {
        let reply = callWorker("open-json")
        if !reply.ok { showError(reply.message ?? "无法打开 JSON 文件。") }
    }

    @objc func openNative() {
        let reply = callWorker("open-native")
        if !reply.ok { showError(reply.message ?? "无法打开原生面板。") }
    }

    @objc func cancel() {
        window.makeFirstResponder(nil)
        if values() != input.values {
            let alert = NSAlert()
            alert.messageText = "放弃尚未保存的修改？"
            alert.informativeText = "关闭后不会保存本窗口的修改。"
            alert.addButton(withTitle: "继续编辑")
            alert.addButton(withTitle: "放弃修改")
            if alert.runModal() != .alertSecondButtonReturn { return }
        }
        finish("cancelled")
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { cancel(); return false }

    func finish(_ action: String) {
        outcome = action
        window.orderOut(nil)
        NSApp.stop(nil)
        // 唤醒 run()，确保停止后返回给 Python 启动器。
        if let event = NSEvent.otherEvent(with: .applicationDefined, location: .zero, modifierFlags: [],
                                         timestamp: 0, windowNumber: 0, context: nil, subtype: 0,
                                         data1: 0, data2: 0) {
            NSApp.postEvent(event, atStart: true)
        }
    }

    func inspect(probeHelper: Bool) throws -> [String: Any] {
        document.layoutSubtreeIfNeeded()
        if let path = input.previewPath,
           let bitmap = document.bitmapImageRepForCachingDisplay(in: document.bounds) {
            document.cacheDisplay(in: document.bounds, to: bitmap)
            guard let data = bitmap.representation(using: .png, properties: [:]) else {
                throw CocoaError(.fileWriteUnknown)
            }
            try data.write(to: URL(fileURLWithPath: path))
        }
        if let path = input.windowPreviewPath,
           let bitmap = root.bitmapImageRepForCachingDisplay(in: root.bounds) {
            root.cacheDisplay(in: root.bounds, to: bitmap)
            guard let data = bitmap.representation(using: .png, properties: [:]) else {
                throw CocoaError(.fileWriteUnknown)
            }
            try data.write(to: URL(fileURLWithPath: path))
        }
        var result: [String: Any] = [
            "groups": groupTitles.map { $0.stringValue },
            "fields": controls.keys.sorted(),
            "masked": controls.filter { $0.value.plain != nil && $0.value.plain!.isHidden }.keys.sorted(),
            "documentWidth": document.frame.width,
            "documentHeight": document.frame.height,
            "separators": separators.map { (separator: NSBox) -> [String: Any] in ["x": separator.frame.minX, "y": separator.frame.minY,
                                             "width": separator.frame.width, "isSeparator": separator.boxType == .separator] }
        ]
        if probeHelper { result["helperOK"] = callWorker("validate").ok }
        return result
    }
}

func installMenu(for controller: SettingsWindow) {
    // 独立程序需要标准编辑菜单，让密钥输入框支持 Cmd-C / Cmd-V 等快捷键。
    let menu = NSMenu()
    let application = NSMenuItem()
    menu.addItem(application)
    let applicationMenu = NSMenu(title: "MultiTranslate")
    let close = NSMenuItem(title: "关闭设置窗口", action: #selector(SettingsWindow.cancel), keyEquivalent: "q")
    close.target = controller
    applicationMenu.addItem(close)
    application.submenu = applicationMenu
    let edit = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
    let editMenu = NSMenu(title: "编辑")
    for (title, action, key) in [("剪切", #selector(NSText.cut(_:)), "x"),
                                 ("复制", #selector(NSText.copy(_:)), "c"),
                                 ("粘贴", #selector(NSText.paste(_:)), "v"),
                                 ("全选", #selector(NSText.selectAll(_:)), "a")] {
        editMenu.addItem(NSMenuItem(title: title, action: action, keyEquivalent: key))
    }
    edit.submenu = editMenu
    menu.addItem(edit)
    NSApp.mainMenu = menu
}

func writeResult(_ object: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}

do {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard data.count <= 1024 * 1024 else { throw CocoaError(.fileReadTooLarge) }
    let input = try JSONDecoder().decode(WindowInput.self, from: data)
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let controller = SettingsWindow(input)
    installMenu(for: controller)
    if CommandLine.arguments.contains("--inspect") {
        try writeResult(controller.inspect(probeHelper: CommandLine.arguments.contains("--probe-helper")))
    } else {
        controller.window.makeKeyAndOrderFront(nil)
        app.activate(ignoringOtherApps: true)
        if CommandLine.arguments.contains("--test-cancel") {
            DispatchQueue.main.async { controller.cancel() }
        } else if CommandLine.arguments.contains("--test-save") {
            DispatchQueue.main.async { controller.save() }
        }
        app.run()
        try writeResult(["action": controller.outcome])
    }
} catch {
    // 不回显输入、错误正文或路径，避免把私有配置写入 Alfred 调试输出。
    try? writeResult(["action": "failed", "message": "无法打开配置窗口，请使用 trconfig-native 或 trconfig-json。"])
    exit(1)
}
