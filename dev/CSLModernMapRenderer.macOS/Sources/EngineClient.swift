import Foundation

typealias JSON = [String: Any]
struct AppFailure: LocalizedError {
    let message: String
    let details: String
    init(message: String) {
        self.message = localized(message)
        details = self.message
    }
    init(payload: JSON) {
        message = textValue(payload["message"] ?? payload["exceptionMessage"] ?? payload["details"])
        let fields = ["stage", "exceptionType", "exceptionMessage", "details", "traceback", "requestId", "version"]
        let lines = fields.compactMap { key -> String? in
            let value = textValue(payload[key])
            return value.isEmpty ? nil : key + ": " + value
        }
        details = ([message] + lines).joined(separator: "\n")
    }
    var errorDescription: String? { message }
}
func object(_ value: Any?) -> JSON { value as? JSON ?? [:] }
func array(_ value: Any?) -> [JSON] { value as? [JSON] ?? [] }
func textValue(_ value: Any?) -> String { guard let value = value, !(value is NSNull) else { return "" }; return String(describing: value) }
func jsonString(_ value: Any) throws -> String { String(data: try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .fragmentsAllowed]), encoding: .utf8)! }

// Pipe readers frame JSONL. The main queue handles requests.
@MainActor
final class EngineClient {
    var event: ((String, JSON) -> Void)?
    var failed: ((Error) -> Void)?
    private var process: Process?
    private var input: FileHandle?
    private var pending: [Int: CheckedContinuation<JSON, Error>] = [:]
    private var serial = 0
    private var closing = false
    private let writes = DispatchQueue(label: "CSLModernMap.engine.write")
    private(set) var diagnostics = ""
    let dataDirectory: URL = {
        if let path = ProcessInfo.processInfo.environment["CSLMM_NATIVE_DATA_DIR"], !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CSLModernMap", isDirectory: true)
    }()

    func start() throws {
        let resources = Bundle.main.resourceURL!
        let launcher = resources.appendingPathComponent("engine/run-engine")
        guard FileManager.default.isExecutableFile(atPath: launcher.path) else { throw AppFailure(message: String(format: localized("缺少渲染引擎：%@"), launcher.path)) }
        let child = Process(), stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        child.executableURL = launcher
        child.currentDirectoryURL = launcher.deletingLastPathComponent()
        var env = ProcessInfo.processInfo.environment
        env["PYTHONIOENCODING"] = "utf-8"
        env["QT_QPA_PLATFORM"] = "offscreen"
        try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        env["CSLMM_USER_DATA_DIR"] = dataDirectory.path
        env["CSLMM_NATIVE_DATA_DIR"] = dataDirectory.path
        env["CSLMM_SPLIT_MAP_SYMBOLS"] = "1"
        child.environment = env
        child.standardInput = stdin; child.standardOutput = stdout; child.standardError = stderr
        child.terminationHandler = { [weak self] child in DispatchQueue.main.async {
            guard let self = self, !self.closing else { return }
            self.fail(AppFailure(message: String(format: localized("引擎已退出（%d）。"), child.terminationStatus) + "\n" + self.diagnostics.suffix(3000)))
        } }
        try child.run(); process = child; input = stdin.fileHandleForWriting
        read(stdout.fileHandleForReading, protocolStream: true)
        read(stderr.fileHandleForReading, protocolStream: false)
    }
    private func read(_ handle: FileHandle, protocolStream: Bool) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var buffer = Data()
            let newline = Data([0x0a])
            while true {
                let bytes = handle.availableData
                if bytes.isEmpty { break }
                // Scan only newly received bytes for delimiters.
                var searchStart = buffer.endIndex
                buffer.append(bytes)
                while let end = buffer.range(of: newline, in: searchStart..<buffer.endIndex) {
                    let line = Data(buffer[..<end.lowerBound])
                    buffer.removeSubrange(buffer.startIndex..<end.upperBound)
                    searchStart = buffer.startIndex
                    DispatchQueue.main.async { self?.receive(line, protocolStream: protocolStream) }
                }
            }
            if !buffer.isEmpty { let last = buffer; DispatchQueue.main.async { self?.receive(last, protocolStream: protocolStream) } }
        }
    }
    private func receive(_ data: Data, protocolStream: Bool) {
        guard !closing else { return }
        if !protocolStream {
            diagnostics = String((diagnostics + (String(data: data, encoding: .utf8) ?? "") + "\n").suffix(32000)); return
        }
        do {
            guard let frame = try JSONSerialization.jsonObject(with: data) as? JSON else { throw AppFailure(message: "引擎返回无效协议帧") }
            if let name = frame["event"] as? String { event?(name, object(frame["payload"])); return }
            guard let id = frame["id"] as? Int, let continuation = pending.removeValue(forKey: id) else { FileHandle.standardError.write(Data(("missing continuation " + textValue(frame["id"]) + "\n").utf8)); return }
            if let error = frame["error"] {
                let payload = object(error)
                let failure = payload.isEmpty ? AppFailure(message: textValue(error)) : AppFailure(payload: payload)
                diagnostics = String((diagnostics + failure.details + "\n").suffix(32000))
                continuation.resume(throwing: failure)
            } else { continuation.resume(returning: object(frame["result"])) }
        } catch { fail(error) }
    }
    func request(_ method: String, _ params: JSON = [:]) async throws -> JSON {
        dispatchPrecondition(condition: .onQueue(.main))
        guard !closing, process?.isRunning == true, let input = input else { throw AppFailure(message: "渲染引擎不可用") }
        serial += 1; let id = serial
        let bytes = Data((try jsonString(["id": id, "method": method, "params": params]) + "\n").utf8)
        return try await withCheckedThrowingContinuation { continuation in
            register(id, continuation)
            writes.async { [weak self] in
                do { try input.write(contentsOf: bytes) }
                catch { DispatchQueue.main.async { self?.pending.removeValue(forKey: id)?.resume(throwing: error) } }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 120) { [weak self] in
                self?.pending.removeValue(forKey: id)?.resume(throwing: AppFailure(message: String(format: localized("等待%@超时，请重试。"), NativePresentation.operation(method))))
            }
        }
    }
    private func register(_ id: Int, _ continuation: CheckedContinuation<JSON, Error>) {
        pending[id] = continuation
    }
    private func fail(_ error: Error) {
        let requests = pending; pending.removeAll()
        for continuation in requests.values { continuation.resume(throwing: error) }
        failed?(error)
    }
    func close() {
        guard !closing else { return }; closing = true
        for continuation in pending.values { continuation.resume(throwing: CancellationError()) }; pending.removeAll()
        let child = process, handle = input
        writes.async {
            try? handle?.write(contentsOf: Data("{\"id\":0,\"method\":\"close\",\"params\":{}}\n".utf8))
            try? handle?.close()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { if child?.isRunning == true { child?.terminate() } }
    }
    func shutdown() async {
        close()
        for _ in 0..<40 {
            if process?.isRunning != true { return }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }
}
