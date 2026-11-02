import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import WatchmanCore
import onnxruntime_objc

/// Pixel helpers for the broadcast extension: a CGImage into RGBA bytes at a size, and a JPEG.
enum Pixels {
    static func rgba(_ image: CGImage, width: Int, height: Int) -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let space = CGColorSpaceCreateDeviceRGB()
        bytes.withUnsafeMutableBytes { buf in
            guard let ctx = CGContext(data: buf.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                      space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
            ctx.interpolationQuality = .medium
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        }
        return bytes
    }

    static func jpeg(_ image: CGImage, maxSide: Int, quality: Double = 0.85) -> Data? {
        let scale = min(1.0, Double(maxSide) / Double(max(image.width, image.height)))
        let w = max(1, Int(Double(image.width) * scale))
        let h = max(1, Int(Double(image.height) * scale))
        let bytes = rgba(image, width: w, height: h)
        guard let provider = CGDataProvider(data: Data(bytes) as CFData),
              let scaled = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: w * 4,
                                   space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                                   provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) else { return nil }
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, scaled, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        return CGImageDestinationFinalize(dest) ? data as Data : nil
    }
}

/// The crop classifier on ONNX Runtime (CoreML execution provider when available, else CPU).
final class OnnxScorer {
    let size: Int
    private let entry: ModelEntry
    private let env: ORTEnv
    private let session: ORTSession
    private let inputName: String

    init(entry: ModelEntry, modelPath: String) throws {
        self.entry = entry
        size = entry.input.size
        env = try ORTEnv(loggingLevel: .warning)
        let options = try ORTSessionOptions()
        try options.setIntraOpNumThreads(2)
        session = try ORTSession(env: env, modelPath: modelPath, sessionOptions: options)
        inputName = try session.inputNames().first ?? entry.input.name
    }

    /// `rgba` is size*size*4 bytes. Returns the probability the image is explicit, or nil on failure.
    func score(_ rgba: [UInt8]) -> Double? {
        let n = size * size
        var tensor = [Float](repeating: 0, count: 3 * n)
        let order = entry.input.color == "BGR" ? [2, 1, 0] : [0, 1, 2]
        for i in 0..<n {
            for c in 0..<3 {
                let v = Float((Double(rgba[i * 4 + order[c]]) / entry.input.scale - entry.input.mean[c]) / entry.input.std[c])
                if entry.input.layout == "NCHW" { tensor[c * n + i] = v } else { tensor[i * 3 + c] = v }
            }
        }
        let shape: [NSNumber] = entry.input.layout == "NCHW" ? [1, 3, NSNumber(value: size), NSNumber(value: size)] : [1, NSNumber(value: size), NSNumber(value: size), 3]
        do {
            let data = NSMutableData(bytes: tensor, length: tensor.count * MemoryLayout<Float>.size)
            let input = try ORTValue(tensorData: data, elementType: .float, shape: shape)
            let outputs = try session.run(withInputs: [inputName: input], outputNames: Set(try session.outputNames()), runOptions: nil)
            guard let out = outputs.values.first else { return nil }
            let outData = try out.tensorData() as Data
            let logits = outData.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
            return softmax(logits)
        } catch {
            return nil
        }
    }

    private func softmax(_ logits: [Float]) -> Double {
        let labels = entry.output.labels
        let idx = labels.firstIndex(of: entry.output.positive ?? labels[0]) ?? 0
        if entry.output.kind == "sigmoid" { return 1 / (1 + exp(-Double(logits[idx]))) }
        let m = logits.max() ?? 0
        let exps = logits.map { exp(Double($0 - m)) }
        return exps[idx] / exps.reduce(0, +)
    }
}
