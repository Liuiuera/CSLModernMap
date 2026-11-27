import Foundation

struct PreviewQuality: Codable, Equatable {
    var pixelScale: Double = 1
    var lineDetail = "standard"
    var polygonDetail = "standard"
    var textPercent = 100
    var linePercent = 100
    var iconPercent = 100

    init(pixelScale: Double = 1, lineDetail: String = "standard", polygonDetail: String = "standard",
         textPercent: Int = 100, linePercent: Int = 100, iconPercent: Int = 100) {
        self.pixelScale = pixelScale; self.lineDetail = lineDetail; self.polygonDetail = polygonDetail
        self.textPercent = textPercent; self.linePercent = linePercent; self.iconPercent = iconPercent
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        pixelScale = try values.decode(Double.self, forKey: .pixelScale)
        lineDetail = try values.decode(String.self, forKey: .lineDetail)
        polygonDetail = try values.decode(String.self, forKey: .polygonDetail)
        textPercent = try values.decodeIfPresent(Int.self, forKey: .textPercent) ?? 100
        linePercent = try values.decodeIfPresent(Int.self, forKey: .linePercent) ?? 100
        iconPercent = try values.decodeIfPresent(Int.self, forKey: .iconPercent) ?? 100
    }

    var isValid: Bool {
        [0.75,1,1.25].contains(pixelScale) &&
        [lineDetail,polygonDetail].allSatisfy { ["standard","fine"].contains($0) } &&
        [textPercent,linePercent,iconPercent].allSatisfy { (50...200).contains($0) }
    }

    var message: [String: Any] {
        ["pixelScale":pixelScale,"lineDetail":lineDetail,"polygonDetail":polygonDetail,
         "textPercent":textPercent,"linePercent":linePercent,"iconPercent":iconPercent]
    }

    func matchesPreset(_ value: Self) -> Bool {
        pixelScale == value.pixelScale && lineDetail == value.lineDetail && polygonDetail == value.polygonDetail
    }

    static var saved: PreviewQuality {
        guard let data = UserDefaults.standard.data(forKey:"previewQuality"),
              let value = try? JSONDecoder().decode(Self.self, from:data), value.isValid else { return Self() }
        return value
    }

    func save() throws {
        UserDefaults.standard.set(try JSONEncoder().encode(self), forKey:"previewQuality")
    }
}
