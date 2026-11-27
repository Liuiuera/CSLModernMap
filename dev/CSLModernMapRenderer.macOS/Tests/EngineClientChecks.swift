import Foundation

@main struct EngineClientChecks {
    @MainActor static func main() async {
        signal(SIGPIPE, SIG_IGN)
        let client = EngineClient()
        var failure = ""
        client.failed = { failure = $0.localizedDescription }
        var checks: [String] = []
        do {
            try client.start()
            let path = "城市 数据/泉都.cmm.gz"
            let result = try await client.request("hello", ["path":path])
            guard result["path"] as? String == path else { throw AppFailure(message:"UTF-8 roundtrip failed") }
            checks.append("jsonl-unicode-space-path")
            let artwork = String(repeating: "城市\n", count: 1_000_000)
            let large = try await client.request("hello", ["artwork": artwork])
            guard large["artwork"] as? String == artwork else { throw AppFailure(message:"Large JSONL frame was truncated") }
            checks.append("multi-megabyte-jsonl-roundtrip")
            do { _ = try await client.request("error"); throw AppFailure(message:"Expected server error") }
            catch { guard error.localizedDescription.contains("fixture error") else { throw error } }
            checks.append("error-propagation")
            do { _ = try await client.request("timeout"); throw AppFailure(message:"Expected timeout") }
            catch {
                let expected = String(format: localized("等待%@超时，请重试。"), NativePresentation.operation("timeout"))
                guard error.localizedDescription == expected else { throw error }
            }
            _ = try await client.request("hello")
            guard client.diagnostics.contains("stderr-marker"), client.diagnostics.count <= 32000 else { throw AppFailure(message:"stderr not drained or unbounded") }
            checks.append("timeout-recovery-stderr-drain")
            do { _ = try await client.request("exit"); throw AppFailure(message:"Expected process failure") }
            catch { guard error.localizedDescription.contains("23") else { throw error } }
            guard !failure.isEmpty else { throw AppFailure(message:"Missing failure callback") }
            checks.append("abnormal-exit-pending-request")
            let closing = EngineClient()
            closing.failed = { _ in failure = "unexpected close error" }
            try closing.start();_ = try await closing.request("hello")
            await closing.shutdown()
            guard failure != "unexpected close error" else { throw AppFailure(message:failure) }
            checks.append("clean-shutdown")
            print(try jsonString(["success":true,"checks":checks]))
        } catch {
            print((try? jsonString(["success":false,"checks":checks,"error":error.localizedDescription])) ?? "failed")
            await client.shutdown()
            exit(1)
        }
    }
}
