import AppKit

/// Display canvas status while WebKit loads.
@MainActor final class PreviewPlaceholder: NSView {
    enum State: Equatable {
        case empty(String), loading(String), unavailable(String), ready
    }

    private(set) var state: State = .ready
    private let icon = NSImageView()
    private let spinner = NSProgressIndicator()
    private let message = NSTextField(wrappingLabelWithString: "")
    private let progressBar = NSProgressIndicator()
    private let progressStage = NSTextField(labelWithString: "")
    private let actionButton: ActionButton?

    private let cancelButton: ActionButton?

    init(open: (() -> Void)? = nil, cancel: (() -> Void)? = nil) {
        cancelButton = cancel.map { ActionButton("取消任务", $0) }
        actionButton = open.map { ActionButton("打开城市…", $0) }
        super.init(frame: .zero)
        icon.contentTintColor = .secondaryLabelColor
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.setAccessibilityElement(false)
        let size: CGFloat = open == nil ? 24 : 36
        icon.widthAnchor.constraint(equalToConstant: size).isActive = true
        icon.heightAnchor.constraint(equalToConstant: size).isActive = true
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.isIndeterminate = true
        spinner.isDisplayedWhenStopped = false
        message.font = .systemFont(ofSize: open == nil ? 13 : 16, weight: .medium)
        message.textColor = .labelColor
        message.alignment = .center
        let stack = column([icon, spinner, message] + [actionButton, cancelButton].compactMap { $0 }, gap: 12)
        if open != nil {
            progressBar.style = .bar
            progressBar.isIndeterminate = false
            progressBar.widthAnchor.constraint(equalToConstant: 184).isActive = true
            progressStage.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
            progressStage.textColor = .secondaryLabelColor
            progressStage.lineBreakMode = .byTruncatingMiddle
            progressStage.alignment = .center
            stack.insertArrangedSubview(progressBar, at: 3)
            stack.insertArrangedSubview(progressStage, at: 4)
            clearProgress()
        }
        stack.alignment = .centerX
        addSubview(stack)
        if open != nil {
            progressStage.widthAnchor.constraint(lessThanOrEqualTo: widthAnchor, constant: -28).isActive = true
        }
        NSLayoutConstraint.activate([stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: widthAnchor, constant: -28)])
        isHidden = true
    }
    required init?(coder: NSCoder) { fatalError() }

    func show(_ newState: State) {
        if state != newState { clearProgress() }
        state = newState
        var working = false
        var symbolName = "map"
        let title: String
        switch newState {
        case .empty(let text): title = text
        case .loading(let text): title = text; working = true; symbolName = "hourglass"
        case .unavailable(let text): title = text; symbolName = "exclamationmark.circle"
        case .ready:
            isHidden = true
            spinner.stopAnimation(nil)
            return
        }
        isHidden = false
        message.stringValue = localized(title)
        spinner.setAccessibilityLabel(message.stringValue)
        let measured = working && actionButton != nil && !progressBar.isHidden
        let animate = working && !measured && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        spinner.isHidden = !animate
        icon.isHidden = animate || measured
        if animate { spinner.startAnimation(nil) }
        else {
            spinner.stopAnimation(nil)
            icon.image = symbol(symbolName, size: actionButton == nil ? 24 : 36)
        }
        cancelButton?.isHidden = !working
        cancelButton?.title = localized("取消任务")
        actionButton?.isHidden = working
        actionButton?.title = localized("打开城市…")
    }

    func clearProgress() {
        guard actionButton != nil else { return }
        progressBar.isHidden = true
        progressStage.isHidden = true
    }

    func showProgress(_ percent: Double, stage: String) {
        guard case .loading = state, actionButton != nil else { return }
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        icon.isHidden = true
        progressBar.doubleValue = percent
        progressBar.setAccessibilityLabel(stage.isEmpty ? message.stringValue : stage)
        let percentage = "\(Int(percent))%"
        progressStage.stringValue = stage.isEmpty ? percentage : "\(stage) · \(percentage)"
        progressStage.isHidden = false
        progressBar.isHidden = false
    }

    func enableOpen(_ enabled: Bool) { actionButton?.isEnabled = enabled }
}
