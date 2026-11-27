import Foundation
import CoreGraphics

struct MapRangeBounds: Equatable {
    let values: [Double]

    init?(_ raw: Any?) {
        guard let bounds = raw as? [Double], bounds.count == 4, bounds.allSatisfy(\.isFinite),
              bounds[2] - bounds[0] > 1e-6, bounds[3] - bounds[1] > 1e-6 else { return nil }
        values = bounds
    }

    var width: Double { values[3] - values[1] }
    var height: Double { values[2] - values[0] }

    func contains(_ other: Self) -> Bool {
        values[0] <= other.values[0] && values[1] <= other.values[1]
            && values[2] >= other.values[2] && values[3] >= other.values[3]
    }

    func point(_ p: CGPoint, in rect: CGRect) -> CGPoint {
        let u = min(1, max(0, (p.x - rect.minX) / rect.width))
        let v = min(1, max(0, (p.y - rect.minY) / rect.height))
        return CGPoint(x: values[2] - v * height, y: values[3] - u * width)
    }

    func rect(of other: Self, in rect: CGRect) -> CGRect {
        CGRect(x: rect.minX + (values[3] - other.values[3]) / width * rect.width,
               y: rect.minY + (values[2] - other.values[2]) / height * rect.height,
               width: other.width / width * rect.width, height: other.height / height * rect.height)
    }

    func fitted(in rect: CGRect) -> CGRect {
        let scale = min(rect.width / width, rect.height / height)
        let w = width * scale, h = height * scale
        return CGRect(x: rect.midX - w / 2, y: rect.midY - h / 2, width: w, height: h)
    }

    func moved(by delta: CGPoint, inside limit: Self) -> Self {
        let dx = min(limit.values[2] - values[2], max(limit.values[0] - values[0], delta.x))
        let dz = min(limit.values[3] - values[3], max(limit.values[1] - values[1], delta.y))
        return Self([values[0]+dx, values[1]+dz, values[2]+dx, values[3]+dz]) ?? self
    }

    func dimensions(edge: Int, rotation: Int) -> (Int, Int) {
        let scale = Double(edge) / max(width, height)
        let w = max(1, Int((width * scale).rounded(.toNearestOrEven)))
        let h = max(1, Int((height * scale).rounded(.toNearestOrEven)))
        return rotation == 90 || rotation == 270 ? (h, w) : (w, h)
    }

    var summary: String { String(format: "%.3f × %.3f km", width / 1000, height / 1000) }
}
