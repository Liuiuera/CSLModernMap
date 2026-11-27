import Foundation
import CoreGraphics

@main struct MapRangeChecks {
    static func main() {
        let world = MapRangeBounds([-4000.0, -8000.0, 4000.0, 8000.0])!
        let region = MapRangeBounds([-2000.0, -4000.0, 2000.0, 2000.0])!
        let frame = world.fitted(in: CGRect(x: 0, y: 0, width: 400, height: 400))
        precondition(frame == CGRect(x: 0, y: 100, width: 400, height: 200))
        precondition(world.point(CGPoint(x: 0, y: 100), in: frame) == CGPoint(x: 4000, y: 8000))
        precondition(world.point(CGPoint(x: 400, y: 300), in: frame) == CGPoint(x: -4000, y: -8000))
        precondition(world.point(CGPoint(x: -30, y: 900), in: frame) == CGPoint(x: -4000, y: 8000))
        let outline = world.rect(of: region, in: frame)
        precondition(outline == CGRect(x: 150, y: 150, width: 150, height: 100))
        precondition(world.point(CGPoint(x: outline.minX, y: outline.minY), in: frame) == CGPoint(x: 2000, y: 2000))
        precondition(world.point(CGPoint(x: outline.maxX, y: outline.maxY), in: frame) == CGPoint(x: -2000, y: -4000))
        let moved = region.moved(by: CGPoint(x: 9999, y: -9999), inside: world)
        precondition(moved.values == [0, -8000, 4000, -2000])
        precondition(world.contains(moved) && moved.width == region.width && moved.height == region.height)
        precondition(!region.contains(world))
        precondition(region.dimensions(edge: 4096, rotation: 0) == (4096, 2731))
        precondition(region.dimensions(edge: 4096, rotation: 90) == (2731, 4096))
        precondition(region.dimensions(edge: 4096, rotation: 180) == (4096, 2731))
        precondition(region.dimensions(edge: 4096, rotation: 270) == (2731, 4096))
        precondition(MapRangeBounds([0.0, 0.0, 0.0, 1.0]) == nil)
        precondition(MapRangeBounds([0.0, 0.0, Double.infinity, 1.0]) == nil)
        precondition(MapRangeBounds([0.0, 0.0, 1.0, 0.0000001]) == nil)
        print("MapRangeChecks passed: aspect, projection, clipping, movement, rotations, invalid bounds")
    }
}
