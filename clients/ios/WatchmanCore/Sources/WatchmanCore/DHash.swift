import Foundation

/// The image hash from docs/api-contract.md: grayscale, area-average to 9 by 8, round to integers,
/// one bit per horizontal neighbour pair. Must reproduce models/test-vectors/dhash/vectors.json.
public enum DHash {
    static let w = 9
    static let h = 8

    /// `rgba` is width*height*4 bytes, row major (as CGImage data in RGBA8 gives it).
    public static func compute(rgba: [UInt8], width: Int, height: Int) -> String {
        var gray = [Double](repeating: 0, count: width * height)
        for i in 0..<gray.count {
            let p = i * 4
            gray[i] = 0.299 * Double(rgba[p]) + 0.587 * Double(rgba[p + 1]) + 0.114 * Double(rgba[p + 2])
        }
        return fromCells(areaAverage(gray, inW: width, inH: height))
    }

    /// `rgb` is packed 0xRRGGBB ints (the raw.json vectors).
    public static func compute(packedRGB: [UInt32], width: Int, height: Int) -> String {
        var gray = [Double](repeating: 0, count: width * height)
        for i in 0..<gray.count {
            let p = packedRGB[i]
            gray[i] = 0.299 * Double((p >> 16) & 0xFF) + 0.587 * Double((p >> 8) & 0xFF) + 0.114 * Double(p & 0xFF)
        }
        return fromCells(areaAverage(gray, inW: width, inH: height))
    }

    public static func areaAverage(_ gray: [Double], inW: Int, inH: Int) -> [Double] {
        var out = [Double](repeating: 0, count: w * h)
        for oy in 0..<h {
            let y0 = Double(oy) * Double(inH) / Double(h)
            let y1 = Double(oy + 1) * Double(inH) / Double(h)
            for ox in 0..<w {
                let x0 = Double(ox) * Double(inW) / Double(w)
                let x1 = Double(ox + 1) * Double(inW) / Double(w)
                var total = 0.0
                var area = 0.0
                var y = Int(y0.rounded(.down))
                while y < min(inH, Int(y1.rounded(.up))) {
                    let cy = min(Double(y + 1), y1) - max(Double(y), y0)
                    if cy > 0 {
                        var x = Int(x0.rounded(.down))
                        while x < min(inW, Int(x1.rounded(.up))) {
                            let cx = min(Double(x + 1), x1) - max(Double(x), x0)
                            if cx > 0 {
                                total += gray[y * inW + x] * cx * cy
                                area += cx * cy
                            }
                            x += 1
                        }
                    }
                    y += 1
                }
                out[oy * w + ox] = total / area
            }
        }
        return out
    }

    public static func fromCells(_ small: [Double]) -> String {
        let cells = small.map { Int(($0 + 0.5).rounded(.down)) }
        var bits: UInt64 = 0
        for row in 0..<h {
            for col in 0..<(w - 1) where cells[row * w + col] > cells[row * w + col + 1] {
                bits |= 1 << UInt64(63 - (row * 8 + col))
            }
        }
        let hex = String(bits, radix: 16)
        return String(repeating: "0", count: 16 - hex.count) + hex
    }

    public static func hamming(_ a: String, _ b: String) -> Int {
        let x = (UInt64(a, radix: 16) ?? 0) ^ (UInt64(b, radix: 16) ?? 0)
        return x.nonzeroBitCount
    }

    public static func matches(_ a: String, _ b: String, tolerance: Int = 6) -> Bool {
        hamming(a, b) <= tolerance
    }
}
