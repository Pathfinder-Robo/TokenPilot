// Gaussian text-contrast wash generator and mask cache for TokenPilot's Liquid Glass theme.
// TokenPilot 液态玻璃主题的高斯文本对比度柔光遮罩生成器与缓存模块。
import AppKit
import CoreGraphics
import QuartzCore
import SwiftUI

// MARK: - Public SwiftUI Text & Anchor Contracts

/// Renders intrinsic `Text(verbatim:)` and records its exact glyph-bounds anchor
/// before any outer layout frames or padding are applied.
///
/// Integration notes for root:
/// 1. Replace `Text(...)` with `FrostText(...)` in `RoutingView`.
/// 2. Tag thin progress bars with `.gaussianFrostAnchor(weight: 0.2)` and
///    icon/control views with `.gaussianFrostAnchor(weight: 0.45)`.
/// 3. Apply `.gaussianFrostField(enabled: palette.materialKind == .liquidGlassEffect)`
///    on the padded panel content BEFORE `LiquidGlassPanelSurfaceModifier`.
public struct FrostText: View {
    public let value: String
    public let weight: Double

    public init(_ value: String) {
        self.value = value
        self.weight = 1.0
    }

    public init(_ value: String, weight: Double) {
        self.value = value
        self.weight = weight
    }

    public var body: some View {
        let activeWeight = value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.0 : weight
        Text(verbatim: value)
            .gaussianFrostAnchor(weight: activeWeight)
    }
}

/// Preference item carrying a view's intrinsic bounds anchor and its Gaussian field weight.
public struct GaussianFrostAnchorItem: Sendable {
    public let bounds: Anchor<CGRect>
    public let weight: Double

    public init(bounds: Anchor<CGRect>, weight: Double = 1.0) {
        self.bounds = bounds
        self.weight = weight
    }
}

/// Preference key accumulating all Gaussian frost anchors within the panel hierarchy.
public struct GaussianFrostAnchorPreferenceKey: PreferenceKey {
    public static let defaultValue: [GaussianFrostAnchorItem] = []

    public static func reduce(
        value: inout [GaussianFrostAnchorItem],
        nextValue: () -> [GaussianFrostAnchorItem]
    ) {
        value.append(contentsOf: nextValue())
    }
}

extension View {
    /// Marks a view's bounds as a Gaussian frost source region with the specified weight.
    /// Typical weights: text = `1.0`, icon/control = `0.45`, progress bar = `0.2`.
    public func gaussianFrostAnchor(weight: Double = 1.0) -> some View {
        transformAnchorPreference(
            key: GaussianFrostAnchorPreferenceKey.self,
            value: .bounds
        ) { items, anchor in
            guard weight > 0.0, weight.isFinite else { return }
            items.append(GaussianFrostAnchorItem(bounds: anchor, weight: weight))
        }
    }

    /// Resolves all descendant frost anchors in card coordinates and renders a single
    /// aggregate non-interactive Gaussian-masked frost + white-wash background.
    public func gaussianFrostField(enabled: Bool = true) -> some View {
        modifier(GaussianFrostFieldModifier(enabled: enabled))
    }
}

// MARK: - Density Field Model & Pure Mask Math

/// A resolved frost region in card coordinates (top-left origin) with a relative weight.
public struct GaussianFrostRegion: Hashable, Sendable {
    public var rect: CGRect
    public var weight: Double

    public init(rect: CGRect, weight: Double = 1.0) {
        self.rect = rect.standardized
        self.weight = weight
    }

    public static func == (lhs: GaussianFrostRegion, rhs: GaussianFrostRegion) -> Bool {
        lhs.rect == rhs.rect && lhs.weight == rhs.weight
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(rect.origin.x)
        hasher.combine(rect.origin.y)
        hasher.combine(rect.size.width)
        hasher.combine(rect.size.height)
        hasher.combine(weight)
    }
}

/// Parameters for a single elliptical 2D Gaussian lobe in card coordinates.
public struct GaussianFrostLobe: Hashable, Sendable {
    public var center: CGPoint
    public var sigmaX: Double
    public var sigmaY: Double
    public var amplitude: Double

    public init(center: CGPoint, sigmaX: Double, sigmaY: Double, amplitude: Double) {
        self.center = center
        self.sigmaX = sigmaX
        self.sigmaY = sigmaY
        self.amplitude = amplitude
    }

    public static func == (lhs: GaussianFrostLobe, rhs: GaussianFrostLobe) -> Bool {
        lhs.center == rhs.center &&
        lhs.sigmaX == rhs.sigmaX &&
        lhs.sigmaY == rhs.sigmaY &&
        lhs.amplitude == rhs.amplitude
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(center.x)
        hasher.combine(center.y)
        hasher.combine(sigmaX)
        hasher.combine(sigmaY)
        hasher.combine(amplitude)
    }
}

/// Pure Gaussian mixture density math, alpha mask generation, and bounded LRU mask cache.
public enum GaussianFrostMask {
    /// Maximum field opacity cap so frost never becomes a hard solid slab.
    public static let maxOpacity: Double = 0.96
    /// Maximum final composited white-wash opacity (`baseWhiteOpacity * maxOpacity <= 0.36`).
    public static let maxWhiteOpacity: Double = 0.36
    /// Smooth rim fade distance in points from the outer card edges.
    public static let rimFadeInset: CGFloat = 8.0
    /// Target horizontal spacing in points per elliptical lobe along a region's baseline.
    public static let lobeSpacing: Double = 45.0
    /// Base Gaussian amplitude for a unit-weight (`1.0`) text region (`2.0...2.8` range).
    public static let unitAmplitude: Double = 2.4
    /// Moderate Gaussian spread preserves soft text-local fades and clear space.
    public static let lobeSpreadMultiplier: Double = 1.25
    /// Smooth cutoff radius in standard deviations (`sigma`).
    public static let cutoffSigma: Double = 3.5
    /// Maximum cached `CGImage` masks in the shared LRU cache (`16...32` range).
    public static let cacheCapacity: Int = 24
    /// Maximum pixel count for a rasterized field mask (`< 1M` pixels).
    public static let maxRasterPixels: Int = 950_000

    private static let cutoffSigmaSquared: Double = cutoffSigma * cutoffSigma
    private static let cutoffTailValue: Double = Foundation.exp(-0.5 * cutoffSigmaSquared)
    private static let cutoffNormalization: Double = 1.0 / (1.0 - cutoffTailValue)

    private static let cache = MaskCache(capacity: cacheCapacity)

    // MARK: Cache Inspection (Testable)

    public static var cacheHits: Int { cache.hits }
    public static var cacheMisses: Int { cache.misses }
    public static var cachedEntryCount: Int { cache.count }

    public static func clearCache() {
        cache.clear()
    }

    // MARK: Lobe Decomposition

    /// Decomposes a single rectangle and weight into one or more elliptical Gaussian lobes along `rect.midY`.
    public static func lobes(for rect: CGRect, weight: Double = 1.0) -> [GaussianFrostLobe] {
        let r = rect.standardized
        guard !r.isNull,
              !r.isInfinite,
              r.origin.x.isFinite,
              r.origin.y.isFinite,
              r.size.width.isFinite,
              r.size.height.isFinite,
              r.width > 0.5,
              r.height > 0.5,
              weight.isFinite,
              weight > 0.0
        else {
            return []
        }

        let clampedWeight = min(max(weight, 0.0), 4.0)
        let width = Double(r.width)
        let height = Double(r.height)
        let count = max(1, Int(Foundation.ceil(width / lobeSpacing)))
        let step = width / Double(count)
        let sigmaX = lobeSpreadMultiplier * max(13.0, step * 0.65)
        let sigmaY = lobeSpreadMultiplier * max(10.0, height * 0.45 + 6.0)
        let amplitude = unitAmplitude * clampedWeight
        let midY = Double(r.midY)
        let minX = Double(r.minX)

        var result: [GaussianFrostLobe] = []
        result.reserveCapacity(count)
        for index in 0..<count {
            let cx = minX + (Double(index) + 0.5) * step
            result.append(
                GaussianFrostLobe(
                    center: CGPoint(x: cx, y: midY),
                    sigmaX: sigmaX,
                    sigmaY: sigmaY,
                    amplitude: amplitude
                )
            )
        }
        return result
    }

    /// Generates all Gaussian lobes for an array of regions and optional parallel weights.
    public static func lobes(regions: [CGRect], weights: [Double]? = nil) -> [GaussianFrostLobe] {
        let normalized = normalizeRegions(regions: regions, weights: weights)
        return lobes(for: normalized)
    }

    /// Generates all Gaussian lobes for an array of `GaussianFrostRegion` values.
    public static func lobes(for regions: [GaussianFrostRegion]) -> [GaussianFrostLobe] {
        var allLobes: [GaussianFrostLobe] = []
        allLobes.reserveCapacity(regions.count * 2)
        for region in regions {
            allLobes.append(contentsOf: lobes(for: region.rect, weight: region.weight))
        }
        return allLobes
    }

    // MARK: Pure Field Evaluation

    /// Evaluates the raw additive Gaussian mixture density at `point`.
    public static func density(at point: CGPoint, lobes: [GaussianFrostLobe]) -> Double {
        guard point.x.isFinite, point.y.isFinite, !lobes.isEmpty else { return 0.0 }
        let px = Double(point.x)
        let py = Double(point.y)
        var sum = 0.0

        for lobe in lobes {
            let dx = px - Double(lobe.center.x)
            let dy = py - Double(lobe.center.y)
            if abs(dx) >= cutoffSigma * lobe.sigmaX || abs(dy) >= cutoffSigma * lobe.sigmaY {
                continue
            }
            let nx = dx / lobe.sigmaX
            let ny = dy / lobe.sigmaY
            let u2 = nx * nx + ny * ny
            if u2 >= cutoffSigmaSquared {
                continue
            }
            let kernel = (Foundation.exp(-0.5 * u2) - cutoffTailValue) * cutoffNormalization
            if kernel > 0.0 {
                sum += lobe.amplitude * kernel
            }
        }
        return sum
    }

    /// Evaluates the raw additive Gaussian mixture density at `point` for regions and optional weights.
    public static func density(
        at point: CGPoint,
        regions: [CGRect],
        weights: [Double]? = nil
    ) -> Double {
        density(at: point, lobes: lobes(regions: regions, weights: weights))
    }

    /// Evaluates the smooth 12pt card-rim attenuation factor in `0.0...1.0`.
    public static func edgeAttenuation(
        at point: CGPoint,
        in size: CGSize,
        rimInset: CGFloat = rimFadeInset
    ) -> Double {
        guard size.width > 0.0, size.height > 0.0, point.x.isFinite, point.y.isFinite else {
            return 0.0
        }
        guard rimInset > 0.0 else { return 1.0 }

        let x = Double(point.x)
        let y = Double(point.y)
        let w = Double(size.width)
        let h = Double(size.height)
        let minEdgeDist = min(min(x, w - x), min(y, h - y))
        if minEdgeDist <= 0.0 {
            return 0.0
        }
        let inset = Double(rimInset)
        if minEdgeDist >= inset {
            return 1.0
        }
        let t = minEdgeDist / inset
        return t * t * (3.0 - 2.0 * t)
    }

    /// Pure testable opacity evaluation `maxOpacity * (1 - exp(-density))` (with optional card-rim fade when `size` is provided).
    public static func opacity(
        at point: CGPoint,
        regions: [CGRect],
        weights: [Double]? = nil,
        in size: CGSize? = nil
    ) -> Double {
        let resolvedLobes = lobes(regions: regions, weights: weights)
        return opacity(at: point, lobes: resolvedLobes, in: size)
    }

    /// Pure testable opacity evaluation for typed `GaussianFrostRegion` inputs.
    public static func opacity(
        at point: CGPoint,
        regions: [GaussianFrostRegion],
        in size: CGSize? = nil
    ) -> Double {
        let resolvedLobes = lobes(for: regions)
        return opacity(at: point, lobes: resolvedLobes, in: size)
    }

    /// Pure testable opacity evaluation from precomputed lobes using smooth saturation `maxOpacity * (1 - exp(-density))`.
    public static func opacity(
        at point: CGPoint,
        lobes: [GaussianFrostLobe],
        in size: CGSize? = nil
    ) -> Double {
        let d = density(at: point, lobes: lobes)
        guard d > 0.0 else { return 0.0 }
        let baseOpacity = min(maxOpacity, maxOpacity * (1.0 - Foundation.exp(-d)))
        if let size {
            return baseOpacity * edgeAttenuation(at: point, in: size)
        }
        return baseOpacity
    }

    // MARK: Raster Mask Generation (`CGImage` & `NSImage`)

    /// Generates (or retrieves from the bounded cache) a premultiplied RGBA `CGImage` mask
    /// where `R = G = B = A = alpha`, suitable for both `NSVisualEffectView.maskImage` and `CALayer.mask`.
    public static func makeCGImage(
        size: NSSize,
        regions: [CGRect],
        weights: [Double]? = nil,
        scale: CGFloat = 1.0
    ) -> CGImage? {
        let normalized = normalizeRegions(regions: regions, weights: weights)
        return makeCGImage(size: size, regions: normalized, scale: scale)
    }

    /// Generates (or retrieves from the bounded cache) a premultiplied RGBA `CGImage` mask for typed regions.
    public static func makeCGImage(
        size: NSSize,
        regions: [GaussianFrostRegion],
        scale: CGFloat = 1.0
    ) -> CGImage? {
        guard size.width >= 1.0,
              size.height >= 1.0,
              size.width.isFinite,
              size.height.isFinite
        else {
            return nil
        }

        let validRegions = regions.filter { region in
            let r = region.rect.standardized
            return !r.isNull &&
                !r.isInfinite &&
                r.origin.x.isFinite &&
                r.origin.y.isFinite &&
                r.width > 0.5 &&
                r.height > 0.5 &&
                region.weight.isFinite &&
                region.weight > 0.0
        }
        guard !validRegions.isEmpty else { return nil }

        let effectiveScale = clampedScale(for: size, requestedScale: scale)
        let key = CacheKey(size: size, scale: effectiveScale, regions: validRegions)
        if let cached = cache.value(for: key) {
            return cached
        }

        let resolvedLobes = lobes(for: validRegions)
        guard !resolvedLobes.isEmpty else { return nil }

        guard let rasterized = rasterizeCGImage(
            size: size,
            scale: effectiveScale,
            lobes: resolvedLobes
        ) else {
            return nil
        }

        cache.insert(rasterized, for: key)
        return rasterized
    }

    /// Generates an `NSImage` mask backed by the cached `CGImage` with explicit stretch semantics.
    public static func makeMask(
        size: NSSize,
        regions: [CGRect],
        weights: [Double]? = nil,
        scale: CGFloat = 1.0
    ) -> NSImage? {
        guard let cgImage = makeCGImage(size: size, regions: regions, weights: weights, scale: scale) else {
            return nil
        }
        return makeStretchingMaskImage(cgImage: cgImage, size: size)
    }

    /// Generates an `NSImage` mask from typed regions backed by the cached `CGImage` with explicit stretch semantics.
    public static func makeMask(
        size: NSSize,
        regions: [GaussianFrostRegion],
        scale: CGFloat = 1.0
    ) -> NSImage? {
        guard let cgImage = makeCGImage(size: size, regions: regions, scale: scale) else {
            return nil
        }
        return makeStretchingMaskImage(cgImage: cgImage, size: size)
    }

    private static func makeStretchingMaskImage(cgImage: CGImage, size: NSSize) -> NSImage {
        let rep = NSBitmapImageRep(cgImage: cgImage)
        rep.size = size
        let image = NSImage(size: size)
        image.addRepresentation(rep)
        image.capInsets = NSEdgeInsets()
        image.resizingMode = .stretch
        image.isTemplate = false
        return image
    }

    // MARK: Internal Helpers

    internal static func normalizeRegions(
        regions: [CGRect],
        weights: [Double]?
    ) -> [GaussianFrostRegion] {
        guard !regions.isEmpty else { return [] }
        var output: [GaussianFrostRegion] = []
        output.reserveCapacity(regions.count)
        for (index, rect) in regions.enumerated() {
            let weight: Double
            if let weights, index < weights.count {
                weight = weights[index]
            } else {
                weight = 1.0
            }
            let r = rect.standardized
            guard !r.isNull,
                  !r.isInfinite,
                  r.origin.x.isFinite,
                  r.origin.y.isFinite,
                  r.width > 0.5,
                  r.height > 0.5,
                  weight.isFinite,
                  weight > 0.0
            else {
                continue
            }
            output.append(GaussianFrostRegion(rect: r, weight: weight))
        }
        return output
    }

    internal static func clampedScale(for size: NSSize, requestedScale: CGFloat) -> CGFloat {
        let base = max(0.25, requestedScale.isFinite ? requestedScale : 1.0)
        let rawPixels = Double(size.width * base) * Double(size.height * base)
        let limit = Double(maxRasterPixels)
        if rawPixels > limit, rawPixels > 0.0 {
            return max(0.25, base * CGFloat(Foundation.sqrt(limit / rawPixels)))
        }
        return base
    }

    private static func rasterizeCGImage(
        size: NSSize,
        scale: CGFloat,
        lobes: [GaussianFrostLobe]
    ) -> CGImage? {
        let pixelWidth = max(1, Int(Foundation.ceil(size.width * scale)))
        let pixelHeight = max(1, Int(Foundation.ceil(size.height * scale)))
        let totalPixels = pixelWidth * pixelHeight
        guard totalPixels > 0 else { return nil }

        let invScale = 1.0 / Double(scale)
        let cardWidth = Double(size.width)
        let cardHeight = Double(size.height)
        let inset = Double(rimFadeInset)

        // Precompute 1D horizontal & vertical rim smoothsteps.
        var rimX = [Double](repeating: 1.0, count: pixelWidth)
        for px in 0..<pixelWidth {
            let x = (Double(px) + 0.5) * invScale
            let dist = min(x, cardWidth - x)
            if dist <= 0.0 {
                rimX[px] = 0.0
            } else if dist < inset {
                let t = dist / inset
                rimX[px] = t * t * (3.0 - 2.0 * t)
            }
        }

        var rimY = [Double](repeating: 1.0, count: pixelHeight)
        for py in 0..<pixelHeight {
            let y = (Double(py) + 0.5) * invScale
            let dist = min(y, cardHeight - y)
            if dist <= 0.0 {
                rimY[py] = 0.0
            } else if dist < inset {
                let t = dist / inset
                rimY[py] = t * t * (3.0 - 2.0 * t)
            }
        }

        // Accumulate Gaussian density per lobe within its 3.5-sigma bounding box.
        var densityBuffer = [Float](repeating: 0.0, count: totalPixels)
        let maxU2Float = Float(cutoffSigmaSquared)
        let tailFloat = Float(cutoffTailValue)
        let normFloat = Float(cutoffNormalization)
        let s = Double(scale)

        for lobe in lobes {
            let cx = Double(lobe.center.x)
            let cy = Double(lobe.center.y)
            let radiusX = cutoffSigma * lobe.sigmaX
            let radiusY = cutoffSigma * lobe.sigmaY

            let minPX = max(0, Int(Foundation.floor((cx - radiusX) * s - 0.5)))
            let maxPX = min(pixelWidth - 1, Int(Foundation.ceil((cx + radiusX) * s + 0.5)))
            let minPY = max(0, Int(Foundation.floor((cy - radiusY) * s - 0.5)))
            let maxPY = min(pixelHeight - 1, Int(Foundation.ceil((cy + radiusY) * s + 0.5)))

            if minPX > maxPX || minPY > maxPY {
                continue
            }

            let invSX = 1.0 / lobe.sigmaX
            let invSY = 1.0 / lobe.sigmaY
            let amp = Float(lobe.amplitude)
            let spanX = maxPX - minPX + 1

            var nx2Table = [Float](repeating: 0.0, count: spanX)
            for idx in 0..<spanX {
                let px = minPX + idx
                let x = (Double(px) + 0.5) * invScale
                let nx = (x - cx) * invSX
                nx2Table[idx] = Float(nx * nx)
            }

            for py in minPY...maxPY {
                let y = (Double(py) + 0.5) * invScale
                let ny = (y - cy) * invSY
                let ny2 = Float(ny * ny)
                if ny2 >= maxU2Float {
                    continue
                }
                let rowBase = py * pixelWidth + minPX
                for idx in 0..<spanX {
                    let u2 = nx2Table[idx] + ny2
                    if u2 < maxU2Float {
                        let g = (Foundation.exp(-0.5 * u2) - tailFloat) * normFloat
                        if g > 0.0 {
                            densityBuffer[rowBase + idx] += amp * g
                        }
                    }
                }
            }
        }

        // Convert accumulated density into premultiplied RGBA (R=G=B=A=alpha).
        // Row 0 of the buffer corresponds to y ≈ 0 (top edge of the SwiftUI card).
        var pixels = [UInt32](repeating: 0, count: totalPixels)
        for py in 0..<pixelHeight {
            let ry = rimY[py]
            if ry <= 0.0 { continue }
            let rowOffset = py * pixelWidth
            for px in 0..<pixelWidth {
                let d = Double(densityBuffer[rowOffset + px])
                if d <= 0.0 { continue }
                let rx = rimX[px]
                let rim = min(rx, ry)
                if rim <= 0.0 { continue }
                let op = min(maxOpacity, maxOpacity * (1.0 - Foundation.exp(-d))) * rim
                let alphaByte = UInt8(clamping: min(Int((maxOpacity * 255.0).rounded(.down)), Int((op * 255.0).rounded())))
                if alphaByte > 0 {
                    pixels[rowOffset + px] = UInt32(alphaByte) * 0x01010101
                }
            }
        }

        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let bitmapInfo = CGBitmapInfo(
            rawValue: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue
        )
        let bytesPerRow = pixelWidth * MemoryLayout<UInt32>.size
        let pixelData = pixels.withUnsafeBufferPointer { Data(buffer: $0) }
        guard let provider = CGDataProvider(data: pixelData as CFData) else {
            return nil
        }

        return CGImage(
            width: pixelWidth,
            height: pixelHeight,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: bitmapInfo,
            provider: provider,
            decode: nil,
            shouldInterpolate: true,
            intent: .defaultIntent
        )
    }
}

// MARK: - Quantized Geometry Fingerprint & Thread-Safe LRU Cache

internal struct CacheKey: Hashable, Sendable {
    private struct QuantizedRegion: Hashable, Sendable {
        let x: Int
        let y: Int
        let w: Int
        let h: Int
        let weight: Int
    }

    private let widthQ: Int
    private let heightQ: Int
    private let scaleQ: Int
    private let regions: [QuantizedRegion]

    init(size: NSSize, scale: CGFloat, regions: [GaussianFrostRegion]) {
        self.widthQ = Int((size.width * 2.0).rounded())
        self.heightQ = Int((size.height * 2.0).rounded())
        self.scaleQ = Int((scale * 20.0).rounded())
        var quantized: [QuantizedRegion] = []
        quantized.reserveCapacity(regions.count)
        for item in regions {
            let r = item.rect.standardized
            quantized.append(
                QuantizedRegion(
                    x: Int((r.minX * 2.0).rounded()),
                    y: Int((r.minY * 2.0).rounded()),
                    w: Int((r.width * 2.0).rounded()),
                    h: Int((r.height * 2.0).rounded()),
                    weight: Int((item.weight * 200.0).rounded())
                )
            )
        }
        self.regions = quantized
    }
}

private final class MaskCache: @unchecked Sendable {
    private let lock = NSLock()
    private let capacity: Int
    private var storage: [CacheKey: CGImage] = [:]
    private var order: [CacheKey] = []
    private var hitCount: Int = 0
    private var missCount: Int = 0

    init(capacity: Int) {
        self.capacity = max(1, capacity)
    }

    var hits: Int {
        lock.lock()
        defer { lock.unlock() }
        return hitCount
    }

    var misses: Int {
        lock.lock()
        defer { lock.unlock() }
        return missCount
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return storage.count
    }

    func value(for key: CacheKey) -> CGImage? {
        lock.lock()
        defer { lock.unlock() }
        if let image = storage[key] {
            hitCount += 1
            if let idx = order.firstIndex(of: key) {
                order.remove(at: idx)
            }
            order.append(key)
            return image
        }
        missCount += 1
        return nil
    }

    func insert(_ image: CGImage, for key: CacheKey) {
        lock.lock()
        defer { lock.unlock() }
        if storage[key] != nil {
            if let idx = order.firstIndex(of: key) {
                order.remove(at: idx)
            }
        } else if order.count >= capacity, let oldest = order.first {
            order.removeFirst()
            storage.removeValue(forKey: oldest)
        }
        storage[key] = image
        order.append(key)
    }

    func clear() {
        lock.lock()
        defer { lock.unlock() }
        storage.removeAll()
        order.removeAll()
        hitCount = 0
        missCount = 0
    }
}

// MARK: - SwiftUI Field Modifier & Anchor Resolution

private struct GaussianFrostFieldModifier: ViewModifier {
    let enabled: Bool
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        if enabled && !Self.shouldDisableForAccessibility(reduceTransparency: reduceTransparency) {
            content.backgroundPreferenceValue(GaussianFrostAnchorPreferenceKey.self) { items in
                GeometryReader { proxy in
                    let cardSize = proxy.size
                    let resolved = Self.resolveRegions(
                        items: items,
                        proxy: proxy,
                        cardSize: cardSize
                    )
                    GaussianFrostNativeRepresentable(cardSize: cardSize, regions: resolved)
                        .frame(width: cardSize.width, height: cardSize.height)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
                .allowsHitTesting(false)
                .accessibilityHidden(true)
            }
        } else {
            content
        }
    }

    private static func shouldDisableForAccessibility(reduceTransparency: Bool) -> Bool {
        if reduceTransparency {
            return true
        }
        let workspace = NSWorkspace.shared
        return workspace.accessibilityDisplayShouldReduceTransparency ||
            workspace.accessibilityDisplayShouldIncreaseContrast
    }

    private static func resolveRegions(
        items: [GaussianFrostAnchorItem],
        proxy: GeometryProxy,
        cardSize: CGSize
    ) -> [GaussianFrostRegion] {
        guard cardSize.width > 1.0, cardSize.height > 1.0, !items.isEmpty else {
            return []
        }

        let validBounds = CGRect(origin: .zero, size: cardSize).insetBy(dx: -36.0, dy: -36.0)
        var regions: [GaussianFrostRegion] = []
        regions.reserveCapacity(items.count)

        for item in items {
            guard item.weight > 0.0, item.weight.isFinite else { continue }
            let rawRect = proxy[item.bounds].standardized
            guard !rawRect.isNull,
                  !rawRect.isInfinite,
                  rawRect.origin.x.isFinite,
                  rawRect.origin.y.isFinite,
                  rawRect.width > 0.5,
                  rawRect.height > 0.5,
                  validBounds.intersects(rawRect)
            else {
                continue
            }

            let clipped = rawRect.intersection(validBounds)
            guard !clipped.isNull, clipped.width > 0.5, clipped.height > 0.5 else {
                continue
            }

            // Guard against accidental full-card container anchors while allowing wide thin bars.
            if cardSize.height > 44.0,
               clipped.width >= cardSize.width * 0.95,
               clipped.height >= cardSize.height * 0.85 {
                continue
            }

            regions.append(GaussianFrostRegion(rect: clipped, weight: item.weight))
        }
        return regions
    }
}

private struct GaussianFrostNativeRepresentable: NSViewRepresentable {
    let cardSize: CGSize
    let regions: [GaussianFrostRegion]

    func makeNSView(context: Context) -> GaussianFrostBackgroundView {
        let view = GaussianFrostBackgroundView(
            frame: NSRect(origin: .zero, size: cardSize)
        )
        view.configure(cardSize: cardSize, regions: regions)
        return view
    }

    func updateNSView(_ nsView: GaussianFrostBackgroundView, context: Context) {
        nsView.configure(cardSize: cardSize, regions: regions)
    }
}

// MARK: - Native Non-Interactive Aggregate Frost & White-Wash NSView

private final class NonInteractiveFlippedView: NSView {
    override var isFlipped: Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func isAccessibilityElement() -> Bool { false }
}

/// Native aggregate background view hosting a single `NSVisualEffectView` and white-wash overlay
/// masked together by a single parent `CALayer` Gaussian mask. Focus and pointer position never change it.
public final class GaussianFrostBackgroundView: NSView {
    public let effectView = NSVisualEffectView()

    private let washContainerView = NonInteractiveFlippedView()
    private let baseWhiteLayer = CALayer()
    private let washMaskLayer = CALayer()

    private var lastAppliedCacheKey: CacheKey?

    public private(set) var currentMaskCGImage: CGImage?
    public var baseWhiteOpacity: Float { baseWhiteLayer.opacity }
    /// Maximum final composited frost-group opacity (`Double(whiteOpacity) * GaussianFrostMask.maxOpacity <= 0.36`).
    /// With a single parent `CALayer` mask (`layer?.mask = washMaskLayer`) and parent `alphaValue = CGFloat(whiteOpacity)`,
    /// the entire native frost group (backdrop blur material + white-wash overlay) multiplied by the 8-bit mask
    /// (`<= 244 / 255 <= 0.96`) is strictly bounded at `<= 0.36` even over an opaque internal material.
    public static let maxWhiteOpacity: Double = GaussianFrostMask.maxWhiteOpacity
    /// Unmasked group & white-wash opacity derived from `maxWhiteOpacity / GaussianFrostMask.maxOpacity` (`0.36 / 0.96`).
    public static let whiteOpacity: Float = {
        let raw = Float(maxWhiteOpacity / GaussianFrostMask.maxOpacity)
        return Double(raw) * GaussianFrostMask.maxOpacity > maxWhiteOpacity ? raw.nextDown : raw
    }()
    // The approved Liquid Glass theme adds only a soft contrast wash.
    // Native NSGlassEffectView supplies the optics; no extra text blur is mounted.
    public static let effectAlpha: CGFloat = 0.0

    override public var isFlipped: Bool { true }

    override public init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setupHierarchy()
    }

    required public init?(coder: NSCoder) {
        super.init(coder: coder)
        setupHierarchy()
    }

    // MARK: Non-Interactive & Accessibility Guarantees

    override public func hitTest(_ point: NSPoint) -> NSView? {
        nil
    }

    override public var acceptsFirstResponder: Bool {
        false
    }

    override public func isAccessibilityElement() -> Bool {
        false
    }

    override public func accessibilityRole() -> NSAccessibility.Role? {
        .unknown
    }

    // MARK: Setup & Layout

    private func setupHierarchy() {
        wantsLayer = true
        alphaValue = CGFloat(Self.whiteOpacity)
        layer?.masksToBounds = true

        // 1. Native behind-window Aqua popover blur view (never uses NSVisualEffectView.maskImage).
        effectView.frame = bounds
        effectView.autoresizingMask = [.width, .height]
        effectView.material = .popover
        effectView.blendingMode = .behindWindow
        effectView.state = .active
        effectView.appearance = NSAppearance(named: .aqua)
        effectView.alphaValue = Self.effectAlpha
        effectView.isEmphasized = false
        effectView.maskImage = nil
        effectView.isHidden = true
        if Self.effectAlpha > 0 { addSubview(effectView) }

        // 2. Flipped white-wash container participating in the shared parent CALayer mask.
        washContainerView.frame = bounds
        washContainerView.autoresizingMask = [.width, .height]
        washContainerView.wantsLayer = true
        washContainerView.isHidden = true
        addSubview(washContainerView)

        guard let washRootLayer = washContainerView.layer else { return }
        washRootLayer.masksToBounds = true
        washRootLayer.mask = nil

        let disabledActions: [String: CAAction] = [
            "bounds": NSNull(),
            "position": NSNull(),
            "frame": NSNull(),
            "contents": NSNull(),
            "sublayers": NSNull(),
            "hidden": NSNull()
        ]

        baseWhiteLayer.frame = bounds
        baseWhiteLayer.backgroundColor = NSColor(calibratedWhite: 1.0, alpha: 1.0).cgColor
        baseWhiteLayer.opacity = Self.whiteOpacity
        baseWhiteLayer.actions = disabledActions
        washRootLayer.addSublayer(baseWhiteLayer)

        washMaskLayer.frame = bounds
        washMaskLayer.contentsGravity = .resize
        washMaskLayer.actions = disabledActions
        layer?.mask = washMaskLayer
    }

    override public func layout() {
        super.layout()
        syncLayerFrames()
    }

    override public func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        syncLayerFrames()
    }

    private func syncLayerFrames() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        effectView.frame = bounds
        washContainerView.frame = bounds
        baseWhiteLayer.frame = bounds
        washMaskLayer.frame = bounds
        if layer?.mask !== washMaskLayer {
            layer?.mask = washMaskLayer
        }
        CATransaction.commit()
    }

    // MARK: Field Configuration & Cached Mask Application

    public func configure(
        cardSize: NSSize,
        regions: [CGRect],
        weights: [Double]? = nil
    ) {
        let normalized = GaussianFrostMask.normalizeRegions(regions: regions, weights: weights)
        configure(cardSize: cardSize, regions: normalized)
    }

    public func configure(
        cardSize: NSSize,
        regions: [GaussianFrostRegion]
    ) {
        let workspace = NSWorkspace.shared
        if workspace.accessibilityDisplayShouldReduceTransparency ||
            workspace.accessibilityDisplayShouldIncreaseContrast ||
            cardSize.width < 1.0 ||
            cardSize.height < 1.0 {
            clearMaskPresentation()
            return
        }

        if frame.size != cardSize {
            frame = NSRect(origin: .zero, size: cardSize)
            syncLayerFrames()
        }

        let validRegions = regions.filter {
            !$0.rect.isNull &&
            !$0.rect.isInfinite &&
            $0.rect.width > 0.5 &&
            $0.rect.height > 0.5 &&
            $0.weight > 0.0 &&
            $0.weight.isFinite
        }

        guard !validRegions.isEmpty else {
            clearMaskPresentation()
            return
        }

        let scale = GaussianFrostMask.clampedScale(for: cardSize, requestedScale: 1.0)
        let key = CacheKey(size: cardSize, scale: scale, regions: validRegions)
        if key == lastAppliedCacheKey, currentMaskCGImage != nil {
            alphaValue = CGFloat(Self.whiteOpacity)
            effectView.state = .active
            effectView.isEmphasized = false
            effectView.maskImage = nil
            if layer?.mask !== washMaskLayer {
                layer?.mask = washMaskLayer
            }
            effectView.isHidden = Self.effectAlpha == 0
            washContainerView.isHidden = false
            return
        }

        guard let cgImage = GaussianFrostMask.makeCGImage(
            size: cardSize,
            regions: validRegions,
            scale: scale
        ) else {
            clearMaskPresentation()
            return
        }

        lastAppliedCacheKey = key
        currentMaskCGImage = cgImage

        CATransaction.begin()
        CATransaction.setDisableActions(true)
        alphaValue = CGFloat(Self.whiteOpacity)
        effectView.state = .active
        effectView.isEmphasized = false
        effectView.maskImage = nil
        washMaskLayer.frame = bounds
        washMaskLayer.contents = cgImage
        if layer?.mask !== washMaskLayer {
            layer?.mask = washMaskLayer
        }
        effectView.isHidden = Self.effectAlpha == 0
        washContainerView.isHidden = false
        CATransaction.commit()
    }

    private func clearMaskPresentation() {
        lastAppliedCacheKey = nil
        currentMaskCGImage = nil
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        effectView.maskImage = nil
        effectView.isHidden = true
        washMaskLayer.contents = nil
        washContainerView.isHidden = true
        CATransaction.commit()
    }
}
