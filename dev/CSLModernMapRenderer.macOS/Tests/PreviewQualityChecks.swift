import Foundation

@main struct PreviewQualityChecks {
    static func main() throws {
        let legacy = Data(#"{"pixelScale":1.25,"lineDetail":"fine","polygonDetail":"fine"}"#.utf8)
        let decoded = try JSONDecoder().decode(PreviewQuality.self, from:legacy)
        precondition(decoded == PreviewQuality(pixelScale:1.25, lineDetail:"fine", polygonDetail:"fine"))
        precondition(decoded.isValid)
        let quality = PreviewQuality(pixelScale:1.25, lineDetail:"fine", polygonDetail:"fine",
                                     textPercent:50, linePercent:150, iconPercent:200)
        let roundTrip = try JSONDecoder().decode(PreviewQuality.self, from:JSONEncoder().encode(quality))
        precondition(roundTrip == quality && quality.isValid && quality.matchesPreset(decoded))
        precondition(quality.message["textPercent"] as? Int == 50)
        precondition(quality.message["linePercent"] as? Int == 150)
        precondition(quality.message["iconPercent"] as? Int == 200)
        precondition(!PreviewQuality(textPercent:49).isValid)
        precondition(!PreviewQuality(iconPercent:201).isValid)
        precondition(!quality.matchesPreset(PreviewQuality()))
        print("Preview quality configuration checks passed")
    }
}
